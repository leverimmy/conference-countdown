#!/usr/bin/env python3
"""Package canonical data and its evidence for GitHub Pages (no network access)."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil

from data_io import DOCUMENTS, load_conference, load_json, utc_now, write_json
from validate_data import validate


def build(data_dir: Path, output_dir: Path, revision: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be the full main commit SHA (40 lowercase hex characters)")
    if output_dir == data_dir or data_dir in output_dir.parents or output_dir in data_dir.parents:
        raise ValueError("output directory must be separate from data/")
    validate(data_dir)
    catalog = load_json(data_dir / "catalog.json")
    conferences = []
    filenames = tuple(f"{name}.json" for name in DOCUMENTS)
    for conference_id in catalog["conference_order"]:
        directory = data_dir / conference_id
        conferences.append(load_conference(directory))
        for name in (*filenames, "LICENSE", "README.md"):
            if (directory / name).is_file():
                target = output_dir / "data" / conference_id / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(directory / name, target)
    for name in ("catalog.json", "schema.json", "README.md", "LICENSE"):
        if (data_dir / name).is_file():
            shutil.copyfile(data_dir / name, output_dir / "data" / name)
    generated_at = utc_now()
    snapshot = {"schema_version": 1, "revision": revision, "generated_at": generated_at,
                "catalog": catalog, "conferences": conferences}
    relative_url = f"snapshots/{revision}.json"
    payload = write_json(output_dir / "v1" / relative_url, snapshot, sort_keys=True)
    manifest = {"schema_version": 1, "revision": revision, "generated_at": generated_at,
                "data_url": relative_url, "sha256": hashlib.sha256(payload).hexdigest(), "byte_count": len(payload)}
    write_json(output_dir / "v1" / "manifest.json", manifest, sort_keys=True)
    (output_dir / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Conference Countdown data</title>'
        '<h1>Conference Countdown data</h1>'
        f'<p>Main commit: <code>{revision}</code></p>'
        '<p><a href="v1/manifest.json">Manifest</a> · <a href="data/catalog.json">Catalog</a> · '
        '<a href="data/README.md">Data and evidence format</a></p>'
        '<p>Each conference has current.json, history.json, sources.json, and evidence.json under data/.</p></html>\n',
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist/feed"))
    parser.add_argument("--revision", default=os.environ.get("GITHUB_SHA"), required=not os.environ.get("GITHUB_SHA"))
    args = parser.parse_args()
    manifest = build(args.data_dir.resolve(), args.output_dir.resolve(), args.revision)
    print(f"Built Pages data for main {manifest['revision']}: {manifest['byte_count']} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
