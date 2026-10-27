"""Capture real web-search answers for the demo prompts (Groq free tier, browser_search).

Each prompt goes to openai/gpt-oss-120b with the browser_search tool. We keep the answer text, the pages
the model opened (its cited sources) and the search results, labelled dev_web_search. These rows are a
stand-in for an AI engine, never presented as ChatGPT/Perplexity output.

  python3 scripts/capture_web_search.py prompts.tsv   # lines: <prompt_id>\t<prompt text>
Writes data/web_search/<prompt_id>.json; skips prompts already captured. Needs GROQ_API_KEY in .env.
Free tier: 8k tokens/min and 200k tokens/day per model, one call ~12k tokens, so calls are spaced out.
"""
import json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "web_search"
MODEL = os.getenv("GROQ_SEARCH_MODEL", "openai/gpt-oss-120b")
GAP_S = int(os.getenv("CAPTURE_GAP_S", "100"))

for line in (ROOT / ".env").read_text().splitlines():
    k, _, v = line.partition("=")
    if k and not k.startswith("#"):
        os.environ.setdefault(k.strip(), v.strip())


def ask(prompt):
    body = {"model": MODEL, "messages": [
        {"role": "system", "content": "You are a helpful AI assistant. Search the web, then answer the user's "
                                      "question directly and name specific brands or shops."},
        {"role": "user", "content": prompt}],
        "tools": [{"type": "browser_search"}], "tool_choice": "required"}
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", json.dumps(body).encode(), {
        "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise RuntimeError(f"groq {e.code}: {e.read()[:300]!r}")
            wait = int(e.headers.get("retry-after", "60")) + 5
            if wait > 900:  # daily budget gone: stop instead of waiting hours
                raise RuntimeError(f"groq daily limit, retry-after {wait}s")
            print(f"  429, waiting {wait}s", flush=True)
            time.sleep(wait)


def trim(prompt_id, prompt, resp):
    msg = resp["choices"][0]["message"]
    opened, searched = [], []
    for t in msg.get("executed_tools") or []:
        urls = [r["url"] for r in (t.get("search_results") or {}).get("results", []) if r.get("url")]
        (opened if t.get("name") == "browser.open" else searched).extend(urls)
    answer = re.sub(r"【[^】]*】", "", msg.get("content") or "")  # drop cursor markers, keep text
    return {"prompt_id": prompt_id, "prompt": prompt, "capture_method": "dev_web_search", "model": resp["model"],
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "answer": answer.strip(),
            "cited_urls": list(dict.fromkeys(opened)), "search_urls": list(dict.fromkeys(searched)),
            "usage": resp.get("usage", {})}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [l.split("\t", 1) for l in Path(sys.argv[1]).read_text().splitlines() if "\t" in l]
    todo = [(pid, text) for pid, text in rows if not (OUT / f"{pid}.json").exists()]
    print(f"{len(todo)} of {len(rows)} prompts to capture with {MODEL}", flush=True)
    for i, (pid, text) in enumerate(todo):
        try:
            rec = trim(pid, text, ask(text))
        except RuntimeError as e:
            print(f"stop: {e}", flush=True)
            break
        (OUT / f"{pid}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        print(f"[{i + 1}/{len(todo)}] {rec['usage'].get('total_tokens')} tok, {len(rec['cited_urls'])} cited: {text}",
              flush=True)
        if i + 1 < len(todo):
            time.sleep(GAP_S)
