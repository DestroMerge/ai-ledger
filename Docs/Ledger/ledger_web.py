#!/usr/bin/env python3
"""Local web view of the ledger. It never writes the ledger.

    python Docs/Ledger/ledger_web.py                    serve http://127.0.0.1:8717 and open the browser
    python Docs/Ledger/ledger_web.py --port N --no-browser

It reads the ledger files (meta.json and epics/*.json) through ledger.py, so "ready", "current wave" and the rule problems the page shows
are the tool's own answers and not a second copy of them. It never writes the ledger: ledger.py stays the
only tool that changes state or commits. Where the page offers a state change, it offers the command to copy.

The one thing it writes: the owner's answer to an open decision. The Decisions page shows each open
decision's options with a button apiece; the answer goes to the inbox file ledger.py names (ANSWERS_JSON, under
Saved/, unversioned), not into the ledger. `ledger.py next` lists the answers waiting, and `ledger.py apply
<decision>` makes one the ruling - so a session still does the state change, with the tool.

The page polls /api/stamp and reloads itself when a ledger file changes on disk, so it follows a working
session without a refresh. Commit history comes from one log call through ledger.py's version control backend,
cached for COMMITS_MAX_AGE seconds.

The project's id prefix, owner, version control and Unreal setting come from ledger_config.json
through ledger.py; the page gets them as derived.profile.

Standard library only. It listens on 127.0.0.1. It answers GET, and one POST: /api/answer.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ledger as ledger_tool  # noqa: E402  (ledger.py, beside this file)

WEB = HERE / "web"
DEFAULT_PORT = 8717
COMMITS_MAX_AGE = 60
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


# --------------------------------------------------------------------------- ledger state

def ledger_files() -> list[Path]:
    """The files the ledger is kept in (one per epic, plus meta.json)."""
    return [ledger_tool.META_JSON] + sorted(ledger_tool.EPICS_DIR.glob(ledger_tool.EPIC_GLOB))


def profile() -> dict:
    """What the page needs from the project config: the id prefix (to find and shorten ids in text), the owner (whose
    column and answers these are), the task field saying what the owner does, the commit id's form, and the version
    control's command names."""
    vcs = ledger_tool.VCS
    return {"idPrefix": ledger_tool.PREFIX, "owner": ledger_tool.OWNER, "needsField": ledger_tool.NEEDS_OWNER,
            "unreal": ledger_tool.UNREAL, "vcs": vcs.name, "vcsCmd": vcs.cmd, "commitId": vcs.RE_ID.pattern,
            "showCmd": vcs.show_cmd}


def saved_at() -> float:
    """When any ledger file was last written."""
    return max((f.stat().st_mtime for f in ledger_files() if f.exists()), default=0.0)


def stamp() -> str:
    """Changes whenever a ledger file is rewritten. The page polls this instead of the whole ledger."""
    try:
        stats = [f.stat() for f in ledger_files()]
    except OSError:
        return "missing"
    files = f"{len(stats)}-{max(st.st_mtime_ns for st in stats)}-{sum(st.st_size for st in stats)}"
    try:
        inbox = ledger_tool.ANSWERS_JSON.stat()
        answers = f"{inbox.st_mtime_ns}-{inbox.st_size}"
    except OSError:
        answers = "none"
    return f"{files}-{answers}"


def build_state() -> dict:
    seen = stamp()
    led = ledger_tool.load(track=False)
    derived: dict = {"stamp": seen, "savedAt": saved_at(), "profile": profile()}
    # validate() indexes straight into the ledger, so a structurally broken file can raise instead of
    # returning errors. The page still has to load to show that, so every derived answer is guarded.
    try:
        derived["problems"] = ledger_tool.validate(led)
    except Exception as e:
        derived["problems"] = [f"validate could not run: {type(e).__name__}: {e}"]
    try:
        derived["ready"] = [t["id"] for t in ledger_tool.ready_tasks(led)]
        derived["currentWave"] = ledger_tool.current_wave(led)
        derived["renderStale"] = ledger_tool.render_check(led)
    except Exception as e:
        derived.setdefault("ready", [])
        derived.setdefault("currentWave", None)
        derived.setdefault("renderStale", [])
        derived["problems"].append(f"derived state could not be computed: {type(e).__name__}: {e}")
    derived["images"] = sorted(p.name for p in HERE.iterdir() if p.suffix.lower() in IMAGE_TYPES)
    derived["plans"] = {p: (ledger_tool.REPO / p).is_file() for p in allowed_plans(led)}
    try:
        answers = ledger_tool.load_answers()
    except ledger_tool.LedgerError as e:  # an unreadable inbox is shown, not read as empty
        answers = {}
        derived["problems"].append(str(e))
    return {"ledger": led, "derived": derived, "answers": answers}


# --------------------------------------------------------------------------- answers

MAX_BODY = 16384
MAX_TEXT = 4000
_answers_lock = threading.Lock()


def record_answer(body: dict) -> tuple[int, dict]:
    """Put the owner's answer to an open decision in the inbox, or take it back out ({"withdraw": true}).

    The same test `ledger.py apply` runs decides whether the answer can be taken, so the page cannot file
    something the tool will refuse later."""
    ident = str(body.get("id", ""))
    # The inbox's other writer is `ledger.py apply`, in another process, so the
    # page takes the same ledger lock for its read-modify-write, not only a lock between its own threads.
    with _answers_lock, ledger_tool.ledger_lock():
        answers = ledger_tool.load_answers()
        if body.get("withdraw"):
            answers.pop(ident, None)
        else:
            choice = str(body.get("choice", "")).strip()
            text = str(body.get("text", "")).strip()[:MAX_TEXT]
            note = str(body.get("note", "")).strip()[:MAX_TEXT]
            d = ledger_tool.index(ledger_tool.load(track=False))[2].get(ident)
            errs = ledger_tool.answer_errors(d, choice, text)
            if errs:
                return 400, {"error": "; ".join(errs)}
            answers[ident] = {"choice": choice, "text": text if choice == ledger_tool.OTHER else "",
                              "note": note, "answeredAt": time.strftime("%Y-%m-%d %H:%M")}
        ledger_tool.save_answers(answers)
    return 200, {"ok": True, "answers": answers}


def allowed_plans(led: dict) -> set[str]:
    return {ledger_tool.norm(e["plan"]) for e in led.get("epics", []) if e.get("plan")}


# --------------------------------------------------------------------------- commits

def read_commits() -> dict:
    """Every commit after the ledger's auditFrom, newest first, with the task or role it names.

    One page of the log, read and parsed by ledger.py's version control backend; the id and role rules are
    ledger.py's own too (subject_id, role_lines)."""
    led = ledger_tool.load(track=False)
    mark = led["auditFrom"]
    commits = ledger_tool.VCS.parse_log(ledger_tool.VCS.log_page(None))
    ids = [c["id"] for c in commits]
    reaches = mark in ids
    window = commits[: ids.index(mark)] if reaches else commits
    recorded = {o["id"]: o.get("reason", "") for o in led.get("outsideCommits", [])}
    out = []
    for c in window:
        message, subject = c["message"], c["subject"]
        out.append({
            "id": c["id"], "date": c.get("date", ""), "subject": subject, "message": message,
            "names": ledger_tool.subject_id(subject),
            "roles": ledger_tool.role_lines(message),
            "outsideReason": recorded.get(c["id"]),
        })
    return {"auditFrom": mark, "reachesMark": reaches, "head": ids[0] if ids else None,
            "readAt": time.time(), "commits": out}


_commits_lock = threading.Lock()
_commits_cache: dict = {"at": 0.0, "data": None}


def commits(force: bool) -> dict:
    with _commits_lock:
        stale = time.time() - _commits_cache["at"] > COMMITS_MAX_AGE
        if force or stale or _commits_cache["data"] is None:
            try:
                data = read_commits()
            except Exception as e:  # version control missing, not signed in, ledger mid-write: the page says so
                data = {"error": f"{type(e).__name__}: {e}", "commits": [], "readAt": time.time()}
            _commits_cache.update(at=time.time(), data=data)
        return _commits_cache["data"]


# --------------------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "LedgerWeb/1"

    def log_message(self, fmt: str, *args) -> None:
        pass  # the page polls every two seconds; a line per request would bury the console

    def send_bytes(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code: int = 200) -> None:
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    def host_ok(self) -> bool:
        # A page on another site can point its own hostname at 127.0.0.1 and read this server as if it
        # were same-origin. Answering only to a loopback Host header closes that.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def do_GET(self) -> None:
        if not self.host_ok():
            self.send_json({"error": "this server answers only on 127.0.0.1"}, 403)
            return
        url = urlparse(self.path)
        path = unquote(url.path)
        query = parse_qs(url.query)
        try:
            if path in STATIC:
                name, ctype = STATIC[path]
                self.send_bytes(200, (WEB / name).read_bytes(), ctype)
            elif path == "/api/stamp":
                self.send_json({"stamp": stamp()})
            elif path == "/api/state":
                self.send_json(build_state())
            elif path == "/api/commits":
                self.send_json(commits(force=query.get("refresh") == ["1"]))
            elif path.startswith("/image/"):
                self.send_image(path[len("/image/"):])
            elif path == "/plan":
                self.send_plan((query.get("path") or [""])[0])
            else:
                self.send_json({"error": f"nothing at {path}"}, 404)
        except (json.JSONDecodeError, OSError) as e:
            # the ledger files are rewritten by other sessions; a read can land between two of them. The page
            # keeps what it has and asks again.
            self.send_json({"error": f"could not read the ledger just now: {type(e).__name__}: {e}"}, 503)
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self) -> None:
        if not self.host_ok():
            self.send_json({"error": "this server answers only on 127.0.0.1"}, 403)
            return
        # A page on another site can post a form at 127.0.0.1 without reading the reply. It cannot send a JSON
        # content type or a header of its own without the browser asking this server first, and this server never
        # says yes; and a browser names the posting site in Origin.
        origin = self.headers.get("Origin")
        if origin is not None and (urlparse(origin).hostname or "").lower() not in ("127.0.0.1", "localhost", "::1"):
            self.send_json({"error": "answers are taken from this page only"}, 403)
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if self.headers.get("X-Ledger-Answer") != "1" or ctype != "application/json":
            self.send_json({"error": "answers are taken from this page only"}, 400)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BODY:
            self.send_json({"error": "no answer in the request, or one too long"}, 400)
            return
        try:
            if urlparse(self.path).path != "/api/answer":
                self.send_json({"error": f"nothing at {self.path}"}, 404)
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("the answer must be a JSON object")
            code, reply = record_answer(body)
            self.send_json(reply, code)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            self.send_json({"error": f"could not read the answer: {e}"}, 400)
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def send_image(self, name: str) -> None:
        """Evidence screenshots that sit beside the ledger. A bare file name only, images only."""
        target = HERE / name
        if Path(name).name != name or target.suffix.lower() not in IMAGE_TYPES or not target.is_file():
            self.send_json({"error": "no such image"}, 404)
            return
        self.send_bytes(200, target.read_bytes(), IMAGE_TYPES[target.suffix.lower()])

    def send_plan(self, rel: str) -> None:
        """The plan document an epic names, as plain text. Only paths the ledger itself lists."""
        rel = ledger_tool.norm(rel)
        if rel not in allowed_plans(ledger_tool.load(track=False)):
            self.send_json({"error": "not a plan named by an epic"}, 404)
            return
        target = (ledger_tool.REPO / rel).resolve()
        if ledger_tool.REPO not in target.parents or not target.is_file():
            self.send_json({"error": "no such file"}, 404)
            return
        self.send_bytes(200, target.read_bytes(), "text/plain; charset=utf-8")


class Server(ThreadingHTTPServer):
    # On Windows the default (True) lets a second copy bind the same port and silently split requests
    # with the first. False makes the second copy fail with "port in use", which is the honest answer.
    allow_reuse_address = False
    daemon_threads = True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Local web view of the ledger")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="serve without opening a browser tab")
    a = ap.parse_args(argv)
    try:
        httpd = Server(("127.0.0.1", a.port), Handler)
    except OSError as e:
        print(f"could not listen on 127.0.0.1:{a.port}: {e}", file=sys.stderr)
        print("another copy may already be running; open the address below, or pass --port", file=sys.stderr)
        print(f"  http://127.0.0.1:{a.port}/", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Ledger view: {url}")
    print("It never writes the ledger; decision answers go to an inbox ledger.py reads. Ctrl+C stops it.")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
