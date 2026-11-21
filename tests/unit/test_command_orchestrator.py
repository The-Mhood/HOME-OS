from datetime import datetime, timezone
from pathlib import Path
import pytest

from home_os.adapters.base import (
    AdapterCommand,
    AdapterRegistry,
    AuthProof,
    AuthResult,
    BaseDeviceAdapter,
    CapabilityDescriptor,
    CommandExecutionReceipt,
    DeviceIdentity,
    DeviceStatusSnapshot,
    DiscoveredEndpoint,
    PairingChallenge,
    PairingRequest,
)
from home_os.db.models import (
    Base,
    Device,
    DeviceCapability,
    DeviceCredential,
    DevicePermission,
    User,
)
from home_os.db.session import create_db_engine_and_session
from home_os.security.crypto import CredentialVault, hash_password, hash_token
from home_os.services.command_orchestrator import CommandOrchestrator, CommandPipelineError
from home_os.services.event_bus import EventBus


class DummyTestAdapter(BaseDeviceAdapter):
    adapter_type = "dummy_test"

    def __init__(self):
        self.fail_count_remaining = 0
        self.verify_state = True
        self.executed: list[AdapterCommand] = []

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]:
        return []

    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        raise NotImplementedError

    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        raise NotImplementedError

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        raise NotImplementedError

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        return DeviceStatusSnapshot(
            device_id=device_id,
            online=True,
            battery_level=85,
            attributes={"torch_on": True},
        )

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        return []

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        if self.fail_count_remaining > 0:
            self.fail_count_remaining -= 1
            raise TimeoutError("Transient device socket timeout")
        self.executed.append(command)
        return CommandExecutionReceipt(
            command_id=command.command_id,
            acknowledged=True,
            state_verified=self.verify_state,
            verification_method="post_state_readback" if self.verify_state else "dispatch_ack_only",
            result={"applied": command.parameters},
        )

    async def subscribe_events(self, device_id: str, callback):
        return None

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        return None

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        return None


@pytest.mark.asyncio
async def test_eight_step_command_pipeline_enforces_all_gates(tmp_data_dir: Path):
    engine, SessionLocal = create_db_engine_and_session(f"sqlite:///{tmp_data_dir / 'cmd.db'}")
    Base.metadata.create_all(bind=engine)
    vault = CredentialVault(tmp_data_dir / "master.key")
    registry = AdapterRegistry()
    adapter = DummyTestAdapter()
    registry.register(adapter)
    bus = EventBus()

    orchestrator = CommandOrchestrator(adapter_registry=registry, event_bus=bus)

    with SessionLocal() as db:
        owner = User(
            id="u-owner",
            username="owner",
            display_name="Owner",
            password_hash=hash_password("StrongPass!123"),
            role="owner",
            is_active=True,
        )
        # 1. Discovered-only device (NOT enrolled)
        unpaired = Device(
            id="dev-unpaired",
            hardware_fingerprint="fp-unpaired",
            name="Unknown Smart TV",
            category="tv",
            lifecycle_state="discovered",
            connection_status="unpaired",
            adapter_type="dummy_test",
        )
        # 2. Authenticated device
        enrolled = Device(
            id="dev-enrolled",
            hardware_fingerprint="fp-enrolled",
            name="Pixel 9 Pro",
            category="smartphone",
            lifecycle_state="authenticated",
            connection_status="online",
            adapter_type="dummy_test",
            enrolled_at=datetime.now(timezone.utc),
        )
        cred = DeviceCredential(
            device_id="dev-enrolled",
            token_hash=hash_token("tok-123"),
            encrypted_hmac_secret=vault.encrypt("hmac-123", aad="device:dev-enrolled"),
            key_version=1,
        )
        cap_torch = DeviceCapability(
            device_id="dev-enrolled",
            capability_key="flashlight.set",
            category="hardware",
            display_name="Flashlight",
            supported=True,
            requires_permission="flashlight:control",
            requires_confirmation=False,
            parameters_schema_json={"required": ["enabled"], "properties": {"enabled": {"type": "boolean"}}},
        )
        cap_sms_unsupported = DeviceCapability(
            device_id="dev-enrolled",
            capability_key="sms.send",
            category="communications",
            display_name="Send SMS",
            supported=False,
            unavailable_reason="SMS role not granted on Google Play build",
            requires_permission="sms:send",
            requires_confirmation=True,
        )
        cap_lock = DeviceCapability(
            device_id="dev-enrolled",
            capability_key="lock.unlock",
            category="security",
            display_name="Unlock Door",
            supported=True,
            requires_permission="smarthome:sensitive",
            requires_confirmation=True,
            parameters_schema_json={"required": ["pin_verified"], "properties": {"pin_verified": {"type": "boolean"}}},
        )
        db.add_all([owner, unpaired, enrolled, cred, cap_torch, cap_sms_unsupported, cap_lock])
        db.commit()

        # Gate A: Unenrolled discovered device MUST be rejected
        with pytest.raises(CommandPipelineError) as exc_unpaired:
            await orchestrator.submit_command(
                db=db,
                user_id="u-owner",
                device_id="dev-unpaired",
                capability_key="flashlight.set",
                parameters={"enabled": True},
            )
        assert exc_unpaired.value.code == "DEVICE_NOT_AUTHENTICATED"

        # Gate B: Unsupported capability MUST be rejected with honest reason
        with pytest.raises(CommandPipelineError) as exc_unsup:
            await orchestrator.submit_command(
                db=db,
                user_id="u-owner",
                device_id="dev-enrolled",
                capability_key="sms.send",
                parameters={"to": "+15550001", "body": "Hi"},
            )
        assert exc_unsup.value.code == "CAPABILITY_UNSUPPORTED"
        assert "Google Play" in exc_unsup.value.message

        # Gate C: Permission not yet granted -> PERMISSION_DENIED
        with pytest.raises(CommandPipelineError) as exc_perm:
            await orchestrator.submit_command(
                db=db,
                user_id="u-owner",
                device_id="dev-enrolled",
                capability_key="flashlight.set",
                parameters={"enabled": True},
            )
        assert exc_perm.value.code == "PERMISSION_DENIED"

        # Grant permission
        perm = DevicePermission(
            device_id="dev-enrolled",
            permission_scope="flashlight:control",
            status="granted",
            granted_by_user_id="u-owner",
        )
        perm_lock = DevicePermission(
            device_id="dev-enrolled",
            permission_scope="smarthome:sensitive",
            status="granted",
            granted_by_user_id="u-owner",
        )
        db.add_all([perm, perm_lock])
        db.commit()

        # Gate D: Invalid parameter schema -> INVALID_PARAMETERS
        with pytest.raises(CommandPipelineError) as exc_param:
            await orchestrator.submit_command(
                db=db,
                user_id="u-owner",
                device_id="dev-enrolled",
                capability_key="flashlight.set",
                parameters={"enabled": "not-a-bool"},
            )
        assert exc_param.value.code == "INVALID_PARAMETERS"

        # Gate E: Transient error retried and verified state recorded
        adapter.fail_count_remaining = 1
        adapter.verify_state = True
        execution = await orchestrator.submit_command(
            db=db,
            user_id="u-owner",
            device_id="dev-enrolled",
            capability_key="flashlight.set",
            parameters={"enabled": True},
            max_retries=2,
        )
        assert execution.status == "verified"
        assert execution.retry_count == 1
        assert execution.verification_method == "post_state_readback"

        # Gate F: Dispatch without physical state verification -> 'acknowledged' (NEVER falsely 'verified')
        adapter.verify_state = False
        exec_ack = await orchestrator.submit_command(
            db=db,
            user_id="u-owner",
            device_id="dev-enrolled",
            capability_key="flashlight.set",
            parameters={"enabled": False},
        )
        assert exec_ack.status == "acknowledged"

        # Gate G: Sensitive command requires explicit user confirmation first
        pending_exec = await orchestrator.submit_command(
            db=db,
            user_id="u-owner",
            device_id="dev-enrolled",
            capability_key="lock.unlock",
            parameters={"pin_verified": True},
        )
        assert pending_exec.status == "pending_confirmation"

        adapter.verify_state = True
        approved_exec = await orchestrator.approve_pending_command(
            db=db,
            command_id=pending_exec.id,
            approver_user_id="u-owner",
        )
        assert approved_exec.status == "verified"

        # Gate H: Isolated device rejects commands immediately
        enrolled.lifecycle_state = "isolated"
        enrolled.connection_status = "isolated"
        db.commit()
        with pytest.raises(CommandPipelineError) as exc_iso:
            await orchestrator.submit_command(
                db=db,
                user_id="u-owner",
                device_id="dev-enrolled",
                capability_key="flashlight.set",
                parameters={"enabled": True},
            )
        assert exc_iso.value.code == "DEVICE_ISOLATED"
