import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.db.models import AutomationRule, AutomationRun, Device
from home_os.security.audit import append_audit_log
from home_os.services.command_orchestrator import CommandOrchestrator, CommandPipelineError
from home_os.services.event_bus import EventBus


def _compare_values(actual: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "neq":
        return actual != expected
    if op == "lt":
        return actual is not None and actual < expected
    if op == "lte":
        return actual is not None and actual <= expected
    if op == "gt":
        return actual is not None and actual > expected
    if op == "gte":
        return actual is not None and actual >= expected
    if op == "contains":
        return expected in (actual or "")
    return False


class AutomationEngine:
    """Structured cross-device automation engine supporting event, schedule, and device-state triggers."""

    def __init__(self, orchestrator: CommandOrchestrator, event_bus: EventBus):
        self.orchestrator = orchestrator
        self.event_bus = event_bus

    def create_rule(
        self,
        *,
        db: Session,
        user_id: str,
        name: str,
        description: str | None,
        trigger_type: str,
        trigger_config: dict[str, Any],
        conditions: dict[str, Any] | None,
        actions: list[dict[str, Any]],
        retry_policy: dict[str, Any] | None = None,
        requires_confirmation: bool = False,
        enabled: bool = True,
    ) -> AutomationRule:
        if trigger_type not in ("event", "schedule", "device_state"):
            raise ValueError(f"Unsupported trigger_type '{trigger_type}'")
        if not actions:
            raise ValueError("Automation rule must contain at least one action")

        rule = AutomationRule(
            name=name,
            description=description,
            enabled=enabled,
            trigger_type=trigger_type,
            trigger_config_json=trigger_config,
            conditions_json=conditions or {"operator": "all", "clauses": []},
            actions_json=actions,
            retry_policy_json=retry_policy or {"max_retries": 1},
            requires_confirmation=requires_confirmation,
            created_by_user_id=user_id,
        )
        db.add(rule)
        append_audit_log(
            db,
            severity="info",
            category="automation",
            event_type="automation.rule_created",
            actor_type="user",
            actor_id=user_id,
            outcome="success",
            details={"rule_name": name, "trigger_type": trigger_type, "action_count": len(actions)},
        )
        db.commit()
        db.refresh(rule)
        return rule

    def _evaluate_conditions(self, db: Session, conditions: dict[str, Any], context: dict[str, Any]) -> bool:
        clauses = conditions.get("clauses") or []
        if not clauses:
            return True

        operator = str(conditions.get("operator", "all")).lower()
        outcomes: list[bool] = []

        for clause in clauses:
            ctype = clause.get("type")
            op = clause.get("op", "eq")
            expected = clause.get("value")

            if ctype == "device_status":
                dev = db.get(Device, clause.get("device_id", ""))
                actual = dev.connection_status if dev is not None else None
                outcomes.append(_compare_values(actual, op, expected))
            elif ctype == "device_battery":
                dev = db.get(Device, clause.get("device_id", ""))
                actual = dev.battery_level if dev is not None else None
                outcomes.append(_compare_values(actual, op, expected))
            elif ctype == "device_attribute":
                dev = db.get(Device, clause.get("device_id", ""))
                attr_key = clause.get("attribute", "")
                actual = (dev.metadata_json or {}).get(attr_key) if dev is not None else None
                outcomes.append(_compare_values(actual, op, expected))
            elif ctype == "time_window":
                now_hhmm = datetime.now(timezone.utc).strftime("%H:%M")
                start_hhmm = clause.get("after", "00:00")
                end_hhmm = clause.get("before", "23:59")
                outcomes.append(start_hhmm <= now_hhmm <= end_hhmm)
            elif ctype == "event_field":
                field_key = clause.get("field", "")
                outcomes.append(_compare_values(context.get(field_key), op, expected))
            else:
                outcomes.append(False)

        return any(outcomes) if operator == "any" else all(outcomes)

    async def evaluate_event(
        self,
        *,
        db: Session,
        event_type: str,
        event_payload: dict[str, Any] | None = None,
    ) -> list[AutomationRun]:
        rules = db.execute(
            select(AutomationRule).where(
                AutomationRule.enabled.is_(True),
                AutomationRule.trigger_type == "event",
            )
        ).scalars().all()

        runs: list[AutomationRun] = []
        ctx = event_payload or {}
        for rule in rules:
            expected_event = rule.trigger_config_json.get("event_type")
            if expected_event != event_type:
                continue
            if not self._evaluate_conditions(db, rule.conditions_json, ctx):
                continue
            run = await self.execute_rule(db=db, rule=rule, trigger_reason=f"event:{event_type}", confirmed=False)
            runs.append(run)
        return runs

    async def evaluate_device_state_change(
        self,
        *,
        db: Session,
        device_id: str,
        field: str,
        new_value: Any,
    ) -> list[AutomationRun]:
        rules = db.execute(
            select(AutomationRule).where(
                AutomationRule.enabled.is_(True),
                AutomationRule.trigger_type == "device_state",
            )
        ).scalars().all()

        runs: list[AutomationRun] = []
        for rule in rules:
            tcfg = rule.trigger_config_json
            if tcfg.get("device_id") != device_id:
                continue
            if tcfg.get("field") != field:
                continue
            if not _compare_values(new_value, tcfg.get("op", "eq"), tcfg.get("value")):
                continue
            if not self._evaluate_conditions(db, rule.conditions_json, {"device_id": device_id, field: new_value}):
                continue
            run = await self.execute_rule(
                db=db,
                rule=rule,
                trigger_reason=f"device_state:{device_id}.{field}={new_value}",
                confirmed=False,
            )
            runs.append(run)
        return runs

    async def evaluate_schedule_tick(self, *, db: Session, current_hhmm: str, weekday: int | None = None) -> list[AutomationRun]:
        rules = db.execute(
            select(AutomationRule).where(
                AutomationRule.enabled.is_(True),
                AutomationRule.trigger_type == "schedule",
            )
        ).scalars().all()

        runs: list[AutomationRun] = []
        for rule in rules:
            tcfg = rule.trigger_config_json
            if tcfg.get("at") != current_hhmm:
                continue
            days = tcfg.get("weekdays")
            if days and weekday is not None and weekday not in days:
                continue
            if not self._evaluate_conditions(db, rule.conditions_json, {"time": current_hhmm}):
                continue
            run = await self.execute_rule(
                db=db,
                rule=rule,
                trigger_reason=f"schedule:{current_hhmm}",
                confirmed=False,
            )
            runs.append(run)
        return runs

    async def execute_rule(
        self,
        *,
        db: Session,
        rule: AutomationRule,
        trigger_reason: str,
        confirmed: bool = False,
    ) -> AutomationRun:
        now = datetime.now(timezone.utc)
        rule.last_triggered_at = now
        run = AutomationRun(
            rule_id=rule.id,
            trigger_reason=trigger_reason,
            status="awaiting_confirmation" if (rule.requires_confirmation and not confirmed) else "running",
            step_results_json=[],
            started_at=now,
        )
        db.add(run)
        db.commit()

        if rule.requires_confirmation and not confirmed:
            await self.event_bus.publish(
                "automation.awaiting_confirmation",
                {"run_id": run.id, "rule_id": rule.id, "rule_name": rule.name, "trigger_reason": trigger_reason},
            )
            return run

        return await self._run_steps(db=db, rule=rule, run=run, confirmed=confirmed)

    async def approve_run(self, *, db: Session, run_id: str, approver_user_id: str) -> AutomationRun:
        run = db.get(AutomationRun, run_id)
        if run is None:
            raise ValueError("Automation run not found")
        if run.status != "awaiting_confirmation":
            raise ValueError(f"Automation run is in state '{run.status}'")
        rule = db.get(AutomationRule, run.rule_id)
        if rule is None:
            raise ValueError("Associated automation rule not found")

        append_audit_log(
            db,
            severity="info",
            category="automation",
            event_type="automation.run_approved",
            actor_type="user",
            actor_id=approver_user_id,
            outcome="approved",
            details={"run_id": run.id, "rule_id": rule.id},
        )
        run.status = "running"
        db.commit()
        return await self._run_steps(db=db, rule=rule, run=run, confirmed=True)

    async def _run_steps(
        self,
        *,
        db: Session,
        rule: AutomationRule,
        run: AutomationRun,
        confirmed: bool,
    ) -> AutomationRun:
        max_retries = int((rule.retry_policy_json or {}).get("max_retries", 1))
        step_results: list[dict[str, Any]] = []
        failures = 0

        for idx, action in enumerate(rule.actions_json):
            atype = action.get("type", "device_command")
            if atype == "device_command":
                try:
                    cmd_exec = await self.orchestrator.submit_command(
                        db=db,
                        user_id=rule.created_by_user_id,
                        device_id=action["device_id"],
                        capability_key=action["capability_key"],
                        parameters=action.get("parameters") or {},
                        automation_run_id=run.id,
                        confirmed=confirmed,
                        max_retries=max_retries,
                    )
                    ok = cmd_exec.status in ("verified", "acknowledged", "pending_confirmation")
                    if not ok:
                        failures += 1
                    step_results.append(
                        {
                            "step": idx + 1,
                            "type": atype,
                            "device_id": action["device_id"],
                            "capability_key": action["capability_key"],
                            "command_id": cmd_exec.id,
                            "status": cmd_exec.status,
                            "verification_method": cmd_exec.verification_method,
                            "error": cmd_exec.error_message,
                        }
                    )
                except CommandPipelineError as exc:
                    failures += 1
                    step_results.append(
                        {
                            "step": idx + 1,
                            "type": atype,
                            "device_id": action.get("device_id"),
                            "capability_key": action.get("capability_key"),
                            "status": "failed",
                            "error_code": exc.code,
                            "error": exc.message,
                        }
                    )
            elif atype == "hub_notification":
                title = str(action.get("title", rule.name))
                message = str(action.get("message", ""))
                await self.event_bus.publish(
                    "automation.notification",
                    {"run_id": run.id, "rule_name": rule.name, "title": title, "message": message},
                )
                step_results.append(
                    {"step": idx + 1, "type": atype, "status": "delivered", "title": title, "message": message}
                )
            elif atype == "delay":
                delay_ms = min(5000, max(0, int(action.get("delay_ms", 250))))
                await asyncio.sleep(delay_ms / 1000.0)
                step_results.append({"step": idx + 1, "type": atype, "status": "completed", "delay_ms": delay_ms})
            else:
                failures += 1
                step_results.append({"step": idx + 1, "type": atype, "status": "failed", "error": f"Unknown action '{atype}'"})

        run.step_results_json = step_results
        run.completed_at = datetime.now(timezone.utc)
        if failures == 0:
            run.status = "succeeded"
        elif failures < len(step_results):
            run.status = "partial_failure"
            run.error_message = f"{failures} of {len(step_results)} actions failed"
        else:
            run.status = "failed"
            run.error_message = "All automation actions failed"

        append_audit_log(
            db,
            severity="info" if run.status == "succeeded" else "warning",
            category="automation",
            event_type=f"automation.run_{run.status}",
            actor_type="automation",
            actor_id=run.id,
            outcome=run.status,
            details={"rule_id": rule.id, "rule_name": rule.name, "steps": len(step_results), "failures": failures},
        )
        db.commit()
        db.refresh(run)

        await self.event_bus.publish(
            "automation.run_completed",
            {"run_id": run.id, "rule_id": rule.id, "rule_name": rule.name, "status": run.status},
        )
        return run
