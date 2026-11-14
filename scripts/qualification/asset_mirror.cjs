// Private, manifest-bound asset delivery. Dynamic APIs are never cached or synthesized.
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const MAX_BYTES = 192 * 1024 * 1024;
const identityKeys = ['optimizerSha256', 'profileContractSha256'];
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const hopHeaders = new Set(['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade']);

function cleanHeaders(headers) {
  const skip = new Set([...hopHeaders, ...String(headers.connection || '').toLowerCase().split(',').map(x => x.trim())]);
  return Object.fromEntries(Object.entries(headers).filter(([key]) => !skip.has(key.toLowerCase())));
}
function localTarget(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.username || url.password)
    throw Error('Explicit loopback upstream required');
  return url;
}
function assertIdentity(runtime, expected) {
  for (const key of identityKeys) if (runtime[key] !== expected[key]) throw Error('Runtime identity differs: ' + key);
  if (!runtime.manifestEnforced || !runtime.pathConsistencyPassed) throw Error('Runtime provenance not enforced');
}
function bodyPath(entry, roots) {
  const root = path.resolve(entry.kind === 'static' ? roots.static : roots.candidates);
  const name = entry.kind === 'static' ? entry.staticRelativePath : entry.body;
  const resolved = path.resolve(root, name);
  if (!name || resolved === root || !resolved.startsWith(root + path.sep)) throw Error('Asset path escapes root');
  return resolved;
}
async function readVerified(entry, roots) {
  if (!Number.isInteger(entry.bytes) || entry.bytes < 1 || entry.bytes > MAX_BYTES) throw Error('Asset size rejected');
  const target = bodyPath(entry, roots);
  const stat = await fs.stat(target);
  if (stat.size !== entry.bytes) throw Error('Asset length differs');
  const bytes = await fs.readFile(target);
  if (bytes.length !== entry.bytes || hash(bytes) !== entry.sha256) throw Error('Asset hash differs');
  return bytes;
}

function createMirror({ upstream, entries, roots, expected, event = () => {} }) {
  const target = localTarget(upstream);
  const assets = new Map();
  for (const entry of entries) {
    if (assets.has(entry.url)) throw Error('Duplicate asset URL');
    if (!entry.url.startsWith('/') || entry.url.startsWith('//') || !['mesh', 'static'].includes(entry.kind))
      throw Error('Asset URL/kind rejected');
    bodyPath(entry, roots);
    assets.set(entry.url, entry);
  }
  let invalidReason = null, active = 0, forwardSequence = 0;
  const waiting = [];
  async function slot() {
    if (active >= 2) await new Promise(resolve => waiting.push(resolve));
    active++;
    let released = false;
    return () => { if (!released) { released = true; active--; waiting.shift()?.(); } };
  }
  function invalidate(reason) { invalidReason = reason; event({ kind: 'invalidate', reason }); }
  function forward(req, res) {
    const forwardId = ++forwardSequence;
    let destroyCause = null, upstreamErrorObserved = false;
    const observe = value => event({ forwardId, method: req.method, url: req.url, ...value });
    const headers = cleanHeaders(req.headers);
    headers.host = target.host;
    const upstreamRequest = http.request(target, { method: req.method, path: req.url, headers }, response => {
      res.writeHead(response.statusCode, cleanHeaders(response.headers));
      response.pipe(res);
      response.once('error', error => {
        upstreamErrorObserved = true;
        observe({ kind: 'upstream_response_error', error: String(error),
          cause: destroyCause || 'upstream_response' });
        res.destroy(error);
      });
      observe({ kind: 'pass_through', status: response.statusCode });
    });
    // The browser retains its independent 600-second request deadline.
    const timeout = req.method === 'POST' && req.url === '/api/optimize' ? 610000 : 60000;
    upstreamRequest.setTimeout(timeout, () => {
      destroyCause ||= 'upstream_timeout';
      upstreamRequest.destroy(Error('Upstream timeout'));
    });
    upstreamRequest.once('error', error => {
      upstreamErrorObserved = true;
      observe({ kind: 'upstream_error', error: String(error), code: error.code,
        cause: destroyCause || 'upstream' });
      if (!res.destroyed && !res.writableEnded) {
        if (!res.headersSent) res.writeHead(502, { 'content-type': 'text/plain', 'cache-control': 'no-store' });
        res.end('Upstream unavailable');
      }
    });
    function cancelFromClient(reason) {
      if (!destroyCause && !upstreamErrorObserved) destroyCause = 'downstream_' + reason;
      observe({ kind: 'downstream_cancel', reason, upstreamErrorObserved,
        cause: destroyCause || 'upstream_failure_already_observed', responseStarted: res.headersSent });
      upstreamRequest.destroy();
    }
    req.once('aborted', () => cancelFromClient('request_aborted'));
    res.once('close', () => { if (!res.writableFinished) cancelFromClient('response_closed'); });
    req.pipe(upstreamRequest);
  }
  const server = http.createServer(async (req, res) => {
    if (!req.url.startsWith('/') || req.url.startsWith('//')) { res.writeHead(400); res.end(); return; }
    const parsed = new URL(req.url, 'http://127.0.0.1');
    if (req.method === 'POST' && ['/api/mri/activate', '/api/atlas/mode'].includes(parsed.pathname))
      invalidate('Live atlas/profile mutation requested; mirror requires requalification');
    const canUseAsset = ['GET', 'HEAD'].includes(req.method) && !req.headers.range && !req.headers.authorization;
    let entry = canUseAsset ? assets.get(req.url) : null;
    // Static cache-busting queries do not change the frozen backend's file selection.
    if (!entry && canUseAsset && !parsed.pathname.startsWith('/api/')) {
      const candidate = assets.get(parsed.pathname);
      if (candidate?.kind === 'static') entry = candidate;
    }
    if (!entry || String(entry.headers['cache-control'] || '').includes('no-cache')) { forward(req, res); return; }
    if (req.headers.origin && req.headers.origin !== 'http://' + req.headers.host) { forward(req, res); return; }
    if (invalidReason) { res.writeHead(503, { 'cache-control': 'no-store' }); res.end('Asset identity invalidated'); return; }
    const release = await slot();
    res.once('close', release);
    res.once('finish', release);
    try {
      if (res.destroyed) { release(); return; }
      const bytes = await readVerified(entry, roots);
      if (invalidReason) throw Error(invalidReason);
      const headers = cleanHeaders(entry.headers);
      for (const key of ['date', 'server', 'x-request-id', 'access-control-allow-origin']) delete headers[key];
      headers['content-length'] = String(bytes.length);
      headers['x-deployment-asset-source'] = 'verified-local-mirror';
      res.writeHead(200, headers);
      res.end(req.method === 'HEAD' ? undefined : bytes);
      event({ kind: 'verified_asset', method: req.method, url: req.url, sha256: entry.sha256, bytes: bytes.length });
    } catch (error) {
      invalidate(String(error));
      if (!res.headersSent) res.writeHead(503, { 'cache-control': 'no-store' });
      res.end('Asset verification failed');
      release();
    }
  });
  return { server, invalidate, assertRuntime: runtime => {
    try { assertIdentity(runtime, expected); } catch (error) { invalidate(String(error)); throw error; }
  } };
}
module.exports = { createMirror, readVerified, bodyPath, assertIdentity, localTarget, hash };
