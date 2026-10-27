"""Local Ask Glisper service. Streams each agent step as one JSON line while the run happens.

  python3 -m glisper_agent.server            # http://127.0.0.1:8090  (SAMPLE_MODE=true works with no keys)
  POST /ask {"question", "brand_id"?, "chat_id"?} -> NDJSON: {"type":"step",...}* then {"type":"final",...}
                                         (a chat_id continues that chat: its earlier turns go to the agent)
  POST /remember {"text", "brand_id"?}  -> stores an approved brand fact in H-MEM
  GET  /memory[?brand=<id>]             -> brand facts H-MEM holds
  GET  /chats[?brand=<id>]              -> this brand's chats, newest first: {chat_id, title, turns, updated, active}
  GET  /history[?brand=<id>&chat=<id>]  -> one chat (or all): finished runs + runs still in progress (with their steps)
  GET  /trace?run=<run_id>              -> every logged step of one run
A run keeps going if the page that asked closes; its answer lands in /history.
brand_id is a Glisper brand id (the panel in Glisper passes the signed-in workspace's brand); without it, the demo brand.
"""
import json, logging, os, re, time, traceback, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from glisper_agent import backends as B
from glisper_agent.agent import LOGS, ask, brand_key, load_brand

PAGE = Path(__file__).with_name("chat.html")
PORT = int(os.getenv("PORT", "8090"))
ACTIVE = {}  # runs in progress: token -> {"question", "brand_key", "chat_id", "steps"}. ponytail: in-process, lost on restart
CHAT_ID = re.compile(r"[0-9a-f-]{8,36}")


def finished(key):
    """This brand's finished runs, oldest first. ponytail: rereads every log file, an index when there are thousands"""
    runs = sorted((json.loads(p.read_text()) for p in LOGS.glob("*.final.json")), key=lambda r: r.get("asked_at", 0))
    return [{**r, "chat_id": r.get("chat_id") or r["run_id"]} for r in runs if r.get("brand_key") == key]  # pre-chat runs: one chat each


def history(key, chat=None, last=20):
    return {"runs": [r for r in finished(key) if chat in (None, r["chat_id"])][-last:],
            "active": [a for a in list(ACTIVE.values()) if a["brand_key"] == key and chat in (None, a["chat_id"])]}


def chats(key):
    by = {}
    for r in finished(key) + [{**a, "asked_at": time.time()} for a in list(ACTIVE.values()) if a["brand_key"] == key]:
        c = by.setdefault(r["chat_id"], {"chat_id": r["chat_id"], "title": r["question"], "turns": 0, "active": False})
        c["turns"] += 1
        c["updated"] = r.get("asked_at") or 0
        c["active"] |= "steps" in r
    return sorted(by.values(), key=lambda c: -c["updated"])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # stream until close, no chunking needed

    def _head(self, code=200, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")  # the Glisper panel calls this from its dev server
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, obj, code=200):
        self._head(code)
        self.wfile.write(json.dumps(obj, default=str).encode())

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def do_OPTIONS(self):
        self._head(204)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self._head(200, "text/html; charset=utf-8")
            self.wfile.write(PAGE.read_bytes())
        elif url.path in ("/history", "/trace", "/chats"):
            qs = parse_qs(url.query)
            try:
                if url.path == "/trace":
                    run_id = qs.get("run", [""])[0]
                    if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", run_id):
                        return self._json({"error": "bad run id"}, 400)
                    log = LOGS / f"{run_id}.jsonl"
                    return self._json({"steps": [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []})
                key = brand_key(load_brand(qs.get("brand", [None])[0]))
                self._json({"chats": chats(key)} if url.path == "/chats" else history(key, qs.get("chat", [None])[0]))
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)
        elif url.path == "/memory":
            try:
                brand = load_brand(parse_qs(url.query).get("brand", [None])[0])
                self._json({"brand": brand["name"], "domain": brand["domain"], "data_label": brand["label"],
                            "memories": B.recall(brand["name"], "brand context", 20)["memories"],
                            "sample_mode": B.SAMPLE_MODE})
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = self._body() if self.path in ("/remember", "/ask") else {}
        if self.path == "/remember":
            text = (body.get("text") or "").strip()[:300]
            if not text:
                return self._json({"error": "empty"}, 400)
            try:
                brand = load_brand(body.get("brand_id"))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json(B.remember(brand["name"], text, "user:approved-in-chat"))
        if self.path != "/ask":
            return self._json({"error": "not found"}, 404)
        q = (body.get("question") or "").strip()[:500]
        if not q:
            return self._json({"error": "empty question"}, 400)
        try:
            brand = load_brand(body.get("brand_id"))
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        key = brand_key(brand)
        chat_id = body.get("chat_id") if CHAT_ID.fullmatch(str(body.get("chat_id"))) else uuid.uuid4().hex
        earlier = history(key, chat_id, last=3)["runs"]
        self._head(200, "application/x-ndjson")
        token = uuid.uuid4().hex
        ACTIVE[token] = {"question": q, "brand_key": key, "chat_id": chat_id, "steps": []}

        def send(obj):
            try:
                self.wfile.write((json.dumps(obj, default=str) + "\n").encode())
                self.wfile.flush()
            except OSError:
                pass  # the panel was closed: keep running, the answer lands in /history

        def step(e):
            ACTIVE[token]["steps"].append(e)
            send({"type": "step", **e})

        try:
            send({"type": "final", **ask(q, emit=step, brand_id=body.get("brand_id"), chat_id=chat_id, history=earlier)})
        except Exception as e:  # noqa: BLE001  - surface any failure in the trace instead of hanging the UI
            logging.exception("run failed")
            send({"type": "error", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-800:]})
        finally:
            ACTIVE.pop(token, None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(f"Ask Glisper on http://127.0.0.1:{PORT}  (SAMPLE_MODE={B.SAMPLE_MODE}, data: "
          f"{'sqlite data/sample' if B.SAMPLE_MODE else 'Zetaris ' + B.CATALOG})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
