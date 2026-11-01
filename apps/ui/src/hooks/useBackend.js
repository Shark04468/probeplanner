/**
 * useBackend hook for managing Flask backend connection state.
 * Connects to the configured backend or the application's HTTP origin.
 * Optimizer execution requires an online backend; this hook only reports
 * connection state and never starts an in-browser planning fallback.
 */

import { useState, useCallback, useRef, useEffect } from "react";

const DEFAULT_BASE = "http://localhost:5000";
const BASE_STORAGE_KEY = "probePlannerBackendBase";
const ATLAS_MODE_STORAGE_KEY = "probePlannerAtlasMode";
const DEFAULT_ATLAS_MODE = "allen_sdk";
const ALLOWED_ATLAS_MODES = new Set(["allen_sdk", "qiu2018", "mri_ccf_hybrid"]);

// baseUrlRef is module-level so flaskFetch can always read the current value
// without needing to be recreated every time the URL changes.
let _currentBase = DEFAULT_BASE;
let _currentToken = null;

function safeStorageGet(key) {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSet(key, value) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ignore storage failures (private mode / restricted webview / quota)
  }
}

function resolveToken(next = null) {
  if (typeof next === "string") return next.trim();
  return "";
}

export function resolveBase(next = null) {
  const normalize = (v) => String(v || "").trim().replace(/\/+$/, "");
  if (typeof next === "string" && normalize(next)) return normalize(next);
  if (typeof window !== "undefined") {
    const fromStorage = safeStorageGet(BASE_STORAGE_KEY);
    if (fromStorage && normalize(fromStorage)) return normalize(fromStorage);
  }
  if (typeof process !== "undefined") {
    const fromEnv = normalize(process.env.REACT_APP_FLASK_BASE || "");
    if (fromEnv) return fromEnv;
  }
  if (typeof window !== 'undefined' && /^https?:$/.test(window.location.protocol)) {
    return normalize(window.location.origin);
  }
  return DEFAULT_BASE;
}

export function buildBaseCandidates(base) {
  const primary = resolveBase(base);
  const out = [];
  const push = (v) => {
    const clean = resolveBase(v);
    if (!clean) return;
    if (!out.includes(clean)) out.push(clean);
  };
  push(primary);
  try {
    const u = new URL(primary);
    if (u.hostname === "localhost") {
      u.hostname = "127.0.0.1";
      push(u.toString());
    } else if (u.hostname === "127.0.0.1") {
      u.hostname = "localhost";
      push(u.toString());
    }
    if (typeof window !== 'undefined' && primary === window.location.origin
        && ['localhost', '127.0.0.1'].includes(u.hostname)) {
      push(DEFAULT_BASE);
      push('http://127.0.0.1:5000');
    }
  } catch {
    // Ignore malformed custom URL; primary candidate is still used.
  }
  return out.length ? out : [DEFAULT_BASE];
}

function resolveAtlasMode(next = null) {
  const normalize = (v) => {
    const raw = String(v || "").trim().toLowerCase();
    if (!raw) return "";
    if (raw === "allen" || raw === "allensdk" || raw === "allen-ccfv3") return "allen_sdk";
    if (raw === "qiu" || raw === "qiu-2018" || raw === "qiu_2018") return "qiu2018";
    if (raw === "mri" || raw === "mri_ccf" || raw === "mri-ccf-hybrid" || raw === "hybrid") {
      return "mri_ccf_hybrid";
    }
    return raw;
  };
  if (typeof next === "string") {
    const n = normalize(next);
    if (ALLOWED_ATLAS_MODES.has(n)) return n;
  }
  if (typeof window !== "undefined") {
    const fromStorage = normalize(safeStorageGet(ATLAS_MODE_STORAGE_KEY));
    if (ALLOWED_ATLAS_MODES.has(fromStorage)) return fromStorage;
  }
  if (typeof process !== "undefined") {
    const fromEnv = normalize(process.env.REACT_APP_ATLAS_MODE || "");
    if (ALLOWED_ATLAS_MODES.has(fromEnv)) return fromEnv;
  }
  return DEFAULT_ATLAS_MODE;
}

function extractAtlasError(data) {
  if (!data || typeof data !== "object") return "";
  const direct = String(data.atlasError || "").trim();
  if (direct) return direct;
  const load = data.atlasLoad;
  if (load && typeof load === "object") {
    const state = String(load.state || "").toLowerCase();
    if (state === "error") return String(load.message || "Atlas load failed").trim();
  }
  return "";
}

function buildStatusPath(atlasMode, regionScope = "all", includeRegions = true) {
  const query = new URLSearchParams({ regionScope: String(regionScope || 'all').trim() || 'all',
    includeRegions: includeRegions ? '1' : '0' });
  if (atlasMode != null) query.set('atlasMode', resolveAtlasMode(atlasMode));
  return `/api/status?${query}`;
}

export function normalizeRegionRecord(item) {
  if (!item || typeof item !== "object") return null;
  const key = String(item.key || item.acronym || "").trim();
  if (!key) return null;
  const structureId = Number(item.structureId);
  const ap = Number(item.ap);
  const dv = Number(item.dv);
  const ml = Number(item.ml);
  const groupKey = String(item.groupKey || "").trim();
  const groupName = String(item.groupName || "").trim();
  const groupNameCn = String(item.groupNameCn || "").trim();
  return {
    structureId: Number.isFinite(structureId) ? structureId : null,
    acronym: String(item.acronym || key),
    name: String(item.name || key),
    voxel_count: Number.isFinite(Number(item.voxelCount))
      ? Number(item.voxelCount)
      : Number.isFinite(Number(item.voxel_count))
        ? Number(item.voxel_count)
        : 0,
    volume_mm3: Number.isFinite(Number(item.volumeMm3))
      ? Number(item.volumeMm3)
      : Number.isFinite(Number(item.volume_mm3))
        ? Number(item.volume_mm3)
        : 0,
    n_subregions: Number.isFinite(Number(item.nSubregions))
      ? Number(item.nSubregions)
      : Number.isFinite(Number(item.n_subregions))
        ? Number(item.n_subregions)
        : 0,
    ap: Number.isFinite(ap) ? ap : 0,
    dv: Number.isFinite(dv) ? dv : 0,
    ml: Number.isFinite(ml) ? ml : 0,
    category: String(item.category || "dynamic"),
    groupKey,
    groupName,
    groupNameCn,
    plannerReady: Boolean(item.plannerReady),
    presentInVolume: typeof item.presentInVolume === 'boolean' ? item.presentInVolume : undefined,
    structureIdPath: Array.isArray(item.structureIdPath) ? item.structureIdPath.map(Number) : [],
  };
}

function toRegionCatalogMap(payload) {
  const list = Array.isArray(payload?.regions)
    ? payload.regions
    : Array.isArray(payload)
      ? payload
      : [];
  const map = {};
  for (const item of list) {
    const normalized = normalizeRegionRecord(item);
    if (!normalized) continue;
    map[normalized.acronym] = normalized;
  }
  return map;
}

async function fetchRegionCatalog(base) {
  const data = await flaskFetch("/api/regions?scope=all", "GET", null, base, {
    timeoutMs: 30000,
  });
  return toRegionCatalogMap(data);
}

async function fetchMriRegistrationSummary(base) {
  try {
    const data = await flaskFetch("/api/mri/registration/summary", "GET", null, base, {
      timeoutMs: 15000,
    });
    if (data && typeof data === "object" && data.ok) return data;
  } catch {
    // keep status polling resilient; registration summary is optional.
  }
  return null;
}

async function fetchMriOverlayStatus(base) {
  try {
    const data = await flaskFetch("/api/mri/overlay/status", "GET", null, base, {
      timeoutMs: 15000,
    });
    if (data && typeof data === "object" && data.ok) return data;
  } catch {
    // keep status polling resilient; overlay readiness is optional.
  }
  return null;
}

async function fetchMriSurfaceMeshStatus(base) {
  try {
    const data = await flaskFetch("/api/mri_surface_meshes/status", "GET", null, base, {
      timeoutMs: 15000,
    });
    if (data && typeof data === "object" && data.ok) return data;
  } catch {
    // keep status polling resilient; MRI surface meshes are optional visualization assets.
  }
  return null;
}

async function enrichStatusPayload(payload, base, atlasMode) {
  const resolved = { ...(payload || {}) };
  const mode = resolveAtlasMode(atlasMode || resolved?.activeAtlasMode || DEFAULT_ATLAS_MODE);
  if (mode === "mri_ccf_hybrid") {
    const [reg, overlay, surfaces] = await Promise.all([
      fetchMriRegistrationSummary(base),
      fetchMriOverlayStatus(base),
      fetchMriSurfaceMeshStatus(base),
    ]);
    if (reg && typeof reg === "object") {
      resolved.mriRegistrationSummary = reg;
    }
    if (overlay && typeof overlay === "object") {
      resolved.mriOverlayStatus = overlay;
    }
    if (surfaces && typeof surfaces === "object") {
      resolved.mriSurfaceMeshes = surfaces.mriSurfaceMeshes || null;
    }
  }
  return resolved;
}

_currentBase = resolveBase(_currentBase);

export function setBackendAuthToken(token) {
  const clean = resolveToken(token);
  _currentToken = clean || null;
}

function createTimeoutSignal(timeoutMs, externalSignal = null) {
  if (externalSignal) return { signal: externalSignal, cancel: () => {} };
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { signal: AbortSignal.timeout(timeoutMs), cancel: () => {} };
  }
  if (typeof AbortController !== "undefined") {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      try {
        controller.abort(new DOMException("Timeout", "TimeoutError"));
      } catch {
        controller.abort();
      }
    }, timeoutMs);
    return {
      signal: controller.signal,
      cancel: () => clearTimeout(timer),
    };
  }
  return { signal: undefined, cancel: () => {} };
}

export async function flaskFetch(
  path,
  method = "GET",
  body = null,
  baseOverride = null,
  requestOptions = null
) {
  const base = baseOverride || _currentBase;
  const token = _currentToken || resolveToken();
  const timeoutMsRaw = requestOptions?.timeoutMs;
  const timeoutMs = Number.isFinite(Number(timeoutMsRaw))
    ? Math.max(1000, Number(timeoutMsRaw))
    : 60000;
  const timeoutBundle = createTimeoutSignal(timeoutMs, requestOptions?.signal || null);
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
    signal: timeoutBundle.signal,
  };
  if (token) {
    opts.headers.Authorization = `Bearer ${token}`;
  }
  if (body) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(base + path, opts);
    if (!res.ok) {
      const raw = await res.text();
      let parsed = null;
      try {
        parsed = JSON.parse(raw);
      } catch {
        // fall back to raw HTTP payload
      }
      if (parsed?.error) {
        const details = Array.isArray(parsed?.details) ? parsed.details.filter(Boolean) : [];
        const detailMsg = details.length ? ` (${details.join("; ")})` : "";
        const err = new Error(`${parsed.error}${detailMsg}`);
        err.payload = parsed;
        err.status = res.status;
        err.code = parsed.code || null;
        err.details = details;
        throw err;
      }
      const err = new Error(`HTTP ${res.status}: ${raw}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  } finally {
    timeoutBundle.cancel();
  }
}

export function useBackend() {
  const [status,   setStatus]   = useState("idle");   // idle | connecting | loading | online | offline
  const [atlasInfo, setAtlasInfo] = useState(null);
  const [error,    setError]    = useState(null);
  const [baseUrl,  setBaseUrl]  = useState(() => resolveBase());
  const [authToken, setAuthTokenState] = useState(() => resolveToken());
  const [atlasMode, setAtlasModeState] = useState(() => resolveAtlasMode());
  const pollRef                 = useRef(null);
  const pollFailureCountRef = useRef(0);
  const detailRefreshSeqRef = useRef(0);
  const autoConnectStartedRef = useRef(false);
  const connectionSeqRef = useRef(0);

  // Keep module-level ref in sync so flaskFetch (called from useOptimizer) also uses the right URL
  const baseRef = useRef(resolveBase());
  const tokenRef = useRef(resolveToken());
  const atlasModeRef = useRef(resolveAtlasMode());
  const updateBase = (url) => {
    const clean = resolveBase(url);
    baseRef.current = clean;
    _currentBase    = clean;
    setBaseUrl(clean);
    if (typeof window !== "undefined") {
      safeStorageSet(BASE_STORAGE_KEY, clean);
    }
  };
  const updateToken = (token) => {
    const clean = resolveToken(token);
    tokenRef.current = clean;
    setBackendAuthToken(clean);
    setAuthTokenState(clean);
  };
  const updateAtlasMode = (mode) => {
    const clean = resolveAtlasMode(mode);
    atlasModeRef.current = clean;
    setAtlasModeState(clean);
    if (typeof window !== "undefined") {
      safeStorageSet(ATLAS_MODE_STORAGE_KEY, clean);
    }
  };

  const publishOnlineStatus = useCallback((payload, candidateBase, activeMode) => {
    const basePayload = { ...(payload || {}) };
    const refreshSeq = ++detailRefreshSeqRef.current;
    setStatus("online");
    setAtlasInfo(basePayload);
    setError(null);
    const isCurrent = () => detailRefreshSeqRef.current === refreshSeq
      && resolveBase(baseRef.current) === resolveBase(candidateBase);
    // Region selection must not wait for optional, potentially slow MRI summaries.
    fetchRegionCatalog(candidateBase).then(regions => {
      if (!isCurrent()) return;
      if (regions && Object.keys(regions).length > 0) {
        setAtlasInfo(previous => ({ ...previous, regions,
          regionCount: Object.keys(regions).length, availableRegionCount: Object.keys(regions).length }));
      }
    }).catch(() => {});
    enrichStatusPayload(basePayload, candidateBase, activeMode).then(enriched => {
      if (!isCurrent()) return;
      setAtlasInfo(previous => ({ ...enriched, ...(previous?.regions ? {
        regions: previous.regions, regionCount: previous.regionCount,
        availableRegionCount: previous.availableRegionCount,
      } : {}) }));
    }).catch(() => {
      // Optional MRI enrichment should not turn a healthy backend offline.
    });
  }, []);

  const connect = useCallback(async (urlOrConfig) => {
    const connectionSeq = ++connectionSeqRef.current;
    const isCurrentConnection = () => connectionSeq === connectionSeqRef.current;
    detailRefreshSeqRef.current += 1;
    const url = typeof urlOrConfig === "string" ? urlOrConfig : urlOrConfig?.url;
    const token = typeof urlOrConfig === "string" ? null : urlOrConfig?.token;
    const mode = typeof urlOrConfig === "string" ? null : urlOrConfig?.atlasMode;
    const requestedBase = resolveBase(url || baseRef.current);
    const candidateBases = buildBaseCandidates(requestedBase);
    updateBase(requestedBase);
    if (token != null) updateToken(token);
    let activeMode = resolveAtlasMode(mode ?? atlasModeRef.current);
    setStatus("connecting");
    setError(null);
    clearInterval(pollRef.current);
    let lastError = null;
    try {
      for (const candidateBase of candidateBases) {
        updateBase(candidateBase);
        try {
          let data = await flaskFetch(
            buildStatusPath(null, "planner", false),
            "GET",
            null,
            candidateBase
          );
          if (!isCurrentConnection()) return;
          activeMode = resolveAtlasMode(data.activeAtlasMode || atlasModeRef.current);
          updateAtlasMode(activeMode);
          // Discover first; only an explicit user request can switch server data.
          if (mode != null && resolveAtlasMode(mode) !== activeMode) {
            try {
              await flaskFetch('/api/atlas/mode', 'POST', { mode: resolveAtlasMode(mode) }, candidateBase,
                { timeoutMs: 12000 });
            } catch (modeError) {
              if (!isCurrentConnection()) return;
              if (data.atlasLoaded) publishOnlineStatus(data, candidateBase, activeMode);
              else { setStatus('offline'); setAtlasInfo(data); }
              setError(modeError.message);
              return;
            }
            if (!isCurrentConnection()) return;
            activeMode = resolveAtlasMode(mode);
            data = await flaskFetch(buildStatusPath(activeMode, 'planner', false), 'GET', null, candidateBase);
            if (!isCurrentConnection()) return;
            activeMode = resolveAtlasMode(data.activeAtlasMode || activeMode);
            updateAtlasMode(activeMode);
          }
          pollFailureCountRef.current = 0;
          const atlasErr = extractAtlasError(data);
          if (data.atlasLoaded) {
            publishOnlineStatus(data, candidateBase, activeMode);
            clearInterval(pollRef.current);
            return;
          }
          const loadingInfo = await enrichStatusPayload(data, candidateBase, activeMode);
          if (!isCurrentConnection()) return;
          setAtlasInfo(loadingInfo);
          if (atlasErr) {
            setStatus("offline");
            setError(atlasErr);
            clearInterval(pollRef.current);
            return;
          }
          setStatus("loading");
          setError(null);
          clearInterval(pollRef.current);
          // Poll until atlas ready
          pollRef.current = setInterval(async () => {
            try {
              const d2 = await flaskFetch(
                buildStatusPath(activeMode, "planner", false),
                "GET",
                null,
                candidateBase
              );
              if (!isCurrentConnection()) return;
              pollFailureCountRef.current = 0;
              const atlasErr2 = extractAtlasError(d2);
              const d2Resolved = await enrichStatusPayload(d2, candidateBase, activeMode);
              if (!isCurrentConnection()) return;
              setAtlasInfo(d2Resolved);
              if (d2.atlasLoaded) {
                clearInterval(pollRef.current);
                publishOnlineStatus(d2Resolved, candidateBase, activeMode);
                return;
              }
              if (atlasErr2) {
                clearInterval(pollRef.current);
                setStatus("offline");
                setError(atlasErr2);
                return;
              }
            } catch {
              if (!isCurrentConnection()) return;
              pollFailureCountRef.current += 1;
              if (pollFailureCountRef.current < 4) {
                // Transient poll hiccup: keep current state to avoid false offline flashes.
                return;
              }
              clearInterval(pollRef.current);
              setStatus("offline");
              setError(`Cannot reach ${candidateBase}`);
            }
          }, 4000);
          return;
        } catch (err) {
          if (!isCurrentConnection()) return;
          lastError = err;
        }
      }
    } catch (e) {
      lastError = e;
    }
    setStatus("offline");
    setError(lastError?.message || `Cannot reach ${candidateBases.join(" / ")}`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publishOnlineStatus]);

  const disconnect = useCallback(() => {
    connectionSeqRef.current += 1;
    detailRefreshSeqRef.current += 1;
    clearInterval(pollRef.current);
    setStatus("idle");
    setAtlasInfo(null);
    setError(null);
  }, []);

  useEffect(() => {
    setBackendAuthToken(tokenRef.current);
  }, []);

  useEffect(() => {
    if (autoConnectStartedRef.current) return;
    autoConnectStartedRef.current = true;
    connect({
      url: baseRef.current,
      token: tokenRef.current || undefined,
    });
  }, [connect]);

  useEffect(() => () => clearInterval(pollRef.current), []);

  return {
    status,
    atlasInfo,
    error,
    baseUrl,
    authToken,
    atlasMode,
    connect,
    disconnect,
    setAuthToken: updateToken,
    setAtlasMode: updateAtlasMode,
  };
}
