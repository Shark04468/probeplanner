import {
  BRAIN_REGIONS,
  CAT_COLORS,
  PROBE_COLORS,
  REGION_DV_SPAN_MM,
  REGION_RADII_UM,
} from "../constants/brainRegions.js";
import { ANATOMY } from "../constants/anatomy.js";
import { t } from "../constants/i18n.js";
import { useZoomPan } from "../hooks/useZoomPan.js";
import { ccfUmToViewer, normalizeProbeSegment } from './brain3d/ccfCoordinates';

const SVW = 480;
const SVH = 310;
const SAP0 = 220;
const SDV0 = 32;
const SAPSCALE = 26;
const SDVSCALE = 24;
const ML_FILTER = 0.9;

const LAMBDA_SX = SAP0 - ANATOMY.lambda_ap_mm * SAPSCALE;
const SAG_OUTLINE = "M 116,32 C 112,28 104,38 98,58 C 92,80 98,104 108,126 C 124,162 148,184 172,200 C 202,218 232,226 268,228 C 302,230 328,220 342,200 C 356,180 360,152 356,122 C 352,94 344,68 338,50 C 332,34 324,28 316,28 L 116,32 Z";
const SAG_HIPPO = "M 248,116 Q 278,124 286,146 Q 292,164 276,174 Q 260,184 244,170 Q 232,156 238,136 Z";
const SAG_THAL = "M 210,114 Q 238,108 248,128 Q 254,146 240,156 Q 222,164 208,152 Q 196,138 202,122 Z";

function toSagSVG(apAnteriorMm, dvMm) {
  return [SAP0 - apAnteriorMm * SAPSCALE, SDV0 + dvMm * SDVSCALE];
}

function sideFootprintPx(key) {
  const radiusMm = (REGION_RADII_UM[key] || 450) / 1000;
  const dvSpanMm = REGION_DV_SPAN_MM[key] || 0.55;
  const rx = Math.max(3, radiusMm * SAPSCALE * 0.7);
  const ry = Math.max(3, (dvSpanMm / 2) * SDVSCALE);
  return [rx, ry];
}

export default function BrainSideView({
  selectedRegions,
  results,
  lang,
  hoveredProbe,
  onHoverProbe,
  showUncovered,
  showAllRegions,
  sagML,
  onSagMLChange,
}) {
  const tr = (key) => t(key, lang);
  const zoom = useZoomPan(SVW, SVH);
  const trajList = results?.probes || results?.selected || [];
  const visibleByML = (reg) => Math.abs(reg.ml - sagML) <= ML_FILTER;

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", background: "#06101e", borderRadius: 10, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <div style={{ flexShrink: 0, padding: "6px 12px 4px", borderBottom: "1px solid #162d48", background: "#08121f", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div style={{ fontSize: 10.5, color: "#5a9ab8", fontWeight: 600, flexShrink: 0 }}>{tr('sagSection')}:</div>

        <div style={{ flex: "1 1 160px", display: "flex", alignItems: "center", gap: 6, minWidth: 120 }}>
          <input
            type="range"
            min={-4}
            max={4}
            step={0.1}
            value={sagML}
            onChange={(e) => onSagMLChange(+e.target.value)}
            style={{ flex: 1, accentColor: "#00d4ff", height: 3, cursor: "pointer" }}
          />
          <span style={{ color: "#00d4ff", fontWeight: 700, fontSize: 11, minWidth: 48, textAlign: "right" }}>
            {sagML > 0 ? "+" : ""}
            {sagML.toFixed(1)} mm
          </span>
        </div>

        {trajList.map((traj, i) => {
          const ml = traj.ml_mm_corrected ?? traj.ml ?? traj.ml_mm ?? 0;
          return (
            <button
              key={i}
              onClick={() => onSagMLChange(+ml.toFixed(1))}
              style={{
                padding: "2px 8px",
                background: "#0a1828",
                border: `1px solid ${PROBE_COLORS[i % PROBE_COLORS.length]}55`,
                borderRadius: 4,
                color: PROBE_COLORS[i % PROBE_COLORS.length],
                fontSize: 10,
                cursor: "pointer",
                fontWeight: 700,
                flexShrink: 0,
              }}
            >
              P{i + 1}
            </button>
          );
        })}
      </div>

      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        <svg
          width={SVW}
          height={SVH}
          viewBox={zoom.viewBox}
          ref={zoom.svgRef}
          onWheel={zoom.onWheel}
          onMouseDown={zoom.onMouseDown}
          onMouseMove={zoom.onMouseMove}
          onMouseUp={zoom.onMouseUp}
          onDoubleClick={zoom.onDoubleClick}
          style={{ ...zoom.style, display: "block", width: "100%", height: "100%" }}
        >
          <defs>
            <radialGradient id="sagGrad" cx="44%" cy="28%" r="70%">
              <stop offset="0%" stopColor="#0a2040" />
              <stop offset="100%" stopColor="#040a14" />
            </radialGradient>
            <filter id="sagGlow">
              <feGaussianBlur stdDeviation="2.5" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <clipPath id="sagClip">
              <path d={SAG_OUTLINE} />
            </clipPath>
          </defs>

          <rect width={SVW} height={SVH} fill="#060e1c" rx="6" />

          {[-5, -4, -3, -2, -1, 0, 1, 2, 3, 4].map((ap) => {
            const x = SAP0 - ap * SAPSCALE;
            if (x < 2 || x > SVW - 2) return null;
            return (
              <line key={`ap-${ap}`} x1={x} y1={6} x2={x} y2={SVH - 6} stroke={ap === 0 ? "#1a3d62" : "#0f2035"} strokeWidth={ap === 0 ? 1.5 : 0.5} strokeDasharray={ap === 0 ? "" : ap % 2 === 0 ? "4,8" : "2,12"} />
            );
          })}

          {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((dv) => {
            const y = SDV0 + dv * SDVSCALE;
            if (y > SVH - 12) return null;
            return (
              <line key={`dv-${dv}`} x1={6} y1={y} x2={SVW - 6} y2={y} stroke={dv === 0 ? "#1a3d62" : "#0f2035"} strokeWidth={dv === 0 ? 1.5 : 0.5} strokeDasharray={dv === 0 ? "" : "4,8"} />
            );
          })}

          <g clipPath="url(#sagClip)">
            <rect x={LAMBDA_SX} y={0} width={SVW - LAMBDA_SX} height={SVH} fill="#ff4a00" opacity="0.07" />
          </g>

          <path d={SAG_OUTLINE} fill="url(#sagGrad)" />
          <g clipPath="url(#sagClip)" opacity="0.16">
            <path d={SAG_HIPPO} fill="#1a4a8a" />
            <path d={SAG_THAL} fill="#1a6060" />
          </g>
          <path d={SAG_OUTLINE} fill="none" stroke="#1a4a7a" strokeWidth="1.4" />

          {showAllRegions &&
            Object.entries(BRAIN_REGIONS).map(([key, reg]) => {
              if (selectedRegions.has(key)) return null;
              if (!visibleByML(reg)) return null;
              const [x, y] = toSagSVG(-reg.ap, reg.dv);
              const [rx, ry] = sideFootprintPx(key);
              const color = CAT_COLORS[reg.category] || "#4a8ab0";
              return (
                <g key={`bg-${key}`} opacity="0.35">
                  <ellipse cx={x} cy={y} rx={rx} ry={ry} fill={color} opacity="0.12" />
                  <ellipse cx={x} cy={y} rx={Math.max(2, rx - 0.8)} ry={Math.max(2, ry - 0.8)} fill="none" stroke={color} strokeWidth="0.8" opacity="0.35" />
                  <circle cx={x} cy={y} r={2.5} fill={color} opacity="0.55" />
                </g>
              );
            })}

          {[...selectedRegions].map((key) => {
            const reg = BRAIN_REGIONS[key];
            if (!reg || !visibleByML(reg)) return null;
            const [x, y] = toSagSVG(-reg.ap, reg.dv);
            const [rx, ry] = sideFootprintPx(key);
            const isCovered = trajList.some((traj) => (traj.coveredRegions || traj.regionsHit || []).includes(key));
            const ring = isCovered ? "#34d399" : "#00d4ff";
            const fill = isCovered ? "#052015" : "#0a2238";
            const tag = isCovered ? "S✓" : "S";

            return (
              <g key={`sel-${key}`}>
                <ellipse cx={x} cy={y} rx={rx} ry={ry} fill={fill} opacity="0.45" />
                <ellipse cx={x} cy={y} rx={rx + 1.5} ry={ry + 1.2} fill="none" stroke={ring} strokeWidth="1.2" />
                <circle cx={x} cy={y} r={6} fill={fill} stroke={ring} strokeWidth="1.7" />
                <text x={x + 9} y={y + 4} fill={ring} fontSize="10.5" fontFamily="monospace" fontWeight="700">
                  {key} {tag}
                </text>
              </g>
            );
          })}

          {showUncovered &&
            results &&
            (results.uncovered || []).map((key) => {
              const reg = BRAIN_REGIONS[key];
              if (!reg || !visibleByML(reg)) return null;
              const [x, y] = toSagSVG(-reg.ap, reg.dv);
              return <circle key={`unc-${key}`} cx={x} cy={y} r={8} fill="none" stroke="#c03030" strokeWidth="1.5" strokeDasharray="2,2" />;
            })}

          {trajList.map((traj, idx) => {
            const color = PROBE_COLORS[idx % PROBE_COLORS.length];
            const isHover = hoveredProbe === idx;
            const hasEndpoints = [traj.entry_xyz, traj.tip_xyz]
              .every(point => Array.isArray(point) && point.length === 3 && point.every(Number.isFinite));
            const segment = hasEndpoints
              ? { entry: ccfUmToViewer(traj.entry_xyz), tip: ccfUmToViewer(traj.tip_xyz) }
              : normalizeProbeSegment({ ...traj, ap_mm: traj.ap_mm ?? traj.ap ?? 0,
                ml_mm: traj.ml_mm ?? traj.ml ?? 0, entryDvCorr_mm: traj.entryDvCorr_mm ?? traj.dvEntry ?? 0,
                theta_deg: traj.theta_deg ?? (traj.elevation != null ? 90 - traj.elevation : 0),
                phi_deg: traj.phi_deg ?? traj.azimuth ?? 0,
                depthUm: traj.depthUm ?? (traj.depth != null ? traj.depth * 1000 : 10000) });
            const ml = segment.entry.x;
            const [x1, y1] = toSagSVG(segment.entry.z, -segment.entry.y);
            const [x2, y2] = toSagSVG(segment.tip.z, -segment.tip.y);

            const mlDist = Math.abs(ml - sagML);
            const opacity = mlDist < ML_FILTER ? 1 : Math.max(0.15, 1 - mlDist * 0.4);

            return (
              <g key={`probe-${idx}`} opacity={isHover ? 1 : opacity} onMouseEnter={() => onHoverProbe(idx)} onMouseLeave={() => onHoverProbe(null)}>
                {isHover && <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth="12" opacity="0.15" strokeLinecap="round" filter="url(#sagGlow)" />}
                <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={isHover ? 2.5 : 2} opacity={isHover ? 1 : 0.85} strokeLinecap="round" />
                <circle cx={x1} cy={y1} r={isHover ? 7 : 5} fill={color} opacity="0.95" />
                <circle cx={x2} cy={y2} r={isHover ? 4 : 3} fill={color} opacity="0.65" />
              </g>
            );
          })}

          <line x1={LAMBDA_SX} y1={6} x2={LAMBDA_SX} y2={SVH - 6} stroke="#ff8c42" strokeWidth="1.5" strokeDasharray="8,5" opacity="0.75" />
          <text x={12} y={18} fill="#4a8ab0" fontSize="11" fontFamily="monospace" fontWeight="600">{tr('sideView')}</text>
        </svg>
      </div>
    </div>
  );
}
