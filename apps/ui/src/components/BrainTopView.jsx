import { BRAIN_REGIONS, CAT_COLORS, PROBE_COLORS, REGION_RADII_UM } from "../constants/brainRegions.js";
import { ANATOMY } from "../constants/anatomy.js";
import { t } from "../constants/i18n.js";
import { useZoomPan } from "../hooks/useZoomPan.js";

const BVW = 560;
const BVH = 430;
const BX0 = 280;
const BY0 = 165;
const BSCALE = 25;

const BRAIN_TOP = "M 280,42 C 330,44 370,74 372,122 C 374,170 356,210 328,246 C 302,280 284,298 280,308 C 276,298 258,280 232,246 C 204,210 186,170 188,122 C 190,74 230,44 280,42 Z";
const BRAIN_R = "M 280,42 C 330,44 370,74 372,122 C 374,170 356,210 328,246 C 302,280 284,298 280,308 Z";
const BRAIN_L = "M 280,42 C 230,44 190,74 188,122 C 186,170 204,210 232,246 C 258,280 276,298 280,308 Z";

function toTopSVG(apAnteriorMm, mlMm) {
  return [BX0 + mlMm * BSCALE, BY0 - apAnteriorMm * BSCALE];
}

function topFootprintPx(regionKey) {
  const radiusMm = (REGION_RADII_UM[regionKey] || 450) / 1000;
  const rx = Math.max(3.5, radiusMm * BSCALE * 0.9);
  const ry = Math.max(3.0, radiusMm * BSCALE * 0.65);
  return [rx, ry];
}

const LAMBDA_Y = BY0 + Math.abs(ANATOMY.lambda_ap_mm) * BSCALE;

export default function BrainTopView({
  selectedRegions,
  results,
  lang,
  hoveredProbe,
  onHoverProbe,
  showUncovered,
  showAllRegions,
}) {
  const tr = (key) => t(key, lang);
  const zoom = useZoomPan(BVW, BVH);
  const trajList = results?.probes || results?.selected || [];

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", background: "#030810", borderRadius: 10, overflow: "hidden" }}>
      <div style={{ position: "absolute", top: 8, right: 8, zIndex: 10, display: "flex", gap: 4 }}>
        {[["+", -1], ["-", 1], ["R", 0]].map(([label, delta]) => (
          <button
            key={label}
            onClick={
              delta === 0
                ? zoom.reset
                : () => zoom.onWheel({ preventDefault: () => {}, deltaY: delta, clientX: BX0, clientY: BY0 })
            }
            style={{
              width: 26,
              height: 26,
              background: "#04182a",
              border: "1px solid #0d3a5a",
              borderRadius: 4,
              color: "#00d4ff",
              fontSize: 14,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <svg
        width={BVW}
        height={BVH}
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
          <radialGradient id="bgL" cx="35%" cy="35%" r="65%">
            <stop offset="0%" stopColor="#0d2a4a" />
            <stop offset="100%" stopColor="#060e1a" />
          </radialGradient>
          <radialGradient id="bgR" cx="65%" cy="35%" r="65%">
            <stop offset="0%" stopColor="#0d2a4a" />
            <stop offset="100%" stopColor="#060e1a" />
          </radialGradient>
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <clipPath id="brainClipT">
            <path d={BRAIN_TOP} />
          </clipPath>
        </defs>

        <rect width={BVW} height={BVH} fill="#060e1c" rx="8" />

        {[-8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8].map((ml) => {
          const x = BX0 + ml * BSCALE;
          return (
            <line
              key={`ml-${ml}`}
              x1={x}
              y1={4}
              x2={x}
              y2={BVH - 4}
              stroke={ml === 0 ? "#1a3d62" : "#0f2035"}
              strokeWidth={ml === 0 ? 1.5 : 0.5}
              strokeDasharray={ml === 0 ? "" : ml % 2 === 0 ? "4,8" : "2,12"}
            />
          );
        })}

        {[-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6].map((ap) => {
          const y = BY0 - ap * BSCALE;
          return (
            <line
              key={`ap-${ap}`}
              x1={4}
              y1={y}
              x2={BVW - 4}
              y2={y}
              stroke={ap === 0 ? "#1a3d62" : "#0f2035"}
              strokeWidth={ap === 0 ? 1.5 : 0.5}
              strokeDasharray={ap === 0 ? "" : ap % 2 === 0 ? "4,8" : "2,12"}
            />
          );
        })}

        <g clipPath="url(#brainClipT)">
          <rect x={0} y={LAMBDA_Y} width={BVW} height={BVH - LAMBDA_Y} fill="#ff4a00" opacity="0.07" />
        </g>

        <path d={BRAIN_L} fill="url(#bgL)" />
        <path d={BRAIN_R} fill="url(#bgR)" />
        <path d={BRAIN_TOP} fill="none" stroke="#1a4a7a" strokeWidth="1.5" />
        <line x1={280} y1={38} x2={280} y2={312} stroke="#0d2f5a" strokeWidth="1" strokeDasharray="3,5" />

        <line x1={4} y1={LAMBDA_Y} x2={BVW - 4} y2={LAMBDA_Y} stroke="#ff8c42" strokeWidth="1.5" strokeDasharray="8,5" opacity="0.75" />

        {showAllRegions &&
          Object.entries(BRAIN_REGIONS).map(([key, reg]) => {
            if (selectedRegions.has(key)) return null;
            const [x, y] = toTopSVG(-reg.ap, reg.ml);
            const [rx, ry] = topFootprintPx(key);
            const color = CAT_COLORS[reg.category] || "#4a8ab0";
            return (
              <g key={`bg-${key}`} opacity="0.35">
                <ellipse cx={x} cy={y} rx={rx} ry={ry} fill={color} opacity="0.12" />
                <ellipse cx={x} cy={y} rx={Math.max(2.5, rx - 1)} ry={Math.max(2, ry - 1)} fill="none" stroke={color} strokeWidth="0.8" opacity="0.35" />
                <circle cx={x} cy={y} r={2.5} fill={color} opacity="0.55" />
                <text x={x + 5} y={y + 3.5} fill={color} fontSize="8.5" fontFamily="monospace" opacity="0.7">
                  {key}
                </text>
              </g>
            );
          })}

        {[...selectedRegions].map((key) => {
          const reg = BRAIN_REGIONS[key];
          if (!reg) return null;
          const [x, y] = toTopSVG(-reg.ap, reg.ml);
          const [rx, ry] = topFootprintPx(key);
          const isCovered = trajList.some((traj) => (traj.coveredRegions || traj.regionsHit || []).includes(key));
          const ring = isCovered ? "#34d399" : "#00d4ff";
          const fill = isCovered ? "#052015" : "#0a2238";
          const tag = isCovered ? "S✓" : "S";

          return (
            <g key={`sel-${key}`}>
              <ellipse cx={x} cy={y} rx={rx} ry={ry} fill={fill} opacity="0.42" />
              <ellipse cx={x} cy={y} rx={rx + 1.8} ry={ry + 1.5} fill="none" stroke={ring} strokeWidth="1.3" />
              <circle cx={x} cy={y} r={6.5} fill={fill} stroke={ring} strokeWidth="1.8" />
              <text x={x + 10} y={y + 4} fill={ring} fontSize="11" fontFamily="monospace" fontWeight="700">
                {key} {tag}
              </text>
            </g>
          );
        })}

        {showUncovered &&
          results &&
          (results.uncovered || []).map((key) => {
            const reg = BRAIN_REGIONS[key];
            if (!reg) return null;
            const [x, y] = toTopSVG(-reg.ap, reg.ml);
            return (
              <g key={`unc-${key}`}>
                <circle cx={x} cy={y} r={8} fill="none" stroke="#c03030" strokeWidth="1.6" strokeDasharray="2,2" />
              </g>
            );
          })}

        {trajList.map((traj, idx) => {
          const color = PROBE_COLORS[idx % PROBE_COLORS.length];
          const isHovered = hoveredProbe === idx;
          const ap = traj.ap ?? traj.ap_mm ?? 0;
          const ml = traj.ml ?? traj.ml_mm ?? 0;
          const [ex, ey] = toTopSVG(ap, ml);
          const dir = traj.dir ?? [0, 1, 0];
          const depthUm = traj.depthUm ?? (traj.depth ? traj.depth * 1000 : 10000);
          const tipAP = ap + dir[0] * depthUm / 1000;
          const tipML = ml + dir[2] * depthUm / 1000;
          const [tx, ty] = toTopSVG(tipAP, tipML);
          const covered = traj.coveredRegions || traj.regionsHit || [];

          return (
            <g key={`probe-${idx}`} onMouseEnter={() => onHoverProbe(idx)} onMouseLeave={() => onHoverProbe(null)}>
              {isHovered && <line x1={ex} y1={ey} x2={tx} y2={ty} stroke={color} strokeWidth="16" opacity="0.1" strokeLinecap="round" filter="url(#glow)" />}
              <line x1={ex} y1={ey} x2={tx} y2={ty} stroke={color} strokeWidth={isHovered ? 2.5 : 1.8} opacity={isHovered ? 1 : 0.8} strokeLinecap="round" strokeDasharray="6,3" />

              {covered.map((k) => {
                const r = BRAIN_REGIONS[k];
                if (!r) return null;
                const [rx, ry] = toTopSVG(-r.ap, r.ml);
                return (
                  <g key={`cov-${idx}-${k}`} opacity={isHovered || hoveredProbe === null ? 1 : 0.3}>
                    <circle cx={rx} cy={ry} r={isHovered ? 7 : 5.5} fill={color} opacity="0.9" filter={isHovered ? "url(#glow)" : undefined} />
                  </g>
                );
              })}

              <circle cx={ex} cy={ey} r={isHovered ? 10 : 7} fill={color} opacity={isHovered ? 1 : 0.95} filter={isHovered ? "url(#glow)" : undefined} />
              <circle cx={ex} cy={ey} r={isHovered ? 5.5 : 3.5} fill="#030810" />
              <text x={ex + 13} y={ey - 8} fill={color} fontSize="12" fontWeight="700" fontFamily="monospace">
                P{idx + 1}
              </text>
            </g>
          );
        })}

        <g>
          <circle cx={280} cy={BY0} r={5} fill="none" stroke="#4a8ab8" strokeWidth="1.8" />
          <line x1={273} y1={BY0} x2={287} y2={BY0} stroke="#4a8ab8" strokeWidth="1.8" />
          <line x1={280} y1={BY0 - 5} x2={280} y2={BY0 + 5} stroke="#4a8ab8" strokeWidth="1.8" />
        </g>

        <text x={BVW - 16} y={BY0 + 4} fill="#3a6888" fontSize="11" fontFamily="monospace" textAnchor="middle">R</text>
        <text x={16} y={BY0 + 4} fill="#3a6888" fontSize="11" fontFamily="monospace" textAnchor="middle">L</text>
        <text x={280} y={16} fill="#3a6888" fontSize="11" fontFamily="monospace" textAnchor="middle">+AP</text>
        <text x={280} y={BVH - 8} fill="#3a6888" fontSize="11" fontFamily="monospace" textAnchor="middle">-AP</text>

        <text x={12} y={22} fill="#4a8ab0" fontSize="11" fontFamily="monospace" fontWeight="600">{tr('topView')}</text>
      </svg>
    </div>
  );
}
