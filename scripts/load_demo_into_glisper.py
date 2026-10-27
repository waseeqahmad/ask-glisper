"""Load the synthetic Lahore Loom audit (data/sample/*.csv) into a local Glisper database as a workspace, so Glisper's
own pages and Ask Glisper show the same brand. Local dev only; every row is capture_method='synthetic'. Idempotent:
re-running deletes and reloads the demo audit.

  GLISPER_ADMIN_DB_URL=postgresql://... python3 scripts/load_demo_into_glisper.py you@example.com

The workspace is added to the company of the user with that email, who becomes its owner.
Glisper's statuses differ from the demo CSVs: won = named in the answer, so the CSVs' 'partial' (named, not first)
becomes won at its position, and 'lost' stays lost.
"""
import csv, json, os, subprocess, sys, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = uuid.UUID("6f1c2b8e-3a5d-4e7f-9b1a-2c3d4e5f6a7b")
BRAND, DOMAIN = "Lahore Loom", "lahoreloom.pk"
# demo source types -> Glisper's source types
TYPES = {"own_site": "owned", "competitor_site": "other", "listicle": "editorial", "news": "editorial",
         "forum": "community", "social": "community", "video": "community", "marketplace": "directory"}


def uid(*key):
    return str(uuid.uuid5(NS, "/".join(map(str, key))))


def q(v):
    return "NULL" if v is None or v == "" else "'" + str(v).replace("'", "''") + "'"


def rows(name):
    with open(ROOT / "data" / "sample" / f"{name}.csv") as f:
        return list(csv.DictReader(f))


def build_sql(email):
    answers, sources, mentions = rows("answers"), rows("answer_sources"), rows("answer_mentions")
    audit, ws, brand = uid("audit"), uid("workspace"), uid("brand")
    rivals = sorted({m["brand"] for m in mentions if m["is_us"] not in ("1", "true", "True")})
    domains = {s["domain"]: s["source_type"] for s in sources}
    site = {r: next((d for d, t in domains.items() if t == "competitor_site" and
                     d.split(".")[0] == r.lower().replace(" ", "")), None) for r in rivals}
    user = f"(SELECT id FROM users WHERE email = {q(email)})"
    out = [
        "BEGIN;",
        # clean re-load: children first
        f"DELETE FROM prompt_result_sources WHERE result_id IN (SELECT pr.id FROM prompt_results pr JOIN runs r "
        f"ON r.id = pr.run_id WHERE r.audit_id = '{audit}');",
        f"DELETE FROM prompt_result_mentions WHERE result_id IN (SELECT pr.id FROM prompt_results pr JOIN runs r "
        f"ON r.id = pr.run_id WHERE r.audit_id = '{audit}');",
        f"DELETE FROM prompt_results WHERE run_id IN (SELECT id FROM runs WHERE audit_id = '{audit}');",
        f"DELETE FROM prompt_engines WHERE prompt_id IN (SELECT id FROM prompts WHERE audit_id = '{audit}');",
        *(f"DELETE FROM {t} WHERE audit_id = '{audit}';" for t in ("prompts", "runs", "sources", "competitors", "brands")),
        f"DELETE FROM audits WHERE id = '{audit}';",
        f"DELETE FROM workspace_members WHERE workspace_id = '{ws}';",
        f"DELETE FROM workspaces WHERE id = '{ws}';",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM users WHERE email = {q(email)}) THEN "
        f"RAISE EXCEPTION 'no Glisper user %', {q(email)}; END IF; END $$;",
        f"INSERT INTO workspaces (id, name, prompt_limit, company_id, created_at) VALUES ('{ws}', {q(BRAND)}, 50, "
        f"(SELECT company_id FROM company_members WHERE user_id = {user} LIMIT 1), now());",
        f"INSERT INTO workspace_members (workspace_id, user_id, role) VALUES ('{ws}', {user}, 'owner');",
        f"INSERT INTO audits (id, public_token, domain, status, prompt_cap, converted_workspace_id, created_at, "
        f"market_country_code) VALUES ('{audit}', 'demo-lahore-loom', {q(DOMAIN)}, 'done', 50, '{ws}', now(), 'PK');",
        f"INSERT INTO brands (id, audit_id, workspace_id, name, website, industry, city, country_code, services, aliases) "
        f"VALUES ('{brand}', '{audit}', '{ws}', {q(BRAND)}, 'https://{DOMAIN}', 'Fashion & apparel', 'Lahore', 'PK', "
        f"'[\"eastern wear\", \"stitched suits\", \"kurtas\"]', '[]');",
    ]
    out += [f"INSERT INTO competitors (id, audit_id, name, website, aliases, you, origin) VALUES ('{uid('comp', r)}', "
            f"'{audit}', {q(r)}, {q(site[r] and 'https://' + site[r])}, '[]', false, 'research');" for r in rivals]
    out += [f"INSERT INTO sources (id, audit_id, domain, type, first_seen_at) VALUES ('{uid('src', d)}', '{audit}', "
            f"{q(d)}, {q(TYPES.get(t, 'other'))}, now());" for d, t in domains.items()]
    prompts = {a["prompt_id"]: a["prompt"] for a in answers}
    engines = sorted({a["engine"] for a in answers})
    for pid, text in prompts.items():
        out.append(f"INSERT INTO prompts (id, audit_id, brand_id, text, intent, created_by, active) VALUES "
                   f"('{uid('prompt', pid)}', '{audit}', '{brand}', {q(text)}, 'discovery', 'system', true);")
        out += [f"INSERT INTO prompt_engines (prompt_id, engine_slug) VALUES ('{uid('prompt', pid)}', {q(e)});"
                for e in engines]
    for d in sorted({a["run_date"] for a in answers}):
        out.append(f"INSERT INTO runs (id, audit_id, status, trigger, started_at, finished_at) VALUES "
                   f"('{uid('run', d)}', '{audit}', 'done', 'schedule', '{d} 06:00+00', '{d} 06:20+00');")
    for a in answers:
        named = a["status"] in ("won", "partial")
        winner = None if a["status"] == "won" else a["winner"]
        out.append(
            f"INSERT INTO prompt_results (id, run_id, prompt_id, engine_slug, raw_answer, capture_method, vendor, "
            f"fetched_at, raw, mention, status, position, winning_competitor_id) VALUES ('{a['result_id']}', "
            f"'{uid('run', a['run_date'])}', '{uid('prompt', a['prompt_id'])}', {q(a['engine'])}, {q(a['answer_text'])}, "
            f"'synthetic', 'synthetic', '{a['run_date']} 06:10+00', '{{}}', {q('named' if named else 'missing')}, "
            f"{q('won' if named else 'lost')}, {a['our_position'] if named and a['our_position'] else 'NULL'}, "
            f"{q(winner and uid('comp', winner))});")
    out += [f"INSERT INTO prompt_result_mentions (id, result_id, competitor_id, position) VALUES "
            f"('{uid('mention', m['result_id'], m['brand'])}', '{m['result_id']}', '{uid('comp', m['brand'])}', "
            f"{m['position']});" for m in mentions if m["brand"] in rivals]
    out += [f"INSERT INTO prompt_result_sources (id, result_id, source_id, url) VALUES ('{uid('cite', s['result_id'], s['url'])}', "
            f"'{s['result_id']}', '{uid('src', s['domain'])}', {q(s['url'])});" for s in sources]
    out.append("COMMIT;")
    return "\n".join(out), {"workspace": ws, "audit": audit, "brand": brand}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sql, ids = build_sql(sys.argv[1])
    subprocess.run(["psql", os.environ["GLISPER_ADMIN_DB_URL"], "-q", "-v", "ON_ERROR_STOP=1"], input=sql, text=True,
                   check=True)
    print(json.dumps(ids))
