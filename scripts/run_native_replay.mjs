import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function parseArgs(argv) {
  const flags = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      flags[key] = true;
    } else {
      flags[key] = next;
      i += 1;
    }
  }
  return flags;
}

function csvCell(value) {
  if (value === null || value === undefined) return "";
  const text = Array.isArray(value) ? value.join("|") : String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function toCsv(rows, columns) {
  return `${columns.join(",")}\n${rows.map((row) => columns.map((col) => csvCell(row[col])).join(",")).join("\n")}\n`;
}

async function postNative(engineUrl, request, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${engineUrl.replace(/\/+$/, "")}/v1/optimize`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    const text = await res.text();
    return { status: res.status, body: text ? JSON.parse(text) : {} };
  } finally {
    clearTimeout(timer);
  }
}

async function loadCases(manifestPath, limit) {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const baseDir = path.dirname(manifestPath);
  const requestDir = path.join(baseDir, "requests");
  const picked = manifest.cases.slice(0, limit);
  const cases = [];
  for (const item of picked) {
    const payload = JSON.parse(await readFile(path.join(requestDir, `${item.id}.json`), "utf8"));
    cases.push({ ...item, request: payload.request });
  }
  return cases;
}

async function main() {
  const flags = parseArgs(process.argv.slice(2));
  const engineUrl = String(flags["engine-url"] || process.env.PROBEPLANNER_ENGINE_URL || "http://127.0.0.1:7878");
  const manifestPath = path.resolve(ROOT, String(flags.manifest || "cases/manifest.json"));
  const outDir = path.resolve(ROOT, String(flags.out || "reports/native-mri-profile-replay"));
  const timeoutMs = Number(flags["timeout-ms"] || 120000);
  const limit = Number(flags.limit || Number.MAX_SAFE_INTEGER);
  const cases = await loadCases(manifestPath, limit);
  await mkdir(outDir, { recursive: true });

  const rows = [];
  for (let i = 0; i < cases.length; i += 1) {
    const replayCase = cases[i];
    const request = JSON.parse(JSON.stringify(replayCase.request));
    request.taskId = `native_replay_${replayCase.id.slice(0, 8)}_${Date.now()}`;
    const started = Date.now();
    let status = 0;
    let body = {};
    try {
      const result = await postNative(engineUrl, request, timeoutMs);
      status = result.status;
      body = result.body;
    } catch (err) {
      body = { ok: false, code: "fetch_failed", error: err?.message || String(err) };
    }
    const selected = Array.isArray(body.selected) ? body.selected : [];
    rows.push({
      caseId: replayCase.id,
      label: replayCase.label,
      source: replayCase.source,
      targetKeys: replayCase.targetKeys || [],
      httpStatus: status,
      ok: body.ok !== false && status < 400,
      code: body.code || "",
      error: body.error || "",
      elapsedMs: Date.now() - started,
      selectedCount: selected.length,
      uncoveredCount: Array.isArray(body.uncovered) ? body.uncovered.length : "",
      requiredCoverageUnits: body.requiredCoverageUnits ?? "",
      coveredCoverageUnits: body.coveredCoverageUnits ?? "",
      minSafetyDistanceMm: selected[0]?.minSafetyDistanceMm ?? "",
      strictAtlasLookupFailed: body.code === "strict_atlas_target_lookup_failed" || String(body.error || "").includes("strict_atlas_target_lookup_failed"),
    });
    console.log(`[native] ${i + 1}/${cases.length} ${replayCase.label}: status=${status} selected=${selected.length} code=${body.code || "ok"}`);
  }

  const summary = {
    generatedAt: new Date().toISOString(),
    engineUrl,
    manifestPath,
    caseCount: rows.length,
    okCount: rows.filter((row) => row.ok).length,
    strictAtlasLookupFailureCount: rows.filter((row) => row.strictAtlasLookupFailed).length,
    selectedCaseCount: rows.filter((row) => Number(row.selectedCount) > 0).length,
    outputFiles: {
      summaryJson: path.join(outDir, "summary.json"),
      caseCsv: path.join(outDir, "cases.csv"),
    },
  };
  await writeFile(summary.outputFiles.caseCsv, toCsv(rows, [
    "caseId",
    "label",
    "source",
    "targetKeys",
    "httpStatus",
    "ok",
    "code",
    "error",
    "elapsedMs",
    "selectedCount",
    "uncoveredCount",
    "requiredCoverageUnits",
    "coveredCoverageUnits",
    "minSafetyDistanceMm",
    "strictAtlasLookupFailed",
  ]), "utf8");
  await writeFile(summary.outputFiles.summaryJson, `${JSON.stringify(summary, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(summary, null, 2));
  if (summary.strictAtlasLookupFailureCount > 0 || summary.okCount !== summary.caseCount) {
    process.exitCode = 2;
  }
}

main().catch((err) => {
  console.error(err?.stack || err?.message || String(err));
  process.exitCode = 1;
});
