"""Ask Glisper: Planner -> Analyst <-> Evaluator -> Reporter, over Zetaris SQL, Nightshift models, H-MEM memory.

  python3 -m glisper_agent.agent "Why are we losing on Perplexity?"
  python3 -m glisper_agent.agent --brand <glisper brand id> "Why are we losing on Perplexity?"
  python3 -m glisper_agent.agent --remember "Ignore Kurta Co: they only ship in Karachi"
Every step goes to the on-screen trace and to logs/<run_id>.jsonl (step, agent, decision, result, next, model).
"""
import json, os, re, sys, time, uuid
from pathlib import Path
from urllib.parse import urlparse

from glisper_agent import backends as B

ENGINES = ("chatgpt", "perplexity", "gemini", "claude", "google_ai_overview")
SIMULATED = "Simulated data (synthetic demo audit, not real engine answers)"
# the standalone demo audit (GLISPER_DEMO / SAMPLE_MODE): won = ranked first
DEMO_BRAND = {"name": os.getenv("BRAND_NAME", "Lahore Loom"), "domain": os.getenv("BRAND_DOMAIN", "lahoreloom.pk"),
              "audit_id": None, "label": SIMULATED, "won": "ranked first", "partial": "mentioned but not first"}
MAX_TOOL_CALLS, MAX_DRAFTS, PASS_SCORE, RUN_TIMEOUT_S = 6, 3, 7, 420  # Ultra writes SQL in ~50 s
LOGS = Path(os.getenv("LOGS_DIR", B.ROOT / "logs"))

def load_brand(brand_id=None):
    """A Glisper brand (by id, read through Zetaris) or the demo brand. Raises ValueError for an unknown id."""
    if not brand_id or B.SAMPLE_MODE:
        return DEMO_BRAND
    p = B.brand_profile(brand_id)
    if not p or not p.get("audit_id"):
        raise ValueError(f"no Glisper brand with an audit for id {brand_id}")
    methods = set(filter(None, str(p.get("capture_methods") or "").split(",")))
    label = (SIMULATED if "synthetic" in methods else
             "Includes dev-mode answers (cheap models, simulated sources), not only real engine captures"
             if any(m.startswith("dev") for m in methods) else "" if methods else "No answers captured yet")
    host = urlparse(p["website"] if "//" in str(p["website"]) else f"//{p['website']}").hostname or ""
    return {"name": p["brand"], "domain": host.removeprefix("www."), "audit_id": p["audit_id"], "label": label,
            "won": "named in the answer", "partial": "only our site cited, brand not named"}


def brand_key(brand):
    """Which chat thread a run belongs to: the Glisper audit, or the demo brand."""
    return brand["audit_id"] or "demo"


def schema(brand):
    return f"""Tables (one audit of the brand "{brand['name']}", site {brand['domain']}):
answers(result_id, run_date, prompt_id, prompt, engine, status, our_position, winner, answer_text, capture_method)
  - one row per AI-engine answer to a tracked prompt. engine: {', '.join(ENGINES)}
  - status: 'won' = {brand['name']} {brand['won']}, 'partial' = {brand['partial']}, 'lost' = not mentioned
  - our_position = {brand['name']}'s rank in the answer (NULL if not named); winner = brand ranked first
answer_sources(result_id, run_date, engine, status, domain, source_type, url)  -- one row per URL the answer cited
answer_mentions(result_id, run_date, engine, status, brand, is_us, position)   -- one row per brand named in the answer
SQL rules: plain ANSI SQL, one SELECT. No FILTER, no ::, no ILIKE. Count with SUM(CASE WHEN ... THEN 1 ELSE 0 END).
Percentages: ROUND(100.0 * part / total, 1). Use the bare table names above.
answer_sources has one row per URL: count answers with COUNT(DISTINCT result_id), never COUNT(*)."""

SKILLS = {
    "competitor_gap": "Who is ranked first instead of us, how often, on the engine in question (include our own row "
                      "so the share is comparable). Percentages are of ALL answers on that engine; excluded brands are "
                      "dropped from the list, not from the total.",
    "source_gap": "Which cited domains show up in answers we do NOT win but rarely in answers we win. For each domain "
                  "give BOTH the % of not-won answers citing it AND the % of won answers citing it.",
}


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def template_sql(skill, engine, exclude):
    """Fallback SQL when the model's SQL fails twice (and the SAMPLE_MODE path). Still real queries over real rows."""
    eng = f"engine = {_q(engine)}" if engine else "1=1"
    if skill == "competitor_gap":
        ex = f" AND winner NOT IN ({', '.join(map(_q, exclude))})" if exclude else ""
        return (f"SELECT winner, COUNT(*) AS answers_ranked_first, ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM answers "
                f"WHERE {eng}), 1) AS pct_of_answers FROM answers WHERE {eng}{ex} GROUP BY winner "
                f"ORDER BY answers_ranked_first DESC")
    nw = f"(SELECT COUNT(*) FROM answers WHERE {eng} AND status <> 'won')"
    w = f"(SELECT COUNT(*) FROM answers WHERE {eng} AND status = 'won')"
    lost_ids, won_ids = "CASE WHEN status <> 'won' THEN result_id END", "CASE WHEN status = 'won' THEN result_id END"
    return (f"SELECT domain, source_type, ROUND(100.0 * COUNT(DISTINCT {lost_ids}) / {nw}, 1) AS pct_of_not_won_citing, "
            f"ROUND(100.0 * COUNT(DISTINCT {won_ids}) / {w}, 1) AS pct_of_won_citing, COUNT(DISTINCT {lost_ids}) AS "
            f"not_won_answers_citing FROM answer_sources WHERE {eng} GROUP BY domain, source_type "
            f"ORDER BY not_won_answers_citing DESC LIMIT 10")


def earlier(history):
    """The chat so far, for follow-ups ("what about Gemini?", "write that email")."""
    return "\n".join(f"- Q: {h['question']}\n  A: {h.get('answer')} Action: {h.get('action')}" for h in history) or "- (none)"


class Run:
    def __init__(self, question, emit=None, brand=None, history=()):
        self.brand, self.history = brand or DEMO_BRAND, list(history)
        self.id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.question, self.emit, self.t0 = question, emit or (lambda e: None), time.time()
        self.tool_calls, self.models = 0, set()
        LOGS.mkdir(exist_ok=True)
        self.log_path = LOGS / f"{self.id}.jsonl"

    def step(self, agent, decision, result=None, next_=None, model=None, **extra):
        if model:
            self.models.add(model)
        e = {"run_id": self.id, "t": round(time.time() - self.t0, 2), "agent": agent, "decision": decision,
             "result": result, "next": next_, "model": model or "code", **extra}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(e, default=str) + "\n")
        B.log.info("%s | %s | %s", agent, decision, model or "code")
        self.emit(e)
        if time.time() - self.t0 > RUN_TIMEOUT_S:
            raise TimeoutError(f"run exceeded {RUN_TIMEOUT_S}s")

    def sql(self, sql, why):
        if self.tool_calls >= MAX_TOOL_CALLS:
            raise RuntimeError(f"tool-call cap ({MAX_TOOL_CALLS}) reached")
        self.tool_calls += 1
        out = B.run_sql(sql, self.brand["audit_id"])
        self.step("Analyst", f"run_sql via {out['backend']}: {why}", f"{len(out['rows'])} rows", sql=out["sql"],
                  rows=out["rows"][:10])
        return out


# ---------- agents ----------

def planner(run, memories):
    mem = "\n".join(f"- {m['content']}" for m in memories) or "- (none)"
    try:
        plan, model = B.chat_json([
            {"role": "system", "content": "You are the Planner of Ask Glisper, an analyst for AI-search visibility. "
             "Reply with ONLY one JSON object."},
            {"role": "user", "content": f"Earlier in this chat:\n{earlier(run.history)}\n"
             f"Question: {run.question}\nBrand memory:\n{mem}\nSkills:\n" +
             "\n".join(f"- {k}: {v}" for k, v in SKILLS.items()) +
             f'\nReturn {{"skills": [1-2 skill names, in order, or [] when the question is a follow-up that the '
             'previous answer\'s data already answers (rephrase it, draft the outreach, explain a number)], '
             f'"engine": one of {list(ENGINES)} or null (a follow-up keeps the earlier engine unless it names another), '
             '"exclude_brands": [brands the memory says to ignore], "remember": a NEW lasting fact about the brand stated '
             'in the question, or null, "reason": "one sentence"}'}], model=B.FAST_MODEL, max_tokens=1500)
    except (OSError, ValueError) as e:
        plan, model = None, None
        run.step("Planner", "model unavailable, using rule routing", str(e)[:200])
    if not plan:  # SAMPLE_MODE / model failure: keyword routing
        q = run.question.lower()
        plan = {"skills": ["competitor_gap", "source_gap"],  # an action needs the source gap
                "engine": next((e for e in ENGINES if e in q), run.history[-1].get("engine") if run.history else None),
                "exclude_brands": [m.group(1) for x in memories if (m := re.match(r"(?i)ignore ([^:,.]+)", x["content"]))],
                "remember": None, "reason": "keyword routing"}
    reuse = plan.get("skills") == [] and bool(run.history and run.history[-1].get("evidence"))
    plan["skills"] = [] if reuse else [s for s in plan.get("skills", []) if s in SKILLS][:2] or ["competitor_gap", "source_gap"]
    plan["engine"] = plan.get("engine") if plan.get("engine") in ENGINES else None
    plan["exclude_brands"] = [b for b in plan.get("exclude_brands") or [] if isinstance(b, str)]
    run.step("Planner", "reuse the previous answer's evidence" if reuse else f"route to {' + '.join(plan['skills'])}" +
             (f" on {plan['engine']}" if plan["engine"] else ""), plan, "Reporter" if reuse else "Analyst", model)
    return plan


def analyst_query(run, skill, plan, critique=None):
    """Model writes the SQL; bad SQL goes back to the model with the error (once); then the template fallback."""
    err = critique
    for attempt in range(2):
        try:
            ask = (f"{schema(run.brand)}\n\nTask ({skill}): {SKILLS[skill]}\nEngine: {plan['engine'] or 'all engines, grouped by engine'}"
                   f"\nExclude brands: {plan['exclude_brands'] or 'none'}\nQuestion: {run.question}")
            if err:
                ask += f"\n\nYour previous attempt was rejected: {err}\nFix it."
            got, model = B.chat_json([{"role": "system", "content": "You are the Analyst. You write one SQL query. "
                                       'Reply with ONLY {"sql": "..."}'}, {"role": "user", "content": ask}])
            if got is None:
                break  # SAMPLE_MODE
            run.step("Analyst", f"wrote SQL for {skill} (attempt {attempt + 1})", None, "run_sql", model, sql=got.get("sql"))
            out = run.sql(got.get("sql", ""), skill)
            if problem := check_findings(skill, out["rows"]):
                raise ValueError(problem)
            return {"skill": skill, **out}
        except (ValueError, KeyError, OSError) as e:
            err = str(e)[:300]
            run.step("Evaluator", f"REJECT {skill} query", err, "Analyst retry" if attempt == 0 else "template fallback")
    out = run.sql(template_sql(skill, plan["engine"], plan["exclude_brands"]), f"{skill} (template)")
    return {"skill": skill, **out}


def check_findings(skill, rows):
    """Evaluator, data stage: empty results or a source gap without the won-vs-not-won contrast are rejected."""
    if not rows:
        return "query returned no rows"
    nums = [k for k, v in rows[0].items() if isinstance(v, (int, float))]
    if skill == "source_gap":
        if not any(isinstance(v, str) and "." in v for v in rows[0].values()):
            return "no domain column"
        if len(nums) < 2:
            return ("needs two numbers per domain (% of not-won answers citing it AND % of won answers citing it); "
                    "a domain common in every answer is not a cause")
    elif not nums:
        return "no counts in the result"
    return None


def reporter_draft(run, plan, findings, memories, critique):
    ev = "\n".join(f"[{f['skill']}] rows: {json.dumps(f['rows'][:10])}" for f in findings)
    mem = "\n".join(f"- {m['content']}" for m in memories) or "- (none)"
    try:
        d, model = B.chat_json([
            {"role": "system", "content": "You are the Reporter of Ask Glisper. Use ONLY numbers that appear in the evidence "
             "rows (copy them exactly). Never invent search volume or traffic, never promise results, never claim one fix "
             'will solve it. Write for a busy shop owner: plain words, answer the latest question directly, build on '
             'the earlier chat instead of repeating it. Reply with ONLY JSON: {"answer": "2-4 sentences (a requested '
             'draft message may be longer)", "action": "ONE concrete thing to do this '
             'week, naming the exact domain or URL", "confidence": "high|medium|low"}'},
            {"role": "user", "content": f"Brand: {run.brand['name']} ({run.brand['domain']})\nEarlier in this chat:\n"
             f"{earlier(run.history)}\nQuestion: {run.question}\nBrand memory:\n{mem}\n"
             f"Evidence:\n{ev}" + (f"\n\nThe Evaluator rejected your last draft:\n{critique}\nRewrite it." if critique else "")}])
    except (OSError, ValueError) as e:
        d, model = None, None
        run.step("Reporter", "model unavailable, using rule draft", str(e)[:200])
    if not d:
        d = rule_draft(plan, findings, run.brand)
    run.step("Reporter", "draft" + (" (revised)" if critique else ""), d, "Evaluator", model)
    return d, model


def rule_draft(plan, findings, brand):
    """SAMPLE_MODE draft built only from the query rows."""
    BRAND, BRAND_DOMAIN = brand["name"], brand["domain"]
    eng = plan["engine"] or "all engines"
    parts, action = [], ""
    for f in findings:
        rows = f["rows"]
        if f["skill"] == "competitor_gap" and rows:
            top = next((r for r in rows if r.get("winner") != BRAND), rows[0])
            us = next((r for r in rows if r.get("winner") == BRAND), None)
            parts.append(f"On {eng}, {top['winner']} is ranked first in {top['pct_of_answers']}% of answers" +
                         (f", {BRAND} in {us['pct_of_answers']}%." if us else "."))
        if f["skill"] == "source_gap" and rows:
            gap = max((r for r in rows if r["domain"] != BRAND_DOMAIN and r["source_type"] not in ("competitor_site", "owned")),
                      key=lambda r: r["pct_of_not_won_citing"] - r["pct_of_won_citing"], default=None)
            if gap:
                parts.append(f"{gap['domain']} is cited in {gap['pct_of_not_won_citing']}% of answers we don't win and "
                             f"{gap['pct_of_won_citing']}% of answers we win.")
                action = f"Ask {gap['domain']} to add {BRAND} to the page it is cited for, with a link to {BRAND_DOMAIN}."
    return {"answer": " ".join(parts) or "No clear gap in this data.", "action": action, "confidence": "medium"}


BANNED_CLAIMS = re.compile(r"(?i)search volume|monthly searches|traffic (will|would)|guarantee|will (definitely|surely)|"
                           r"(this|the) (one )?fix will|will (win|fix|solve) (it|this|everything)|visibility score")
CAUSAL = re.compile(r"(?i)\b(because of|is the reason|the reason (is|why)|caus(es|ed|ing)|due to|drives?|driving)\b")


def evaluate(run, draft, findings, memories, exclude):
    """Rubric 0-3 each: grounding, honesty, actionability. Code checks + one small model judgement."""
    BRAND, BRAND_DOMAIN = run.brand["name"], run.brand["domain"]
    text = f"{draft.get('answer', '')} {draft.get('action', '')}"
    rows = [r for f in findings for r in f["rows"]]
    values = [v for r in rows for v in r.values()]
    known_nums = {round(float(v), 1) for v in values if isinstance(v, (int, float))}
    strings = " ".join(str(v) for v in values if isinstance(v, str)).lower()
    critique = []

    bare = re.sub(r"https?://\S+|\b[\w-]+(\.[a-z][\w-]*)+\b", " ", text, flags=re.I)  # numbers in URLs/domains don't count
    nums = [float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", bare)]
    rounded = {round(k) for k in known_nums}
    # every decimal and every whole number > 10 must be a query value, or that value rounded to a whole number
    untraced = [n for n in nums if (n % 1 or n > 10) and round(n, 1) not in known_nums
                and not (n % 1 == 0 and n in rounded)]
    grounding = 0 if untraced else 3  # one invented number sinks the answer
    if untraced:
        critique.append(f"numbers {untraced} are not in any query result; copy numbers exactly from the evidence rows")

    honesty = 3
    if m := BANNED_CLAIMS.search(text):
        honesty -= 2
        critique.append(f"unsupported claim '{m.group(0)}': no volume/traffic claims, no promise that one fix solves it")
    for b in exclude:
        if b.lower() in text.lower():
            honesty -= 2
            critique.append(f"brand memory says to ignore {b}; do not present them as a competitor")
    if m := CAUSAL.search(text):
        honesty -= 1
        critique.append(f"'{m.group(0)}': the rows show which sources are cited together with losses, not what causes "
                        "them; say 'is cited in X% of answers we don't win'")
    if m := re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):  # not in any row, so invented: an invented fact fails honesty
        honesty -= 3
        critique.append(f"'{m.group(0)}' is a contact detail that is not in the evidence; name the domain only")
    # a rival's own site shows up in answers because the rival wins them: a symptom, never a gap to close
    rival_slugs = {re.sub(r"\W", "", str(v)).lower() for r in rows for k, v in r.items()
                   if k in ("winner", "competitor", "brand")} - {"", re.sub(r"\W", "", BRAND).lower()}
    is_rival_site = lambda d: d.split(".")[0] in rival_slugs  # noqa: E731
    if rival := next((d for d in re.findall(r"\b[\w-]+(?:\.[\w-]+)+\b", text.lower()) if is_rival_site(d)), None):
        honesty -= 2
        critique.append(f"{rival} is a rival's own site: it is cited because they win those answers, so it is a "
                        "result, not a gap we can close. Leave it out and use third-party sources from the rows")
    for r in rows:  # a domain cited as often in wins as in losses is not a gap
        dom = next((v for v in r.values() if isinstance(v, str) and "." in v and " " not in v), None)
        lost = [v for k, v in r.items() if isinstance(v, (int, float)) and re.search(r"lost|lose|loss|not|non", k)]
        won = [v for k, v in r.items() if isinstance(v, (int, float)) and "won" in k and not re.search(r"lost|loss|not|non", k)]
        if dom and lost and won and lost[0] <= won[0] and dom.lower() in text.lower() and dom != BRAND_DOMAIN:
            honesty -= 1
            critique.append(f"{dom} is cited in {lost[0]}% of answers we don't win vs {won[0]}% we win: not a gap, "
                            "leave it out")
            break
    honesty = max(honesty, 0)

    action = draft.get("action") or ""
    domains = re.findall(r"\b[\w-]+(?:\.[\w-]+)+\b", action.lower())
    target = next((d for d in domains if d in strings and d != BRAND_DOMAIN and not is_rival_site(d)), None)
    actionability = 0 if not action else (2 if target else 1)
    if not action:
        critique.append("no action given")
    elif not target:
        critique.append("the action must name a specific third-party domain or URL from the evidence rows "
                        "(not our site, not a rival's site)")
    if action and target:
        try:
            j, model = B.chat_json([{"role": "user", "content": "Is this marketing action specific and doable by a small "
                                     f"business within one week? Action: {action}\n"
                                     'Reply with ONLY {"specific": true|false, "why": "short"}'}],
                                   model=B.FAST_MODEL, max_tokens=1200)
        except (OSError, ValueError):
            j, model = None, None
        if j is None or j.get("specific"):
            actionability = 3
        else:
            critique.append(f"action not specific enough: {j.get('why', '')}")
        if model:
            run.models.add(model)
    score = grounding + honesty + actionability
    rubric = {"grounding": grounding, "honesty": honesty, "actionability": actionability, "total": score}
    ok = score >= PASS_SCORE
    run.step("Evaluator", f"{'PASS' if ok else 'REJECT'} {score}/9", rubric, "Reporter final" if ok else "Reporter revise",
             critique=critique)
    return score, "\n".join(f"- {c}" for c in critique), target


def get_quotes(run, plan, domain):
    """Evidence quotes: real answer lines from not-won answers that cite the action's target domain."""
    eng = f" AND a.engine = {_q(plan['engine'])}" if plan["engine"] else ""
    out = run.sql(f"SELECT a.result_id, a.run_date, a.engine, a.prompt, a.winner, a.answer_text, s.url FROM answers a "
                  f"JOIN answer_sources s ON a.result_id = s.result_id WHERE s.domain = {_q(domain)} AND a.status <> 'won'"
                  f"{eng} ORDER BY a.run_date DESC LIMIT 3", f"get_answer: quotes citing {domain}")
    return [{"result_id": r["result_id"], "date": str(r["run_date"]), "engine": r["engine"], "prompt": r["prompt"],
             "quote": r["answer_text"].split("\n")[1] if "\n" in r["answer_text"] else r["answer_text"][:160],
             "cited": r["url"]} for r in out["rows"]]


# ---------- the loop ----------

def ask(question, emit=None, brand_id=None, chat_id=None, history=()):
    """history: earlier finished runs of the same chat (oldest first), so follow-ups build on them."""
    brand = load_brand(brand_id)
    run = Run(question, emit, brand, history[-3:])
    run.step("System", "start", {"question": question, "brand": brand["name"], "sample_mode": B.SAMPLE_MODE,
                                 "data_layer": "sqlite (SAMPLE_MODE)" if B.SAMPLE_MODE else
                                 f"Zetaris {B.GLISPER_CATALOG} (audit {brand['audit_id']})" if brand["audit_id"]
                                 else f"Zetaris {B.CATALOG}"})
    try:
        memories = B.recall(brand["name"], question)["memories"]
    except (RuntimeError, OSError, ValueError) as e:
        memories = []
        run.step("Memory", "H-MEM recall failed, continuing without brand memory", str(e)[:200])
    else:
        run.step("Memory", f"H-MEM recalled {len(memories)} brand facts", [m["content"] for m in memories], "Planner",
                 engine="meterless/hmem")
    plan = planner(run, memories)
    findings = [analyst_query(run, s, plan) for s in plan["skills"]] or run.history[-1]["evidence"]

    critique, draft, score, target, retries = None, None, 0, None, 0
    for i in range(MAX_DRAFTS):
        draft, _ = reporter_draft(run, plan, findings, memories, critique)
        score, critique, target = evaluate(run, draft, findings, memories, plan["exclude_brands"])
        if score >= PASS_SCORE:
            break
        retries = i + 1
    passed = score >= PASS_SCORE
    quotes = get_quotes(run, plan, target) if target else []
    final = {
        "run_id": run.id, "chat_id": chat_id or run.id, "engine": plan["engine"], "question": question, "brand": brand["name"], "answer": draft.get("answer"), "action": draft.get("action"),
        "confidence": ("high" if score == 9 and retries == 0 else "medium") if passed else "low",
        "score": score, "passed": passed, "retries": min(retries, MAX_DRAFTS - 1),
        "evidence": [{"skill": f["skill"], "sql": f["sql"], "rows": f["rows"][:10], "backend": f["backend"]} for f in findings],
        "quotes": quotes, "memories_used": [m["content"] for m in memories], "propose_memory": plan.get("remember"),
        "models": sorted(run.models), "data_label": brand["label"],
        "log": str(run.log_path.relative_to(B.ROOT) if run.log_path.is_relative_to(B.ROOT) else run.log_path),
    }
    if not passed:
        final["limitations"] = f"Evaluator did not pass the answer after {MAX_DRAFTS} drafts: low confidence.\n{critique}"
    run.step("Reporter", "final answer", {k: final[k] for k in ("answer", "action", "confidence", "score")}, None)
    # the chat thread: reopening the panel reloads finished runs for this brand (server.py /history)
    (LOGS / f"{run.id}.final.json").write_text(json.dumps({**final, "brand_key": brand_key(brand),
                                                           "asked_at": time.time()}, default=str))
    return final


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    args = sys.argv[1:]
    brand_id = args[1] if args[:1] == ["--brand"] else None
    args = args[2:] if brand_id else args
    if args[:1] == ["--remember"]:
        print(json.dumps(B.remember(load_brand(brand_id)["name"], " ".join(args[1:])), indent=2))
        sys.exit()
    q = " ".join(args) or "Why are we losing on Perplexity?"
    out = ask(q, brand_id=brand_id, emit=lambda e: print(f"[{e['t']:>6}s] {e['agent']:<9} {e['decision']}  ({e['model']})", flush=True))
    print(json.dumps({k: v for k, v in out.items() if k != "evidence"}, indent=2, default=str))
