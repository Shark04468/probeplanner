import { chromium } from "@playwright/test";
import type { Browser3dMetrics, Browser3dResourceMetric } from "../types.js";
import { median, percentile } from "../util/time.js";

type BridgeEvent = { event: string; atMs: number; data?: unknown };
type BufferedBridgeEvent = { event: string; atEpochMs: number; data?: unknown };
type ResponseMeta = Pick<Browser3dResourceMetric, "status" | "contentLengthBytes" | "contentType">;
type PerformanceResource = {
  url: string;
  durationMs: number | null;
  responseEndMs: number | null;
  transferSizeBytes: number | null;
  encodedBodySizeBytes: number | null;
  decodedBodySizeBytes: number | null;
};
type RuntimeSnapshot = {
  jsHeapUsedBytes: number | null;
  jsHeapTotalBytes: number | null;
  wasmHeapUsedBytes: number | null;
  wasmHeapTotalBytes: number | null;
  canvasCssPixels: number | null;
  canvasDevicePixels: number | null;
  devicePixelRatio: number | null;
  webglRenderer: string | null;
  webglVendor: string | null;
  webglDrawCallsPerSecond: number | null;
  webglTrianglesPerSecond: number | null;
  webglTextureUploadBytes: number | null;
  webglTextureUploads: number | null;
};

function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function firstEventMs(events: BridgeEvent[], names: string[]): number | null {
  const event = events.find((item) => names.includes(item.event));
  return event ? event.atMs : null;
}

function maxPayloadBytes(events: BridgeEvent[]): number {
  let max = 0;
  for (const item of events) {
    const data = item.data && typeof item.data === "object" ? item.data as Record<string, unknown> : {};
    const bytes = Number(data.payloadBytes ?? data.maxPayloadBytes ?? 0);
    if (Number.isFinite(bytes)) max = Math.max(max, bytes);
  }
  return max;
}

function largePayloadCount(events: BridgeEvent[]): number {
  return events.filter((item) => {
    const data = item.data && typeof item.data === "object" ? item.data as Record<string, unknown> : {};
    const bytes = Number(data.payloadBytes ?? 0);
    return Number.isFinite(bytes) && bytes >= 128 * 1024;
  }).length;
}

async function drainBufferedEvents(
  page: { evaluate: <T>(fn: () => T) => Promise<T> },
  events: BridgeEvent[],
  started: number,
): Promise<void> {
  const buffered = await page.evaluate(() => {
    const win = window as Window & { __probeplannerPerfEvents?: BufferedBridgeEvent[] };
    return win.__probeplannerPerfEvents || [];
  }).catch(() => [] as BufferedBridgeEvent[]);
  const seen = new Set(events.map((item) => `${item.event}:${item.atMs}:${JSON.stringify(item.data ?? null)}`));
  for (const item of buffered) {
    const atMs = Math.max(0, Number(item.atEpochMs) - started);
    const event = { event: String(item.event || "message"), data: parseMaybeJson(item.data), atMs };
    const key = `${event.event}:${event.atMs}:${JSON.stringify(event.data ?? null)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    events.push(event);
  }
  events.sort((a, b) => a.atMs - b.atMs);
}

function finiteOrNull(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function unityResourceKind(url: string): Browser3dResourceMetric["kind"] | null {
  const lower = url.toLowerCase();
  if (!lower.includes("/unity/build/") && !lower.includes("\\unity\\build\\")) return null;
  if (lower.endsWith(".loader.js")) return "loader";
  if (lower.includes(".framework.js")) return "framework";
  if (lower.includes(".wasm")) return "wasm";
  if (lower.includes(".data")) return "data";
  return "other";
}

function measuredResourceBytes(item: Browser3dResourceMetric): number | null {
  return item.contentLengthBytes ?? item.encodedBodySizeBytes ?? item.transferSizeBytes ?? item.decodedBodySizeBytes;
}

function summarizeUnityResourceBytes(resources: Browser3dResourceMetric[]): number | null {
  let total = 0;
  let seen = false;
  for (const item of resources) {
    const bytes = measuredResourceBytes(item);
    if (bytes === null) continue;
    total += bytes;
    seen = true;
  }
  return seen ? total : null;
}

function summarizeUnityResourceMaxLoad(resources: Browser3dResourceMetric[]): number | null {
  const values = resources.map((item) => item.durationMs).filter((value): value is number => Number.isFinite(value));
  return values.length > 0 ? Math.max(...values) : null;
}

function emptyRuntimeSnapshot(): RuntimeSnapshot {
  return {
    jsHeapUsedBytes: null,
    jsHeapTotalBytes: null,
    wasmHeapUsedBytes: null,
    wasmHeapTotalBytes: null,
    canvasCssPixels: null,
    canvasDevicePixels: null,
    devicePixelRatio: null,
    webglRenderer: null,
    webglVendor: null,
    webglDrawCallsPerSecond: null,
    webglTrianglesPerSecond: null,
    webglTextureUploadBytes: null,
    webglTextureUploads: null,
  };
}

async function collectPerformanceResources(contexts: Array<{ evaluate: <T>(fn: () => T) => Promise<T> }>): Promise<PerformanceResource[]> {
  const byUrl = new Map<string, PerformanceResource>();
  for (const context of contexts) {
    const resources = await context.evaluate(() => {
      return performance.getEntriesByType("resource").map((entry) => {
        const resource = entry as PerformanceResourceTiming;
        return {
          url: resource.name,
          durationMs: Number.isFinite(resource.duration) ? resource.duration : null,
          responseEndMs: Number.isFinite(resource.responseEnd) ? resource.responseEnd : null,
          transferSizeBytes: Number.isFinite(resource.transferSize) ? resource.transferSize : null,
          encodedBodySizeBytes: Number.isFinite(resource.encodedBodySize) ? resource.encodedBodySize : null,
          decodedBodySizeBytes: Number.isFinite(resource.decodedBodySize) ? resource.decodedBodySize : null,
        };
      });
    }).catch(() => [] as PerformanceResource[]);
    for (const item of resources) {
      if (!unityResourceKind(item.url)) continue;
      const existing = byUrl.get(item.url);
      if (!existing || Number(item.responseEndMs ?? 0) > Number(existing.responseEndMs ?? 0)) {
        byUrl.set(item.url, item);
      }
    }
  }
  return [...byUrl.values()];
}

async function collectRuntimeSnapshot(frame: { evaluate: <T>(fn: () => T) => Promise<T> } | null): Promise<RuntimeSnapshot> {
  if (!frame) return emptyRuntimeSnapshot();
  return frame.evaluate(() => {
    const perf = performance as Performance & {
      memory?: {
        usedJSHeapSize?: number;
        totalJSHeapSize?: number;
      };
    };
    const canvas = document.querySelector("#unity-canvas") as HTMLCanvasElement | null;
    const rect = canvas?.getBoundingClientRect();
    let webglRenderer: string | null = null;
    let webglVendor: string | null = null;

    if (canvas) {
      const gl = (canvas.getContext("webgl2") || canvas.getContext("webgl")) as WebGLRenderingContext | WebGL2RenderingContext | null;
      if (gl) {
        const debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
        if (debugInfo) {
          webglRenderer = String(gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || "");
          webglVendor = String(gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) || "");
        } else {
          webglRenderer = String(gl.getParameter(gl.RENDERER) || "");
          webglVendor = String(gl.getParameter(gl.VENDOR) || "");
        }
      }
    }

    const win = window as Window & {
      unityInstance?: {
        GetMemoryInfo?: () => {
          totalWASMHeapSize?: number;
          usedWASMHeapSize?: number;
          totalJSHeapSize?: number;
          usedJSHeapSize?: number;
        };
      };
    };
    const unityMemory = win.unityInstance?.GetMemoryInfo?.();
    const webglStats = (window as Window & {
      __probeplannerWebglStats?: {
        startMs?: number;
        drawCalls?: number;
        triangles?: number;
        textureUploadBytes?: number;
        textureUploads?: number;
      };
    }).__probeplannerWebglStats;
    const webglElapsedSeconds = Math.max(0.001, (performance.now() - Number(webglStats?.startMs ?? performance.now())) / 1000);
    const cssPixels = rect ? Math.round(rect.width * rect.height) : null;
    const devicePixels = canvas ? Math.round(canvas.width * canvas.height) : null;

    return {
      jsHeapUsedBytes: perf.memory?.usedJSHeapSize ?? unityMemory?.usedJSHeapSize ?? null,
      jsHeapTotalBytes: perf.memory?.totalJSHeapSize ?? unityMemory?.totalJSHeapSize ?? null,
      wasmHeapUsedBytes: unityMemory?.usedWASMHeapSize ?? null,
      wasmHeapTotalBytes: unityMemory?.totalWASMHeapSize ?? null,
      canvasCssPixels: cssPixels,
      canvasDevicePixels: devicePixels,
      devicePixelRatio: Number.isFinite(window.devicePixelRatio) ? window.devicePixelRatio : null,
      webglRenderer,
      webglVendor,
      webglDrawCallsPerSecond: webglStats ? Number(webglStats.drawCalls || 0) / webglElapsedSeconds : null,
      webglTrianglesPerSecond: webglStats ? Number(webglStats.triangles || 0) / webglElapsedSeconds : null,
      webglTextureUploadBytes: webglStats ? Number(webglStats.textureUploadBytes || 0) : null,
      webglTextureUploads: webglStats ? Number(webglStats.textureUploads || 0) : null,
    };
  }).catch(() => emptyRuntimeSnapshot());
}

function buildUnityResources(resources: PerformanceResource[], responseMeta: Map<string, ResponseMeta>): Browser3dResourceMetric[] {
  return resources
    .map((item) => {
      const kind = unityResourceKind(item.url);
      if (!kind) return null;
      const meta = responseMeta.get(item.url);
      return {
        kind,
        url: item.url,
        durationMs: item.durationMs,
        responseEndMs: item.responseEndMs,
        transferSizeBytes: item.transferSizeBytes,
        encodedBodySizeBytes: item.encodedBodySizeBytes,
        decodedBodySizeBytes: item.decodedBodySizeBytes,
        contentLengthBytes: meta?.contentLengthBytes ?? null,
        status: meta?.status ?? null,
        contentType: meta?.contentType ?? null,
      };
    })
    .filter((item): item is Browser3dResourceMetric => item !== null)
    .sort((a, b) => {
      const order = { loader: 0, framework: 1, wasm: 2, data: 3, other: 4 } as const;
      return order[a.kind] - order[b.kind] || a.url.localeCompare(b.url);
    });
}

function baseMetrics(input: {
  ok: boolean;
  error?: string;
  frontendUrl: string;
  runIndex: number;
  started: number;
  events: BridgeEvent[];
  frameTimes?: number[];
  fpsValues?: number[];
  unityResources?: Browser3dResourceMetric[];
  runtimeSnapshot?: RuntimeSnapshot;
  directUnityInstanceReadyMs?: number | null;
  tracePath?: string;
}): Browser3dMetrics {
  const unityResources = input.unityResources ?? [];
  const runtimeSnapshot = input.runtimeSnapshot ?? emptyRuntimeSnapshot();
  const interactiveReadyMs = input.ok
    ? firstEventMs(input.events, ["snapshotApplied", "stateAck", "heartbeatAck"]) ??
      firstEventMs(input.events, ["capabilities", "instanceReady"])
    : firstEventMs(input.events, ["snapshotApplied", "stateAck", "heartbeatAck"]);
  return {
    ok: input.ok,
    error: input.error,
    frontendUrl: input.frontendUrl,
    runIndex: input.runIndex,
    totalLoadMs: Date.now() - input.started,
    unityInstanceReadyMs: firstEventMs(input.events, ["instanceReady"]) ?? input.directUnityInstanceReadyMs ?? null,
    bridgeReadyMs: firstEventMs(input.events, ["capabilities"]),
    interactiveReadyMs: interactiveReadyMs ?? input.directUnityInstanceReadyMs ?? null,
    allOutlinesLoadedMs: firstEventMs(input.events, ["snapshotApplied"]),
    fpsMedian: median(input.fpsValues ?? []),
    fpsP05: percentile(input.fpsValues ?? [], 5),
    frameMsP95: percentile(input.frameTimes ?? [], 95),
    bridgeLargePayloads: largePayloadCount(input.events),
    bridgeMaxPayloadBytes: maxPayloadBytes(input.events),
    unityResources,
    unityResourceTotalBytes: summarizeUnityResourceBytes(unityResources),
    unityResourceMaxLoadMs: summarizeUnityResourceMaxLoad(unityResources),
    ...runtimeSnapshot,
    tracePath: input.tracePath,
    events: input.events,
  };
}

export async function collect3dMetrics(options: {
  frontendUrl: string;
  runIndex: number;
  timeoutMs?: number;
  sampleMs?: number;
  tracePath?: string;
  headless?: boolean;
  browserChannel?: string;
}): Promise<Browser3dMetrics> {
  const timeoutMs = options.timeoutMs ?? 45_000;
  const events: BridgeEvent[] = [];
  const responseMeta = new Map<string, ResponseMeta>();
  const started = Date.now();
  let browser = null as Awaited<ReturnType<typeof chromium.launch>> | null;
  let traceStarted = false;
  try {
    browser = await chromium.launch({
      headless: options.headless ?? true,
      channel: options.browserChannel,
      args: [
        "--ignore-gpu-blocklist",
        "--enable-gpu-rasterization",
        "--enable-zero-copy",
      ],
    });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    if (options.tracePath) {
      await context.tracing.start({ screenshots: true, snapshots: true });
      traceStarted = true;
    }
    const page = await context.newPage();
    page.on("response", (response) => {
      const url = response.url();
      if (!unityResourceKind(url)) return;
      const headers = response.headers();
      responseMeta.set(url, {
        status: response.status(),
        contentLengthBytes: finiteOrNull(headers["content-length"]),
        contentType: headers["content-type"] || null,
      });
    });
    await page.exposeFunction("__probeplannerPerfRecord", (event: string, data: unknown) => {
      events.push({ event, data: parseMaybeJson(data), atMs: Date.now() - started });
    });
    await page.goto(options.frontendUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    await page.evaluate(() => {
      const bridgeWindow = window as Window & { __probeplannerPerfBridgeRuntimeInstalled?: boolean };
      if (bridgeWindow.__probeplannerPerfBridgeRuntimeInstalled) return;
      const record = (event: string, data: unknown) => {
        const win = window as unknown as {
          __probeplannerPerfEvents?: Array<{ event: string; atEpochMs: number; data?: unknown }>;
          __probeplannerPerfRecord?: (event: string, data: unknown) => void;
        };
        win.__probeplannerPerfEvents = win.__probeplannerPerfEvents || [];
        win.__probeplannerPerfEvents.push({ event, data, atEpochMs: Date.now() });
        win.__probeplannerPerfRecord?.(event, data);
      };
      window.addEventListener("message", (event) => {
        const payload = event.data;
        if (!payload || payload.source !== "BrainPlannerUnity") return;
        record(String(payload.event || "message"), payload.data);
      });
      window.addEventListener("probeplanner:unity-bridge-metrics", (event) => {
        const custom = event as CustomEvent;
        record("bridgeMetrics", custom.detail || {});
      });
      bridgeWindow.__probeplannerPerfBridgeRuntimeInstalled = true;
      record("perfListenerInstalled", { ts: Date.now() });
    }).catch(() => undefined);
    const toggle = page.locator('[data-testid="probeplanner-toggle-3d"]');
    const findUnityFrame = () =>
      page.frames().find((item) => item.url().includes("/unity/") || item.url().includes("unity/index.html")) ?? null;
    if (!findUnityFrame() && await toggle.count()) {
      await toggle.first().click({ timeout: 10_000 });
    }

    await page.waitForFunction(
      () => Boolean(document.querySelector('iframe[src*="unity"], iframe[src*="/unity/"]')),
      null,
      { timeout: Math.min(timeoutMs, 20_000) },
    ).catch(() => undefined);
    if (!findUnityFrame() && await toggle.count()) {
      await toggle.first().click({ timeout: 10_000 }).catch(() => undefined);
      await page.waitForFunction(
        () => Boolean(document.querySelector('iframe[src*="unity"], iframe[src*="/unity/"]')),
        null,
        { timeout: Math.min(timeoutMs, 10_000) },
      ).catch(() => undefined);
    }

    await page.waitForTimeout(Math.min(timeoutMs, 8_000));
    const frame = findUnityFrame();
    let directUnityInstanceReadyMs: number | null = null;
    if (frame) {
      const unityReady = await frame.waitForFunction(
        () => Boolean((window as Window & { unityInstance?: unknown }).unityInstance),
        null,
        { timeout: Math.min(timeoutMs, 15_000) },
      ).then(() => true).catch(() => false);
      if (unityReady) directUnityInstanceReadyMs = Date.now() - started;
    }
    await frame?.evaluate(() => {
      const estimateTriangles = (mode: number, count: number) => {
        if (!Number.isFinite(count) || count <= 0) return 0;
        if (mode === 4) return Math.floor(count / 3);
        if (mode === 5 || mode === 6) return Math.max(0, count - 2);
        return 0;
      };
      const estimateTextureBytes = (args: IArguments) => {
        const width = typeof args[3] === "number" ? args[3] : Number((args[5] as { width?: number } | undefined)?.width);
        const height = typeof args[4] === "number" ? args[4] : Number((args[5] as { height?: number } | undefined)?.height);
        return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0 ? width * height * 4 : 0;
      };
      const win = window as Window & {
        __probeplannerWebglStats?: {
          startMs: number;
          drawCalls: number;
          triangles: number;
          textureUploadBytes: number;
          textureUploads: number;
        };
      };
      if (!win.__probeplannerWebglStats) {
        win.__probeplannerWebglStats = {
          startMs: performance.now(),
          drawCalls: 0,
          triangles: 0,
          textureUploadBytes: 0,
          textureUploads: 0,
        };
      } else {
        win.__probeplannerWebglStats.startMs = performance.now();
        win.__probeplannerWebglStats.drawCalls = 0;
        win.__probeplannerWebglStats.triangles = 0;
        win.__probeplannerWebglStats.textureUploadBytes = 0;
        win.__probeplannerWebglStats.textureUploads = 0;
      }
      const patch = (ctor?: typeof WebGLRenderingContext | typeof WebGL2RenderingContext) => {
        const proto = ctor?.prototype as ({
          __probeplannerPatched?: boolean;
          drawArrays: (...args: unknown[]) => unknown;
          drawElements: (...args: unknown[]) => unknown;
          texImage2D: (...args: unknown[]) => unknown;
          texSubImage2D: (...args: unknown[]) => unknown;
        }) | undefined;
        if (!proto || proto.__probeplannerPatched) return;
        proto.__probeplannerPatched = true;
        const drawArrays = proto.drawArrays;
        proto.drawArrays = function patchedDrawArrays(...args: unknown[]) {
          const mode = Number(args[0]);
          const count = Number(args[2]);
          win.__probeplannerWebglStats!.drawCalls += 1;
          win.__probeplannerWebglStats!.triangles += estimateTriangles(mode, count);
          return drawArrays.apply(this, args);
        };
        const drawElements = proto.drawElements;
        proto.drawElements = function patchedDrawElements(...args: unknown[]) {
          const mode = Number(args[0]);
          const count = Number(args[1]);
          win.__probeplannerWebglStats!.drawCalls += 1;
          win.__probeplannerWebglStats!.triangles += estimateTriangles(mode, count);
          return drawElements.apply(this, args);
        };
        const texImage2D = proto.texImage2D;
        proto.texImage2D = function patchedTexImage2D(...args: unknown[]) {
          win.__probeplannerWebglStats!.textureUploads += 1;
          win.__probeplannerWebglStats!.textureUploadBytes += estimateTextureBytes(arguments);
          return texImage2D.apply(this, args);
        };
        const texSubImage2D = proto.texSubImage2D;
        proto.texSubImage2D = function patchedTexSubImage2D(...args: unknown[]) {
          win.__probeplannerWebglStats!.textureUploads += 1;
          win.__probeplannerWebglStats!.textureUploadBytes += estimateTextureBytes(arguments);
          return texSubImage2D.apply(this, args);
        };
      };
      patch(window.WebGLRenderingContext);
      patch(window.WebGL2RenderingContext);
    }).catch(() => undefined);
    const sampleMs = options.sampleMs ?? 10_000;
    const frameTimes: number[] = frame
      ? await frame.evaluate(`(async () => {
          const deltas = [];
          let last = performance.now();
          const end = last + ${JSON.stringify(sampleMs)};
          await new Promise((resolve) => {
            function step(now) {
              deltas.push(now - last);
              last = now;
              if (now >= end) resolve();
              else requestAnimationFrame(step);
            }
            requestAnimationFrame(step);
          });
          return deltas.slice(1);
        })()`) as number[]
      : [];

    const fpsValues = frameTimes
      .filter((value) => Number.isFinite(value) && value > 0)
      .map((frameMs) => 1000 / frameMs);
    const resources = await collectPerformanceResources(frame ? [page, frame] : [page]);
    const unityResources = buildUnityResources(resources, responseMeta);
    const runtimeSnapshot = await collectRuntimeSnapshot(frame);
    await drainBufferedEvents(page, events, started);

    if (traceStarted && options.tracePath) {
      await context.tracing.stop({ path: options.tracePath });
      traceStarted = false;
    }

    return baseMetrics({
      ok: true,
      frontendUrl: options.frontendUrl,
      runIndex: options.runIndex,
      started,
      events,
      frameTimes,
      fpsValues,
      unityResources,
      runtimeSnapshot,
      directUnityInstanceReadyMs,
      tracePath: options.tracePath,
    });
  } catch (err) {
    const tracePath = traceStarted && options.tracePath ? options.tracePath : undefined;
    if (browser && traceStarted && options.tracePath) {
      const pages = browser.contexts()[0];
      await pages?.tracing.stop({ path: options.tracePath }).catch(() => undefined);
      traceStarted = false;
    }
    return baseMetrics({
      ok: false,
      error: err instanceof Error ? err.message : String(err),
      frontendUrl: options.frontendUrl,
      runIndex: options.runIndex,
      started,
      events,
      tracePath,
    });
  } finally {
    await browser?.close().catch(() => undefined);
  }
}
