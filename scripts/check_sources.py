#!/usr/bin/env python3
"""Watch official conference and OpenReview endpoints for date-related changes.

The script only updates compact observation state. It never edits current.json or
history.json; a person must review the generated PR and make any canonical data
change before merging.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MONTH_PATTERN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
DATE_PATTERN = re.compile(
    rf"(?:\b20\d{{2}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}\b|"
    rf"\b{MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+20\d{{2}})?\b|"
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH_PATTERN}(?:,?\s+20\d{{2}})?\b)",
    re.IGNORECASE,
)
KEYWORD_PATTERN = re.compile(
    r"deadline|due|date|submission|abstract|paper|review|rebuttal|response|discussion|"
    r"notification|decision|accept|reject|camera[- ]ready|conference|commitment|author",
    re.IGNORECASE,
)
OPENREVIEW_KEY_PATTERN = re.compile(
    r"date|deadline|due|expire|start|end|submission|review|rebuttal|response|"
    r"notification|decision|venue",
    re.IGNORECASE,
)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []

    def handle_data(self, data: str) -> None:
        value = normalize_text(data)
        if value:
            self.fragments.append(value)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def fetch(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "conference-countdown-source-monitor/1.0 (+https://github.com/leverimmy/conference-countdown)",
            "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read(5_000_001)


def html_candidates(payload: bytes) -> list[str]:
    parser = TextExtractor()
    parser.feed(payload.decode("utf-8", errors="replace"))
    fragments = parser.fragments
    candidates: set[str] = set()
    for index in range(len(fragments)):
        window = normalize_text(" · ".join(fragments[index:index + 6]))
        if DATE_PATTERN.search(window) and KEYWORD_PATTERN.search(window):
            candidates.add(window[:300])
    return sorted(candidates)[:80]


def openreview_candidates(payload: bytes) -> list[str]:
    value = json.loads(payload.decode("utf-8"))
    results: set[str] = set()

    def walk(node: object, path: str = "") -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = f"{path}.{key}" if path else str(key)
                if OPENREVIEW_KEY_PATTERN.search(str(key)) and isinstance(child, (str, int, float, bool)):
                    results.add(f"{child_path}: {child}"[:300])
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node[:100]):
                walk(child, f"{path}[{index}]")

    walk(value)
    return sorted(results)[:80]


def digest_candidates(candidates: list[str]) -> str:
    canonical = json.dumps(candidates, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_object(path: Path, default: dict | None = None) -> dict:
    if not path.exists() and default is not None:
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_object(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--state-dir", type=Path, default=Path(".github/source-state"))
    parser.add_argument("--report", type=Path, default=Path("source-monitor-report.md"))
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    state_dir = args.state_dir.resolve()
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report: list[str] = [
        "# Conference source monitor",
        "",
        "> This PR records an automated observation; it does not change the canonical conference dates.",
        "> Compare every item with the linked source, edit the relevant `data/<id>/current.json`",
        "> (and `history.json` when appropriate), run `python scripts/validate_data.py`, then merge.",
        "",
        "- [ ] I checked each changed source.",
        "- [ ] I updated canonical data, or confirmed that no date change is needed.",
        "- [ ] I checked that official and predicted dates remain clearly distinguished.",
        "",
    ]
    changed_sources = 0
    errors = 0

    catalog = load_object(data_dir / "catalog.json")
    for conference_id in catalog["conference_order"]:
        sources = load_object(data_dir / conference_id / "sources.json")
        state_path = state_dir / f"{conference_id}.json"
        state = load_object(
            state_path,
            {"schema_version": 1, "conference_id": conference_id, "sources": {}},
        )
        state_sources = state.setdefault("sources", {})
        conference_changes: list[tuple[dict, dict | None, dict]] = []

        for watcher in sources["watch"]:
            watcher_id = watcher["id"]
            try:
                payload = fetch(watcher["url"])
                if len(payload) > 5_000_000:
                    raise ValueError("response exceeds 5 MB")
                candidates = (
                    openreview_candidates(payload)
                    if watcher["kind"] == "openreview"
                    else html_candidates(payload)
                )
                if not candidates:
                    raise ValueError("no date-related fields were extracted")
            except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                optional = watcher.get("optional", False)
                report.append(f"- ⚠️ `{conference_id}/{watcher_id}` could not be checked: {error}")
                if not optional:
                    errors += 1
                continue

            new_state = {
                "kind": watcher["kind"],
                "url": watcher["url"],
                "sha256": digest_candidates(candidates),
                "observed_at": observed_at,
                "candidates": candidates[:30],
            }
            old_state = state_sources.get(watcher_id)
            if not isinstance(old_state, dict) or old_state.get("sha256") != new_state["sha256"]:
                state_sources[watcher_id] = new_state
                conference_changes.append((watcher, old_state, new_state))
                changed_sources += 1

        if conference_changes:
            write_object(state_path, state)
            report.extend(["", f"## {conference_id.upper()}", ""])
            for watcher, old_state, new_state in conference_changes:
                change_kind = "initialized" if old_state is None else "changed"
                report.append(f"### {watcher['id']} ({change_kind})")
                report.append("")
                report.append(f"Source: {watcher['url']}")
                report.append("")
                report.append(f"New observation: `{new_state['sha256']}`")
                report.append("")
                report.append("Extracted date-related fields:")
                report.append("")
                for candidate in new_state["candidates"][:12]:
                    report.append(f"- {candidate}")
                report.append("")

    if changed_sources == 0:
        report.extend(["", "No source changes were detected.", ""])
    if errors:
        report.extend(["", f"Required sources with errors: **{errors}**. See the workflow log.", ""])

    args.report.write_text("\n".join(report), encoding="utf-8")
    print(f"Detected {changed_sources} changed sources; {errors} required sources could not be checked.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Source monitor failed: {error}", file=sys.stderr)
        raise
