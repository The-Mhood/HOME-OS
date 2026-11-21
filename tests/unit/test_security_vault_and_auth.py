import time
from pathlib import Path
import pytest
from cryptography.exceptions import InvalidTag

from home_os.security.crypto import (
    CredentialVault,
    hash_password,
    verify_password,
    hash_token,
    ensure_local_tls_certificate,
)
from home_os.security.replay import (
    ReplayGuard,
    ReplayAttackError,
    sign_device_request,
)
from home_os.security.audit import append_audit_log, verify_audit_chain
from home_os.db.session import create_db_engine_and_session
from home_os.db.models import Base, AuditLog


def test_argon2id_password_hashing_and_rejection():
    pw = "CorrectHorse-BatteryStaple!99"
    digest = hash_password(pw)
    assert digest.startswith("$argon2id$")
    assert pw not in digest
    assert verify_password(pw, digest) is True
    assert verify_password("WrongPassword!99", digest) is False


def test_aes_256_gcm_vault_encrypts_and_detects_tampering(tmp_data_dir: Path):
    vault = CredentialVault(key_file=tmp_data_dir / "master.key")
    secret = "ha_long_lived_token_super_secret_12345"
    ciphertext = vault.encrypt(secret, aad="integration:home_assistant")
    assert secret not in ciphertext
    # Key file must be strict owner-only permissions (0600)
    mode = (tmp_data_dir / "master.key").stat().st_mode & 0o777
    assert mode == 0o600
    assert vault.decrypt(ciphertext, aad="integration:home_assistant") == secret

    # Wrong AAD must fail authentication tag
    with pytest.raises(InvalidTag):
        vault.decrypt(ciphertext, aad="integration:tampered")


def test_tls_certificate_generation_and_fingerprint(tmp_data_dir: Path):
    cert_info = ensure_local_tls_certificate(tmp_data_dir / "tls")
    assert cert_info.cert_path.exists()
    assert cert_info.key_path.exists()
    assert (cert_info.key_path.stat().st_mode & 0o777) == 0o600
    assert len(cert_info.sha256_fingerprint) == 64


def test_hmac_signing_and_replay_protection():
    guard = ReplayGuard(window_seconds=60, max_entries=1000)
    secret = "device_hmac_secret_hex_abcdef0123456789"
    now = int(time.time())
    body = b'{"action":"flashlight.toggle","enabled":true}'

    sig = sign_device_request(
        secret=secret,
        method="POST",
        path="/api/v1/agent/telemetry",
        timestamp=now,
        nonce="nonce-unique-001",
        body=body,
    )

    # First verification succeeds
    guard.verify_signed_request(
        device_id="dev-1",
        secret=secret,
        method="POST",
        path="/api/v1/agent/telemetry",
        timestamp=now,
        nonce="nonce-unique-001",
        body=body,
        signature=sig,
    )

    # Exact replay with identical nonce must fail
    with pytest.raises(ReplayAttackError, match="Nonce already used"):
        guard.verify_signed_request(
            device_id="dev-1",
            secret=secret,
            method="POST",
            path="/api/v1/agent/telemetry",
            timestamp=now,
            nonce="nonce-unique-001",
            body=body,
            signature=sig,
        )

    # Expired timestamp outside 60s window must fail
    stale_ts = now - 120
    stale_sig = sign_device_request(
        secret=secret,
        method="POST",
        path="/api/v1/agent/telemetry",
        timestamp=stale_ts,
        nonce="nonce-unique-002",
        body=body,
    )
    with pytest.raises(ReplayAttackError, match="Timestamp outside allowed replay window"):
        guard.verify_signed_request(
            device_id="dev-1",
            secret=secret,
            method="POST",
            path="/api/v1/agent/telemetry",
            timestamp=stale_ts,
            nonce="nonce-unique-002",
            body=body,
            signature=stale_sig,
        )

    # Tampered body must fail signature check
    with pytest.raises(ReplayAttackError, match="Invalid request signature"):
        guard.verify_signed_request(
            device_id="dev-1",
            secret=secret,
            method="POST",
            path="/api/v1/agent/telemetry",
            timestamp=now,
            nonce="nonce-unique-003",
            body=b'{"action":"flashlight.toggle","enabled":false}',
            signature=sig,
        )


def test_tamper_evident_hash_chained_audit_log(tmp_data_dir: Path):
    engine, SessionLocal = create_db_engine_and_session(f"sqlite:///{tmp_data_dir / 'audit.db'}")
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        append_audit_log(
            db,
            severity="info",
            category="auth",
            event_type="owner.initialized",
            actor_type="user",
            actor_id="u-1",
            outcome="success",
            details={"username": "admin"},
        )
        append_audit_log(
            db,
            severity="warning",
            category="security",
            event_type="permission.revoked",
            actor_type="user",
            actor_id="u-1",
            device_id="dev-android-1",
            outcome="success",
            details={"scope": "screen:view"},
        )
        db.commit()

        report = verify_audit_chain(db)
        assert report.valid is True
        assert report.checked_entries == 2

        # Simulate DB tampering on first entry
        first = db.query(AuditLog).order_by(AuditLog.id.asc()).first()
        first.outcome = "tampered"
        db.commit()

        tampered_report = verify_audit_chain(db)
        assert tampered_report.valid is False
        assert tampered_report.broken_at_id == first.id
