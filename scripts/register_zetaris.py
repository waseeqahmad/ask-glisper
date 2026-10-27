"""Register the demo audit (Postgres) in Zetaris as data source GLISPER_DEMO. Idempotent.

  python3 scripts/register_zetaris.py                          # demo audit -> GLISPER_DEMO
  python3 scripts/register_zetaris.py GLISPER_DB_URL GLISPER   # Glisper's ask_glisper views (scripts/glisper_views.sql)
Needs GLISPER_DEMO_DB_URL (postgresql://user:pass@localhost:5433/ask_glisper_demo) in .env and ZETARIS_ADMIN_PASSWORD
in zetaris-platform/.env. Zetaris runs in Docker, so localhost becomes host.docker.internal. Load the data first:
  python3 scripts/make_demo_data.py --load "$GLISPER_DEMO_DB_URL"
"""
import os, sys, urllib.error, urllib.parse, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from glisper_agent import backends as B  # noqa: E402  (loads .env)

SQL_FILE = Path(__file__).with_name("register_zetaris_sources.sql")


def run(sql):
    try:
        out = B._post(f"{B.ZETARIS}/api/proxy/sql-editor/sqls/run-query", {"sql": sql, "pageLimit": 50},
                      {**B._zetaris_headers(), "X-Request-ID": str(uuid.uuid4())}, timeout=120)
        print("ok  ", sql.split("(")[0].strip()[:70], out.get("data", "")[:3] if isinstance(out.get("data"), list) else "")
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        print("fail", sql.split("(")[0].strip()[:70], e.code, body)
        if "exist" not in body.lower():
            raise


if __name__ == "__main__":
    url_env, catalog = sys.argv[1:3] if len(sys.argv) > 2 else ("GLISPER_DEMO_DB_URL", B.CATALOG)
    u = urllib.parse.urlparse(os.environ[url_env])
    host = "host.docker.internal" if u.hostname in ("localhost", "127.0.0.1") else u.hostname
    extra = ', schema "ask_glisper"' if catalog != B.CATALOG else ""  # only the views, never Glisper's own tables
    sql = SQL_FILE.read_text().format(catalog=catalog, jdbcurl=f"jdbc:postgresql://{host}:{u.port or 5432}{u.path}", extra=extra,
                                      user=urllib.parse.unquote(u.username), password=urllib.parse.unquote(u.password))
    sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        run(stmt)
    if catalog == B.CATALOG:
        print(B.run_sql("SELECT engine, COUNT(*) AS answers FROM answers GROUP BY engine")["rows"])
