"""Shared JSON I/O and hashing. No network access or review decisions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

DOCUMENTS = ("current", "history", "sources", "evidence")


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"{path}: cannot read JSON: {error}") from error
    require(isinstance(value, dict), f"{path}: top-level JSON value must be an object")
    return value


def load_conference(directory: Path, *, optional_evidence: bool = False) -> dict:
    return {name: ({} if name == "evidence" and optional_evidence and not (directory / "evidence.json").exists()
                   else load_json(directory / f"{name}.json")) for name in DOCUMENTS}


def json_text(value: object, *, sort_keys: bool = False) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys, indent=2) + "\n"


def write_json(path: Path, value: object, *, sort_keys: bool = False) -> bytes:
    encoded = json_text(value, sort_keys=sort_keys).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return encoded


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def object_hash(value: object) -> str:
    return text_hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
