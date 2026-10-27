// Thin CLI over the H-MEM reference engine, called from Python (glisper_agent/backends.py).
//   tsx hmem_cli.ts remember <dir> <text> [source]   -> {"id", "content", ...}
//   tsx hmem_cli.ts recall   <dir> <query> [topN]    -> {"memories": [...], "trace": {...}}
//   tsx hmem_cli.ts list     <dir>                    -> [{"id", "content", ...}]
// <dir> holds state.json + ledger.jsonl, so memory survives across sessions.
import { HMEM } from "./reference/src/index.ts";

const [cmd, dir, arg, extra] = process.argv.slice(2);
if (!cmd || !dir) {
  console.error("usage: hmem_cli.ts remember|recall|list <dir> [text|query] [source|topN]");
  process.exit(2);
}
const hmem = new HMEM({ persistDir: dir, actor: "ask-glisper" });
const slim = (m: any) => ({ id: m.id, content: m.content, type: m.type, layer: m.layer, source: m.source,
  confidence: m.confidence, timestamp: m.timestamp });

if (cmd === "remember") {
  const m = hmem.add({ content: arg, type: "preference", layer: "long_term", confidence: 0.9,
    source: extra || "user:approved", tags: ["brand-context"],
    provenance: { origin: "user_correction", learnedAt: Date.now(), label: "User approved this in Ask Glisper" } as any });
  console.log(JSON.stringify(slim(m)));
} else if (cmd === "recall") {
  // threshold 0: brand context is few, short records; let H-MEM rank them, keep the top N
  const r = hmem.query(arg || "brand context", { topN: Number(extra || 5), threshold: 0 });
  console.log(JSON.stringify({ memories: r.memories.map((x) => ({ ...slim(x.memory), relevance: x.relevance })),
    trace: r.trace }));
} else if (cmd === "list") {
  console.log(JSON.stringify(hmem.store.all().map(slim)));
} else {
  console.error(`unknown command ${cmd}`);
  process.exit(2);
}
