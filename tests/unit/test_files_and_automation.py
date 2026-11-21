from datetime import datetime, timezone
from pathlib import Path
import pytest

from home_os.adapters.base import (
    AdapterCommand,
    AdapterRegistry,
    BaseDeviceAdapter,
    CommandExecutionReceipt,
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
from home_os.services.automation_engine import AutomationEngine
from home_os.services.command_orchestrator import CommandOrchestrator
from home_os.services.event_bus import EventBus
from home_os.services.media_files import PathAuthorizationError, resolve_authorized_path


class _AutomationTargetAdapter(BaseDeviceAdapter):
    adapter_type = "auto_target"

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def discover(self, timeout: float = 3.0):
        return []

    async def identify(self, endpoint):
        raise NotImplementedError

    async def pair(self, device_id, request):
        raise NotImplementedError

    async def authenticate(self, device_id, proof):
        raise NotImplementedError

    async def get_status(self, device_id):
        raise NotImplementedError

    async def get_capabilities(self, device_id):
        return []

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        self.calls.append((device_id, command.capability_key, command.parameters))
        return CommandExecutionReceipt(
            command_id=command.command_id,
            acknowledged=True,
            state_verified=True,
            verification_method="direct_state_check",
            result={"ok": True},
        )

    async def subscribe_events(self, device_id, callback):
        return None

    async def disconnect(self, device_id, reason="operator_requested"):
        return None

    async def revoke_access(self, device_id, reason="permission_revoked"):
        return None


def test_file_path_traversal_protection(tmp_data_dir: Path):
    allowed_root = tmp_data_dir / "shared_docs"
    allowed_root.mkdir(parents=True)
    safe_file = allowed_root / "notes.txt"
    safe_file.write_text("authorized content")

    secret_outside = tmp_data_dir / "outside_secret.txt"
    secret_outside.write_text("top secret")

    resolved = resolve_authorized_path(str(safe_file), [str(allowed_root)])
    assert resolved == safe_file.resolve()

    # Traversal attempt via ../ must raise PathAuthorizationError
    with pytest.raises(PathAuthorizationError):
        resolve_authorized_path(str(allowed_root / ".." / "outside_secret.txt"), [str(allowed_root)])

    # Symlink escape attempt must also raise PathAuthorizationError
    escape_link = allowed_root / "escape_link.txt"
    escape_link.symlink_to(secret_outside)
    with pytest.raises(PathAuthorizationError):
        resolve_authorized_path(str(escape_link), [str(allowed_root)])


@pytest.mark.asyncio
async def test_cross_device_automation_engine_executes_multi_step_workflow(tmp_data_dir: Path):
    engine, SessionLocal = create_db_engine_and_session(f"sqlite:///{tmp_data_dir / 'auto.db'}")
    Base.metadata.create_all(bind=engine)
    vault = CredentialVault(tmp_data_dir / "master.key")
    registry = AdapterRegistry()
    target_adapter = _AutomationTargetAdapter()
    registry.register(target_adapter)
    bus = EventBus()
    orchestrator = CommandOrchestrator(adapter_registry=registry, event_bus=bus)
    auto_engine = AutomationEngine(orchestrator=orchestrator, event_bus=bus)

    with SessionLocal() as db:
        owner = User(
            id="u-1",
            username="owner",
            display_name="Owner",
            password_hash=hash_password("ValidPass!1234"),
            role="owner",
            is_active=True,
        )
        speaker = Device(
            id="dev-speaker",
            hardware_fingerprint="fp-speaker",
            name="Bedroom Speaker",
            category="speaker",
            lifecycle_state="authenticated",
            connection_status="online",
            adapter_type="auto_target",
            enrolled_at=datetime.now(timezone.utc),
        )
        tv = Device(
            id="dev-tv",
            hardware_fingerprint="fp-tv",
            name="Living Room TV",
            category="tv",
            lifecycle_state="authenticated",
            connection_status="online",
            adapter_type="auto_target",
            enrolled_at=datetime.now(timezone.utc),
        )
        phone = Device(
            id="dev-phone",
            hardware_fingerprint="fp-phone",
            name="Owner Phone",
            category="smartphone",
            lifecycle_state="authenticated",
            connection_status="offline",
            adapter_type="auto_target",
            enrolled_at=datetime.now(timezone.utc),
        )
        db.add_all([owner, speaker, tv, phone])
        for dev_id in ("dev-speaker", "dev-tv", "dev-phone"):
            db.add(
                DeviceCredential(
                    device_id=dev_id,
                    token_hash=hash_token(f"tok-{dev_id}"),
                    encrypted_hmac_secret=vault.encrypt(f"sec-{dev_id}", aad=f"device:{dev_id}"),
                )
            )
        db.add_all(
            [
                DeviceCapability(
                    device_id="dev-speaker",
                    capability_key="media.control",
                    category="media",
                    display_name="Media Playback",
                    supported=True,
                    requires_permission="media:control",
                ),
                DeviceCapability(
                    device_id="dev-tv",
                    capability_key="volume.set",
                    category="audio",
                    display_name="TV Volume",
                    supported=True,
                    requires_permission="volume:control",
                ),
                DevicePermission(device_id="dev-speaker", permission_scope="media:control", status="granted", granted_by_user_id="u-1"),
                DevicePermission(device_id="dev-tv", permission_scope="volume:control", status="granted", granted_by_user_id="u-1"),
            ]
        )
        db.commit()

        # Rule: "Play music on bedroom speaker and reduce television volume when phone is offline"
        rule = auto_engine.create_rule(
            db=db,
            user_id="u-1",
            name="Evening Focus Audio",
            description="Play music on bedroom speaker and reduce TV volume if phone is offline",
            trigger_type="event",
            trigger_config={"event_type": "network.laptop_connected"},
            conditions={
                "operator": "all",
                "clauses": [
                    {"type": "device_status", "device_id": "dev-phone", "op": "eq", "value": "offline"}
                ],
            },
            actions=[
                {
                    "type": "device_command",
                    "device_id": "dev-speaker",
                    "capability_key": "media.control",
                    "parameters": {"action": "play"},
                },
                {
                    "type": "device_command",
                    "device_id": "dev-tv",
                    "capability_key": "volume.set",
                    "parameters": {"level": 15},
                },
                {
                    "type": "hub_notification",
                    "title": "Phone Offline Alert",
                    "message": "Your phone is currently offline while your laptop joined the home network.",
                },
            ],
            retry_policy={"max_retries": 2},
            requires_confirmation=False,
        )

        runs = await auto_engine.evaluate_event(
            db=db,
            event_type="network.laptop_connected",
            event_payload={"interface": "wlan0"},
        )
        assert len(runs) == 1
        assert runs[0].status == "succeeded"
        assert len(runs[0].step_results_json) == 3
        assert target_adapter.calls == [
            ("dev-speaker", "media.control", {"action": "play"}),
            ("dev-tv", "volume.set", {"level": 15}),
        ]
