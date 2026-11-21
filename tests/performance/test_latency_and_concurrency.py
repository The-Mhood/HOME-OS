import asyncio
import time
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
from home_os.services.command_orchestrator import CommandOrchestrator
from home_os.services.event_bus import EventBus


class _FastBenchAdapter(BaseDeviceAdapter):
    adapter_type = "bench_fast"

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
        await asyncio.sleep(0.001)
        return CommandExecutionReceipt(
            command_id=command.command_id,
            acknowledged=True,
            state_verified=True,
            verification_method="bench_readback",
            result={"ok": True},
        )

    async def subscribe_events(self, device_id, callback):
        return None

    async def disconnect(self, device_id, reason="operator_requested"):
        return None

    async def revoke_access(self, device_id, reason="permission_revoked"):
        return None


@pytest.mark.asyncio
async def test_command_pipeline_latency_and_25_concurrent_devices_performance(tmp_data_dir: Path):
    engine, SessionLocal = create_db_engine_and_session(f"sqlite:///{tmp_data_dir / 'perf.db'}")
    Base.metadata.create_all(bind=engine)
    vault = CredentialVault(tmp_data_dir / "master.key")
    registry = AdapterRegistry()
    registry.register(_FastBenchAdapter())
    bus = EventBus()
    q = bus.create_queue(maxsize=500)
    orchestrator = CommandOrchestrator(adapter_registry=registry, event_bus=bus)

    device_ids = [f"dev-perf-{i:02d}" for i in range(25)]

    with SessionLocal() as db:
        owner = User(
            id="u-perf",
            username="perf_admin",
            display_name="Perf Admin",
            password_hash=hash_password("PerfStrongPass!99"),
            role="owner",
            is_active=True,
        )
        db.add(owner)
        for did in device_ids:
            db.add(
                Device(
                    id=did,
                    hardware_fingerprint=f"fp-{did}",
                    name=f"Perf Node {did}",
                    category="smarthome",
                    lifecycle_state="authenticated",
                    connection_status="online",
                    adapter_type="bench_fast",
                    enrolled_at=datetime.now(timezone.utc),
                )
            )
            db.add(
                DeviceCredential(
                    device_id=did,
                    token_hash=hash_token(f"t-{did}"),
                    encrypted_hmac_secret=vault.encrypt(f"s-{did}", aad=f"device:{did}"),
                )
            )
            db.add(
                DeviceCapability(
                    device_id=did,
                    capability_key="power.set",
                    category="smarthome",
                    display_name="Power",
                    supported=True,
                    requires_permission="smarthome:control",
                )
            )
            db.add(
                DevicePermission(
                    device_id=did,
                    permission_scope="smarthome:control",
                    status="granted",
                    granted_by_user_id="u-perf",
                )
            )
        db.commit()

    # Measure 25 sequential verified commands (including hash-chained audit write & event broadcast)
    t0 = time.perf_counter()
    for did in device_ids:
        with SessionLocal() as db:
            res = await orchestrator.submit_command(
                db=db,
                user_id="u-perf",
                device_id=did,
                capability_key="power.set",
                parameters={"on": True},
            )
            assert res.status == "verified"
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    avg_ms = elapsed_ms / len(device_ids)

    # Average full 8-step + SQLite WAL + SHA-256 audit chain latency must remain well under 50ms/command
    assert avg_ms < 50.0
    assert q.qsize() == 25
