"""Append-only, plaintext-free audit log (Vault audit-device model): string values are HMAC-SHA256'd."""
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

PLAIN = {"ts", "action", "decision", "policy"}  # structural, never sensitive; every other string value is hashed


class AuditUnavailable(RuntimeError):
    """The entry could not be durably written; the caller must refuse the action (fail closed)."""


class AuditLog:
    def __init__(self, path, key: bytes):
        if not key:
            raise RuntimeError("audit HMAC key is empty")
        self.path, self.key = path, key

    def hmac(self, value: str) -> str:
        return "hmac-sha256:" + hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    def append(self, event: dict) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        line = json.dumps({k: self.hmac(v) if isinstance(v, str) and k not in PLAIN else v
                           for k, v in event.items()})
        try:
            with open(self.path, "a", encoding="utf-8") as f:  # "a": append-only, never rewrite
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise AuditUnavailable(str(e)) from e


def from_env() -> AuditLog:
    """Fails at startup when LABELPIPE_AUDIT_KEY is unset, so no action can ever run unaudited."""
    key = os.environ.get("LABELPIPE_AUDIT_KEY")
    if not key:
        raise RuntimeError("LABELPIPE_AUDIT_KEY must be set (HMAC key for the audit log)")
    return AuditLog(os.environ.get("LABELPIPE_AUDIT_LOG", "audit.jsonl"), key.encode())
