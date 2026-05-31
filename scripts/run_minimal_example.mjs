import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      out[key] = "true";
    } else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const casePath = args.case || "cases/requests/8b83d8d249834bcba970b8e463ff6a9d.json";
const engineUrl = (args["engine-url"] || "http://127.0.0.1:7878").replace(/\/$/, "");
const outPath = args.out || "output/minimal_example_accepted.json";

const fixture = JSON.parse(await readFile(casePath, "utf8"));
const request = fixture.request || fixture;
const response = await fetch(`${engineUrl}/v1/optimize`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(request),
});

const payload = await response.json();
const summary = {
  httpStatus: response.status,
  ok: Boolean(payload.ok),
  code: payload.code ?? null,
  selectedCount: Array.isArray(payload.selected) ? payload.selected.length : 0,
  uncoveredCount: Array.isArray(payload.uncovered) ? payload.uncovered.length : null,
  requiredCoverageUnits: payload.requiredCoverageUnits ?? null,
  coveredCoverageUnits: payload.coveredCoverageUnits ?? null,
  engineStatus: payload.engineStatus ?? payload.engine_status ?? null,
};

await mkdir(path.dirname(outPath), { recursive: true });
await writeFile(outPath, JSON.stringify({ summary, response: payload }, null, 2));
console.log(JSON.stringify(summary, null, 2));

if (!response.ok || !payload.ok || payload.code === "strict_atlas_target_lookup_failed") {
  process.exitCode = 2;
}
