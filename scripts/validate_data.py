#!/usr/bin/env python3
"""Validate the canonical conference data without third-party dependencies."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from data_io import ValidationError, load_conference, load_json, object_hash, require, text_hash
from source_evidence import DATE_FIELDS, EXTRACTOR, date_claims, snapshot_hash, source_config_hash


HISTORICAL_KEYS = set(DATE_FIELDS) - {"conference_end"}


def validate_iso_date(value: object, location: str) -> date:
    require(isinstance(value, str), f"{location}: expected an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError(f"{location}: invalid ISO date {value!r}") from error


def validate_timestamp(value: object, location: str) -> None:
    require(isinstance(value, str), f"{location}: expected an ISO date-time string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError(f"{location}: invalid ISO date-time {value!r}") from error
    require(parsed.tzinfo is not None, f"{location}: date-time needs a UTC offset")


def validate_https_url(value: object, location: str) -> None:
    require(isinstance(value, str), f"{location}: expected a URL string")
    parsed = urlparse(value)
    require(parsed.scheme == "https" and bool(parsed.netloc), f"{location}: expected an HTTPS URL")


def validate_reference(ref: dict, snapshots: dict, location: str) -> None:
    require(isinstance(ref, dict), f"{location}: invalid evidence reference")
    snapshot = snapshots.get(ref.get("source"), {})
    snippet = next((s for s in snapshot.get("snippets", []) if s["sha256"] == ref.get("snippet_sha256")), None)
    require(snapshot.get("status") == 200 and snippet is not None, f"{location}: referenced snippet is missing or changed")
    if "quote" not in ref and "highlights" not in ref:
        return
    quote = ref.get("quote")
    require(isinstance(quote, str) and bool(quote) and snippet["text"].count(quote) == 1,
            f"{location}: quote must occur exactly once in the referenced excerpt")
    highlights = ref.get("highlights")
    require(isinstance(highlights, list) and bool(highlights)
            and all(isinstance(text, str) and bool(text) and quote.count(text) == 1 for text in highlights),
            f"{location}: highlights must identify exact text within the quote")


def validate_candidate(candidate: dict, snapshots: dict, location: str) -> None:
    require(isinstance(candidate, dict), f"{location}: expected a candidate object")
    require(set(candidate) <= {"value", "evidence", "display", "status", "note"}, f"{location}: unknown candidate field")
    require(candidate.get("status", "supported") in {"supported", "date_only"}, f"{location}: invalid candidate status")
    if "note" in candidate:
        require(isinstance(candidate["note"], str) and bool(candidate["note"].strip()), f"{location}: invalid candidate note")
    if "display" in candidate:
        display = candidate["display"]
        require(isinstance(display, dict) and set(display) == {"date_label", "detail_label"}
                and all(isinstance(value, str) and bool(value.strip()) for value in display.values()),
                f"{location}: display needs exactly date_label and detail_label")
    value = candidate.get("value")
    require(isinstance(value, str), f"{location}: candidate value must be an ISO date or date-time")
    if len(value) == 10:
        validate_iso_date(value, location)
    else:
        validate_timestamp(value, f"{location}: candidate.value")
    refs = candidate.get("evidence")
    require(isinstance(refs, list) and bool(refs), f"{location}: candidate needs supporting excerpts")
    for ref in refs:
        validate_reference(ref, snapshots, location)
    require(any(ref.get("highlights") for ref in refs), f"{location}: candidate needs highlighted evidence")


def validate_undo(decision: dict, location: str) -> None:
    undo = decision["undo"]
    require(isinstance(undo, dict) and set(undo) == {"before", "after_sha256", "guard_sha256", "sha256"},
            f"{location}: invalid undo record")
    require(undo["sha256"] == object_hash({k: v for k, v in undo.items() if k != "sha256"})
            and all(isinstance(undo[key], str) and re.fullmatch(r"[0-9a-f]{64}", undo[key])
                    for key in ("after_sha256", "guard_sha256")), f"{location}: undo hash mismatch")
    before, target = undo["before"], decision["target"]
    require(isinstance(before, dict), f"{location}: invalid original record")
    if target.startswith("source/"):
        require(set(before) == {"config", "index", "snapshot"} and before["config"] == decision["proposal"]
                and before["config"].get("id") == target.split("/", 1)[1]
                and before["config"].get("candidate") is True
                and type(before["index"]) is int and before["index"] >= 0,
                f"{location}: invalid original source")
        if before["snapshot"] is not None:
            snapshot = before["snapshot"]
            require(isinstance(snapshot, dict) and snapshot.get("sha256") == snapshot_hash(snapshot),
                    f"{location}: original source hash mismatch")
    else:
        require(set(before) == {"record", "claim"} and isinstance(before["record"], dict)
                and isinstance(before["claim"], dict) and before["claim"].get("candidate") == decision["proposal"]
                and before["claim"].get("value") == decision.get("previous_value"), f"{location}: invalid original date")
        parts = target.split("/")
        record = before["record"]
        require((len(parts) == 2 and parts[0] == "current" and record.get("id") == parts[1]
                 and record.get("at") == decision.get("previous_value"))
                or (len(parts) == 3 and parts[0] == "history" and str(record.get("year")) == parts[1]
                    and record.get(parts[2]) == decision.get("previous_value")), f"{location}: original date target mismatch")


def validate_decisions(decisions: list, location: str) -> None:
    require(isinstance(decisions, list), f"{location}: decisions must be a list")
    for decision in decisions:
        require(isinstance(decision, dict) and decision.get("action") in {"accept", "reject"}, f"{location}: invalid decision")
        require(isinstance(decision.get("target"), str) and decision["target"].startswith(("current/", "history/", "source/")),
                f"{location}: invalid decision target")
        require(isinstance(decision.get("proposal"), dict) and decision.get("proposal_sha256") == object_hash(decision["proposal"]),
                f"{location}: decision proposal hash mismatch")
        if "undo" in decision:
            validate_undo(decision, location)
        require(("undone_at" in decision) == ("undo_reason" in decision)
                and ("undone_at" not in decision or "undo" in decision), f"{location}: incomplete withdrawal")
        stamps = [("reviewed_at", "reason")]
        if "undone_at" in decision:
            stamps.append(("undone_at", "undo_reason"))
        for stamp, reason in stamps:
            require(isinstance(decision.get(reason), str) and len(decision[reason]) <= 500, f"{location}: invalid decision reason")
            validate_timestamp(decision.get(stamp), f"{location}: {stamp}")
        if decision.get("source_snapshot") is not None:
            snapshot = decision["source_snapshot"]
            require(isinstance(snapshot, dict) and snapshot.get("sha256") == snapshot_hash(snapshot),
                    f"{location}: rejected source hash mismatch")


def validate_current(current: dict, current_path: Path, global_event_ids: set[str]) -> int:
    conference_id = current_path.parent.name
    require(current.get("schema_version") == 1, f"{current_path}: unsupported schema_version")
    require(current.get("id") == conference_id, f"{current_path}: id must match directory")
    validate_iso_date(current.get("last_verified"), f"{current_path}: last_verified")
    require(isinstance(current.get("edition"), int), f"{current_path}: edition must be an integer")
    for field in ("name", "short_name", "subtitle", "symbol", "default_event_id", "time_zone"):
        require(isinstance(current.get(field), str) and current[field], f"{current_path}: missing {field}")
    validate_https_url(current.get("official_url"), f"{current_path}: official_url")
    try:
        ZoneInfo(current["time_zone"])
    except ZoneInfoNotFoundError as error:
        raise ValidationError(f"{current_path}: unknown time_zone {current['time_zone']!r}") from error

    events = current.get("events")
    require(isinstance(events, list) and events, f"{current_path}: events must be non-empty")
    local_event_ids: set[str] = set()
    for index, event in enumerate(events):
        location = f"{current_path}: events[{index}]"
        require(isinstance(event, dict), f"{location}: event must be an object")
        for field in ("id", "title", "compact_title", "date_label", "detail_label", "symbol"):
            require(isinstance(event.get(field), str) and event[field], f"{location}: missing {field}")
        event_id = event["id"]
        require(event_id.startswith(f"{conference_id}."), f"{location}: id must start with {conference_id}.")
        require(event_id not in local_event_ids, f"{current_path}: duplicate event {event_id}")
        require(event_id not in global_event_ids, f"duplicate global event ID {event_id}")
        local_event_ids.add(event_id)
        global_event_ids.add(event_id)

        at = event.get("at")
        historical_key = event.get("historical_key")
        target_year = event.get("target_year")
        if at is None:
            require(historical_key in HISTORICAL_KEYS, f"{location}: undated event needs historical_key")
            require(isinstance(target_year, int), f"{location}: undated event needs target_year")
        else:
            validate_timestamp(at, f"{location}: at")
            require(historical_key is None or historical_key in HISTORICAL_KEYS, f"{location}: invalid historical_key")
            require(target_year is None or isinstance(target_year, int), f"{location}: invalid target_year")

    require(current["default_event_id"] in local_event_ids, f"{current_path}: default_event_id is missing")
    return len(events)


def validate_history(history: dict, history_path: Path) -> int:
    conference_id = history_path.parent.name
    require(history.get("schema_version") == 1, f"{history_path}: unsupported schema_version")
    require(history.get("id") == conference_id, f"{history_path}: id must match directory")
    validate_iso_date(history.get("last_verified"), f"{history_path}: last_verified")
    records = history.get("records")
    require(isinstance(records, list) and records, f"{history_path}: records must be non-empty")
    years: set[int] = set()
    for index, record in enumerate(records):
        location = f"{history_path}: records[{index}]"
        require(isinstance(record, dict), f"{location}: record must be an object")
        year = record.get("year")
        require(isinstance(year, int) and year not in years, f"{location}: invalid or duplicate year")
        years.add(year)
        validate_https_url(record.get("source"), f"{location}: source")
        dates = {field: validate_iso_date(record[field], f"{location}: {field}") for field in DATE_FIELDS if field in record}
        for earlier, later, message in (("conference_start", "conference_end", "invalid conference range"),
                                        ("review_release", "rebuttal_deadline", "review follows response"),
                                        ("rebuttal_deadline", "final_decision", "response follows decision"),
                                        ("final_decision", "conference_start", "decision follows conference")):
            if earlier in dates and later in dates:
                require(dates[earlier] <= dates[later], f"{location}: {message}")
    return len(records)


def validate_sources(sources: dict, sources_path: Path) -> set[str]:
    conference_id = sources_path.parent.name
    require(sources.get("schema_version") == 1, f"{sources_path}: unsupported schema_version")
    require(sources.get("id") == conference_id, f"{sources_path}: id must match directory")
    references = sources.get("sources")
    require(isinstance(references, list) and references, f"{sources_path}: sources must be non-empty")
    source_ids: set[str] = set()
    for index, reference in enumerate(references):
        location = f"{sources_path}: sources[{index}]"
        require(isinstance(reference, dict), f"{location}: source must be an object")
        source_id = reference.get("id")
        require(
            isinstance(source_id, str) and bool(source_id) and source_id not in source_ids,
            f"{location}: invalid or duplicate id",
        )
        source_ids.add(source_id)
        validate_https_url(reference.get("url"), f"{location}: url")
        require(reference.get("kind") in {"html", "json", "pdf"}, f"{location}: invalid kind")
        require(isinstance(reference.get("edition"), int), f"{location}: missing edition")
        for flag in ("discover_links", "allow_empty", "allow_missing", "candidate"):
            require(isinstance(reference.get(flag, False), bool), f"{location}: {flag} must be boolean")
        if "section_pattern" in reference:
            require(isinstance(reference["section_pattern"], str), f"{location}: invalid section_pattern")
            try:
                re.compile(reference["section_pattern"])
            except re.error as error:
                raise ValidationError(f"{location}: invalid section_pattern: {error}") from error
        if "start_text" in reference or "end_text" in reference:
            require(reference["kind"] == "html" and all(isinstance(reference.get(key), str) and reference[key]
                    for key in ("start_text", "end_text")), f"{location}: HTML range needs start_text and end_text")
    return source_ids


def validate_evidence(documents: dict, evidence_path: Path, source_ids: set[str]) -> tuple[int, int]:
    conference_id = evidence_path.parent.name
    evidence, current, history = (documents[name] for name in ("evidence", "current", "history"))
    references = documents["sources"]["sources"]
    evidence_count = unresolved_count = 0
    require(evidence.get("schema_version") == 1 and evidence.get("id") == conference_id,
            f"{evidence_path}: invalid schema or id")
    validate_decisions(evidence.get("decisions", []), str(evidence_path))
    snapshots = evidence.get("sources")
    require(isinstance(snapshots, dict) and set(snapshots) <= source_ids, f"{evidence_path}: unknown source IDs")
    for reference in references:
        snapshot = snapshots.get(reference["id"])
        if snapshot is None:
            continue  # An explicitly unresolved source is reported by Check Source.
        location = f"{evidence_path}: sources.{reference['id']}"
        require(isinstance(snapshot, dict), f"{location}: expected snapshot object")
        require(snapshot.get("extractor") == EXTRACTOR, f"{location}: unsupported extractor")
        require(snapshot.get("config_sha256") == source_config_hash(reference), f"{location}: source configuration changed; recapture evidence")
        require(snapshot.get("url") == reference["url"], f"{location}: URL differs from sources.json")
        validate_https_url(snapshot.get("final_url"), f"{location}: final_url")
        require(snapshot.get("status") == 200 or (reference.get("allow_missing", False) and snapshot.get("status") in {404, 410}),
                f"{location}: invalid HTTP status")
        validate_timestamp(snapshot.get("retrieved_at"), f"{location}: retrieved_at")
        snippets = snapshot.get("snippets")
        require(isinstance(snippets, list), f"{location}: missing snippets")
        for snippet in snippets:
            require(isinstance(snippet, dict) and isinstance(snippet.get("section"), str)
                    and isinstance(snippet.get("text"), str) and bool(snippet["text"]), f"{location}: invalid snippet")
            require(snippet.get("sha256") == text_hash(snippet["text"]), f"{location}: snippet hash mismatch")
        links = snapshot.get("links")
        require(isinstance(links, list), f"{location}: missing links")
        for link in links:
            require(isinstance(link, dict) and isinstance(link.get("title"), str), f"{location}: invalid link")
            validate_https_url(link.get("url"), f"{location}: link.url")
        require(snapshot.get("sha256") == snapshot_hash(snapshot), f"{location}: snapshot hash mismatch")
        evidence_count += len(snippets)

    claims = evidence.get("claims")
    expected = date_claims(current, history)
    require(isinstance(claims, dict) and set(claims) == set(expected), f"{evidence_path}: claim coverage differs from current/history dates")
    for target, value in expected.items():
        claim = claims[target]
        location = f"{evidence_path}: claims.{target}"
        require(isinstance(claim, dict) and "value" in claim and claim["value"] == value, f"{location}: date changed; review evidence too")
        status = claim.get("status")
        require(status in {"supported", "date_only", "unverified", "conflict", "predicted", "newly_announced"}, f"{location}: invalid status")
        require((status in {"predicted", "newly_announced"}) == (value is None), f"{location}: prediction/announcement status must match a null date")
        require(isinstance(claim.get("note"), str) and claim["note"], f"{location}: explanation required")
        refs = claim.get("evidence")
        require(isinstance(refs, list), f"{location}: evidence must be a list")
        for ref in refs:
            validate_reference(ref, snapshots, location)
        require(status not in {"supported", "date_only", "conflict", "newly_announced"} or bool(refs), f"{location}: supporting/conflicting excerpts required")
        if "candidate" in claim:
            require(status in {"conflict", "newly_announced"}, f"{location}: candidate requires a conflict or new announcement")
            validate_candidate(claim["candidate"], snapshots, location)
            require(claim["candidate"]["value"] != value, f"{location}: candidate must differ from the recorded value")
        if status in {"unverified", "conflict", "newly_announced"}:
            unresolved_count += 1
    return evidence_count, unresolved_count


def validate(data_dir: Path) -> dict[str, int]:
    catalog_path = data_dir / "catalog.json"
    schema_path = data_dir / "schema.json"
    catalog = load_json(catalog_path)
    load_json(schema_path)

    require(catalog.get("schema_version") == 1, f"{catalog_path}: unsupported schema_version")
    order = catalog.get("conference_order")
    require(isinstance(order, list) and order, f"{catalog_path}: conference_order must be non-empty")
    require(len(order) <= 100, f"{catalog_path}: at most 100 conferences are supported")
    require(
        all(isinstance(item, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item) for item in order),
        f"{catalog_path}: invalid ID",
    )
    require(len(order) == len(set(order)), f"{catalog_path}: duplicate conference ID")

    conference_dirs = sorted(
        path for path in data_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    directory_ids = {path.name for path in conference_dirs}
    require(set(order) == directory_ids, f"{catalog_path}: order and conference directories differ")

    counts = {"conferences": len(order), "events": 0, "history_records": 0, "sources": 0, "snippets": 0, "unresolved_claims": 0}
    global_event_ids: set[str] = set()
    for conference_id in order:
        directory = data_dir / conference_id
        documents = load_conference(directory)
        counts["events"] += validate_current(documents["current"], directory / "current.json", global_event_ids)
        counts["history_records"] += validate_history(documents["history"], directory / "history.json")
        source_ids = validate_sources(documents["sources"], directory / "sources.json")
        snippets, unresolved = validate_evidence(documents, directory / "evidence.json", source_ids)
        counts["sources"] += len(source_ids)
        counts["snippets"] += snippets
        counts["unresolved_claims"] += unresolved
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    try:
        counts = validate(args.data_dir.resolve())
    except ValidationError as error:
        print(f"Data validation failed: {error}", file=sys.stderr)
        return 1
    print(
        "Validated {conferences} conferences, {events} current events, "
        "{history_records} history records, {sources} sources, and {snippets} evidence snippets. "
        "Claims needing review: {unresolved_claims}.".format(**counts)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
