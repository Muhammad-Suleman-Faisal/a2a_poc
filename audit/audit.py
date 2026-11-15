"""Best-effort append-only audit event writer for IAM services."""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)
_FIELDS = ("cid", "actor", "role", "service", "event", "agent", "scope",
           "decision", "status", "reason")


def write_event(service: str, **event: object) -> None:
    """Append one sanitized event to this service's JSONL audit file safely."""
    if service not in {"auth", "client"}:
        _LOGGER.error("Audit write rejected for unknown service")
        return
    try:
        record = {"ts": datetime.now(timezone.utc).isoformat()}
        record.update({key: event.get(key) for key in _FIELDS})
        record["service"] = service
        path = Path(os.getenv("AUDIT_DIR", Path(__file__).resolve().parent)) / f"{service}.jsonl"
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        _LOGGER.exception("Audit event write failed for service=%s", service)


def read_events() -> list[dict]:
    """Read valid JSON objects from both service audit files, skipping bad lines."""
    records = []
    directory = Path(os.getenv("AUDIT_DIR", Path(__file__).resolve().parent))
    for service in ("auth", "client"):
        try:
            with (directory / f"{service}.jsonl").open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                        if isinstance(record, dict):
                            records.append(record)
                    except (json.JSONDecodeError, TypeError):
                        continue
        except FileNotFoundError:
            continue
        except Exception:
            _LOGGER.exception("Audit read failed for service=%s", service)
    return records
