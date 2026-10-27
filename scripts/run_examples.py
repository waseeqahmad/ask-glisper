"""Run every question in input_examples/questions.txt and save the final answer + full trace to output_examples/.
  python3 scripts/run_examples.py ["one question"]  (live: Zetaris + Nightshift)   SAMPLE_MODE=true ... (no services)
"""
import json, re, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from glisper_agent import backends as B  # noqa: E402
from glisper_agent.agent import ask  # noqa: E402

OUT = B.ROOT / "output_examples"
mode = "sample" if B.SAMPLE_MODE else "live"
for q in sys.argv[1:] or (B.ROOT / "input_examples" / "questions.txt").read_text().splitlines():
    if q.strip():
        slug = f"{mode}-" + re.sub(r"\W+", "-", q.lower()).strip("-")[:50]
        final = ask(q, emit=lambda e: print(f"  {e['agent']:<9} {e['decision']}  ({e['model']})", flush=True))
        (OUT / f"{slug}.json").write_text(json.dumps(final, indent=2, default=str) + "\n")
        shutil.copy(B.ROOT / final["log"], OUT / f"{slug}.trace.jsonl")
        print(f"{slug}: {final['score']}/9 retries={final['retries']}", flush=True)
