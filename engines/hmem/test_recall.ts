// Our H-MEM feature test: a brand fact remembered in one session is recalled in a NEW process (next session).
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hmem-recall-"));
const cli = (...a: string[]) => JSON.parse(execFileSync("npx", ["tsx", "hmem_cli.ts", ...a], { encoding: "utf-8" }));

cli("remember", dir, "Ignore Kurta Co: they only ship in Karachi, not a competitor for us", "user:approved");
cli("remember", dir, "We only serve customers in Lahore", "user:approved");
const r = cli("recall", dir, "why do we lose to rivals on Perplexity", "5");
const contents: string[] = r.memories.map((m: any) => m.content);
const ok = contents.some((c) => c.includes("Kurta Co")) && contents.some((c) => c.includes("Lahore"));
const ledger = fs.readFileSync(path.join(dir, "ledger.jsonl"), "utf-8").trim().split("\n").length;
console.log(ok && ledger >= 2 ? `recall test: PASS (${contents.length} memories, ${ledger} ledger entries)` : "recall test: FAIL");
fs.rmSync(dir, { recursive: true });
process.exit(ok ? 0 : 1);
