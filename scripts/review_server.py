"""Local-only review buttons. Decisions are validated file edits, never LLM calls."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import webbrowser

from data_io import ValidationError, json_text, load_json, object_hash, require, text_hash, utc_now
from review_data import apply_choice, compare_local, decision_options, load_documents, local_snapshot, undo_choice
from validate_data import validate


def read_files(data_dir: Path) -> dict[str, str]:
    require(not any(p.is_symlink() for p in data_dir.rglob("*")), "审核模式不支持 data 内的符号链接")
    return {str(p.relative_to(data_dir)): p.read_text(encoding="utf-8") for p in sorted(data_dir.rglob("*.json"))}


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".review-", delete=False) as output:
        temporary = Path(output.name)
        try:
            if path.exists():
                os.fchmod(output.fileno(), path.stat().st_mode & 0o777)
            output.write(text.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def journal_path(data_dir: Path) -> Path:
    name = text_hash(str(data_dir))[:16]
    return data_dir.parent / "build" / "source-check" / f"review-{name}.transaction.json"


def validate_files(files: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="conference-review-") as directory:
        staged = Path(directory) / "data"
        for name, content in files.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        validate(staged)


def recover(data_dir: Path) -> None:
    """Finish a completed save, or restore an interrupted multi-file save."""
    journal = journal_path(data_dir)
    if not journal.exists():
        return
    changes = load_json(journal)
    files = read_files(data_dir)
    for name, change in changes.items():
        require(re.fullmatch(r"[a-z0-9-]+/(?:current|history|sources|evidence)\.json", name)
                and name in files and files[name] in (change["before"], change["after"]),
                f"恢复备份与本地文件冲突，请保留并检查 {journal}")
    if not all(files[name] == change["after"] for name, change in changes.items()):
        validate_files({**files, **{name: change["before"] for name, change in changes.items()}})
        for name, change in changes.items():
            atomic_write(data_dir / name, change["before"])
    validate(data_dir)
    journal.unlink()


def save_changes(data_dir: Path, before: dict[str, str], changes: dict[str, str]) -> None:
    # Validate the complete result before touching the real files.
    validate_files({**before, **changes})
    require(read_files(data_dir) == before, "本地数据已变化，请刷新页面后重新审核")
    journal = journal_path(data_dir)
    atomic_write(journal, json_text({name: {"before": before[name], "after": content} for name, content in changes.items()}))
    try:
        for name, content in changes.items():
            atomic_write(data_dir / name, content)
    except OSError:
        # Keep the journal if rollback itself fails, so the next start can recover.
        for name in changes:
            atomic_write(data_dir / name, before[name])
        journal.unlink()
        raise
    journal.unlink()


def decide(data_dir: Path, request: dict, baseline: dict | None = None) -> dict:
    """Called under the server's data lock; clients send IDs, never file patches."""
    require(isinstance(request, dict) and set(request) == {"conference", "target", "action", "revision", "reason"}
            and all(isinstance(value, str) for value in request.values()), "无效的审核请求")
    action, target, conference_id = request["action"], request["target"], request["conference"]
    require(action in {"accept", "reject", "undo"} and len(request["reason"]) <= 500, "无效的决定或原因过长")
    before = read_files(data_dir)
    require(request["revision"] == object_hash(before), "本地数据已变化，请刷新页面后重新审核")
    validate(data_dir)
    require(conference_id in json.loads(before["catalog.json"])["conference_order"], "会议不存在")
    documents = load_documents(before, conference_id)
    reason, reviewed_at = request["reason"].strip(), utc_now()
    if action == "undo":
        require(re.fullmatch(r"decision/(0|[1-9]\d*)", target), "无效的决定编号")
        changed = undo_choice(documents, int(target.split("/")[1]), reason, reviewed_at, baseline)
    else:
        changed = apply_choice(documents, target, action, reason, reviewed_at)
    save_changes(data_dir, before, {f"{conference_id}/{name}.json": json_text(documents[name]) for name in sorted(changed)})
    return {"saved": True, "action": action, "conference": conference_id}


def make_server(data_dir: Path, baseline: dict, port: int = 0) -> ThreadingHTTPServer:
    from review_sources import render_report

    data_dir = data_dir.resolve()
    mutex = threading.Lock()
    token = secrets.token_urlsafe(32)

    @contextmanager
    def locked_data():
        with mutex, (data_dir / "catalog.json").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            recover(data_dir)
            yield

    with locked_data():
        validate(data_dir)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass

        def reply(self, status, body, kind="application/json"):
            content = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if self.headers.get("Host") != host or self.path != "/":
                return self.reply(404, json_text({"error": "Not found"}))
            try:
                with locked_data():
                    files = read_files(data_dir)
                    current = local_snapshot(data_dir)
                    options = {c["id"]: decision_options(load_documents(files, c["id"]), baseline) for c in current["conferences"]}
                    require(files == read_files(data_dir), "本地数据已变化，请刷新")
                page = render_report(compare_local(baseline, current), review={"token": token, "revision": object_hash(files), "options": options})
                self.reply(200, page, "text/html")
            except (OSError, ValueError, KeyError, TypeError, StopIteration, ValidationError) as error:
                self.reply(409, json_text({"error": str(error) or "缺少相关记录，请刷新并核对数据"}))

        def do_POST(self):
            if (self.path != "/decision" or self.headers.get("Host") != host or self.headers.get("Origin") != origin
                    or not secrets.compare_digest(self.headers.get("X-Review-Token", "").encode(), token.encode())):
                return self.reply(403, json_text({"error": "请从本机审核页面操作"}))
            if self.headers.get("Content-Type") != "application/json" or self.headers.get("Transfer-Encoding"):
                return self.reply(400, json_text({"error": "需要 JSON 请求"}))
            try:
                length = int(self.headers.get("Content-Length", "0"))
                require(0 < length <= 8192, "请求大小无效")
                request = json.loads(self.rfile.read(length))
                with locked_data():
                    result = decide(data_dir, request, baseline)
                self.reply(200, json_text(result))
            except (OSError, ValueError, KeyError, TypeError, StopIteration, ValidationError) as error:
                self.reply(409, json_text({"error": str(error) or "缺少相关记录，请刷新并核对数据"}))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    host = f"127.0.0.1:{server.server_port}"
    origin = "http://" + host
    return server


def serve(data_dir: Path, baseline: dict, port: int = 0, open_browser: bool = True) -> int:
    with make_server(data_dir, baseline, port) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"本地审核：{url}\n审核操作写入 {data_dir}；Ctrl+C 停止。", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
