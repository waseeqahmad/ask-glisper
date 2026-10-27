"""The three swap points: run_sql(), chat(), remember()/recall(). One function each, one env switch each.

  run_sql   SAMPLE_MODE=false -> Zetaris: GLISPER (Glisper's ask_glisper views, scoped to one audit) or the GLISPER_DEMO
                                 source when no brand is given                    | true -> sqlite over data/sample/*.csv
  chat      SAMPLE_MODE=false -> Meterless Nightshift (NVIDIA Nemotron)                 | true -> None (agents use rule fallbacks)
  remember  always H-MEM (Meterless engine, engines/hmem), stored in data/memory/<brand>/

Credentials come only from env (./.env and zetaris-platform/.env, both gitignored).
"""
import csv, json, logging, os, re, sqlite3, subprocess, uuid, urllib.error, urllib.request
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for env_file in (ROOT / ".env", ROOT / "zetaris-platform" / ".env"):
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            k, _, v = line.partition("=")
            if k and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip())

log = logging.getLogger("ask_glisper")
SAMPLE_MODE = os.getenv("SAMPLE_MODE", "false").lower() == "true"
ZETARIS = os.getenv("ZETARIS_URL", "http://localhost:3001")
CATALOG = os.getenv("ZETARIS_CATALOG", "GLISPER_DEMO")
GLISPER_CATALOG = os.getenv("GLISPER_CATALOG", "GLISPER")
NIGHTSHIFT = os.getenv("NIGHTSHIFT_URL", "http://127.0.0.1:8088/v1")
MODEL = os.getenv("NIGHTSHIFT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
FAST_MODEL = os.getenv("NIGHTSHIFT_FAST_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
TABLES = ("answers", "answer_sources", "answer_mentions")
ROW_CAP = 200
HMEM_DIR = ROOT / "engines" / "hmem"


def _post(url, body, headers=None, timeout=60):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---------- run_sql ----------

BANNED = re.compile(r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|attach|pragma|register|"
                    r"filter)\b", re.I)


def check_sql(sql):
    """SELECT-only, one statement, only our three tables. Returns the cleaned SQL or raises ValueError."""
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("only one statement allowed")
    if not re.match(r"(?is)^(select|with)\b", s):
        raise ValueError("must start with SELECT or WITH")
    if m := BANNED.search(s):
        hint = " (Zetaris ignores FILTER; use SUM(CASE WHEN ... THEN 1 ELSE 0 END))" if m.group(1).lower() == "filter" else ""
        raise ValueError(f"keyword not allowed: {m.group(1)}{hint}")
    if "::" in s:
        raise ValueError("no :: casts; use CAST(x AS DOUBLE)")
    if re.search(r'[`"]', s) or re.search(rf"(?i)\b({CATALOG}|{GLISPER_CATALOG})\b", s):
        raise ValueError("use the bare table names only (no catalogs, no quoted identifiers)")
    used = {t.lower() for t in re.findall(r"(?i)\b(?:from|join)\s+([a-z_][\w.]*)", s)}
    ctes = {c.lower() for c in re.findall(r"(?i)\b(\w+)\s+as\s*\(", s)}
    bad = {t for t in used - ctes if t not in TABLES}
    if bad:
        raise ValueError(f"unknown table(s): {', '.join(sorted(bad))}; use only {', '.join(TABLES)}")
    return s


def _num(v):
    if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v):
        return float(v) if "." in v else int(v)
    return v


@lru_cache(maxsize=1)
def _sqlite():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    for t in TABLES:
        with open(ROOT / "data" / "sample" / f"{t}.csv") as f:
            rows = list(csv.DictReader(f))
        db.execute(f"CREATE TABLE {t} ({', '.join(rows[0])})")
        db.executemany(f"INSERT INTO {t} VALUES ({', '.join('?' * len(rows[0]))})",
                       [[_num(v) if v != "" else None for v in r.values()] for r in rows])
    return db


def _zetaris_headers():
    login = _post(f"{ZETARIS}/api/auth/login", {"username": os.getenv("ZETARIS_ADMIN_EMAIL", "admin@zetaris.com"),
                                                "password": os.environ["ZETARIS_ADMIN_PASSWORD"]}, timeout=15)
    raw = json.dumps(login)
    token = re.search(r"[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}", raw).group()
    org = re.search(r'"orgId"\s*:\s*(\d+)', raw).group(1)
    return {"Authorization": f"Bearer {token}", "X-Org-ID": org}


def scope(s, audit_id):
    """Pin every table to one audit: CTEs named like the tables shadow them, so the query can't see other brands."""
    audit_id = str(uuid.UUID(audit_id))  # raises on anything that isn't a UUID: nothing else reaches the SQL
    ctes = ", ".join(f"{t} AS (SELECT * FROM {GLISPER_CATALOG}.{t} WHERE audit_id = '{audit_id}')" for t in TABLES)
    m = re.match(r"(?i)with\s+", s)
    return f"WITH {ctes}, {s[m.end():]}" if m else f"WITH {ctes} {s}"


def _zetaris(sql, timeout=30):
    try:
        out = _post(f"{ZETARIS}/api/proxy/query/sql/start", {"select": sql, "pageLimit": ROW_CAP},
                    {**_zetaris_headers(), "X-Request-ID": str(uuid.uuid4())}, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise ValueError(f"zetaris {e.code}: {e.read()[:300].decode(errors='replace')}")
    return [{k: _num(v) for k, v in r.items()} for r in out.get("records", [])]


def run_sql(sql, audit_id=None, timeout=30):
    """Validated SELECT -> {"rows": [...], "backend": "zetaris"|"sqlite", "sql": <sql as run>}.
    With audit_id the query runs on Glisper's views, limited to that audit; without it, on the demo source."""
    s = check_sql(sql)
    if not re.search(r"(?i)\blimit\s+\d+\s*$", s):
        s += f" LIMIT {ROW_CAP}"
    if SAMPLE_MODE:
        cur = _sqlite().execute(s)
        cols = [d[0] for d in cur.description]
        return {"rows": [dict(zip(cols, r)) for r in cur.fetchall()[:ROW_CAP]], "backend": "sqlite", "sql": s}
    z = scope(s, audit_id) if audit_id else re.sub(r"(?i)\b(from|join)\s+(" + "|".join(TABLES) + r")\b",
                                                    rf"\1 {CATALOG}.\2", s)
    return {"rows": _zetaris(z, timeout), "backend": "zetaris", "sql": z}


def brand_profile(brand_id):
    """Glisper brand -> {brand, website, audit_id, capture_methods}, read through Zetaris. None if unknown."""
    rows = _zetaris(f"SELECT brand_id, audit_id, brand, website, capture_methods FROM {GLISPER_CATALOG}.brand_profiles "
                    f"WHERE brand_id = '{uuid.UUID(brand_id)}'", timeout=30)
    return rows[0] if rows else None


# ---------- chat ----------

def chat(messages, model=None, max_tokens=4000, timeout=90):
    """Nightshift chat -> (text, model_used). Strips Nemotron <think> blocks. Returns (None, None) in SAMPLE_MODE."""
    if SAMPLE_MODE:
        return None, None
    model = model or MODEL
    auth = {"Authorization": f"Bearer {os.getenv('NIGHTSHIFT_API_KEY', '')}"}
    try:
        out = _post(f"{NIGHTSHIFT}/chat/completions", {"model": model, "max_tokens": max_tokens, "messages": messages},
                    auth, timeout=timeout)
    except OSError as e:  # HTTPError/URLError/timeout: the small model sometimes 502s with empty_response
        if model == MODEL:
            raise
        log.warning("nightshift %s failed (%s), falling back to %s", model, e, MODEL)
        model = MODEL
        out = _post(f"{NIGHTSHIFT}/chat/completions", {"model": model, "max_tokens": max_tokens, "messages": messages},
                    auth, timeout=timeout)
    text = out["choices"][0]["message"].get("content") or ""
    return text.split("</think>")[-1].strip(), out.get("model", model)


def chat_json(messages, model=None, **kw):
    """chat() and parse the last {...} object in the reply. Raises ValueError if there is none."""
    text, used = chat(messages, model, **kw)
    if text is None:
        return None, None
    for m in reversed(list(re.finditer(r"\{", text))):
        try:
            return json.JSONDecoder().raw_decode(text[m.start():])[0], used
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no JSON in model reply: {text[:200]!r}")


# ---------- remember / recall (H-MEM) ----------

def _hmem(*args, timeout=30):
    out = subprocess.run([str(HMEM_DIR / "node_modules" / ".bin" / "tsx"), "hmem_cli.ts", *args], cwd=HMEM_DIR,
                         capture_output=True, text=True, timeout=timeout, env={**os.environ, "NODE_NO_WARNINGS": "1"})
    if out.returncode:
        raise RuntimeError(f"hmem: {out.stderr.strip()[:300]}")
    return json.loads(out.stdout)


def memory_dir(brand):
    return str(Path(os.getenv("MEMORY_DIR", ROOT / "data" / "memory")) / re.sub(r"\W+", "_", brand.lower()))


def remember(brand, text, source="user:approved"):
    return _hmem("remember", memory_dir(brand), text, source)


def recall(brand, query, top_n=5):
    return _hmem("recall", memory_dir(brand), query, str(top_n))
