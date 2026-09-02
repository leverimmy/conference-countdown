#!/usr/bin/env python3
"""Collect contextual evidence from official conference and OpenReview sources.

The script records source evidence but never edits canonical conference data.
Copilot may propose current.json changes on the resulting pull request; a person
still reviews and merges those changes.
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
    r"notification|decision",
    re.IGNORECASE,
)
IGNORED_HTML_TAGS = {"script", "style", "noscript", "svg", "template"}
HTML_BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "footer",
    "header",
    "li",
    "main",
    "nav",
    "p",
    "section",
    "tr",
}
HTML_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
OPENREVIEW_METADATA_KEYS = {
    "cdate",
    "mdate",
    "pdate",
    "tcdate",
    "tmdate",
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, str]] = []
        self.current_section = ""
        self._parts: list[str] = []
        self._heading_tag: str | None = None
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if self._ignored_depth:
            if tag in IGNORED_HTML_TAGS:
                self._ignored_depth += 1
            return
        if tag in IGNORED_HTML_TAGS:
            self._flush_block()
            self._ignored_depth = 1
            return
        if tag in HTML_HEADING_TAGS:
            self._flush_block()
            self._heading_tag = tag
            return
        if tag in HTML_BLOCK_TAGS:
            self._flush_block()
        elif tag in {"td", "th"} and self._parts:
            self._parts.append(" | ")
        elif tag == "br" and self._parts:
            self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_depth:
            if tag in IGNORED_HTML_TAGS:
                self._ignored_depth -= 1
            return
        if tag == self._heading_tag:
            heading = normalize_text(" ".join(self._parts))
            self._parts = []
            self._heading_tag = None
            if heading:
                self.current_section = heading
                self._append_block(heading)
            return
        if tag in HTML_BLOCK_TAGS:
            self._flush_block()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = normalize_text(data)
        if value:
            self._parts.append(value)

    def finish(self) -> None:
        self._flush_block()

    def _flush_block(self) -> None:
        value = normalize_text(" ".join(self._parts).strip(" |"))
        self._parts = []
        if value:
            self._append_block(value)

    def _append_block(self, value: str) -> None:
        block = {"section": self.current_section, "text": value}
        if not self.blocks or self.blocks[-1] != block:
            self.blocks.append(block)


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


def html_evidence(payload: bytes) -> list[dict[str, str]]:
    parser = TextExtractor()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    parser.finish()

    ranges: list[tuple[int, int, str]] = []
    for index, block in enumerate(parser.blocks):
        if not DATE_PATTERN.search(block["text"]):
            continue
        start = max(0, index - 2)
        end = min(len(parser.blocks), index + 3)
        context = "\n".join(item["text"] for item in parser.blocks[start:end])
        if not KEYWORD_PATTERN.search(context):
            continue

        section = block["section"]
        if ranges:
            previous_start, previous_end, previous_section = ranges[-1]
            if (
                section == previous_section
                and start <= previous_end
                and end - previous_start <= 12
            ):
                ranges[-1] = (previous_start, max(previous_end, end), section)
                continue
        ranges.append((start, end, section))

    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for start, end, section in ranges:
        context = "\n".join(item["text"] for item in parser.blocks[start:end])[:2_000]
        key = (section, context)
        if key in seen:
            continue
        seen.add(key)
        evidence.append({"section": section, "context": context})
    return evidence[:80]


def openreview_evidence(payload: bytes) -> list[dict[str, object]]:
    value = json.loads(payload.decode("utf-8"))
    results: list[dict[str, object]] = []
    seen_paths: set[str] = set()

    def scalar_value(node: object) -> str | int | float | bool | None:
        if isinstance(node, (str, int, float, bool)):
            return node
        if isinstance(node, dict) and isinstance(
            node.get("value"), (str, int, float, bool)
        ):
            return node["value"]
        return None

    def sibling_context(node: dict) -> dict[str, object]:
        context: dict[str, object] = {}
        for key, child in node.items():
            if str(key).lower() in OPENREVIEW_METADATA_KEYS:
                continue
            scalar = scalar_value(child)
            if scalar is None:
                continue
            context[str(key)] = scalar
            if len(context) == 12:
                break
        return context

    def interpreted_timestamp(node: object) -> str | None:
        if (
            not isinstance(node, (int, float))
            or not 1_000_000_000_000 <= node <= 9_999_999_999_999
        ):
            return None
        return (
            datetime.fromtimestamp(node / 1_000, timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def walk(node: object, path: str = "") -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = f"{path}.{key}" if path else str(key)
                normalized_key = str(key).lower()
                scalar = scalar_value(child)
                if (
                    child_path not in seen_paths
                    and normalized_key not in OPENREVIEW_METADATA_KEYS
                    and OPENREVIEW_KEY_PATTERN.search(normalized_key)
                    and scalar is not None
                ):
                    item: dict[str, object] = {
                        "path": child_path,
                        "value": scalar,
                        "context": sibling_context(node),
                    }
                    interpreted = interpreted_timestamp(scalar)
                    if interpreted:
                        item["interpreted_utc"] = interpreted
                    seen_paths.add(child_path)
                    results.append(item)
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node[:100]):
                walk(child, f"{path}[{index}]")

    walk(value)
    return sorted(results, key=lambda item: str(item["path"]))[:80]


def digest_evidence(evidence: list[dict[str, object]]) -> str:
    canonical = json.dumps(
        evidence,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
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
        "> This PR records untrusted source evidence. Copilot may propose `current.json` changes",
        "> on this branch, but every proposed date still requires human review before merge.",
        "",
        "- [ ] I checked each changed source.",
        "- [ ] I reviewed Copilot's proposed `current.json` diff, or confirmed no change is needed.",
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
            {"schema_version": 2, "conference_id": conference_id, "sources": {}},
        )
        state["schema_version"] = 2
        state_sources = state.setdefault("sources", {})
        conference_changes: list[tuple[dict, dict | None, dict]] = []

        for watcher in sources["watch"]:
            watcher_id = watcher["id"]
            try:
                payload = fetch(watcher["url"])
                if len(payload) > 5_000_000:
                    raise ValueError("response exceeds 5 MB")
                evidence = (
                    openreview_evidence(payload)
                    if watcher["kind"] == "openreview"
                    else html_evidence(payload)
                )
                if not evidence and not watcher.get("optional", False):
                    raise ValueError("no date-related evidence was extracted")
            except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                optional = watcher.get("optional", False)
                report.append(f"- ⚠️ `{conference_id}/{watcher_id}` could not be checked: {error}")
                if not optional:
                    errors += 1
                continue

            new_state = {
                "kind": watcher["kind"],
                "url": watcher["url"],
                "sha256": digest_evidence(evidence),
                "observed_at": observed_at,
                "evidence": evidence[:30],
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
                report.append(
                    f"Evidence: `.github/source-state/{conference_id}.json` "
                    f"→ `sources.{watcher['id']}.evidence`"
                )
                report.append("")
                for item in new_state["evidence"][:6]:
                    if "section" in item and item["section"]:
                        section = str(item["section"]).replace("`", "'")
                        report.append(f"Section: `{section}`")
                    elif "path" in item:
                        field_path = str(item["path"]).replace("`", "'")
                        report.append(f"Field: `{field_path}`")
                    report.append("")
                    preview = item.get("context", item)
                    if not isinstance(preview, str):
                        preview = json.dumps(preview, ensure_ascii=False, sort_keys=True)
                    report.extend([
                        "```text",
                        preview[:800].replace("```", "'''"),
                        "```",
                        "",
                    ])
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
