"""Generate the SYNTHETIC demo audit for a fictional brand, Lahore Loom (lahoreloom.pk).

Every row is synthetic (capture_method='synthetic'): no real engine output, no real customer.
The tables have the shape of the flat views Ask Glisper reads in Glisper (v_answers, v_answer_sources,
v_answer_mentions), so the same agent SQL runs against either.

Planted pattern (what a good answer should find; the agent is never told this):
  - Perplexity: we lose most answers, mostly to Threadline. Lost Perplexity answers usually cite the listicle
    lahorestyleguide.pk/best-boutiques-lahore-2026 (which lists Threadline, not us) and reddit.com/r/lahore.
  - Won answers cite our own site. Kurta Co wins a lot on Gemini but only ships in Karachi (memory test).
  - Other sources (instagram, dawn.com, daraz.pk, youtube) are random noise across won and lost.

  python3 scripts/make_demo_data.py                       # writes data/sample/*.csv
  python3 scripts/make_demo_data.py --load "postgresql://user:pass@localhost:5433/ask_glisper_demo"
"""
import csv, random, subprocess, sys, uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "sample"
US = "Lahore Loom"
RIVALS = {"Threadline": "threadline.pk", "Kurta Co": "kurtaco.pk", "Silk Route Studio": "silkroutestudio.pk",
          "Rang Bazaar": "rangbazaar.pk"}
BLURB = {US: "hand-finished lawn and khaddar suits, stitched in Gulberg", "Threadline": "trendy pret and quick delivery",
         "Kurta Co": "budget kurtas, ships across Karachi", "Silk Route Studio": "premium silk formals",
         "Rang Bazaar": "bright festive wear at mid-range prices"}
ENGINES = ["chatgpt", "perplexity", "gemini", "claude"]
RUNS = ["2026-10-05", "2026-10-12", "2026-10-19"]
PROMPTS = [
    "best eastern wear boutique in Lahore", "where to buy lawn suits in Lahore", "affordable kurta shop Lahore",
    "boutique for stitched khaddar suits Lahore", "best place for Eid outfits in Lahore", "Lahore boutiques with home delivery",
    "unstitched lawn brands Lahore 2026", "custom stitching boutique Gulberg", "best pret wear Lahore",
    "where to buy embroidered kurtis in Lahore", "local clothing brands Lahore under 5000", "best winter shawls shop Lahore",
    "mehndi outfit boutique Lahore", "sustainable clothing brand Lahore", "Lahore boutique for office wear women",
    "best online clothing store Pakistan", "handmade clothes Lahore", "Lahore boutique with good reviews",
    "where to buy chikankari in Lahore", "new clothing brands Lahore", "best kurta brand Pakistan",
    "boutique near Liberty Market Lahore", "Lahore fashion brands for students", "formal dresses shop Lahore",
]
# win rate for us, and who tends to win when we don't, per engine
WIN = {"chatgpt": 0.55, "perplexity": 0.15, "gemini": 0.4, "claude": 0.5}
RIVAL_W = {"perplexity": [0.75, 0.05, 0.1, 0.1], "gemini": [0.25, 0.5, 0.1, 0.15],
           "chatgpt": [0.3, 0.2, 0.25, 0.25], "claude": [0.3, 0.15, 0.3, 0.25]}
LISTICLE = ("lahorestyleguide.pk", "listicle", "https://lahorestyleguide.pk/best-boutiques-lahore-2026")
REDDIT = ("reddit.com", "forum", "https://www.reddit.com/r/lahore/comments/1f8kq2/best_place_for_eastern_wear/")
NOISE = [("instagram.com", "social", "https://www.instagram.com/{h}/"), ("dawn.com", "news",
         "https://www.dawn.com/news/lifestyle/lahore-fashion-week-2026"), ("daraz.pk", "marketplace",
         "https://www.daraz.pk/catalog/?q=lawn+suits"), ("youtube.com", "video", "https://www.youtube.com/watch?v=lhrstyle26")]


def make(rng):
    answers, sources, mentions = [], [], []
    for run in RUNS:
        for pi, prompt in enumerate(PROMPTS):
            for eng in ENGINES:
                rid = str(uuid.UUID(int=rng.getrandbits(128)))
                won = rng.random() < WIN[eng]
                rivals = list(RIVALS)
                if won:
                    rng.shuffle(rivals)
                    ranked = [US] + rivals[:rng.randint(1, 3)]
                else:
                    first = rng.choices(rivals, RIVAL_W[eng])[0]
                    rest = [r for r in rivals if r != first]
                    rng.shuffle(rest)
                    ranked = [first] + rest[:rng.randint(1, 2)]
                    if rng.random() < 0.35:  # mentioned, but not first
                        ranked.insert(rng.randint(1, len(ranked)), US)
                status = "won" if ranked[0] == US else ("partial" if US in ranked else "lost")
                cites = []
                if status == "won":
                    cites.append(("lahoreloom.pk", "own_site", "https://lahoreloom.pk/collections/lawn"))
                    if rng.random() < 0.1:
                        cites.append(LISTICLE)
                else:
                    p_list, p_reddit = (0.8, 0.5) if eng == "perplexity" else (0.2, 0.15)
                    if rng.random() < p_list:
                        cites.append(LISTICLE)
                    if rng.random() < p_reddit:
                        cites.append(REDDIT)
                    w = RIVALS[ranked[0]]
                    cites.append((w, "competitor_site", f"https://{w}/"))
                for d, t, u in rng.sample(NOISE, rng.randint(1, 2)):
                    cites.append((d, t, u.format(h=ranked[0].lower().replace(" ", ""))))
                lines = [f"{i + 1}. {b}: {BLURB[b]}." for i, b in enumerate(ranked)]
                text = f"Here are good options for \"{prompt}\":\n" + "\n".join(lines) + \
                       "\nSources: " + ", ".join(u for _, _, u in cites)
                answers.append({"result_id": rid, "run_date": run, "prompt_id": f"p{pi + 1:02d}", "prompt": prompt,
                                "engine": eng, "status": status, "our_position": ranked.index(US) + 1 if US in ranked else "",
                                "winner": ranked[0], "answer_text": text, "capture_method": "synthetic"})
                for d, t, u in cites:
                    sources.append({"result_id": rid, "run_date": run, "engine": eng, "status": status,
                                    "domain": d, "source_type": t, "url": u})
                for i, b in enumerate(ranked):
                    mentions.append({"result_id": rid, "run_date": run, "engine": eng, "status": status,
                                     "brand": b, "is_us": int(b == US), "position": i + 1})
    return {"answers": answers, "answer_sources": sources, "answer_mentions": mentions}


DDL = """
DROP TABLE IF EXISTS answers, answer_sources, answer_mentions;
CREATE TABLE answers (result_id text PRIMARY KEY, run_date date, prompt_id text, prompt text, engine text, status text,
  our_position int, winner text, answer_text text, capture_method text);
CREATE TABLE answer_sources (result_id text, run_date date, engine text, status text, domain text, source_type text, url text);
CREATE TABLE answer_mentions (result_id text, run_date date, engine text, status text, brand text, is_us int, position int);
"""

if __name__ == "__main__":
    tables = make(random.Random(42))
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with open(OUT / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"{name}: {len(rows)} rows -> {OUT / (name + '.csv')}")
    if "--load" in sys.argv:
        url = sys.argv[sys.argv.index("--load") + 1]
        cmds = DDL + "".join(f"\\copy {n} FROM '{OUT / n}.csv' CSV HEADER\n" for n in tables)
        subprocess.run(["psql", url, "-v", "ON_ERROR_STOP=1", "-q"], input=cmds, text=True, check=True)
        print("loaded into Postgres")
