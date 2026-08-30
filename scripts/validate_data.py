#!/usr/bin/env python3
"""Validate the canonical conference data without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DATE_FIELDS = (
    "abstract_deadline",
    "paper_deadline",
    "commitment_deadline",
    "review_release",
    "rebuttal_deadline",
    "final_decision",
    "conference_start",
    "conference_end",
)
HISTORICAL_KEYS = set(DATE_FIELDS) - {"conference_end"}


class ValidationError(RuntimeError):
    pass


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"{path}: cannot read JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: top-level JSON value must be an object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_iso_date(value: object, location: str) -> date:
    require(isinstance(value, str), f"{location}: expected an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError(f"{location}: invalid ISO date {value!r}") from error


def validate_https_url(value: object, location: str) -> None:
    require(isinstance(value, str), f"{location}: expected a URL string")
    parsed = urlparse(value)
    require(parsed.scheme == "https" and bool(parsed.netloc), f"{location}: expected an HTTPS URL")


def validate(data_dir: Path) -> dict[str, int]:
    catalog_path = data_dir / "catalog.json"
    schema_path = data_dir / "schema.json"
    catalog = load_json(catalog_path)
    load_json(schema_path)

    require(catalog.get("schema_version") == 1, f"{catalog_path}: unsupported schema_version")
    order = catalog.get("conference_order")
    require(isinstance(order, list) and order, f"{catalog_path}: conference_order must be non-empty")
    require(all(isinstance(item, str) and item for item in order), f"{catalog_path}: invalid ID")
    require(len(order) == len(set(order)), f"{catalog_path}: duplicate conference ID")

    conference_dirs = sorted(
        path for path in data_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    directory_ids = {path.name for path in conference_dirs}
    require(set(order) == directory_ids, f"{catalog_path}: order and conference directories differ")

    global_event_ids: set[str] = set()
    event_count = 0
    history_count = 0
    source_count = 0

    for conference_id in order:
        conference_dir = data_dir / conference_id
        current_path = conference_dir / "current.json"
        history_path = conference_dir / "history.json"
        sources_path = conference_dir / "sources.json"
        current = load_json(current_path)
        history = load_json(history_path)
        sources = load_json(sources_path)

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
                require(isinstance(at, str), f"{location}: at must be a string or null")
                try:
                    parsed_at = datetime.fromisoformat(at)
                except ValueError as error:
                    raise ValidationError(f"{location}: invalid ISO date-time {at!r}") from error
                require(parsed_at.tzinfo is not None, f"{location}: at must include a UTC offset")
                require(historical_key is None or historical_key in HISTORICAL_KEYS, f"{location}: invalid historical_key")
                require(target_year is None or isinstance(target_year, int), f"{location}: invalid target_year")

        require(current["default_event_id"] in local_event_ids, f"{current_path}: default_event_id is missing")
        event_count += len(events)

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
            parsed_dates: dict[str, date] = {}
            for field in DATE_FIELDS:
                if field in record:
                    parsed_dates[field] = validate_iso_date(record[field], f"{location}: {field}")
            if "conference_start" in parsed_dates and "conference_end" in parsed_dates:
                require(parsed_dates["conference_start"] <= parsed_dates["conference_end"], f"{location}: invalid conference range")
            if "review_release" in parsed_dates and "rebuttal_deadline" in parsed_dates:
                require(parsed_dates["review_release"] <= parsed_dates["rebuttal_deadline"], f"{location}: review follows response")
            if "rebuttal_deadline" in parsed_dates and "final_decision" in parsed_dates:
                require(parsed_dates["rebuttal_deadline"] <= parsed_dates["final_decision"], f"{location}: response follows decision")
            if "final_decision" in parsed_dates and "conference_start" in parsed_dates:
                require(parsed_dates["final_decision"] <= parsed_dates["conference_start"], f"{location}: decision follows conference")
        history_count += len(records)

        require(sources.get("schema_version") == 1, f"{sources_path}: unsupported schema_version")
        require(sources.get("id") == conference_id, f"{sources_path}: id must match directory")
        watchers = sources.get("watch")
        require(isinstance(watchers, list) and watchers, f"{sources_path}: watch must be non-empty")
        watcher_ids: set[str] = set()
        for index, watcher in enumerate(watchers):
            location = f"{sources_path}: watch[{index}]"
            require(isinstance(watcher, dict), f"{location}: watcher must be an object")
            watcher_id = watcher.get("id")
            require(isinstance(watcher_id, str) and watcher_id not in watcher_ids, f"{location}: invalid or duplicate id")
            watcher_ids.add(watcher_id)
            require(watcher.get("kind") in {"html", "openreview"}, f"{location}: invalid kind")
            validate_https_url(watcher.get("url"), f"{location}: url")
            require(
                isinstance(watcher.get("optional", False), bool),
                f"{location}: optional must be boolean",
            )
        source_count += len(watchers)

    return {
        "conferences": len(order),
        "events": event_count,
        "history_records": history_count,
        "sources": source_count,
    }


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
        "{history_records} history records, and {sources} monitored sources.".format(**counts)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
