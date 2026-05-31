/**
 * Brain regions data — Allen CCFv3 reference coordinates
 *
 * Coordinate convention:
 *   ap: posterior-positive (mm from Bregma; positive = posterior)
 *   dv: ventral-positive (mm from skull surface)
 *   ml: right-positive (mm from midline; negative = left hemisphere)
 *
 * NOTE: When generating trajectories, ap_mm is ANTERIOR-positive,
 * so we negate reg.ap when computing entry points.
 *
 * FIX v3.1: Added ILA, GPi, SNr (were in RegionSelector but missing here,
 *            causing silent failures in all brain views).
 */

export const BRAIN_REGIONS = {
  VISp:  { name_cn:"初级视觉皮层",   name_en:"Primary Visual Ctx",      id:385,  ap:2.10, dv:2.30, ml:-0.90, category:"visual"       },
  VISl:  { name_cn:"外侧视觉皮层",   name_en:"Lateral Visual Ctx",       id:394,  ap:2.30, dv:2.50, ml:-1.60, category:"visual"       },
  VISpm: { name_cn:"后内侧视觉皮层", name_en:"Post-Med Visual Ctx",      id:533,  ap:2.80, dv:2.00, ml:-0.40, category:"visual"       },
  VISal: { name_cn:"前外侧视觉皮层", name_en:"Ant-Lat Visual Ctx",       id:402,  ap:1.50, dv:2.10, ml:-1.60, category:"visual"       },
  VISrl: { name_cn:"后外侧视觉皮层", name_en:"Post-Lat Visual Ctx",      id:417,  ap:1.90, dv:2.20, ml:-1.10, category:"visual"       },
  LGd:   { name_cn:"外侧膝状体背核", name_en:"Lateral Geniculate (D)",   id:170,  ap:1.80, dv:4.00, ml:-1.60, category:"visual"       },
  SCsg:  { name_cn:"上丘浅灰层",     name_en:"Sup.Colliculus SG",        id:834,  ap:3.30, dv:4.00, ml:-0.40, category:"midbrain"     },
  SCm:   { name_cn:"上丘运动层",     name_en:"Sup.Colliculus M",         id:294,  ap:3.50, dv:4.50, ml:-0.40, category:"midbrain"     },
  MRN:   { name_cn:"中缝背核",       name_en:"Midbrain Raphe",           id:128,  ap:3.40, dv:4.80, ml:-0.40, category:"midbrain"     },
  SNr:   { name_cn:"黑质网状部",     name_en:"Substantia Nigra (R)",     id:1036, ap:3.20, dv:4.80, ml:-1.60, category:"midbrain"     }, // FIX: added
  CA1:   { name_cn:"海马CA1区",      name_en:"Hippocampus CA1",          id:382,  ap:1.80, dv:3.80, ml:-1.20, category:"hippocampus"  },
  CA3:   { name_cn:"海马CA3区",      name_en:"Hippocampus CA3",          id:463,  ap:1.60, dv:3.60, ml:-1.40, category:"hippocampus"  },
  DG:    { name_cn:"齿状回",         name_en:"Dentate Gyrus",            id:726,  ap:2.00, dv:4.20, ml:-1.10, category:"hippocampus"  },
  ENTl:  { name_cn:"外侧内嗅皮层",   name_en:"Lat.Entorhinal Ctx",       id:28,   ap:3.50, dv:4.50, ml:-2.00, category:"hippocampus"  },
  RSP:   { name_cn:"后压皮层",       name_en:"Retrosplenial Ctx",        id:254,  ap:2.50, dv:2.60, ml:-0.50, category:"hippocampus"  },
  LP:    { name_cn:"丘脑外侧后核",   name_en:"Thal.Lat.Post.",           id:218,  ap:1.50, dv:3.80, ml:-1.30, category:"thalamus"     },
  VPM:   { name_cn:"腹后内侧核",     name_en:"Thal.Vent.Post.Med.",      id:733,  ap:0.80, dv:4.20, ml:-1.50, category:"thalamus"     },
  VPL:   { name_cn:"腹后外侧核",     name_en:"Thal.Vent.Post.Lat.",      id:718,  ap:0.90, dv:4.00, ml:-1.90, category:"thalamus"     },
  MD:    { name_cn:"背内侧核",       name_en:"Mediodorsal Thalamus",     id:362,  ap:0.20, dv:3.70, ml:-0.40, category:"thalamus"     },
  PO:    { name_cn:"后丘脑核",       name_en:"Post.Thal.Nucleus",        id:1020, ap:1.80, dv:3.90, ml:-1.40, category:"thalamus"     },
  MOp:   { name_cn:"初级运动皮层",   name_en:"Primary Motor Ctx",        id:985,  ap:-0.50,dv:1.70, ml:-1.20, category:"frontal"      },
  MOs:   { name_cn:"次级运动皮层",   name_en:"Secondary Motor Ctx",      id:993,  ap:-0.70,dv:1.50, ml:-1.10, category:"frontal"      },
  SSp:   { name_cn:"初级体感皮层",   name_en:"Primary Somatosensory",    id:322,  ap:0.20, dv:2.00, ml:-1.50, category:"frontal"      },
  PFC:   { name_cn:"前额叶皮层",     name_en:"Prefrontal Cortex",        id:184,  ap:-1.70,dv:2.00, ml:-0.40, category:"frontal"      },
  ACA:   { name_cn:"前扣带皮层",     name_en:"Anterior Cingulate",       id:31,   ap:-1.50,dv:1.60, ml:-0.20, category:"frontal"      },
  PL:    { name_cn:"前边缘皮层",     name_en:"Prelimbic Ctx",            id:667,  ap:-1.20,dv:2.40, ml:-0.40, category:"frontal"      },
  ILA:   { name_cn:"下边缘皮层",     name_en:"Infralimbic Ctx",          id:44,   ap:-1.80,dv:2.70, ml:-0.40, category:"frontal"      }, // FIX: added
  CP:    { name_cn:"尾壳核",         name_en:"Caudoputamen",             id:672,  ap:-0.30,dv:3.20, ml:-1.80, category:"basal_ganglia"},
  ACB:   { name_cn:"伏隔核",         name_en:"Nucleus Accumbens",        id:56,   ap:-0.90,dv:3.60, ml:-0.60, category:"basal_ganglia"},
  GPe:   { name_cn:"苍白球外侧",     name_en:"Globus Pallidus Ext",      id:1022, ap:0.20, dv:4.00, ml:-1.60, category:"basal_ganglia"},
  GPi:   { name_cn:"苍白球内侧",     name_en:"Globus Pallidus Int",      id:1031, ap:0.50, dv:4.20, ml:-1.80, category:"basal_ganglia"}, // FIX: added
};

export const CATEGORIES = {
  visual:       { cn:"视觉系统",  en:"Visual System",   icon:"👁" },
  hippocampus:  { cn:"海马系统",  en:"Hippocampal",     icon:"🧠" },
  thalamus:     { cn:"丘脑核团",  en:"Thalamus",        icon:"⚡" },
  frontal:      { cn:"额叶皮层",  en:"Frontal Cortex",  icon:"🔬" },
  midbrain:     { cn:"中脑结构",  en:"Midbrain",        icon:"🎯" },
  basal_ganglia:{ cn:"基底节",    en:"Basal Ganglia",   icon:"🔗" },
};

export const CAT_COLORS = {
  visual:       "#00d4ff",
  hippocampus:  "#a78bfa",
  thalamus:     "#34d399",
  frontal:      "#fb923c",
  midbrain:     "#f472b6",
  basal_ganglia:"#facc15",
};

export const PROBE_COLORS = [
  "#00d4ff","#a78bfa","#34d399","#fb923c",
  "#f472b6","#facc15","#60a5fa","#f87171"
];

/**
 * Compute CCFv3 µm coordinates for a brain region centroid.
 */
export function regionToXYZ(reg) {
  return [
    5400 + reg.ap * 1000,
    440  + reg.dv * 1000,
    5700 + reg.ml * 1000,
  ];
}

/**
 * Approximate bounding radius (µm) for each brain region.
 * FIX v3.1: Added ILA, GPi, SNr to match newly added BRAIN_REGIONS entries.
 */
export const REGION_RADII_UM = {
  VISp:  700, VISl:  550, VISpm: 450, VISal: 500, VISrl: 450,
  LGd:   400, SCsg:  500, SCm:   450, MRN:   350, SNr:   500, // FIX: added SNr
  CA1:   600, CA3:   450, DG:    500, ENTl:  550, RSP:   600,
  LP:    400, VPM:   400, VPL:   450, MD:    350, PO:    350,
  MOp:   700, MOs:   650, SSp:   900, PFC:   500, ACA:   400, PL:  400, ILA: 350, // FIX: added ILA
  CP:   1100, ACB:   500, GPe:   500, GPi:   450, // FIX: added GPi
};

/**
 * Estimated vertical extent (DV span in mm) for each region.
 * FIX v3.1: Added ILA, GPi, SNr.
 */
export const REGION_DV_SPAN_MM = {
  VISp: 0.80, VISl: 0.70, VISpm: 0.60, VISal: 0.65, VISrl: 0.55,
  LGd:  0.65, SCsg: 0.50, SCm:  0.55, MRN:  0.45, SNr:  0.60, // FIX: added SNr
  CA1:  0.50, CA3:  0.40, DG:   0.55, ENTl: 0.70, RSP:  0.65,
  LP:   0.55, VPM:  0.50, VPL:  0.55, MD:   0.45, PO:   0.40,
  MOp:  0.85, MOs:  0.75, SSp:  1.00, PFC:  0.70, ACA:  0.55, PL: 0.50, ILA: 0.45, // FIX: added ILA
  CP:   1.40, ACB:  0.60, GPe:  0.60, GPi:  0.55, // FIX: added GPi
};
