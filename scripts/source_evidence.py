"""Deterministic, contextual source snapshots. Remote content is data, never code."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from data_io import object_hash, text_hash, utc_now

EXTRACTOR = "date-context-v1"
MAX_BYTES = 5_000_000
DATE_FIELDS = ("abstract_deadline", "paper_deadline", "commitment_deadline", "review_release",
               "rebuttal_deadline", "final_decision", "conference_start", "conference_end")
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE = re.compile(rf"\b(?:20\d{{2}}[-/]\d{{1,2}}[-/]\d{{1,2}}|\d{{1,2}}[./-]\d{{1,2}}[./-]20\d{{2}}|{MONTH}\.?[\s-]+\d{{1,2}}(?:\s*(?:st|nd|rd|th))?|\d{{1,2}}(?:\s*(?:st|nd|rd|th))?[\s-]+{MONTH})\b", re.I)
JSON_DATE = re.compile(rf"\b(?:20\d{{2}}-\d{{2}}-\d{{2}}|{MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?|\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH})\b", re.I)
TOPIC = re.compile(r"date|deadline|submission|abstract|paper|review|rebuttal|response|discussion|notification|decision|conference|meeting|symposium|commitment|camera.?ready|AoE|anywhere on earth|UTC|\bheld\b", re.I)
PENDING = re.compile(r"\b(?:TBA|TBD|to be (?:announced|determined))\b", re.I)
SOURCE_LINK = re.compile(r"important.?dates|call.{0,25}(?:papers|submission)|research.track|\bcfp\b|openreview\.net|/dates(?:[/#?]|$)", re.I)
BLOCK_TAGS = {"article", "blockquote", "dd", "div", "dl", "dt", "li", "main", "p", "section", "tr"}
HEADINGS = {f"h{number}" for number in range(1, 7)}
IGNORED = {"script", "style", "noscript", "svg", "template", "nav", "footer", "aside", "select"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def source_config_hash(source: dict) -> str:
    # Approval metadata does not change the fetched content or extraction rules.
    return object_hash({key: value for key, value in source.items() if key != "candidate"})


def snapshot_hash(snapshot: dict) -> str:
    # Retrieval timestamps and HTTP headers must not cause daily false positives.
    return object_hash({key: snapshot[key] for key in (
        "extractor", "config_sha256", "url", "final_url", "status", "snippets", "links"
    )})


def source_status(source: dict) -> str:
    if source.get("removed"):
        return "removed"
    if source.get("unrecorded"):
        return "unrecorded"
    if source.get("error") or source.get("after") is None:
        return "error"
    if source.get("before") is None:
        return "new"
    return "unchanged" if source["before"]["sha256"] == source["after"]["sha256"] else "changed"


def summarize(report: dict) -> dict:
    counts = {"unchanged": 0, "changed": 0, "errors": int(bool(report.get("validation_error")))}
    for conference in report["conferences"]:
        counts["errors"] += int(bool(conference.get("error")))
        for source in conference["sources"]:
            status = source_status(source)
            counts[{"new": "changed", "removed": "changed", "unrecorded": "errors", "error": "errors"}.get(status, status)] += 1
    return counts


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.section = ""
        self.parts: list[str] = []
        self.heading: str | None = None
        self.ignored: list[str] = []
        self.anchor: str | None = None
        self.anchor_text: list[str] = []

    def flush(self) -> None:
        text = normalize_text(" ".join(self.parts)).strip(" |")
        self.parts = []
        if text:
            block = {"section": self.section, "text": text}
            if not self.blocks or block != self.blocks[-1]:
                self.blocks.append(block)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        # Links in navigation can lead to newly announced CFPs, even though navigation
        # text itself is excluded from the evidence paragraphs.
        if tag == "a":
            self.anchor = attributes.get("href")
            self.anchor_text = []
        if self.ignored:
            if tag not in VOID_TAGS:
                self.ignored.append(tag)
            return
        marker = " ".join(attributes.get(key) or "" for key in ("id", "class"))
        if tag in IGNORED or re.search(r"countdown|count-down|time-remaining|cookie|consent", marker, re.I):
            self.flush()
            if tag not in VOID_TAGS:
                self.ignored.append(tag)
            return
        if tag in HEADINGS:
            self.flush()
            self.heading = tag
        elif tag in BLOCK_TAGS:
            self.flush()
        elif tag in {"td", "th"} and self.parts:
            self.parts.append(" | ")
        elif tag == "br":
            self.parts.append(" ")
        elif tag in {"del", "s", "strike"}:
            self.parts.append("[removed]")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor is not None:
            self.links.append({"url": self.anchor, "title": normalize_text(" ".join(self.anchor_text))})
            self.anchor = None
            self.anchor_text = []
        if self.ignored:
            if tag in self.ignored:
                index = len(self.ignored) - 1 - self.ignored[::-1].index(tag)
                del self.ignored[index:]
            return
        if tag == self.heading:
            self.section = normalize_text(" ".join(self.parts))
            self.heading = None
            self.flush()
        elif tag in BLOCK_TAGS:
            self.flush()
        elif tag in {"del", "s", "strike"}:
            self.parts.append("[/removed]")

    def handle_data(self, data: str) -> None:
        if self.anchor is not None:
            self.anchor_text.append(data)
        if not self.ignored:
            self.parts.append(data)


def contextual_snippets(blocks: list[dict[str, str]], section_pattern: str | None = None) -> list[dict]:
    blocks = [block for block in blocks if not re.fullmatch(
        r"(?:\d+\s*(?:days?|hours?|minutes?|seconds?)\s*)+", block["text"], re.I)]
    if section_pattern:
        pattern = re.compile(section_pattern, re.I)
        blocks = [block for block in blocks if pattern.search(block["section"]) or re.search(
            r"all (?:times|dates|deadlines)|anywhere on earth|UTC.?12", block["text"], re.I)]
    selected: set[int] = set()
    for index, block in enumerate(blocks):
        future_year = "future" in block["section"].lower() and re.search(r"\b20\d{2}\b", block["text"])
        if not (DATE.search(block["text"]) or PENDING.search(block["text"]) or future_year):
            continue
        start, end = max(0, index - 1), min(len(blocks), index + 2)
        context = " ".join(item["text"] for item in blocks[start:end])
        if block["section"] == "PDF" or TOPIC.search(block["section"] + " " + context):
            selected.update(range(start, end))
    groups: list[list[int]] = []
    for index in sorted(selected):
        if (groups and index == groups[-1][-1] + 1 and len(groups[-1]) < 8
                and blocks[index]["section"] == blocks[groups[-1][0]]["section"]):
            groups[-1].append(index)
        else:
            groups.append([index])
    return [make_snippet(blocks[group[0]]["section"], "\n".join(blocks[index]["text"] for index in group)) for group in groups]


def make_snippet(section: str, text: str) -> dict:
    return {"section": section, "text": text, "sha256": text_hash(text)}


def json_snippets(payload: bytes) -> list[dict]:
    results: list[dict] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, child in sorted(node.items()):
                pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
                value = child.get("value") if isinstance(child, dict) else child
                is_timestamp = isinstance(value, (int, float)) and not isinstance(value, bool) and 1e12 <= value < 1e13
                is_date = isinstance(value, str) and (JSON_DATE.search(value) or PENDING.search(value))
                # Ignore mdate/cdate and OpenReview invitation IDs, which are not dates.
                if TOPIC.search(key) and (is_timestamp or is_date):
                    text = json.dumps({"path": pointer, "value": value}, ensure_ascii=False, sort_keys=True)
                    if is_timestamp:
                        text += "\nUTC: " + datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
                    results.append(make_snippet(pointer, text))
                elif isinstance(child, (dict, list)):
                    walk(child, pointer)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, path + f"/{index}")

    walk(json.loads(payload), "")
    return results


def fetch(url: str) -> tuple[int, str, bytes]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source must be a public HTTPS URL without credentials")
    # System curl avoids differences between macOS Python's and CI's TLS libraries.
    # A regular output file lets curl discard partial bodies before retrying;
    # stdout would concatenate failed and successful attempts into false evidence.
    with tempfile.TemporaryDirectory(prefix="conference-source-") as directory:
        body = Path(directory) / "response"
        result = subprocess.run([
            "curl", "--silent", "--show-error", "--location", "--max-redirs", "5",
            "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "10",
            "--max-time", "30", "--retry", "2", "--retry-all-errors", "--retry-delay", "2", "--retry-max-time", "45",
            "--max-filesize", str(MAX_BYTES), "--user-agent", "ConferenceCountdown-SourceCheck/1.0",
            "--header", "Accept: text/html,application/json,application/pdf;q=0.9",
            "--output", str(body), "--write-out", "%{http_code}\n%{url_effective}", url,
        ], capture_output=True, timeout=100, check=False)
        if result.returncode:
            raise ValueError(result.stderr.decode("utf-8", errors="replace").strip() or f"curl exited {result.returncode}")
        status, final_url = result.stdout.rsplit(b"\n", 1)
        with body.open("rb") as response:
            payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("response exceeds 5 MB")
    return int(status), final_url.decode("utf-8"), payload


def observe(source: dict) -> dict:
    status, final_url, payload = fetch(source["url"])
    snippets: list[dict] = []
    links: list[dict] = []
    if status in {404, 410} and source.get("allow_missing", False):
        pass  # Absence is a watch baseline, never evidence for a date.
    elif status != 200:
        raise ValueError(f"HTTP {status}: {final_url}")
    elif source["kind"] == "json":
        snippets = json_snippets(payload)
    elif source["kind"] == "pdf":
        if not payload.startswith(b"%PDF-"):
            raise ValueError("expected PDF, received a different document")
        try:
            result = subprocess.run(["pdftotext", "-", "-"], input=payload, capture_output=True, timeout=30, check=True)
        except FileNotFoundError as error:
            raise ValueError("PDF sources require pdftotext (brew install poppler)") from error
        lines = [normalize_text(line) for line in result.stdout.decode("utf-8").splitlines()]
        snippets = contextual_snippets([{"section": "PDF", "text": line} for line in lines if line])
    else:
        page = PageText()
        page.feed(payload.decode("utf-8", errors="replace"))
        page.close()
        page.flush()
        blocks = page.blocks
        if source.get("start_text"):
            start = next((index for index, block in enumerate(blocks) if block["text"] == source["start_text"]), None)
            if start is None:
                raise ValueError("start_text anchor is missing; inspect the source")
            end = next((index for index in range(start + 1, len(blocks)) if blocks[index]["text"] == source["end_text"]), None)
            if end is None:
                raise ValueError("end_text anchor is missing; inspect the source")
            blocks = blocks[start:end]
            snippets = [make_snippet(blocks[index]["section"], "\n".join(block["text"] for block in blocks[index:index+8]))
                        for index in range(0, len(blocks), 8)]
        else:
            snippets = contextual_snippets(blocks, source.get("section_pattern"))
        if source.get("discover_links", False):
            discovered = {}
            for link in page.links:
                url = urldefrag(urljoin(final_url, link["url"]))[0]
                if (urlparse(url).scheme == "https" and url != urldefrag(final_url)[0]
                        and SOURCE_LINK.search(link["title"] + " " + url)):
                    discovered[url] = {"url": url, "title": link["title"]}
            links = [discovered[url] for url in sorted(discovered)]
        if not page.blocks:
            raise ValueError("no readable HTML body; the page may require JavaScript or block requests")
    if status == 200 and not snippets and not source.get("allow_empty", False):
        raise ValueError("no contextual date evidence extracted; inspect the page/extraction rule")
    snapshot = {
        "extractor": EXTRACTOR, "config_sha256": source_config_hash(source),
        "url": source["url"], "final_url": final_url, "status": status,
        "retrieved_at": utc_now(), "snippets": snippets, "links": links,
    }
    snapshot["sha256"] = snapshot_hash(snapshot)
    return snapshot


def date_claims(current: dict, history: dict) -> dict:
    """Stable identifiers let validation catch a changed date with stale evidence."""
    claims = {f"current/{event['id']}": event["at"] for event in current["events"]}
    for record in history["records"]:
        for field in DATE_FIELDS:
            if field in record:
                claims[f"history/{record['year']}/{field}"] = record[field]
    return claims
