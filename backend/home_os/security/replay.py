import hashlib
import hmac
import threading
import time
from collections import OrderedDict


class ReplayAttackError(Exception):
    """Raised when a device request fails timestamp, nonce, or HMAC verification."""


def sign_device_request(
    *,
    secret: str,
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body: bytes = b"",
) -> str:
    body_sha = hashlib.sha256(body).hexdigest()
    canonical = f"{method.upper()}|{path}|{timestamp}|{nonce}|{body_sha}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


class ReplayGuard:
    """Enforces HMAC-SHA256 authenticity, bounded clock skew, and single-use nonces."""

    def __init__(self, window_seconds: int = 60, max_entries: int = 20_000):
        self.window_seconds = window_seconds
        self.max_entries = max_entries
        self._seen_nonces: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def _prune_expired(self, now: float) -> None:
        cutoff = now - (self.window_seconds * 2)
        while self._seen_nonces:
            oldest_key, oldest_ts = next(iter(self._seen_nonces.items()))
            if oldest_ts < cutoff or len(self._seen_nonces) > self.max_entries:
                self._seen_nonces.popitem(last=False)
            else:
                break

    def verify_signed_request(
        self,
        *,
        device_id: str,
        secret: str,
        method: str,
        path: str,
        timestamp: int,
        nonce: str,
        body: bytes,
        signature: str,
    ) -> None:
        if not nonce or len(nonce) < 8 or len(nonce) > 128:
            raise ReplayAttackError("Malformed nonce")

        now = time.time()
        if abs(now - float(timestamp)) > self.window_seconds:
            raise ReplayAttackError("Timestamp outside allowed replay window")

        expected_sig = sign_device_request(
            secret=secret,
            method=method,
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
        )
        if not hmac.compare_digest(expected_sig, signature):
            raise ReplayAttackError("Invalid request signature")

        cache_key = f"{device_id}:{nonce}"
        with self._lock:
            self._prune_expired(now)
            if cache_key in self._seen_nonces:
                raise ReplayAttackError("Nonce already used")
            self._seen_nonces[cache_key] = now
