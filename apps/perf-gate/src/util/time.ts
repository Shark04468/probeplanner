import { performance } from "node:perf_hooks";

export function nowMs(): number {
  return performance.now();
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function median(values: Array<number | null | undefined>): number | null {
  const clean = values.filter((v): v is number => Number.isFinite(v));
  if (clean.length === 0) return null;
  clean.sort((a, b) => a - b);
  const mid = Math.floor(clean.length / 2);
  if (clean.length % 2) return clean[mid] ?? null;
  return ((clean[mid - 1] ?? 0) + (clean[mid] ?? 0)) / 2;
}

export function percentile(values: Array<number | null | undefined>, pct: number): number | null {
  const clean = values.filter((v): v is number => Number.isFinite(v));
  if (clean.length === 0) return null;
  clean.sort((a, b) => a - b);
  const idx = Math.ceil((pct / 100) * clean.length) - 1;
  return clean[Math.max(0, Math.min(clean.length - 1, idx))] ?? null;
}

export function numericSummary(values: Array<number | null | undefined>) {
  const clean = values.filter((v): v is number => Number.isFinite(v));
  if (clean.length === 0) {
    return { count: 0, median: null, p95: null, min: null, max: null };
  }
  return {
    count: clean.length,
    median: median(clean),
    p95: percentile(clean, 95),
    min: Math.min(...clean),
    max: Math.max(...clean),
  };
}

export function formatMs(value: number | null | undefined): string {
  return Number.isFinite(value) ? `${Math.round(Number(value))}ms` : "n/a";
}
