"""Review snapshots and deterministic decisions. No HTTP or file writes."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from data_io import DOCUMENTS, ValidationError, load_conference, load_json, object_hash, require, utc_now
from validate_data import validate


def snapshot_conference(conference_id: str, documents: dict) -> dict:
    evidence = documents["evidence"]
    configs = documents["sources"]["sources"]
    source_ids = {s["id"] for s in configs}
    snapshots = evidence.get("sources", {})
    sources = [{"config": config, "before": snapshots.get(config["id"])} for config in configs]
    for key, snapshot in snapshots.items():
        if key not in source_ids:
            sources.append({"config": {"id": key, "url": snapshot["url"], "kind": "unknown", "edition": "—"},
                            "before": snapshot, "removed": True})
    return {"id": conference_id, "current": documents["current"], "history": documents["history"],
            "claims": evidence.get("claims", {}), "decisions": evidence.get("decisions", []), "sources": sources}


def local_snapshot(data_dir: Path) -> dict:
    validate(data_dir)
    conferences = [snapshot_conference(cid, load_conference(data_dir / cid))
                   for cid in load_json(data_dir / "catalog.json")["conference_order"]]
    return {"schema_version": 1, "checked_at": utc_now(), "conferences": conferences}


def compare_local(baseline: dict, current: dict) -> dict:
    old_conferences = {c["id"]: c for c in baseline["conferences"]}
    new_conferences = {c["id"]: c for c in current["conferences"]}
    conferences = []
    for conference_id in new_conferences | old_conferences:
        old = old_conferences.get(conference_id)
        new = new_conferences.get(conference_id)
        template = new or old
        empty = {"current": {**template["current"], "events": []}, "history": {**template["history"], "records": []},
                 "claims": {}, "sources": []}
        old, new = old or empty, new or empty
        old_sources = {s["config"]["id"]: s for s in old["sources"] if not s.get("removed")}
        new_sources = {s["config"]["id"]: s for s in new["sources"]}
        sources = []
        for source_id in new_sources | old_sources:
            previous, latest = old_sources.get(source_id), new_sources.get(source_id)
            snapshot = latest["before"] if latest else None
            sources.append({
                "config": (latest or previous)["config"],
                "before_config": previous["config"] if previous else None,
                "before": previous.get("before") if previous else None,
                "after": snapshot,
                "removed": latest is None, "unrecorded": latest is not None and snapshot is None, "error": None,
            })
        conferences.append({
            "id": conference_id,
            "current": old["current"],
            "history": old["history"],
            "claims": old.get("claims", {}),
            "sources": sources,
            "local": {
                "current": new["current"],
                "history": new["history"],
                "claims": new.get("claims", {}),
                "decisions": new.get("decisions", []),
            },
        })
    return {"schema_version": 1, "checked_at": current["checked_at"], "baseline_at": baseline["checked_at"],
            "conferences": conferences}


def claim_refs(claim: dict) -> list:
    return claim.get("evidence", []) + claim.get("candidate", {}).get("evidence", [])


def review_options(evidence: dict, sources: dict) -> dict:
    options = {}
    configs = {s["id"]: s for s in sources["sources"]}
    for target, claim in evidence["claims"].items():
        candidate = claim.get("candidate")
        if not candidate:
            continue
        blocked = ""
        if any(configs[ref["source"]].get("candidate") for ref in candidate["evidence"]):
            blocked = "请先采用该日期引用的候选来源"
        elif target.startswith("history/") and len(candidate["value"]) != 10:
            blocked = "历史记录需要 YYYY-MM-DD 日期"
        options[target] = {"accept": blocked, "reject": ""}
    for source in sources["sources"]:
        if not source.get("candidate"):
            continue
        used = any(ref["source"] == source["id"] for claim in evidence["claims"].values()
                   for ref in claim_refs(claim))
        options[f"source/{source['id']}"] = {
            "accept": "" if source["id"] in evidence["sources"] else "请先保存此来源的证据快照",
            "reject": "该来源仍被日期或候选引用，请先处理引用" if used else ("至少保留一个来源" if len(configs) == 1 else ""),
        }
    return options


def current_event(candidate: dict) -> dict:
    """Resolve an approved date without changing the original evidence proposal."""
    date_only = len(candidate["value"]) == 10
    value = candidate["value"] + "T23:59:59-12:00" if date_only else candidate["value"]
    display = candidate.get("display") if not date_only else None
    if not display:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        offset = at.strftime("%z")
        zone = "AoE" if offset == "-1200" else ("UTC" if offset == "+0000" else f"UTC{offset[:3]}:{offset[3:]}")
        beijing = at.astimezone(timezone(timedelta(hours=8)))
        display = {
            "date_label": f"{at.year} 年 {at.month} 月 {at.day} 日 {at:%H:%M:%S} {zone}",
            "detail_label": f"北京时间 {beijing.year} 年 {beijing.month} 月 {beijing.day} 日 {beijing:%H:%M:%S}",
        }
    return {"at": value, **display}


def load_documents(files: dict, conference_id: str) -> dict:
    return {name: json.loads(files[f"{conference_id}/{name}.json"]) for name in DOCUMENTS}


def date_record(documents: dict, target: str) -> tuple[dict, str]:
    scope, identifier = target.split("/", 1)
    if scope == "current":
        return next(e for e in documents[scope]["events"] if e["id"] == identifier), "at"
    year, field = identifier.split("/")
    return next(r for r in documents[scope]["records"] if r["year"] == int(year)), field


def target_state(documents: dict, target: str) -> dict:
    evidence = documents["evidence"]
    if target.startswith("source/"):
        source_id = target.split("/", 1)[1]
        sources = documents["sources"]["sources"]
        index = next((i for i, s in enumerate(sources) if s["id"] == source_id), None)
        return deepcopy({"config": sources[index] if index is not None else None, "index": index,
                         "snapshot": evidence["sources"].get(source_id)})
    record, _ = date_record(documents, target)
    return deepcopy({"record": record, "claim": evidence["claims"][target]})


def evidence_guard(documents: dict, target: str, previous: dict) -> str:
    evidence = documents["evidence"]
    if target.startswith("source/"):
        source_id = target.split("/", 1)[1]
        return object_hash({key: claim for key, claim in evidence["claims"].items()
                            if any(ref["source"] == source_id for ref in claim_refs(claim))})
    source_ids = {ref["source"] for claim in (previous["claim"], evidence["claims"][target]) for ref in claim_refs(claim)}
    configs = {s["id"]: s for s in documents["sources"]["sources"]}
    return object_hash({key: {"config": configs.get(key), "snapshot": evidence["sources"].get(key, {}).get("sha256")}
                        for key in source_ids})


def apply_choice(documents: dict, target: str, action: str, reason: str, reviewed_at: str) -> set[str]:
    evidence = documents["evidence"]
    options = review_options(evidence, documents["sources"])
    require(target in options, "候选已处理或不存在，请刷新页面")
    require(not options[target][action], options[target][action])
    previous = target_state(documents, target)
    changed = {"evidence"}
    receipt = {"target": target, "action": action, "reviewed_at": reviewed_at, "reason": reason}
    if target.startswith("source/"):
        source_id = target.split("/", 1)[1]
        source = next(s for s in documents["sources"]["sources"] if s["id"] == source_id)
        receipt["proposal"] = dict(source)
        if action == "accept":
            source.pop("candidate")
        else:
            documents["sources"]["sources"].remove(source)
            # Retain rejected evidence with the decision, not as an active source.
            receipt["source_snapshot"] = evidence["sources"].pop(source_id, None)
        changed.add("sources")
    else:
        claim = evidence["claims"][target]
        candidate = claim.pop("candidate")
        receipt.update(previous_value=claim["value"], proposal=candidate)
        if action == "accept":
            scope = target.split("/", 1)[0]
            record, field = date_record(documents, target)
            record[field] = candidate["value"]
            if scope == "current":
                record.update(current_event(candidate))
                record.pop("historical_key", None)
                record.pop("target_year", None)
            documents[scope]["last_verified"] = reviewed_at[:10]
            claim.update(value=record[field], evidence=candidate["evidence"], status=candidate.get("status", "supported"),
                         note=reason or candidate.get("note") or f"人工确认采用来源中的日期 {candidate['value']}。")
            if scope == "current" and len(candidate["value"]) == 10:
                claim["status"] = "date_only"
                claim["note"] += " 时刻按默认 23:59:59 AoE（UTC−12）计时。"
            changed.add(scope)
    receipt["proposal_sha256"] = object_hash(receipt["proposal"])
    undo = {"before": previous, "after_sha256": object_hash(target_state(documents, target)),
            "guard_sha256": evidence_guard(documents, target, previous)}
    receipt["undo"] = {**undo, "sha256": object_hash(undo)}
    evidence.setdefault("decisions", []).append(receipt)
    return changed


def undo_record(documents: dict, index: int, baseline: dict | None) -> dict:
    decisions = documents["evidence"].get("decisions", [])
    require(0 <= index < len(decisions), "决定不存在，请刷新页面")
    receipt = decisions[index]
    require(not receipt.get("undone_at"), "此决定已撤销")
    target = receipt["target"]
    require(not any(d["target"] == target and not d.get("undone_at") for d in decisions[index + 1:]), "请先撤销此项较新的决定")
    undo = receipt.get("undo")
    if undo is None:
        # Earlier versions stored only the proposal. Reconstruct only from a
        # matching pre-review baseline, never from a guessed old claim or label.
        conference_id = documents["evidence"]["id"]
        old = next((c for c in (baseline or {}).get("conferences", []) if c["id"] == conference_id), None)
        require(old is not None, "旧决定缺少原记录，请使用审批前的基线")
        original = deepcopy({"current": old["current"], "history": old["history"],
                             "sources": {"sources": [s["config"] for s in old["sources"] if not s.get("removed")]},
                             "evidence": {"claims": old.get("claims", {}), "sources": {
                                 s["config"]["id"]: s["before"] for s in old["sources"] if s.get("before")}}})
        apply_choice(original, target, receipt["action"], receipt["reason"], receipt["reviewed_at"])
        restored = original["evidence"]["decisions"][-1]
        require({k: v for k, v in restored.items() if k != "undo"} == receipt, "基线与旧决定不匹配，不能安全撤销")
        undo = restored["undo"]
    require(object_hash(target_state(documents, target)) == undo["after_sha256"]
            and evidence_guard(documents, target, undo["before"]) == undo["guard_sha256"],
            "相关记录或证据已变化，不能覆盖；请先核对后续修改")
    return undo


def undo_choice(documents: dict, index: int, reason: str, reviewed_at: str, baseline: dict | None) -> set[str]:
    undo = undo_record(documents, index, baseline)
    receipt = documents["evidence"]["decisions"][index]
    target, previous = receipt["target"], deepcopy(undo["before"])
    evidence = documents["evidence"]
    changed = {"evidence"}
    if target.startswith("source/"):
        source_id = target.split("/", 1)[1]
        sources = documents["sources"]["sources"]
        sources[:] = [s for s in sources if s["id"] != source_id]
        sources.insert(previous["index"], previous["config"])
        if previous["snapshot"] is None:
            evidence["sources"].pop(source_id, None)
        else:
            evidence["sources"][source_id] = previous["snapshot"]
        changed.add("sources")
    else:
        record, _ = date_record(documents, target)
        record.clear()
        record.update(previous["record"])
        evidence["claims"][target] = previous["claim"]
        if receipt["action"] == "accept":
            scope = target.split("/", 1)[0]
            documents[scope]["last_verified"] = reviewed_at[:10]
            changed.add(scope)
    receipt.update(undo=undo, undone_at=reviewed_at, undo_reason=reason)
    return changed


def decision_options(documents: dict, baseline: dict | None) -> dict:
    options = review_options(documents["evidence"], documents["sources"])
    for index, decision in enumerate(documents["evidence"].get("decisions", [])):
        if decision.get("undone_at"):
            continue
        try:
            undo_record(documents, index, baseline)
            blocked = ""
        except (KeyError, TypeError, ValueError, StopIteration, ValidationError) as error:
            blocked = str(error) or "缺少原记录，不能安全撤销"
        options[f"decision/{index}"] = {"undo": blocked}
    return options
