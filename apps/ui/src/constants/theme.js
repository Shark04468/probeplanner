/**
 * Shared theme constants for ProbeplannerGUI.
 * Dark-mode scientific instrument aesthetic.
 */

export const COLORS = {
  bg:           "#060e1c",
  panel:        "#08121f",
  section:      "#0a1828",
  border:       "#162d48",
  borderBright: "#1e3d5e",
  text:         "#d8eefa",
  textSub:      "#90c8e4",
  textDim:      "#5a9ab8",
  textMuted:    "#3a6888",
  accent:       "#00d4ff",
  accentDim:    "#00d4ff44",
  success:      "#34d399",
  warning:      "#facc15",
  danger:       "#f87171",
  dangerBg:     "#1a0808",
  dangerBorder: "#5a1818",
};

export const sectionStyle = {
  background:   COLORS.section,
  border:       `1px solid ${COLORS.border}`,
  borderRadius: 8,
  padding:      "12px 14px",
};

export const titleStyle = {
  fontSize:       11,
  fontWeight:     700,
  color:          COLORS.textDim,
  letterSpacing:  "0.12em",
  textTransform:  "uppercase",
  marginBottom:   10,
};

export const titleRowStyle = {
  display:        "flex",
  justifyContent: "space-between",
  alignItems:     "center",
  marginBottom:   10,
};

export const labelStyle = {
  fontSize:       12,
  color:          COLORS.textSub,
  marginBottom:   4,
  display:        "flex",
  justifyContent: "space-between",
};

export const valStyle = {
  color:      COLORS.accent,
  fontWeight: 700,
  fontSize:   12,
};

export const chipStyle = (color, active = true) => ({
  padding:      "2px 7px",
  background:   active ? `${color}12` : "#03101e",
  border:       `1px solid ${active ? `${color}28` : COLORS.border}`,
  borderRadius: 3,
  fontSize:     10,
  color:        active ? color : COLORS.textMuted,
});

export const buttonStyle = (variant = "default") => {
  const variants = {
    default: {
      background: "#04182a",
      border: `1px solid #1a4a7a`,
      color: "#3a8ab8",
    },
    primary: {
      background: "linear-gradient(90deg,#003a5a,#004a72)",
      border: `1px solid #00d4ff44`,
      color: COLORS.accent,
      boxShadow: "0 0 18px #00d4ff22",
    },
    danger: {
      background: COLORS.dangerBg,
      border: `1px solid ${COLORS.dangerBorder}`,
      color: "#c06060",
    },
    success: {
      background: "#041a0e",
      border: "1px solid #1a5a38",
      color: COLORS.success,
    },
  };
  return {
    ...variants[variant] || variants.default,
    padding:       "5px 12px",
    borderRadius:  5,
    fontSize:      11,
    cursor:        "pointer",
    fontFamily:    "inherit",
    letterSpacing: "0.06em",
  };
};

export const GLOBAL_CSS = `
  html,body,#root{width:100%;height:100%;margin:0;padding:0;overflow:hidden;}
  *{box-sizing:border-box;}
  ::-webkit-scrollbar{width:4px;}
  ::-webkit-scrollbar-track{background:#030912;}
  ::-webkit-scrollbar-thumb{background:#0d2848;border-radius:2px;}
  input[type=range]{-webkit-appearance:none;appearance:none;height:3px;border-radius:2px;background:#0a1f38;outline:none;}
  input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:13px;height:13px;border-radius:50%;background:#00d4ff;cursor:pointer;box-shadow:0 0 7px #00d4ff55;}
  select option{background:#040f1e;color:#c8e4f4;}
  @keyframes spin{from{transform:rotate(0deg);}to{transform:rotate(360deg);}}
  button:hover:not(:disabled){filter:brightness(1.18);}
  button:disabled{opacity:0.4;}
`;
export const THEME = {
  // 基础色（映射到你已有的 COLORS）
  bg:            COLORS.bg,
  panelBg:       COLORS.panel,
  border:        COLORS.border,
  textPrimary:   COLORS.text,
  textSecondary: COLORS.textDim,
  accent:        COLORS.accent,
  success:       COLORS.success,
  warning:       COLORS.warning,
  danger:        COLORS.danger,
  inputBg:       COLORS.panel,   // 如果 COLORS 里有 inputBg 就改成 COLORS.inputBg

  // 字体
  fontFamily: "'Inter', 'PingFang SC', sans-serif",

  // 约束模式颜色
  modeColors: {
    enforce: COLORS.success  ?? '#4ade80',
    warn:    COLORS.warning  ?? '#facc15',
    off:     COLORS.textDim  ?? '#64748b',
  },

  // 预置样式（兼容旧引用）
  button:   buttonStyle,
  chip:     chipStyle,
  label:    labelStyle,
  val:      valStyle,
  section:  sectionStyle,
  titleRow: titleRowStyle,
  title:    titleStyle,
};