const UNITY_COMMAND_FLUSH_INTERVAL_MS = 80;
const UNITY_COMMAND_RETRY_DELAYS_MS = [0, 180, 520];
const UNITY_LEGACY_BRIDGE_COMPAT =
  String(process.env.REACT_APP_UNITY_LEGACY_BRIDGE_COMPAT || '')
    .trim()
    .toLowerCase() === '1';
const UNITY_NO_RETRY_METHODS = new Set([
  'SetSlice',
  'SetOpacity',
  'SetCameraAngle',
  'SetInteractionQuality',
  'SetRegionHighlight',
]);
const UNITY_FALLBACK_MAX_PAYLOAD_BYTES = 192 * 1024;
const UNITY_FALLBACK_UNSUPPORTED_METHODS = new Set([
  'SetEnvironmentLighting',
  'SetMriAppearance',
]);
const UNITY_LARGE_PAYLOAD_METHODS = new Set([
  'SetStateSnapshot',
  'SetProbes',
  'SetSelectedRegions',
  'SetContactProfile',
]);
const BRIDGE_METRIC_ABSOLUTE_KEYS = new Set([
  'lastFlushAt',
  'lastPublishedAt',
  'maxPayloadBytes',
  'lastAckLatencyMs',
]);

function estimateUnityPayloadBytes(value) {
  const text = String(value ?? '');
  if (typeof TextEncoder !== 'undefined') {
    return new TextEncoder().encode(text).length;
  }
  return text.length * 2;
}

function normalizeUnityCapabilities(raw = null) {
  const caps = raw && typeof raw === 'object' ? raw : {};
  const supportedMethods = Array.isArray(caps.supportedMethods)
    ? new Set(caps.supportedMethods.map((method) => String(method || '').trim()).filter(Boolean))
    : null;
  const unsupportedMethods = Array.isArray(caps.unsupportedMethods)
    ? new Set(caps.unsupportedMethods.map((method) => String(method || '').trim()).filter(Boolean))
    : new Set();
  if (caps.supportsSlice === false) unsupportedMethods.add('SetSlice');
  if (caps.supportsLighting === false) unsupportedMethods.add('SetEnvironmentLighting');
  const maxPayloadBytes = Number(caps.maxPayloadBytes);
  return {
    protocolVersion: Number.isFinite(Number(caps.protocolVersion)) ? Number(caps.protocolVersion) : 0,
    unityBuildVersion: String(caps.unityBuildVersion || caps.buildVersion || 'legacy'),
    maxPayloadBytes: Number.isFinite(maxPayloadBytes) && maxPayloadBytes > 0
      ? maxPayloadBytes
      : UNITY_FALLBACK_MAX_PAYLOAD_BYTES,
    supportedMethods,
    unsupportedMethods,
    supportsSlice: caps.supportsSlice !== false,
    supportsLighting: caps.supportsLighting !== false,
    supportsBulkState: caps.supportsBulkState !== false,
    supportsBinaryBuffer: Boolean(caps.supportsBinaryBuffer),
    supportsFetchPayload: Boolean(caps.supportsFetchPayload),
    largePayloadTransport: String(caps.largePayloadTransport || 'inline-json'),
    negotiated: Boolean(Array.isArray(caps.supportedMethods) && caps.supportedMethods.length > 0),
    raw: caps,
  };
}

function summarizeUnityCapabilities(caps) {
  if (!caps) return null;
  return {
    protocolVersion: caps.protocolVersion,
    unityBuildVersion: caps.unityBuildVersion,
    maxPayloadBytes: caps.maxPayloadBytes,
    supportedMethodCount: caps.supportedMethods ? caps.supportedMethods.size : 0,
    unsupportedMethods: Array.from(caps.unsupportedMethods || []),
    supportsSlice: caps.supportsSlice,
    supportsLighting: caps.supportsLighting,
    supportsBulkState: caps.supportsBulkState,
    supportsBinaryBuffer: caps.supportsBinaryBuffer,
    supportsFetchPayload: caps.supportsFetchPayload,
    largePayloadTransport: caps.largePayloadTransport,
    negotiated: caps.negotiated,
  };
}

module.exports = {
  BRIDGE_METRIC_ABSOLUTE_KEYS,
  UNITY_COMMAND_FLUSH_INTERVAL_MS,
  UNITY_COMMAND_RETRY_DELAYS_MS,
  UNITY_FALLBACK_MAX_PAYLOAD_BYTES,
  UNITY_FALLBACK_UNSUPPORTED_METHODS,
  UNITY_LARGE_PAYLOAD_METHODS,
  UNITY_LEGACY_BRIDGE_COMPAT,
  UNITY_NO_RETRY_METHODS,
  estimateUnityPayloadBytes,
  normalizeUnityCapabilities,
  summarizeUnityCapabilities,
};
