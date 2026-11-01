import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";
import type { JsonObject } from "../types.js";
import { taskId } from "../util/hash.js";

export function parseFlags(argv: string[]): Record<string, string | boolean> {
  const flags: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i] || "";
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

export function flagString(flags: Record<string, string | boolean>, key: string, fallback?: string): string | undefined {
  const value = flags[key];
  return typeof value === "string" ? value : fallback;
}

export function flagNumber(flags: Record<string, string | boolean>, key: string, fallback: number): number {
  const value = Number(flags[key]);
  return Number.isFinite(value) ? value : fallback;
}

export function repoRoot(): string {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
}

export async function readJson<T = JsonObject>(filePath: string): Promise<T> {
  return JSON.parse(await readFile(filePath, "utf8")) as T;
}

export async function writeJson(filePath: string, value: unknown): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export async function writeText(filePath: string, value: string): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, value, "utf8");
}

export function joinUrl(base: string, route: string): string {
  return `${base.replace(/\/+$/, "")}/${route.replace(/^\/+/, "")}`;
}

export async function fetchJson(url: string, init?: RequestInit & { timeoutMs?: number }): Promise<JsonObject> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), init?.timeoutMs ?? 900_000);
  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    const text = await res.text();
    const body = text ? JSON.parse(text) as JsonObject : {};
    if (!res.ok) {
      const error = new Error(String(body.error || body.message || `HTTP ${res.status}`));
      (error as Error & { payload?: JsonObject }).payload = body;
      throw error;
    }
    return body;
  } finally {
    clearTimeout(timeout);
  }
}

export function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function ensureObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

export function numberOrNull(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

export function numberArray(value: unknown): number[] {
  return Array.isArray(value) ? value.map(Number).filter(Number.isFinite) : [];
}

export function withTask(baseRequest: JsonObject, prefix: string): JsonObject {
  const request = cloneJson(baseRequest);
  request.taskId = taskId(prefix);
  const clinicalContext = ensureObject(request.clinicalContext);
  request.clinicalContext = {
    ...clinicalContext,
    paperExperimentTaskId: request.taskId,
    paperExperimentRunAt: new Date().toISOString(),
  };
  return request;
}

export async function postOptimize(backendUrl: string, request: JsonObject, timeoutMs = 900_000): Promise<{
  ok: boolean;
  response?: JsonObject;
  error?: string;
  elapsedMs: number;
}> {
  const started = performance.now();
  try {
    const response = await fetchJson(joinUrl(backendUrl, "/api/optimize"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
      timeoutMs,
    });
    return { ok: response.ok !== false, response, elapsedMs: performance.now() - started };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
      response: (err as Error & { payload?: JsonObject }).payload,
      elapsedMs: performance.now() - started,
    };
  }
}

export function median(values: Array<number | null | undefined>): number | null {
  const clean = values.filter((value): value is number => Number.isFinite(value));
  if (clean.length <= 0) return null;
  clean.sort((a, b) => a - b);
  const mid = Math.floor(clean.length / 2);
  return clean.length % 2 ? clean[mid] ?? null : ((clean[mid - 1] ?? 0) + (clean[mid] ?? 0)) / 2;
}

export function percentile(values: Array<number | null | undefined>, pct: number): number | null {
  const clean = values.filter((value): value is number => Number.isFinite(value));
  if (clean.length <= 0) return null;
  clean.sort((a, b) => a - b);
  const idx = Math.max(0, Math.min(clean.length - 1, Math.ceil(clean.length * pct / 100) - 1));
  return clean[idx] ?? null;
}

export function fmt(value: number | null | undefined, digits = 2): string {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : "n/a";
}
