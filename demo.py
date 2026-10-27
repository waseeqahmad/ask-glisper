"""Minimal smoke demo: SQL through Zetaris, then one model call through Nightshift.

Run: python3 demo.py ["SELECT ..."]
Env: NIGHTSHIFT_API_KEY (Nightshift > Console > API key). Optional: ZETARIS_URL, ZETARIS_ADMIN_EMAIL, ZETARIS_ADMIN_PASSWORD, NIGHTSHIFT_URL, NIGHTSHIFT_MODEL
Reads ./.env then zetaris-platform/.env (both gitignored).
"""
import json, os, re, sys, uuid, urllib.request
from pathlib import Path

for env_file in (Path(__file__).parent / ".env", Path(__file__).parent / "zetaris-platform" / ".env"):
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            k, _, v = line.partition("=")
            if k and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip())

ZETARIS = os.getenv("ZETARIS_URL", "http://localhost:3001")
NIGHTSHIFT = os.getenv("NIGHTSHIFT_URL", "http://127.0.0.1:8088/v1")
NS_AUTH = {"Authorization": f"Bearer {os.getenv('NIGHTSHIFT_API_KEY', '')}"}


def post(url, body, headers=None, timeout=60):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def zetaris_query(sql, limit=10):
    login = post(f"{ZETARIS}/api/auth/login", {
        "username": os.getenv("ZETARIS_ADMIN_EMAIL", "admin@zetaris.com"),
        "password": os.environ["ZETARIS_ADMIN_PASSWORD"],
    })
    raw = json.dumps(login)
    token = re.search(r"[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}", raw).group()
    org = re.search(r'"orgId"\s*:\s*(\d+)', raw).group(1)
    return post(f"{ZETARIS}/api/proxy/query/sql/start", {"select": sql, "pageLimit": limit}, {
        "Authorization": f"Bearer {token}", "X-Org-ID": org, "X-Request-ID": str(uuid.uuid4()),
    })


def nightshift_chat(prompt):
    with urllib.request.urlopen(urllib.request.Request(f"{NIGHTSHIFT}/models", headers=NS_AUTH), timeout=10) as r:
        models = [m["id"] for m in json.loads(r.read()).get("data", [])]
    # Prefer Nemotron via the NVIDIA provider (DGX award), then a free OpenRouter one (paid lanes need credits).
    chat = [m for m in models if "nemotron" in m and "embed" not in m]
    pick = next((m for m in chat if m.startswith("nvidia/")), None) or next((m for m in chat if m.endswith(":free")), None)
    model = os.getenv("NIGHTSHIFT_MODEL") or pick or models[0]
    out = post(f"{NIGHTSHIFT}/chat/completions",
               {"model": model, "messages": [{"role": "user", "content": prompt}]}, NS_AUTH, timeout=120)
    return model, out["choices"][0]["message"]["content"]


if __name__ == "__main__":
    sql = sys.argv[1] if len(sys.argv) > 1 else "SELECT * FROM NY_TAXI.customers LIMIT 5"
    print(f"[zetaris] {sql}")
    rows = zetaris_query(sql)
    print(json.dumps(rows, indent=2)[:1500])

    try:
        model, answer = nightshift_chat(f"In 2 sentences, describe this data:\n{json.dumps(rows)[:4000]}")
        print(f"\n[nightshift model={model}]\n{answer}")
    except OSError as e:
        print(f"\n[nightshift] not reachable at {NIGHTSHIFT}: {e}  -> is the app open and NIGHTSHIFT_API_KEY set?")
