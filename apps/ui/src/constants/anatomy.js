/**
 * Anatomical safety limits — Allen CCFv3 coordinate system
 * All values in mm relative to Bregma unless noted.
 */
export const ANATOMY = {
  lambda_ap_mm:         -4.0,   // Lambda suture AP position
  entry_ant_limit_mm:    4.5,   // Anterior-most safe entry point
  entry_post_limit_mm:  -4.5,   // Posterior-most safe entry (slightly past Lambda)
  max_dv_mm:             8.5,   // Maximum DV insertion depth
  tip_post_limit_mm:    -6.0,   // Posterior-most probe tip position
};

/**
 * CCFv3 Bregma position in µm (used for coordinate transforms)
 */
export const BREGMA_UM = [5400.0, 440.0, 5700.0];

/**
 * Probe hardware presets — Neuropixels specifications
 */
export const PROBE_PRESETS = {
  "NP2.0-4sh": {
    label:            "NP 2.0 四剪切 (4-shank)",
    shanks:           4,
    depthUm:          10000,
    entrySpacingUm:   3500,
    channelCount:     384,
    channelsPerShank: 96,
    electrodePitchUm: 15,
    electrodeColumns:  2,
    recordingLengthUm: 720,
    shankWidthUm:      70,
    shankPitchUm:      250,
  },
  "NP2.0-1sh": {
    label:            "NP 2.0 单剪切 (1-shank)",
    shanks:           1,
    depthUm:          10000,
    entrySpacingUm:   500,
    channelCount:     384,
    channelsPerShank: 384,
    electrodePitchUm: 15,
    electrodeColumns:  2,
    recordingLengthUm: 2880,
    shankWidthUm:      70,
    shankPitchUm:      0,
  },
  "NP1.0": {
    label:            "NP 1.0 (1-shank)",
    shanks:           1,
    depthUm:          10000,
    entrySpacingUm:   500,
    channelCount:     384,
    channelsPerShank: 384,
    electrodePitchUm: 20,
    electrodeColumns:  2,
    recordingLengthUm: 3840,
    shankWidthUm:      70,
    shankPitchUm:      0,
  },
  "custom": {
    label:            "自定义 / Custom",
    shanks:           4,
    depthUm:          10000,
    entrySpacingUm:   3500,
    channelCount:     384,
    channelsPerShank: 96,
    electrodePitchUm: 15,
    electrodeColumns:  2,
    recordingLengthUm: 720,
    shankWidthUm:      70,
    shankPitchUm:      250,
  },
};

/**
 * Constraint definitions for the planner.
 *
 * FIX v3.1: key 'lambda' → 'lambdaBoundary' 与 ConstraintsPanel.jsx
 *           的 defaultConstraints 保持一致，防止未来 import 时重新引入 bug。
 */
export const CONSTRAINT_DEFS = [
  { key:"angle",          labelCn:"插入角度",   labelEn:"Insert Angle",
    descCn:"≤ 最大角度参数",      descEn:"≤ max angle param",        default:"enforce" },
  { key:"entrySpacing",   labelCn:"入颅点间距", labelEn:"Entry Spacing",
    descCn:"≥ 探针底座宽度",       descEn:"≥ probe base width",       default:"enforce" },
  { key:"trajClear",      labelCn:"轨迹净间距", labelEn:"Traj. Clearance",
    descCn:"≥ 最小间距参数",       descEn:"≥ min clearance param",    default:"enforce" },
  { key:"lambdaBoundary", labelCn:"Lambda边界", labelEn:"Lambda Boundary",  // FIX: was 'lambda'
    descCn:"入颅≤ Bregma -4mm",  descEn:"entry ≤ Bregma -4mm",      default:"warn" },
  { key:"brainBounds",    labelCn:"脑组织范围", labelEn:"Brain Boundary",
    descCn:"探针尖端在脑内",       descEn:"tip within brain tissue",  default:"warn" },
  { key:"hemisphere",     labelCn:"半球一致性", labelEn:"Hemisphere Match",
    descCn:"探针与目标同侧",       descEn:"probe same side as target",default:"warn" },
];
