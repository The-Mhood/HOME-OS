import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.adapters.base import AdapterCommand, AdapterRegistry
from home_os.db.models import (
    CommandExecution,
    Device,
    DeviceCapability,
    DeviceCredential,
    DevicePermission,
    User,
)
from home_os.security.audit import append_audit_log
from home_os.services.event_bus import EventBus


class CommandPipelineError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 403):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _ensure_utc(dt_val: datetime) -> datetime:
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(timezone.utc)


def _validate_parameters(parameters: dict[str, Any], schema: dict[str, Any]) -> None:
    if not schema:
        return
    required = schema.get("required", [])
    for req_key in required:
        if req_key not in parameters:
            raise CommandPipelineError(
                "INVALID_PARAMETERS",
                f"Missing required parameter '{req_key}'",
                status_code=422,
            )

    props = schema.get("properties", {})
    type_map: dict[str, tuple[type, ...]] = {
        "boolean": (bool,),
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "object": (dict,),
        "array": (list,),
    }
    for key, val in parameters.items():
        if key not in props:
            continue
        spec = props[key]
        expected_type = spec.get("type")
        if expected_type == "integer" and isinstance(val, bool):
            raise CommandPipelineError("INVALID_PARAMETERS", f"Parameter '{key}' must be an integer", status_code=422)
        if expected_type == "number" and isinstance(val, bool):
            raise CommandPipelineError("INVALID_PARAMETERS", f"Parameter '{key}' must be a number", status_code=422)
        if expected_type in type_map and not isinstance(val, type_map[expected_type]):
            raise CommandPipelineError(
                "INVALID_PARAMETERS",
                f"Parameter '{key}' must be of type {expected_type}",
                status_code=422,
            )
        if "minimum" in spec and isinstance(val, (int, float)) and val < spec["minimum"]:
            raise CommandPipelineError(
                "INVALID_PARAMETERS",
                f"Parameter '{key}' must be >= {spec['minimum']}",
                status_code=422,
            )
        if "maximum" in spec and isinstance(val, (int, float)) and val > spec["maximum"]:
            raise CommandPipelineError(
                "INVALID_PARAMETERS",
                f"Parameter '{key}' must be <= {spec['maximum']}",
                status_code=422,
            )
        if "enum" in spec and val not in spec["enum"]:
            raise CommandPipelineError(
                "INVALID_PARAMETERS",
                f"Parameter '{key}' must be one of {spec['enum']}",
                status_code=422,
            )


class CommandOrchestrator:
    """Enforces the mandatory 8-step security, validation, execution, verification, and audit pipeline."""

    def __init__(self, adapter_registry: AdapterRegistry, event_bus: EventBus):
        self.adapter_registry = adapter_registry
        self.event_bus = event_bus

    async def submit_command(
        self,
        *,
        db: Session,
        user_id: str | None,
        device_id: str,
        capability_key: str,
        parameters: dict[str, Any] | None = None,
        automation_run_id: str | None = None,
        confirmed: bool = False,
        timeout_ms: int = 8000,
        max_retries: int = 1,
    ) -> CommandExecution:
        params = parameters or {}
        actor_type = "automation" if automation_run_id else "user"
        actor_id = automation_run_id or user_id

        # Step 1: Caller Authentication
        if automation_run_id is None:
            if not user_id:
                raise CommandPipelineError("UNAUTHENTICATED", "User authentication required", status_code=401)
            user = db.get(User, user_id)
            if user is None or not user.is_active:
                raise CommandPipelineError("UNAUTHENTICATED", "Active user account required", status_code=401)

        # Step 2: Device Authorization
        device = db.get(Device, device_id)
        if device is None:
            raise CommandPipelineError("DEVICE_NOT_FOUND", f"Device '{device_id}' does not exist", status_code=404)

        if device.lifecycle_state == "isolated" or device.connection_status == "isolated":
            append_audit_log(
                db,
                severity="warning",
                category="command",
                event_type="command.rejected.isolated",
                actor_type=actor_type,
                actor_id=actor_id,
                device_id=device_id,
                outcome="rejected",
                details={"capability_key": capability_key},
            )
            db.commit()
            raise CommandPipelineError(
                "DEVICE_ISOLATED",
                f"Device '{device.name}' is currently isolated/quarantined",
                status_code=403,
            )

        if device.lifecycle_state != "authenticated":
            append_audit_log(
                db,
                severity="warning",
                category="command",
                event_type="command.rejected.unauthenticated_device",
                actor_type=actor_type,
                actor_id=actor_id,
                device_id=device_id,
                outcome="rejected",
                details={"lifecycle_state": device.lifecycle_state, "capability_key": capability_key},
            )
            db.commit()
            raise CommandPipelineError(
                "DEVICE_NOT_AUTHENTICATED",
                f"Device '{device.name}' is in state '{device.lifecycle_state}' — enroll and authenticate before control",
                status_code=403,
            )

        cred = db.execute(select(DeviceCredential).where(DeviceCredential.device_id == device_id)).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if cred is None or cred.revoked_at is not None or (cred.expires_at and _ensure_utc(cred.expires_at) <= now):
            raise CommandPipelineError(
                "DEVICE_CREDENTIAL_INVALID",
                "Device credentials are missing, expired, or revoked",
                status_code=403,
            )

        # Step 3: Capability Validation
        cap = db.execute(
            select(DeviceCapability).where(
                DeviceCapability.device_id == device_id,
                DeviceCapability.capability_key == capability_key,
            )
        ).scalar_one_or_none()

        if cap is None:
            raise CommandPipelineError(
                "CAPABILITY_NOT_REGISTERED",
                f"Capability '{capability_key}' is not advertised by device '{device.name}'",
                status_code=400,
            )

        if not cap.supported:
            reason = cap.unavailable_reason or "Platform or hardware does not support this operation"
            append_audit_log(
                db,
                severity="info",
                category="command",
                event_type="command.rejected.unsupported",
                actor_type=actor_type,
                actor_id=actor_id,
                device_id=device_id,
                outcome="rejected",
                details={"capability_key": capability_key, "reason": reason},
            )
            db.commit()
            raise CommandPipelineError(
                "CAPABILITY_UNSUPPORTED",
                f"Capability '{capability_key}' is unavailable: {reason}",
                status_code=400,
            )

        # Step 4: Permission Validation
        perm = db.execute(
            select(DevicePermission).where(
                DevicePermission.device_id == device_id,
                DevicePermission.permission_scope == cap.requires_permission,
            )
        ).scalar_one_or_none()

        if (
            perm is None
            or perm.status != "granted"
            or perm.revoked_at is not None
            or (perm.expires_at and _ensure_utc(perm.expires_at) <= now)
        ):
            append_audit_log(
                db,
                severity="warning",
                category="security",
                event_type="command.rejected.permission_denied",
                actor_type=actor_type,
                actor_id=actor_id,
                device_id=device_id,
                outcome="denied",
                details={"capability_key": capability_key, "required_permission": cap.requires_permission},
            )
            db.commit()
            raise CommandPipelineError(
                "PERMISSION_DENIED",
                f"Permission '{cap.requires_permission}' is not granted for device '{device.name}'",
                status_code=403,
            )

        # Step 5: Parameter & Sensitive Consent Validation
        _validate_parameters(params, cap.parameters_schema_json or {})

        cmd_exec = CommandExecution(
            device_id=device_id,
            initiated_by_user_id=user_id,
            automation_run_id=automation_run_id,
            capability_key=capability_key,
            parameters_json=params,
            status="pending_confirmation" if (cap.requires_confirmation and not confirmed) else "queued",
            max_retries=max(0, max_retries),
            timeout_ms=timeout_ms,
        )
        db.add(cmd_exec)
        db.flush()

        if cap.requires_confirmation and not confirmed:
            append_audit_log(
                db,
                severity="warning",
                category="command",
                event_type="command.awaiting_confirmation",
                actor_type=actor_type,
                actor_id=actor_id,
                device_id=device_id,
                outcome="pending",
                details={"command_id": cmd_exec.id, "capability_key": capability_key},
            )
            db.commit()
            await self.event_bus.publish(
                "command.pending_confirmation",
                {
                    "command_id": cmd_exec.id,
                    "device_id": device_id,
                    "device_name": device.name,
                    "capability_key": capability_key,
                    "parameters": params,
                },
            )
            return cmd_exec

        # Steps 6, 7, 8: Dispatch, Verify, and Audit
        return await self._dispatch_and_verify(db=db, device=device, cmd_exec=cmd_exec, actor_type=actor_type, actor_id=actor_id)

    async def approve_pending_command(
        self,
        *,
        db: Session,
        command_id: str,
        approver_user_id: str,
    ) -> CommandExecution:
        approver = db.get(User, approver_user_id)
        if approver is None or not approver.is_active:
            raise CommandPipelineError("UNAUTHENTICATED", "Valid approver required", status_code=401)

        cmd_exec = db.get(CommandExecution, command_id)
        if cmd_exec is None:
            raise CommandPipelineError("COMMAND_NOT_FOUND", "Command not found", status_code=404)
        if cmd_exec.status != "pending_confirmation":
            raise CommandPipelineError("INVALID_STATE", f"Command is in state '{cmd_exec.status}'", status_code=400)

        device = db.get(Device, cmd_exec.device_id)
        if device is None:
            raise CommandPipelineError("DEVICE_NOT_FOUND", "Target device no longer exists", status_code=404)

        append_audit_log(
            db,
            severity="info",
            category="security",
            event_type="command.confirmation_approved",
            actor_type="user",
            actor_id=approver_user_id,
            device_id=device.id,
            outcome="approved",
            details={"command_id": cmd_exec.id, "capability_key": cmd_exec.capability_key},
        )
        cmd_exec.status = "queued"
        db.flush()

        return await self._dispatch_and_verify(
            db=db,
            device=device,
            cmd_exec=cmd_exec,
            actor_type="user",
            actor_id=approver_user_id,
        )

    async def reject_pending_command(
        self,
        *,
        db: Session,
        command_id: str,
        approver_user_id: str,
    ) -> CommandExecution:
        cmd_exec = db.get(CommandExecution, command_id)
        if cmd_exec is None:
            raise CommandPipelineError("COMMAND_NOT_FOUND", "Command not found", status_code=404)
        if cmd_exec.status != "pending_confirmation":
            raise CommandPipelineError("INVALID_STATE", f"Command is in state '{cmd_exec.status}'", status_code=400)

        cmd_exec.status = "rejected"
        cmd_exec.error_code = "OPERATOR_REJECTED"
        cmd_exec.error_message = "Rejected by operator during sensitive command confirmation"
        cmd_exec.completed_at = datetime.now(timezone.utc)
        append_audit_log(
            db,
            severity="warning",
            category="security",
            event_type="command.confirmation_rejected",
            actor_type="user",
            actor_id=approver_user_id,
            device_id=cmd_exec.device_id,
            outcome="rejected",
            details={"command_id": cmd_exec.id, "capability_key": cmd_exec.capability_key},
        )
        db.commit()
        return cmd_exec

    async def _dispatch_and_verify(
        self,
        *,
        db: Session,
        device: Device,
        cmd_exec: CommandExecution,
        actor_type: str,
        actor_id: str | None,
    ) -> CommandExecution:
        adapter = self.adapter_registry.get(device.adapter_type)
        if adapter is None:
            cmd_exec.status = "failed"
            cmd_exec.error_code = "ADAPTER_UNAVAILABLE"
            cmd_exec.error_message = f"No adapter registered for '{device.adapter_type}'"
            cmd_exec.completed_at = datetime.now(timezone.utc)
            db.commit()
            return cmd_exec

        cmd_exec.status = "dispatched"
        cmd_exec.dispatched_at = datetime.now(timezone.utc)
        db.commit()

        adapter_cmd = AdapterCommand(
            command_id=cmd_exec.id,
            capability_key=cmd_exec.capability_key,
            parameters=cmd_exec.parameters_json,
            timeout_ms=cmd_exec.timeout_ms,
        )

        attempt = 0
        last_exc: Exception | None = None
        receipt = None

        while attempt <= cmd_exec.max_retries:
            try:
                receipt = await asyncio.wait_for(
                    adapter.execute_command(device.id, adapter_cmd),
                    timeout=max(0.2, cmd_exec.timeout_ms / 1000.0),
                )
                break
            except (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError) as exc:
                last_exc = exc
                attempt += 1
                if attempt <= cmd_exec.max_retries:
                    cmd_exec.retry_count = attempt
                    await asyncio.sleep(min(0.05 * (2 ** (attempt - 1)), 0.5))
                else:
                    break
            except Exception as exc:
                last_exc = exc
                break

        now = datetime.now(timezone.utc)
        cmd_exec.completed_at = now

        # Step 7: Result Verification (never falsely report physical execution without verification!)
        if receipt is None:
            is_timeout = isinstance(last_exc, (TimeoutError, asyncio.TimeoutError))
            cmd_exec.status = "timed_out" if is_timeout else "failed"
            cmd_exec.error_code = "COMMAND_TIMEOUT" if is_timeout else "EXECUTION_ERROR"
            cmd_exec.error_message = str(last_exc) if last_exc else "Unknown execution failure"
        elif receipt.error_code or not receipt.acknowledged:
            cmd_exec.status = "failed"
            cmd_exec.error_code = receipt.error_code or "NOT_ACKNOWLEDGED"
            cmd_exec.error_message = receipt.error_message or "Device did not acknowledge command"
            cmd_exec.result_json = receipt.result
        else:
            cmd_exec.verification_method = receipt.verification_method
            cmd_exec.result_json = receipt.result
            cmd_exec.status = "verified" if receipt.state_verified else "acknowledged"

        # Step 8: Cryptographic Hash-Chained Audit Log & Real-Time Event Broadcast
        append_audit_log(
            db,
            severity="info" if cmd_exec.status in ("verified", "acknowledged") else "warning",
            category="command",
            event_type=f"command.{cmd_exec.status}",
            actor_type=actor_type,
            actor_id=actor_id,
            device_id=device.id,
            outcome=cmd_exec.status,
            details={
                "command_id": cmd_exec.id,
                "capability_key": cmd_exec.capability_key,
                "verification_method": cmd_exec.verification_method,
                "retry_count": cmd_exec.retry_count,
                "error_code": cmd_exec.error_code,
            },
        )
        db.commit()

        await self.event_bus.publish(
            "command.updated",
            {
                "command_id": cmd_exec.id,
                "device_id": device.id,
                "capability_key": cmd_exec.capability_key,
                "status": cmd_exec.status,
                "verification_method": cmd_exec.verification_method,
                "result": cmd_exec.result_json,
                "error_code": cmd_exec.error_code,
                "error_message": cmd_exec.error_message,
            },
        )
        return cmd_exec
