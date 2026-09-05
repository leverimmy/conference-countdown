#!/usr/bin/env python3
"""Read-only, deterministic source check. Exit 0 unchanged, 1 changed, 2 unknown/error."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import sys

from data_io import ValidationError, load_conference, load_json, utc_now, write_json
from review_data import snapshot_conference
from review_sources import render_report
from source_evidence import observe, source_status, summarize
from validate_data import validate


def collect(data_dir: Path, conference_ids: list[str], workers: int) -> dict:
    # Freeze the original data so later local edits cannot change the "before"
    # side of a review. Fetching never accepts a baseline or edits dates.
    report = {"schema_version": 1, "checked_at": utc_now(), "validation_error": None, "conferences": []}
    try:
        validate(data_dir)
    except (ValidationError, ValueError, KeyError, TypeError) as error:
        report["validation_error"] = str(error)

    jobs = []
    for conference_id in conference_ids:
        directory = data_dir / conference_id
        conference = {"id": conference_id, "sources": []}
        report["conferences"].append(conference)
        try:
            conference.update(snapshot_conference(conference_id, load_conference(directory, optional_evidence=True)))
            for source in conference["sources"]:
                source.update(after=None, error=None)
                if not source.get("removed"):
                    jobs.append((conference_id, source))
        except (ValidationError, KeyError, TypeError) as error:
            conference["error"] = str(error)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(observe, source["config"]): (conference_id, source) for conference_id, source in jobs}
        for future in as_completed(futures):
            conference_id, source = futures[future]
            try:
                source["after"] = future.result()
            except Exception as error:
                source["error"] = str(error)
            label = f"{conference_id}/{source['config']['id']}"
            print(f"{source_status(source).upper()} {label}" + (f": {source['error']}" if source["error"] else ""), flush=True)
    return report


def check(data_dir: Path, output_dir: Path, conference_ids: list[str], workers: int) -> int:
    report = collect(data_dir, conference_ids, workers)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "report.json", report)
    (output_dir / "report.html").write_text(render_report(report), encoding="utf-8")
    counts = summarize(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        # Actions summaries cannot host an interactive standalone HTML document.
        # Keep only a status receipt here; the actual report is in the artifact.
        summary = (f"Check Source：未变化 {counts['unchanged']}；变化/新来源 {counts['changed']}；错误 {counts['errors']}。\n\n"
                   "下载本次 source-check artifact，在浏览器打开 report.html 查看原文差异与日期高亮。\n")
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as destination:
            destination.write(summary)
    print(f"Unchanged: {counts['unchanged']}; changed/new: {counts['changed']}; errors: {counts['errors']}. "
          f"Report: {output_dir / 'report.html'}")
    # Human interpretation belongs to the local review, not the source check.
    return 2 if counts["errors"] else (1 if counts["changed"] else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("build/source-check"))
    parser.add_argument("--conference", action="append", help="limit to an ID; may be repeated")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    data_dir, output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if output_dir == data_dir or data_dir in output_dir.parents or output_dir in data_dir.parents:
        parser.error("output-dir must be separate from data/; checks never overwrite reviewed evidence")
    if not 1 <= args.workers <= 8:
        parser.error("workers must be between 1 and 8")
    catalog = load_json(data_dir / "catalog.json")["conference_order"]
    if args.conference and set(args.conference) - set(catalog):
        parser.error("unknown conference ID")
    conference_ids = [conference_id for conference_id in catalog if not args.conference or conference_id in args.conference]
    return check(data_dir, output_dir, conference_ids, args.workers)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as error:
        print(f"Check Source failed: {error}", file=sys.stderr)
        raise SystemExit(2)
