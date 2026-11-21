import base64
import dataclasses
import datetime as dt
import hashlib
import ipaddress
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

_password_hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_secure_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def generate_pairing_pin() -> str:
    # 6-digit numeric/alphanumeric unambiguous code
    return f"{secrets.randbelow(1_000_000):06d}"


class CredentialVault:
    """AES-256-GCM authenticated envelope encryption for device secrets and external API tokens."""

    def __init__(self, key_file: Path):
        self.key_file = key_file
        self._key = self._load_or_create_key()
        self._aesgcm = AESGCM(self._key)

    def _load_or_create_key(self) -> bytes:
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        if self.key_file.exists():
            os.chmod(self.key_file, 0o600)
            raw = base64.urlsafe_b64decode(self.key_file.read_bytes().strip())
            if len(raw) != 32:
                raise ValueError("Corrupted master vault key length")
            return raw

        key = AESGCM.generate_key(bit_length=256)
        encoded = base64.urlsafe_b64encode(key)
        fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)
        os.chmod(self.key_file, 0o600)
        return key

    def encrypt(self, plaintext: str, aad: str = "") -> str:
        nonce = os.urandom(12)
        aad_bytes = aad.encode("utf-8") if aad else None
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad_bytes)
        payload = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
        return f"v1:{payload}"

    def decrypt(self, token: str, aad: str = "") -> str:
        if not token.startswith("v1:"):
            raise ValueError("Unsupported vault ciphertext version")
        raw = base64.urlsafe_b64decode(token[3:].encode("ascii"))
        nonce, ct = raw[:12], raw[12:]
        aad_bytes = aad.encode("utf-8") if aad else None
        pt = self._aesgcm.decrypt(nonce, ct, aad_bytes)
        return pt.decode("utf-8")


@dataclasses.dataclass(frozen=True)
class TlsCertInfo:
    cert_path: Path
    key_path: Path
    sha256_fingerprint: str


def ensure_local_tls_certificate(tls_dir: Path, hostnames: list[str] | None = None) -> TlsCertInfo:
    """Generates or loads a local ECDSA P-256 TLS certificate for encrypted local hub communication."""
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_path = tls_dir / "hub.crt"
    key_path = tls_dir / "hub.key"

    if cert_path.exists() and key_path.exists():
        os.chmod(key_path, 0o600)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        fp = cert.fingerprint(hashes.SHA256()).hex()
        return TlsCertInfo(cert_path=cert_path, key_path=key_path, sha256_fingerprint=fp)

    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HOME OS Local Command Hub"),
            x509.NameAttribute(NameOID.COMMON_NAME, "home-os.local"),
        ]
    )

    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName("home-os.local"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    for h in hostnames or []:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            san_entries.append(x509.DNSName(h))

    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(private_key, hashes.SHA256())
    )

    key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key_bytes)
    finally:
        os.close(fd)
    os.chmod(key_path, 0o600)

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    fp = cert.fingerprint(hashes.SHA256()).hex()
    return TlsCertInfo(cert_path=cert_path, key_path=key_path, sha256_fingerprint=fp)
