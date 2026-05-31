const L1_GROUPS = new Set([
  'CTX',
  'CNU',
  'IB',
  'MB',
  'HB',
  'CBX',
  'CBN',
  'CH',
  'BS',
  'CB',
  'HY',
  'TH',
  'STR',
  'PAL',
  'CTXsp',
  'OLF',
  'HPF',
  'P',
  'MY',
]);

const L2_GROUPS = new Set(['VS', 'AQ', 'V3', 'V4', 'VL', 'IVF', 'chpl', 'SEZ', 'RC', 'c']);

const L3_GROUPS = new Set([
  'fiber tracts',
  'cst',
  'sst',
  'rst',
  'rust',
  'tsp',
  'cbp',
  'cc',
  'lfbs',
  'mfbs',
  'mfsbshy',
  'lfbst',
  'mfbc',
  'cbf',
  'eps',
  'epsc',
  'ctb',
  'drt',
  'vrt',
  'cbc',
  'arb',
  'scwm',
  'Vn',
  'IIn',
  'IIIn',
  'IVn',
  'VIn',
  'VIIn',
  'VIIIn',
  'IXn',
  'Xn',
  'XIn',
  'XIIn',
  'In',
  'tn',
  'von',
]);

const L4_GROUPS = new Set([
  'grv',
  'grv of CTX',
  'grv of CBX',
  'hf',
  'rf',
  'pfs',
  'plf',
  'icf',
  'sec',
  'chfl',
  'ri',
  'eg',
  'pce',
  'pcf',
  'ppf',
  'pri',
  'psf',
  'pms',
  'sif',
  'nf',
  'apf',
  'ipf',
]);

export const DISPLAY_LAYER_ORDER = ['L1', 'L2', 'L3', 'L4'];
export const DISPLAY_LAYER_DEFAULT_VISIBILITY = {
  L1: true,
  L2: true,
  L3: false,
  L4: false,
};

export const DISPLAY_LAYER_LABEL_KEY = {
  L1: 'brain3dLayerL1',
  L2: 'brain3dLayerL2',
  L3: 'brain3dLayerL3',
  L4: 'brain3dLayerL4',
};

export const CLINICAL_CLASS_ORDER = [
  'cortex',
  'nuclei',
  'diencephalon',
  'midbrain',
  'hindbrain_cerebellum',
  'ventricle_csf',
  'pathway',
  'nerve_fissure',
];

export const CLINICAL_CLASS_LABEL_KEY = {
  cortex: 'clinicalClassCortex',
  nuclei: 'clinicalClassNuclei',
  diencephalon: 'clinicalClassDiencephalon',
  midbrain: 'clinicalClassMidbrain',
  hindbrain_cerebellum: 'clinicalClassHindbrainCerebellum',
  ventricle_csf: 'clinicalClassVentricleCsf',
  pathway: 'clinicalClassPathway',
  nerve_fissure: 'clinicalClassNerveFissure',
};

function normalizeToken(value) {
  return String(value || '').trim();
}

function lower(value) {
  return normalizeToken(value).toLowerCase();
}

function looksLikeNerveToken(groupKey, groupName) {
  const k = lower(groupKey);
  const n = lower(groupName);
  return (
    k.endsWith('n') ||
    n.includes('nerve') ||
    n.includes('root') ||
    n.includes('cranial')
  );
}

function looksLikeFissureToken(groupKey, groupName) {
  const n = lower(groupName);
  return (
    n.includes('fissure') ||
    n.includes('sulcus') ||
    n.includes('groove') ||
    n.includes('incisure') ||
    L4_GROUPS.has(groupKey)
  );
}

function looksLikePathwayToken(groupKey, groupName) {
  const n = lower(groupName);
  return (
    L3_GROUPS.has(groupKey) ||
    n.includes('tract') ||
    n.includes('pathway') ||
    n.includes('bundle') ||
    n.includes('peduncle') ||
    n.includes('white matter')
  );
}

export function classifyDisplayLayer(meta) {
  const groupKey = normalizeToken(meta?.groupKey);
  const groupName = normalizeToken(meta?.groupName);
  if (L1_GROUPS.has(groupKey)) return 'L1';
  if (L2_GROUPS.has(groupKey)) return 'L2';
  if (L3_GROUPS.has(groupKey)) return 'L3';
  if (L4_GROUPS.has(groupKey)) return 'L4';

  const nameL = lower(groupName);
  if (
    nameL.includes('ventricle') ||
    nameL.includes('aqueduct') ||
    nameL.includes('foramen') ||
    nameL.includes('choroid')
  ) {
    return 'L2';
  }
  if (looksLikePathwayToken(groupKey, groupName) || looksLikeNerveToken(groupKey, groupName)) return 'L3';
  if (looksLikeFissureToken(groupKey, groupName)) return 'L4';
  return 'L1';
}

export function classifyClinicalClass(meta) {
  const groupKey = normalizeToken(meta?.groupKey);
  const groupName = normalizeToken(meta?.groupName);
  const layer = classifyDisplayLayer(meta);

  if (layer === 'L4') return 'nerve_fissure';
  if (layer === 'L3') {
    return looksLikeNerveToken(groupKey, groupName) ? 'nerve_fissure' : 'pathway';
  }
  if (layer === 'L2') return 'ventricle_csf';

  if (groupKey === 'CTX' || groupKey === 'CTXsp' || groupKey === 'CH' || groupKey === 'OLF') return 'cortex';
  if (groupKey === 'CNU' || groupKey === 'STR' || groupKey === 'PAL' || groupKey === 'HPF') return 'nuclei';
  if (groupKey === 'IB' || groupKey === 'TH' || groupKey === 'HY') return 'diencephalon';
  if (groupKey === 'MB') return 'midbrain';
  if (groupKey === 'HB' || groupKey === 'BS' || groupKey === 'P' || groupKey === 'MY') return 'hindbrain_cerebellum';
  if (groupKey === 'CB' || groupKey === 'CBX' || groupKey === 'CBN') return 'hindbrain_cerebellum';

  const nameL = lower(groupName);
  if (nameL.includes('cortex') || nameL.includes('isocortex') || nameL.includes('pallium')) return 'cortex';
  if (nameL.includes('nuclei') || nameL.includes('striat') || nameL.includes('pallid')) return 'nuclei';
  if (nameL.includes('thalam') || nameL.includes('hypothalam') || nameL.includes('dienceph')) return 'diencephalon';
  if (nameL.includes('midbrain') || nameL.includes('tect')) return 'midbrain';
  if (nameL.includes('hindbrain') || nameL.includes('pons') || nameL.includes('medulla') || nameL.includes('cerebell')) return 'hindbrain_cerebellum';
  return 'pathway';
}

export function classifyRegionDisplay(meta) {
  const clinicalClass = classifyClinicalClass(meta);
  const layer = classifyDisplayLayer(meta);
  return { clinicalClass, layer };
}
