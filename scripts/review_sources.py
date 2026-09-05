#!/usr/bin/env python3
"""Render a self-contained HTML source review. Offline, standard library only."""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import html
from itertools import zip_longest
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlparse

from data_io import ValidationError, load_json, require, text_hash, write_json
from review_data import compare_local, local_snapshot
from source_evidence import MONTH, date_claims, source_status
from validate_data import validate_reference

SOURCE_STATUS = {"unchanged": "来源快照未变化", "changed": "内容变化", "new": "新增来源", "removed": "移除来源",
                 "unrecorded": "尚无证据快照", "error": "抓取失败"}
HISTORY_LABELS = {"abstract_deadline": "摘要截止", "paper_deadline": "全文截止", "commitment_deadline": "Commitment 截止",
                  "review_release": "初审结果", "rebuttal_deadline": "作者回复截止", "final_decision": "最终结果",
                  "conference_start": "会议开始", "conference_end": "会议结束"}
MISSING = object()
DISPLAY_MONTH = MONTH.replace("Sep(?:tember)?", "Sep(?:t(?:ember)?)?")
DAY = r"\d{1,2}(?:\s*(?:st|nd|rd|th))?"
RANGE = rf"(?:\s*(?:[-–—]|to|through)\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+(?:the\s+)?)?{DAY})?"
YEAR = r"(?:\s*,?\s*(?:20\d{2}|['’]\d{2}))?"
# Display-only: highlighting a date never interprets its event or changes a hash.
HIGHLIGHT_DATE = re.compile(
    rf"\b(?:20\d{{2}}-\d{{2}}-\d{{2}}(?:T[\d:.]+(?:Z|[+-]\d{{2}}:\d{{2}}))?"
    rf"|{DISPLAY_MONTH}\.?\s+{DAY}{RANGE}(?:\s*[-–—]\s*{DISPLAY_MONTH}\.?\s+{DAY})?{YEAR}"
    rf"|{DAY}{RANGE}\s+(?:of\s+)?{DISPLAY_MONTH}\.?{YEAR}"
    rf"|{DISPLAY_MONTH}\s+20\d{{2}}|\d{{1,2}}[./]\d{{1,2}}[./]20\d{{2}}"
    rf"|\d{{1,2}}:\d{{2}}(?:\s*[ap]m)?|UTC(?:[ \t]*[-+][ \t]*\d{{1,2}})?|AoE|TBA|TBD)\b", re.I)
SCRIPT = """
function reveal() {
  let node;
  try { node = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch (_) { return; }
  if (!node) return;
  if (node.tagName === 'DETAILS') node.open = true;
  for (let parent = node.parentElement; parent; parent = parent.parentElement)
    if (parent.tagName === 'DETAILS') parent.open = true;
  node.scrollIntoView({block: 'start'});
}
addEventListener('hashchange', reveal);
addEventListener('DOMContentLoaded', reveal);
document.addEventListener('click', function(event) {
  const anchor = event.target.closest('a[href^="#"]');
  if (anchor && anchor.hash === location.hash) reveal();
});
"""


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def link(title: str, url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return escape(url)
    return f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(title)} ↗</a>'


def anchor(*parts: str) -> str:
    return "-".join(parts)


def jump(title: str, target: str) -> str:
    return f'<a class="evidence-ref" href="#{quote(target, safe="-")}">{escape(title)}</a>'


def badge(label: str, kind: str = "") -> str:
    return f'<span class="badge {kind}">{escape(label)}</span>'


def highlighted(text: str, changes: list[tuple[int, int]] = (), tag: str = "ins") -> str:
    dates = [match.span() for match in HIGHLIGHT_DATE.finditer(text)]
    boundaries = sorted({0, len(text), *(point for span in [*dates, *changes] for point in span)})
    result = []
    for start, end in zip(boundaries, boundaries[1:]):
        value = escape(text[start:end])
        if any(a <= start < b for a, b in dates):
            value = f"<mark>{value}</mark>"
        if any(a <= start < b for a, b in changes):
            value = f"<{tag}>{value}</{tag}>"
        result.append(value)
    return "".join(result)


def inline_diff(before: str, after: str) -> tuple[str, str]:
    left, right = [], []
    # Token offsets retain whitespace and punctuation; never run a diff on HTML.
    old = list(re.finditer(r"\s+|\w+|[^\w\s]", before))
    new = list(re.finditer(r"\s+|\w+|[^\w\s]", after))
    matcher = difflib.SequenceMatcher(None, [m.group() for m in old], [m.group() for m in new], autojunk=False)
    for operation, a, b, c, d in matcher.get_opcodes():
        if operation != "equal":
            if a < b:
                left.append((old[a].start(), old[b - 1].end()))
            if c < d:
                right.append((new[c].start(), new[d - 1].end()))
    return highlighted(before, left, "del"), highlighted(after, right, "ins")


def date_value(value: object) -> str:
    if value is MISSING:
        return '<span class="muted">无此项</span>'
    if value is None:
        return '<span class="muted">尚未录入 · 使用预测</span>'
    return f'<span class="date-value">{escape(str(value).replace("T", " "))}</span>'


def source_changes(conference: dict) -> list[dict]:
    before = {}
    for source in conference["sources"]:
        config = source.get("before_config")
        if config:
            before[config["id"]] = config["url"]
        elif "before_config" not in source and source.get("before"):
            before[source["config"]["id"]] = source["before"]["url"]
    configs = [s["config"] for s in conference["sources"] if not s.get("removed")]
    after = {s["id"]: s["url"] for s in configs}
    candidates = {s["id"] for s in configs if s.get("candidate", False)}
    changes = []
    for key in before | after:
        old, new = before.get(key), after.get(key)
        if old != new:
            changes.append({"label": "新增来源" if old is None else ("移除来源" if new is None else "替换来源"),
                            "name": key, "before": old, "after": new, "pending": key in candidates, "target": f"source/{key}"})
        elif key in candidates:
            changes.append({"label": "候选来源", "name": key, "before": None, "after": new, "pending": True, "target": f"source/{key}"})
    for source in conference["sources"]:
        if not source.get("after") or source.get("error") or source.get("removed"):
            continue  # An unsuccessful fetch never means that links were removed.
        old = {item["url"]: item["title"] for item in (source.get("before") or {}).get("links", [])}
        new = {item["url"]: item["title"] for item in source["after"]["links"]}
        for url in old | new:
            if (url in old) != (url in new):
                changes.append({"label": "发现候选链接" if url in new else "页面移除链接",
                                "name": f'{source["config"]["id"]} · {new.get(url, old.get(url)) or url}',
                                "before": url if url in old else None, "after": url if url in new else None,
                                "pending": url in new and url not in after.values()})
    return changes


def supporting_excerpt(conference: dict, ref: dict) -> dict:
    source_id = ref.get("source")
    source = next((s for s in conference["sources"] if s["config"]["id"] == source_id), {})
    latest = source.get("after") if not source.get("error") and not source.get("removed") else None
    if latest and latest["url"] != source["config"]["url"]:
        latest = None
    for snapshot in (latest, source.get("before")):
        if not snapshot:
            continue
        snippet = next((s for s in snapshot["snippets"] if s["sha256"] == ref.get("snippet_sha256")), None)
        if snippet is None:
            continue
        require(snippet["sha256"] == text_hash(snippet["text"]), "supporting excerpt hash mismatch")
        validate_reference(ref, {source_id: snapshot}, f"{conference['id']}/{source_id}")
        return {"source": source_id, "url": snapshot["final_url"], "snippet": snippet,
                "quote": ref.get("quote"), "highlights": ref.get("highlights", []),
                "fresh": snapshot is latest and snapshot["status"] == 200}
    return {"source": source_id, "error": "引用的支撑段落缺失"}


def data_updates(conference: dict) -> list[dict]:
    local = conference.get("local", conference)
    before = date_claims(conference["current"], conference["history"])
    after = date_claims(local["current"], local["history"])
    claims = local.get("claims", {})
    updates = []
    for target in before | after:
        old, new = before.get(target, MISSING), after.get(target, MISSING)
        claim = claims.get(target, {})
        entries = []
        if old != new and not (old is MISSING and new is None):
            refs = claim.get("evidence", []) if claim.get("value", MISSING) == new else []
            if new is MISSING:
                refs = conference.get("claims", {}).get(target, {}).get("evidence", [])
            entries.append((old, new, refs, False))
        candidate = claim.get("candidate")
        if candidate and candidate["value"] != new:
            entries.append((new, candidate["value"], candidate["evidence"], True))
        for previous, value, refs, pending in entries:
            kind = "补全日期" if previous is None or previous is MISSING else "更新日期"
            if value is MISSING or value is None:
                kind = "移除日期"
            updates.append({
                "target": target, "before": previous, "after": value, "kind": kind, "pending": pending,
                "proofs": [supporting_excerpt(conference, ref) for ref in refs],
                "note": claim.get("note", ""),
            })
    return updates


def date_title(conference: dict, target: str) -> str:
    if target.startswith("history/"):
        _, year, field = target.split("/")
        return f"{year} · {HISTORY_LABELS[field]}"
    events = {f"current/{e['id']}": e for data in (conference["current"], conference.get("local", conference)["current"]) for e in data["events"]}
    return events[target]["title"]


def date_table(conference: dict, scope: str, targets: set[str]) -> str:
    local = conference.get("local", conference)
    before = date_claims(conference["current"], conference["history"])
    after = date_claims(local["current"], local["history"])
    local_changed = local is not conference
    headers = ["事件", "原记录"] + (["本地记录"] if local_changed else []) + ["证据关联"]
    rows = ['<div class="table-wrap"><table><thead><tr>' + "".join(f"<th>{value}</th>" for value in headers) + "</tr></thead><tbody>"]
    for target in before | after:
        if not target.startswith(scope + "/") or target not in targets:
            continue
        original, current = before.get(target, MISSING), after.get(target, MISSING)
        edition = conference["current"]["edition"] if scope == "current" else int(target.split("/")[1])
        claim = conference.get("claims", {}).get(target, {})
        refs = claim.get("evidence", [])
        if refs:
            evidence = "".join(jump(ref["source"], anchor("e", conference["id"], ref["source"], "before", ref["snippet_sha256"])) for ref in refs)
        else:
            sources = [s for s in conference["sources"] if s["config"]["edition"] == edition]
            snippets = sum(len(s["after"]["snippets"]) for s in sources if s.get("after"))
            evidence = (f'<span class="caption">该届已抓取 {snippets} 段，尚未关联到此事件</span>' if snippets else
                        '<span class="caption">尚无可关联的本次原文</span>')
            if sources:
                evidence += jump("查看该届来源", anchor("s", conference["id"], sources[0]["config"]["id"]))
        row = [f'{escape(date_title(conference, target))}<small>{escape(target)}</small>', date_value(original)]
        if local_changed:
            row.append('<span class="muted">未修改</span>' if original == current else
                       ('<span class="new-value">删除此项</span>' if current is MISSING else date_value(current)))
        row.append(evidence)
        rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>")
    return "".join(rows) + "</tbody></table></div>"


def focused_text(proof: dict) -> str:
    text, quote = proof["snippet"]["text"], proof.get("quote")
    if not quote:
        return escape(text)
    start = text.index(quote)
    end = start + len(quote)
    # Keep the matching line(s) and one surrounding line on each side. The full
    # hashed excerpt is still available in the source details below.
    line_start = text.rfind("\n", 0, start)
    context_start = text.rfind("\n", 0, max(0, line_start)) + 1
    line_end = text.find("\n", end)
    context_end = text.find("\n", line_end + 1) if line_end >= 0 else -1
    if context_end < 0:
        context_end = len(text)
    spans = [(start + quote.index(value), start + quote.index(value) + len(value)) for value in proof["highlights"]]
    boundaries = sorted({context_start, context_end, *(point for span in spans for point in span)})
    result = []
    for left, right in zip(boundaries, boundaries[1:]):
        value = escape(text[left:right])
        result.append(f'<mark class="updated-date">{value}</mark>' if any(a <= left < b for a, b in spans) else value)
    return "".join(result)


def update_card(conference: dict, update: dict) -> str:
    target = update["target"]
    title = date_title(conference, target)
    candidate = update["after"]
    new_value = '<span class="muted">移除</span>' if candidate is MISSING else date_value(candidate)
    value_label = "来源中的值" if update["pending"] else "更新后的值"
    result = [f'<article class="data-update" data-target="{escape(target)}"><div class="update-head"><h4>{escape(title)}</h4>'
              f'<div>{badge(update["kind"])}{badge("待审核", "warn") if update["pending"] else ""}</div></div>'
              f'<div class="date-comparison"><div><span class="caption">原记录</span>{date_value(update["before"])}</div>'
              f'<span class="comparison-arrow" aria-label="变为">→</span><div><span class="caption">{value_label}</span>{new_value}</div></div>']
    if isinstance(candidate, str) and len(candidate) == 10 and target.startswith("current/"):
        result.append('<p class="caption">采用时默认 23:59:59 AoE（UTC−12），显示文字自动生成。</p>')
    for proof in update["proofs"]:
        if proof.get("error"):
            result.append(f'<p class="notice error">{escape(proof["source"])}：{escape(proof["error"])}</p>')
            continue
        if not proof["fresh"] and update["kind"] != "移除日期":
            result.append('<p class="notice error">本次快照中未找到所引用段落；下方为已保存的证据。</p>')
        result.append('<div class="supporting-evidence">'
                      f'<div class="source-info">{link(proof["source"], proof["url"])}'
                      f'<span class="caption">{escape(proof["snippet"]["section"] or "正文")}</span>'
                      f'<span class="hash" title="{escape(proof["snippet"]["sha256"])}">{escape(proof["snippet"]["sha256"][:12])}</span></div>'
                      f'<blockquote class="support-text">{focused_text(proof)}</blockquote></div>')
    if not update["proofs"] and update["kind"] != "移除日期":
        result.append('<p class="notice warn">缺少新值的支撑段落</p>')
    if update["note"]:
        result.append(f'<p class="review-note">{escape(update["note"])}</p>')
    if update["pending"]:
        claim = conference.get("local", conference)["claims"][target]
        display = claim["candidate"].get("display")
        if display and (not target.startswith("current/") or len(claim["candidate"]["value"]) != 10):
            result.append(f'<p class="caption">App 显示：{escape(display["date_label"])} · {escape(display["detail_label"])}</p>')
        result.append(decision_controls(conference, target))
    return "".join(result) + "</article>"


def decision_controls(conference: dict, target: str) -> str:
    options = conference.get("review_options", {}).get(target)
    if options is None:
        return ""
    result = [f'<form class="decision-form" data-conference="{escape(conference["id"])}" data-target="{escape(target)}">'
              '<input name="reason" aria-label="审核原因（可选，会随数据发布）" placeholder="原因（可选，会随数据发布）" maxlength="500">']
    actions = (("undo", "撤销并重新审核"),) if "undo" in options else (("accept", "采用"), ("reject", "拒绝"))
    for action, title in actions:
        blocked = options[action]
        result.append(f'<button type="submit" value="{action}" title="{escape(blocked or title)}"{" disabled" if blocked else ""}>{title}</button>')
    if "undo" not in options:
        result.append('<button type="button" data-defer>暂不处理</button>')
    result.append(f'<p class="caption" role="status" aria-live="polite">{escape("；".join(value for value in options.values() if value))}</p></form>')
    return "".join(result)


def source_change_list(conference: dict, changes: list[dict]) -> str:
    result = ['<h3>来源增删与替换</h3><div class="source-changes">']
    for change in changes:
        result.append(f'<div class="source-change">{badge(change["label"])}<strong>{escape(change["name"])}</strong>'
                      f'{badge("待审核", "warn") if change["pending"] else ""}')
        for key, tag in (("before", "del"), ("after", "ins")):
            if change[key]:
                result.append(f'<div class="source-url"><{tag}>{link(change[key], change[key])}</{tag}></div>')
        if change["pending"]:
            result.append(decision_controls(conference, change.get("target", "")))
        result.append('</div>')
    return "".join(result) + '</div>'


def excerpt(snippet: dict | None, source_id: str, side: str, content: str | None = None, seen: set[str] | None = None) -> str:
    if snippet is None:
        return f'<div class="excerpt empty" data-side="{side}">{"基线" if side == "before" else "本次"}无此段</div>'
    identifier = anchor("e", source_id, side, snippet["sha256"])
    if seen is not None:
        if identifier in seen:
            identifier += f"-{len(seen)}"
        seen.add(identifier)
    return (f'<div class="excerpt" id="{escape(identifier)}" data-side="{"原来" if side == "before" else "本次"}">'
            f'<div class="chapter">{escape(snippet["section"] or "正文")} · {escape(snippet["sha256"][:12])}</div>'
            f'<div class="evidence-text">{highlighted(snippet["text"]) if content is None else content}</div></div>')


def evidence_diff(before: list[dict], after: list[dict], source_id: str, unchanged: bool) -> str:
    seen: set[str] = set()
    if unchanged:
        return '<div class="evidence-grid single"><div class="evidence-label">与已保存来源快照相同的段落</div>' + "".join(
            excerpt(item, source_id, "before", seen=seen) for item in after) + "</div>"
    result = ['<div class="evidence-grid"><div class="evidence-label">原来 · 已保存的基线</div><div class="evidence-label">本次 · 新抓取的原文</div>']
    matcher = difflib.SequenceMatcher(None, [(s["section"], s["sha256"]) for s in before],
                                     [(s["section"], s["sha256"]) for s in after], autojunk=False)
    for operation, a, b, c, d in matcher.get_opcodes():
        for old, new in zip_longest(before[a:b], after[c:d]):
            left, right = inline_diff(old["text"] if old else "", new["text"] if new else "")
            result.extend([excerpt(old, source_id, "before", left, seen), excerpt(new, source_id, "after", right, seen)])
    return "".join(result) + "</div>"


def source_card(conference_id: str, source: dict) -> str:
    config, before, after = source["config"], source.get("before"), source.get("after")
    status = source_status(source)
    old_snippets = before["snippets"] if before else []
    new_snippets = after["snippets"] if after else []
    source_id = anchor(conference_id, config["id"])
    kind = "error" if status == "error" else ("warn" if status in {"changed", "new"} else "")
    opened = status != "unchanged"
    observed_count = str(len(new_snippets)) if after else "未取得"
    result = [f'<details class="source" id="{escape(anchor("s", source_id))}"{" open" if opened else ""}><summary>'
              f'<strong>{escape(config["id"])}</strong>{badge(SOURCE_STATUS[status], kind)}'
              f'<span class="caption">{escape(config["edition"])} · {escape(config["kind"].upper())} · {len(old_snippets)} → {observed_count} 段</span>'
              '</summary><div class="source-body">',
              f'<div class="source-info">{link("打开官方来源", config["url"])}</div>']
    if config["kind"] == "pdf":
        result.append('<p class="notice warn">PDF 多列或表格的阅读顺序可能把事件标签与日期分开。高亮不代表配对正确；请结合原 PDF 核对。</p>')
    for title, snapshot in (("基线", before), ("本次", after)):
        if snapshot:
            result.append(f'<div class="source-info"><span>{title} · {escape(snapshot["retrieved_at"])} · HTTP {snapshot["status"]}</span>'
                          f'<span class="hash" title="{escape(snapshot["sha256"])}">SHA-256 {escape(snapshot["sha256"][:16])}</span>'
                          f'{link("实际地址", snapshot["final_url"])}</div>')
    if status in {"error", "removed", "unrecorded"}:
        if status == "unrecorded":
            result.append('<p class="notice">尚未保存此来源的证据快照。</p>')
        if status == "error":
            result.append(f'<p class="notice error">{escape(source.get("error") or "未取得本次响应")}。本次抓取失败，不是原文被删除。</p>')
        if old_snippets:
            seen: set[str] = set()
            result.append('<p class="caption">已保存的原文</p><div class="evidence-grid single">' +
                          "".join(excerpt(s, source_id, "before", seen=seen) for s in old_snippets) + "</div>")
    else:
        if before and (before["config_sha256"] != after["config_sha256"] or before["extractor"] != after["extractor"]):
            result.append('<p class="notice warn">提取规则或来源配置已变更；即使文字相同，也需要核对新基线。</p>')
        if not new_snippets:
            message = (f'页面尚不可访问（HTTP {after["status"]}），因此没有原文可抓；这是允许监测的未来页面状态。'
                       if after["status"] in {404, 410} else '页面可访问，但固定提取规则未找到日期段落。空结果不证明官网尚未公布日期。')
            result.append(f'<p class="notice warn">{message}</p>')
        if old_snippets or new_snippets:
            result.append(evidence_diff(old_snippets, new_snippets, source_id, status == "unchanged"))
        old_links = {item["url"]: item["title"] for item in before["links"]} if before else {}
        new_links = {item["url"]: item["title"] for item in after["links"]}
        if old_links or new_links:
            result.append(f'<details class="fold"><summary>页面中的候选来源链接 · {len(new_links)} 个（不自动信任）</summary><ul class="links">')
            for url in new_links | old_links:
                change = "added" if url not in old_links else ("removed" if url not in new_links else "")
                label = "新增 · " if change == "added" else ("移除 · " if change == "removed" else "")
                result.append(f'<li class="{change}">{label}{link(new_links.get(url, old_links.get(url)) or url, url)}</li>')
            result.append("</ul></details>")
    return "".join(result) + "</div></details>"


def prepare_conference(conference: dict) -> dict:
    """Compute differences once for the summary, ordering, and conference section."""
    local = conference.get("local", conference)
    updates = data_updates(conference) if not conference.get("error") else []
    inventory = source_changes(conference)
    files = [name for name in ("current", "history") if local.get(name) != conference.get(name)]
    changed, same = [], []
    for source in conference["sources"]:
        (same if source_status(source) == "unchanged" else changed).append(source)
    priority = 0 if updates or inventory else (1 if conference.get("error") or files or changed else 2)
    return {**conference, "updates": updates, "inventory": inventory, "changed_files": files,
            "changed_sources": changed, "same_sources": same, "priority": priority}


def conference_report(conference: dict) -> str:
    local = conference.get("local", conference)
    current = local.get("current", {})
    updates, inventory = conference["updates"], conference["inventory"]
    focused = {update["target"] for update in updates}
    changed_sources, same_sources = conference["changed_sources"], conference["same_sources"]
    decisions = local.get("decisions", [])
    labels = []
    if inventory:
        labels.append(f"{len(inventory)} 项来源变动")
    if updates:
        labels.append(f'{len(updates)} 项记录差异')
        pending = sum(u["pending"] for u in updates)
        if pending:
            labels.append(f"{pending} 项待审核")
    if conference["changed_files"] and not focused:
        labels.append("其他数据变更")
    if changed_sources and not inventory:
        labels.append(f"{len(changed_sources)} 个来源内容变化 / 异常")
    if conference.get("error"):
        labels.append("数据错误")
    if decisions:
        labels.append(f"{len(decisions)} 条审核记录")
    result = [f'<details class="conference" id="{escape(conference["id"])}"{" open" if conference["priority"] < 2 else ""}><summary class="section-head">'
              f'<h2>{escape(current.get("short_name", conference["id"]))}<small>{escape(current.get("edition", ""))}</small></h2>'
              f'<span class="section-meta">{escape(" · ".join(labels) or "无差异")}</span></summary><div class="conference-body">']
    if conference.get("error"):
        return "".join(result) + f'<p class="notice error">{escape(conference["error"])}</p></div></details>'
    if inventory:
        result.append(source_change_list(conference, inventory))
    if updates:
        result.append('<h3>信息变化</h3>')
        result.extend(update_card(conference, update) for update in updates)
    if decisions:
        result.append(f'<details class="fold"><summary>审核记录 · {len(decisions)} 条</summary><ul class="decisions">')
        for index, decision in reversed(list(enumerate(decisions))):
            proposal = decision["proposal"]
            value = proposal.get("value", proposal.get("url", ""))
            label = "已采用" if decision["action"] == "accept" else "已拒绝"
            if decision.get("undone_at"):
                label = "已撤销 · 原" + ("采用" if decision["action"] == "accept" else "拒绝")
            result.append(f'<li>{badge(label)}'
                          f' {escape(decision["target"])} · {escape(value)}'
                          f'<p class="caption">{escape(decision["reviewed_at"])} · {escape(decision["reason"] or "未填写原因")}</p>')
            if decision.get("undone_at"):
                result.append(f'<p class="caption">撤销于 {escape(decision["undone_at"])} · {escape(decision["undo_reason"] or "未填写原因")}</p>')
            else:
                result.append(decision_controls(conference, f"decision/{index}"))
            result.append('</li>')
        result.append('</ul></details>')
    for name in conference["changed_files"]:
        before = json.dumps(conference[name], ensure_ascii=False, indent=2, sort_keys=True)
        after = json.dumps(local[name], ensure_ascii=False, indent=2, sort_keys=True)
        left, right = inline_diff(before, after)
        result.append(f'<details class="fold"><summary>{name}.json 完整差异</summary>'
                      '<div class="evidence-grid"><div class="evidence-label">原来</div><div class="evidence-label">本地记录</div>'
                      f'<div class="excerpt evidence-text">{left}</div><div class="excerpt evidence-text">{right}</div></div></details>')
    if changed_sources:
        covered = {proof["source"] for update in updates for proof in update["proofs"] if not proof.get("error")}
        pending = any(s["config"]["id"] not in covered or source_status(s) == "error" for s in changed_sources)
        result.append(f'<details class="fold"{" open" if pending else ""}><summary>来源内容变化与待整理项 · {len(changed_sources)} 个</summary>')
        for source in changed_sources:
            result.append(source_card(conference["id"], source))
        result.append('</details>')
    remaining = (date_claims(conference["current"], conference["history"]).keys()
                 | date_claims(local["current"], local["history"]).keys()) - focused
    if remaining:
        result.append('<details class="fold"><summary>其他日期</summary>')
        for scope in ("current", "history"):
            if any(target.startswith(scope + "/") for target in remaining):
                result.append(date_table(conference, scope, remaining))
        result.append('</details>')
    if same_sources:
        result.append(f'<details class="fold"><summary>未变化的来源 · {len(same_sources)} 个</summary>')
        for source in same_sources:
            result.append(source_card(conference["id"], source))
        result.append('</details>')
    return "".join(result) + "</div></details>"


def render_report(report: dict, *, review: dict | None = None) -> str:
    require(report.get("schema_version") == 1, "unsupported report schema")
    conferences = []
    for original in report["conferences"]:
        try:
            if review:
                original = {**original, "review_options": review["options"].get(original["id"], {})}
            conference = prepare_conference(original)
            section = conference_report(conference)
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            conference = {**original, "priority": 1, "updates": [], "inventory": []}
            section = (f'<section class="conference"><h2>{escape(original["id"])}</h2>'
                       f'<p class="notice error">原始记录格式有误：{escape(error)}</p></section>')
        conferences.append({**conference, "html": section})
    conferences.sort(key=lambda c: c["priority"])
    updates = [update for c in conferences for update in c["updates"]]
    inventory = [change for c in conferences for change in c["inventory"]]
    stylesheet = Path(__file__).with_name("report.css").read_text(encoding="utf-8")
    script = SCRIPT + (Path(__file__).with_name("review.js").read_text(encoding="utf-8") if review else "")
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy = f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'"
    if review:
        policy += "; connect-src 'self'"
    result = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
              f'<meta http-equiv="Content-Security-Policy" content="{escape(policy)}"><meta name="referrer" content="no-referrer">',
              '<title>Conference Countdown · 更新审核</title>',
              (f'<meta name="review-token" content="{escape(review["token"])}"><meta name="review-revision" content="{escape(review["revision"])}">' if review else ""),
              f"<style>{stylesheet}</style></head><body><main>",
              '<header><div class="eyebrow">Conference Countdown / Update Review</div><h1>会议数据更新审核</h1>',
              f'<p class="muted">记录于 {escape(report["checked_at"])} · 本地数据对照</p></header><div class="stats">']
    for value, label in ((len(inventory), "来源增删 / 替换"),
                         (sum(u["kind"] == "补全日期" for u in updates), "补全日期 · 从无到有"),
                         (sum(u["kind"] == "更新日期" for u in updates), "更新已有日期"),
                         (sum(u["pending"] for u in updates) + sum(c["pending"] for c in inventory), "待审核")):
        result.append(f'<div class="stat"><strong>{value}</strong><span>{label}</span></div>')
    result.append('</div><div class="legend"><span><mark class="updated-date">更新 / 待核对日期</mark></span><span><del>移除内容</del></span>'
                  '<span><ins>新增内容</ins></span></div><nav class="tabs" aria-label="会议">')
    for conference in conferences:
        current = conference.get("local", conference).get("current", {})
        result.append(f'<a href="#{quote(conference["id"], safe="")}">{escape(current.get("short_name", conference["id"]))}</a>')
    result.append("</nav>")
    if report.get("validation_error"):
        result.append(f'<p class="notice error">数据校验失败：{escape(report["validation_error"])}</p>')
    result.extend(conference["html"] for conference in conferences)
    result.append('<footer>Conference Countdown</footer>')
    result.append(f"</main><script>{script}</script></body></html>")
    return "\n".join(result) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", nargs="?", type=Path, default=Path("build/source-check/baseline.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--snapshot", action="store_true", help="save local data as the baseline before editing")
    parser.add_argument("--output", type=Path, help="defaults to report.html beside the baseline")
    parser.add_argument("--serve", action="store_true", help="open a local review page with accept/reject/undo buttons")
    parser.add_argument("--port", type=int, default=0, help="local port (default: choose a free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser in serve mode")
    args = parser.parse_args()
    require(0 <= args.port <= 65535, "invalid port")
    require(args.serve or (not args.port and not args.no_browser), "--port and --no-browser require --serve")
    require(not (args.serve and (args.snapshot or args.output)), "--serve cannot be combined with --snapshot or --output")
    protected = {args.data_dir.resolve(), Path(__file__).resolve().parent.parent / "data"}
    if args.snapshot:
        require(args.output is None, "--output cannot be used with --snapshot")
        require(args.baseline.suffix == ".json", "baseline must be a JSON file")
        require(not any(path in args.baseline.resolve().parents for path in protected), "baseline must be outside data/")
        write_json(args.baseline, local_snapshot(args.data_dir.resolve()))
        print(f"Saved local baseline: {args.baseline}")
        return 0
    require(args.baseline.is_file(), "baseline missing: run with --snapshot before editing data, or pass a saved source-check report.json")
    baseline = load_json(args.baseline)
    require(baseline.get("schema_version") == 1, "unsupported baseline schema")
    if args.serve:
        from review_server import serve
        return serve(args.data_dir.resolve(), baseline, args.port, not args.no_browser)
    output = args.output or args.baseline.with_name("report.html")
    require(output.suffix == ".html", "output must be an HTML file")
    require(not any(path in output.resolve().parents for path in protected), "output must be outside data/")
    document = render_report(compare_local(baseline, local_snapshot(args.data_dir.resolve())))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(f"Report: {output} (local data only; no fetching or approval changes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as error:
        print(f"Report failed: {error}", file=sys.stderr)
        raise SystemExit(2)
