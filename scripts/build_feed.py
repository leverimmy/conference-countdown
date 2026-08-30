#!/usr/bin/env python3
"""Build the immutable data payload consumed by installed apps."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from validate_data import load_json, validate


def write_json(path: Path, value: object) -> bytes:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(encoded)
    return encoded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist/feed/v1"))
    parser.add_argument("--revision", default=os.environ.get("GITHUB_SHA", "local"))
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    validate(data_dir)

    catalog = load_json(data_dir / "catalog.json")
    conferences = []
    for conference_id in catalog["conference_order"]:
        conference_dir = data_dir / conference_id
        conferences.append({
            "current": load_json(conference_dir / "current.json"),
            "history": load_json(conference_dir / "history.json"),
        })

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    feed = {
        "schema_version": 1,
        "revision": args.revision,
        "generated_at": generated_at,
        "catalog": catalog,
        "conferences": conferences,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = write_json(output_dir / "conference-data.json", feed)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "schema_version": 1,
        "revision": args.revision,
        "generated_at": generated_at,
        "data_url": "conference-data.json",
        "sha256": digest,
        "byte_count": len(payload),
    }
    write_json(output_dir / "manifest.json", manifest)
    print(f"Built feed revision {args.revision} ({len(payload)} bytes, sha256 {digest}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
