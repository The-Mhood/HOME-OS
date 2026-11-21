import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AgentCredentialStore:
    """Stores enrolled hub credentials encrypted at rest (AES-256-GCM, 0600 owner-only permissions)."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.key_path = self.state_dir / "agent.key"
        self.cred_path = self.state_dir / "enrollment.enc"
        self._aes = AESGCM(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            os.chmod(self.key_path, 0o600)
            return base64.urlsafe_b64decode(self.key_path.read_bytes().strip())
        key = AESGCM.generate_key(bit_length=256)
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, base64.urlsafe_b64encode(key))
        finally:
            os.close(fd)
        return key

    def save(self, data: dict[str, Any]) -> None:
        nonce = os.urandom(12)
        raw = json.dumps(data).encode("utf-8")
        ct = self._aes.encrypt(nonce, raw, b"homeos-linux-agent-v1")
        encoded = base64.urlsafe_b64encode(nonce + ct)
        fd = os.open(self.cred_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)

    def load(self) -> dict[str, Any] | None:
        if not self.cred_path.exists():
            return None
        os.chmod(self.cred_path, 0o600)
        blob = base64.urlsafe_b64decode(self.cred_path.read_bytes().strip())
        nonce, ct = blob[:12], blob[12:]
        pt = self._aes.decrypt(nonce, ct, b"homeos-linux-agent-v1")
        return json.loads(pt.decode("utf-8"))
