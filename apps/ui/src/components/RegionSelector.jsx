import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BRAIN_REGIONS } from '../constants/brainRegions';
import { t } from '../constants/i18n';
import {
  CLINICAL_CLASS_LABEL_KEY,
  CLINICAL_CLASS_ORDER,
  classifyRegionDisplay,
} from '../constants/atlasDisplayPolicy';

const SECTION_HEIGHT = 34;
const ROW_HEIGHT = 62;
const VIRTUAL_OVERSCAN = 8;

const S = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, overflow: 'hidden' },
  toolbar: { display: 'flex', gap: 6, padding: 8, borderBottom: '1px solid #162d48', flexWrap: 'wrap' },
  toolBtn: {
    flex: '1 1 84px',
    background: '#0a1828',
    color: '#90c8e4',
    border: '1px solid #1e3d5e',
    borderRadius: 6,
    padding: '6px 8px',
    fontSize: 11,
    cursor: 'pointer',
  },
  searchWrap: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '8px 8px 6px',
    borderBottom: '1px solid #162d48',
    background: '#08121f',
  },
  searchInput: {
    flex: 1,
    minWidth: 0,
    background: '#07101c',
    color: '#d8eefa',
    border: '1px solid #1e3d5e',
    borderRadius: 6,
    padding: '7px 9px',
    fontSize: 11,
    outline: 'none',
  },
  clearBtn: {
    background: '#0a1828',
    color: '#90c8e4',
    border: '1px solid #1e3d5e',
    borderRadius: 6,
    padding: '6px 8px',
    fontSize: 10,
    cursor: 'pointer',
    flexShrink: 0,
  },
  categoryRow: {
    display: 'flex',
    gap: 6,
    padding: '6px 8px',
    borderBottom: '1px solid #162d48',
    overflowX: 'auto',
    overflowY: 'hidden',
    alignItems: 'center',
    flexWrap: 'nowrap',
  },
  categoryChip: {
    borderRadius: 999,
    padding: '4px 10px',
    fontSize: 10,
    border: '1px solid #1e3d5e',
    background: '#0a1828',
    color: '#90c8e4',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    flexShrink: 0,
  },
  filterRow: {
    display: 'flex',
    gap: 6,
    padding: '6px 8px',
    borderBottom: '1px solid #162d48',
    alignItems: 'center',
    flexWrap: 'wrap',
    background: '#071523',
  },
  filterLabel: {
    fontSize: 9.5,
    color: '#5a9ab8',
  },
  filterSelect: {
    minWidth: 74,
    background: '#03101f',
    border: '1px solid #1f4664',
    borderRadius: 6,
    color: '#9ed8f2',
    fontSize: 10.5,
    padding: '4px 6px',
    outline: 'none',
    cursor: 'pointer',
  },
  indexWrap: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'stretch',
    gap: 4,
    padding: '6px 8px',
    borderBottom: '1px solid #162d48',
    background: '#08121f',
  },
  indexLabel: {
    color: '#5a9ab8',
    fontSize: 10,
    flexShrink: 0,
    width: 'auto',
    lineHeight: 1.2,
  },
  indexRow: {
    display: 'flex',
    gap: 4,
    flexWrap: 'nowrap',
    flex: 1,
    minWidth: 0,
    paddingBottom: 2,
    overflowX: 'auto',
    overflowY: 'hidden',
    rowGap: 4,
  },
  indexChip: {
    minWidth: 20,
    textAlign: 'center',
    borderRadius: 5,
    border: '1px solid #1e3d5e',
    background: '#0a1828',
    color: '#90c8e4',
    fontSize: 10,
    padding: '2px 6px',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    flexShrink: 0,
  },
  stats: {
    display: 'flex',
    gap: 6,
    padding: '6px 8px',
    borderBottom: '1px solid #162d48',
    background: '#08121f',
    flexWrap: 'wrap',
  },
  statChip: {
    flex: '1 1 calc(50% - 6px)',
    borderRadius: 6,
    padding: '4px 6px',
    fontSize: 10,
    border: '1px solid #1e3d5e',
    background: '#0a1828',
    color: '#90c8e4',
    textAlign: 'center',
    fontWeight: 600,
  },
  list: {
    flex: 1,
    minHeight: 0,
    overflowY: 'auto',
    overflowX: 'hidden',
    overscrollBehavior: 'contain',
    WebkitOverflowScrolling: 'touch',
    scrollbarGutter: 'stable both-edges',
    background: '#061221',
  },
  virtualCanvas: {
    position: 'relative',
    width: '100%',
    minHeight: 0,
  },
  sectionHdr: {
    position: 'absolute',
    height: SECTION_HEIGHT - 4,
    boxSizing: 'border-box',
    left: 8,
    right: 8,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '6px 8px',
    border: '1px solid #13283f',
    borderRadius: 8,
    background: '#0a1828',
    color: '#90c8e4',
    fontSize: 10.5,
    fontWeight: 700,
    cursor: 'pointer',
    userSelect: 'none',
    zIndex: 1,
    fontFamily: 'inherit',
    textAlign: 'left',
  },
  sectionHdrLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    minWidth: 0,
    flex: 1,
  },
  sectionLabel: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  sectionChevron: {
    width: 12,
    textAlign: 'center',
    color: '#5a9ab8',
    fontSize: 10,
    flexShrink: 0,
  },
  sectionCount: {
    flexShrink: 0,
    borderRadius: 999,
    border: '1px solid #1e3d5e',
    color: '#5a9ab8',
    fontSize: 10,
    padding: '0 7px',
  },
  row: {
    position: 'absolute',
    height: ROW_HEIGHT - 4,
    boxSizing: 'border-box',
    left: 16,
    right: 16,
    display: 'grid',
    gridTemplateColumns: '18px minmax(0, 1fr)',
    gridTemplateRows: '28px 18px',
    alignItems: 'center',
    justifyContent: 'space-between',
    columnGap: 8,
    rowGap: 2,
    border: '1px solid #162d48',
    borderRadius: 6,
    padding: '4px 8px',
    background: '#08121f',
    cursor: 'pointer',
    fontFamily: 'inherit',
    textAlign: 'left',
  },
  rowOn: {
    border: '1px solid #00d4ff66',
    background: '#00d4ff14',
    boxShadow: 'inset 0 0 0 1px #00d4ff33',
  },
  rowFocus: {
    border: '1px solid #67e8f9',
    boxShadow: '0 0 0 1px #22d3ee55, 0 0 12px #22d3ee33',
  },
  rowCovered: {
    border: '1px solid #34d39955',
  },
  rowUnsupported: {
    opacity: 0.72,
    cursor: 'pointer',
    borderStyle: 'dashed',
  },
  check: {
    gridRow: '1 / 3',
    width: 18,
    height: 18,
    borderRadius: 9,
    border: '1px solid #1e3d5e',
    background: '#0a1828',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    fontSize: 11,
    color: '#3a6888',
  },
  checkOn: {
    border: '1px solid #00d4ff88',
    background: '#00d4ff22',
    color: '#00d4ff',
  },
  left: { minWidth: 0, gridColumn: 2, gridRow: 1 },
  title: {
    color: '#d8eefa',
    fontSize: 12,
    lineHeight: '14px',
    fontWeight: 600,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  subtitle: { color: '#5a9ab8', fontSize: 10, lineHeight: '12px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  tag: {
    gridColumn: 2,
    gridRow: 2,
    justifySelf: 'start',
    maxWidth: '100%',
    boxSizing: 'border-box',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    lineHeight: '14px',
    borderRadius: 10,
    padding: '0 5px',
    fontSize: 10,
    border: '1px solid #1e3d5e',
    color: '#90c8e4',
    flexShrink: 0,
  },
  tagOn: {
    border: '1px solid #00d4ff66',
    color: '#00d4ff',
    background: '#00d4ff12',
  },
  tagCovered: {
    border: '1px solid #34d39966',
    color: '#34d399',
    background: '#34d39912',
  },
  tagSelectedCovered: {
    border: '1px solid #34d39988',
    color: '#34d399',
    background: 'linear-gradient(90deg,#00d4ff14,#34d39918)',
    fontWeight: 700,
  },
  tagUnsupported: {
    border: '1px solid #6b7280',
    color: '#9ca3af',
    background: '#0f172a',
  },
  empty: {
    border: '1px dashed #274862',
    borderRadius: 8,
    margin: 8,
    padding: '16px 10px',
    textAlign: 'center',
    color: '#5a9ab8',
    fontSize: 11,
    background: '#08121f',
  },
};

const CLINICAL_CLASS_SET = new Set(CLINICAL_CLASS_ORDER);
const CATEGORY_ORDER = ['all', ...CLINICAL_CLASS_ORDER];
const CATEGORY_COLOR = {
  cortex: '#60a5fa',
  nuclei: '#f59e0b',
  diencephalon: '#a78bfa',
  midbrain: '#f472b6',
  hindbrain_cerebellum: '#34d399',
  ventricle_csf: '#22d3ee',
  pathway: '#facc15',
  nerve_fissure: '#fb7185',
};

const REGION_NAME_CN_KEY_BY_ACRONYM = {
  VISp: 'regionNameVisp',
  VISl: 'regionNameVisl',
  VISpm: 'regionNameVispm',
  VISal: 'regionNameVisal',
  VISrl: 'regionNameVisrl',
  LGd: 'regionNameLgd',
  SCsg: 'regionNameScsg',
  SCm: 'regionNameScm',
  MRN: 'regionNameMrn',
  SNr: 'regionNameSnr',
  CA1: 'regionNameCa1',
  CA3: 'regionNameCa3',
  DG: 'regionNameDg',
  ENTl: 'regionNameEntl',
  RSP: 'regionNameRsp',
  LP: 'regionNameLp',
  VPM: 'regionNameVpm',
  VPL: 'regionNameVpl',
  MD: 'regionNameMd',
  PO: 'regionNamePo',
  MOp: 'regionNameMop',
  MOs: 'regionNameMos',
  SSp: 'regionNameSsp',
  PFC: 'regionNamePfc',
  ACA: 'regionNameAca',
  PL: 'regionNamePl',
  ILA: 'regionNameIla',
  CP: 'regionNameCp',
  ACB: 'regionNameAcb',
  GPe: 'regionNameGpe',
  GPi: 'regionNameGpi',
};

function normalizeText(v) {
  return String(v || '').toLowerCase().trim();
}

function normalizeClinicalClass(value) {
  const raw = String(value || '').trim().toLowerCase();
  if (!raw) return 'pathway';
  if (CLINICAL_CLASS_SET.has(raw)) return raw;
  return 'pathway';
}

function normalizeHemisphere(raw) {
  const v = String(raw || '').trim().toLowerCase();
  if (v === 'left' || v === 'l') return 'left';
  if (v === 'right' || v === 'r') return 'right';
  if (v === 'bilateral' || v === 'both' || v === 'lr') return 'both';
  return 'unknown';
}

function inferHemisphere(item) {
  const fromField = normalizeHemisphere(item?.hemisphere);
  if (fromField !== 'unknown') return fromField;
  const key = String(item?.key || '').trim();
  if (!key) return 'unknown';
  if (/[ _/-]L$/i.test(key)) return 'left';
  if (/[ _/-]R$/i.test(key)) return 'right';
  return 'unknown';
}

function getIndexKey(regionKey) {
  const first = String(regionKey || '').trim().charAt(0).toUpperCase();
  return /[A-Z]/.test(first) ? first : '#';
}

function getRegionNamePair(regionKey, reg, tr) {
  const en = String(reg?.name_en || reg?.name || reg?.acronym || regionKey || '').trim() || regionKey;
  const nameKey = REGION_NAME_CN_KEY_BY_ACRONYM[regionKey];
  const cn = String((nameKey ? tr(nameKey) : '') || reg?.name_cn || '').trim();
  return { en, cn };
}

function buildSectionMeta(item, tr, lang = 'cn') {
  const baseCategory = normalizeClinicalClass(item?.clinicalClass);
  const sectionLabelFallback = tr(CLINICAL_CLASS_LABEL_KEY[baseCategory] || 'clinicalClassPathway');
  const groupKey = String(item?.groupKey || '').trim();
  const groupName = String(item?.groupName || '').trim();
  const groupNameCn = String(item?.groupNameCn || '').trim();
  const locale = String(lang || '').toLowerCase();

  const subgroup = groupKey || 'ungrouped';
  const subgroupLabel = locale.startsWith('cn')
    ? (groupNameCn || groupName || groupKey || tr('regionSubgroupUngrouped'))
    : (groupName || groupKey || groupNameCn || tr('regionSubgroupUngrouped'));
  const sectionKey = `group:${baseCategory}:${subgroup.toLowerCase()}`;

  return {
    sectionKey,
    sectionLabel: subgroupLabel,
    sectionClassLabel: sectionLabelFallback,
    subgroupKey: subgroup,
    baseCategory,
  };
}

function rowStatusLabel(tr, selected, covered, supported) {
  if (supported === 'unplannable') return tr('regionNotPlannable');
  if (supported === 'ondemand') return tr('onDemandLoad');
  if (selected && covered) return tr('selectedCoveredLabel');
  if (covered) return tr('coverage');
  if (selected) return tr('selected');
  return tr('off');
}

export default function RegionSelector({
  selectedRegions = new Set(),
  onToggleRegion,
  results = null,
  focusedRegion = null,
  regionCatalog = null,
  supportedRegionKeys = null,
  catalogRegionCount = null,
  presentRegionCount = null,
  lang = 'cn',
}) {
  const tr = useCallback((key) => t(key, lang), [lang]);
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [activeIndex, setActiveIndex] = useState('all');
  const [expandedSections, setExpandedSections] = useState({});
  const [selectionFilter, setSelectionFilter] = useState('all');
  const [planFilter, setPlanFilter] = useState('all');
  const [hemisphereFilter, setHemisphereFilter] = useState('all');
  const [pinSelectedTop, setPinSelectedTop] = useState(true);
  const [listHeight, setListHeight] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);

  const listRef = useRef(null);
  const rowRefs = useRef({});

  const supportedSet = useMemo(() => {
    if (!Array.isArray(supportedRegionKeys) || supportedRegionKeys.length === 0) return null;
    return new Set(supportedRegionKeys);
  }, [supportedRegionKeys]);

  const regions = useMemo(() => {
    const catalogEntries =
      regionCatalog && typeof regionCatalog === 'object'
        ? Object.entries(regionCatalog)
        : [];
    if (catalogEntries.length > 0) {
      return catalogEntries
        .map(([key, raw]) => {
          const fallback = BRAIN_REGIONS[key] || {};
          const groupKey = String(raw?.groupKey || '').trim();
          const groupName = String(raw?.groupName || '').trim();
          const groupNameCn = String(raw?.groupNameCn || '').trim();
          const { clinicalClass, layer } = classifyRegionDisplay({
            groupKey,
            groupName,
          });
          return {
            key,
            clinicalClass: normalizeClinicalClass(clinicalClass),
            layer,
            hemisphere: normalizeHemisphere(raw?.hemisphere || raw?.side),
            presentInVolume: raw?.presentInVolume !== false,
            unavailableReason: raw?.unavailableReason || '',
            plannerReady: raw?.plannerReady !== false && raw?.presentInVolume !== false,
            name_en: fallback.name_en || raw?.name || raw?.acronym || key,
            name_cn: raw?.name_cn || '',
            groupKey,
            groupName,
            groupNameCn,
          };
        })
        .sort((a, b) => {
          if (a.clinicalClass !== b.clinicalClass) return a.clinicalClass.localeCompare(b.clinicalClass);
          return a.key.localeCompare(b.key);
        });
    }
    return Object.entries(BRAIN_REGIONS)
      .map(([key, raw]) => ({
        key,
        clinicalClass: 'pathway',
        layer: 'L1',
        hemisphere: normalizeHemisphere(raw?.hemisphere || raw?.side),
        plannerReady: true,
        name_en: raw?.name_en || key,
        name_cn: '',
        groupKey: '',
        groupName: '',
        groupNameCn: '',
      }))
      .sort((a, b) => {
        if (a.clinicalClass !== b.clinicalClass) return a.clinicalClass.localeCompare(b.clinicalClass);
        return a.key.localeCompare(b.key);
      });
  }, [regionCatalog]);

  const coveredRegions = useMemo(() => {
    const set = new Set();
    for (const p of results?.selected || []) {
      for (const k of p.coveredRegions || p.regionsHit || []) set.add(k);
    }
    for (const p of results?.probes || []) {
      for (const k of p.regionsHit || []) set.add(k);
    }
    return set;
  }, [results]);

  const categoryCounts = useMemo(() => {
    const counts = { all: regions.length };
    for (const item of regions) {
      const cat = normalizeClinicalClass(item.clinicalClass);
      counts[cat] = (counts[cat] || 0) + 1;
    }
    return counts;
  }, [regions]);

  const searchNorm = normalizeText(search);

  const indexedAvailable = useMemo(() => {
    const idx = new Set();
    for (const item of regions) {
      const selected = selectedRegions.has(item.key);
      const inCatalog = !supportedSet || supportedSet.has(item.key);
      const plannable = inCatalog && item.plannerReady !== false;
      const hemi = inferHemisphere(item);

      if (activeCategory !== 'all' && normalizeClinicalClass(item.clinicalClass) !== activeCategory) continue;
      if (selectionFilter === 'selected' && !selected) continue;
      if (selectionFilter === 'unselected' && selected) continue;
      if (planFilter === 'plannable' && !plannable) continue;
      if (planFilter === 'unsupported' && plannable) continue;
      if (hemisphereFilter !== 'all' && hemi !== hemisphereFilter) continue;

      const names = getRegionNamePair(item.key, item, tr);
      const haystack = [
        normalizeText(item.key),
        normalizeText(names.en),
        normalizeText(names.cn),
        normalizeText(item.groupKey),
        normalizeText(item.groupName),
        normalizeText(item.groupNameCn),
        normalizeText(tr(CLINICAL_CLASS_LABEL_KEY[normalizeClinicalClass(item.clinicalClass)] || 'clinicalClassPathway')),
      ].join(' ');
      if (searchNorm && !haystack.includes(searchNorm)) continue;
      idx.add(getIndexKey(item.key));
    }
    return ['all', ...Array.from(idx).sort((a, b) => a.localeCompare(b))];
  }, [
    activeCategory,
    hemisphereFilter,
    planFilter,
    regions,
    searchNorm,
    selectedRegions,
    selectionFilter,
    supportedSet,
    tr,
  ]);

  useEffect(() => {
    if (!indexedAvailable.includes(activeIndex)) setActiveIndex('all');
  }, [indexedAvailable, activeIndex]);

  useEffect(() => {
    if (activeCategory !== 'all' && !categoryCounts[activeCategory]) {
      setActiveCategory('all');
    }
  }, [categoryCounts, activeCategory]);

  const visibleRegions = useMemo(() => {
    return regions.filter((item) => {
      const selected = selectedRegions.has(item.key);
      const inCatalog = !supportedSet || supportedSet.has(item.key);
      const plannable = inCatalog && item.plannerReady !== false;
      const hemi = inferHemisphere(item);
      const category = normalizeClinicalClass(item.clinicalClass);

      if (activeCategory !== 'all' && category !== activeCategory) return false;
      if (selectionFilter === 'selected' && !selected) return false;
      if (selectionFilter === 'unselected' && selected) return false;
      if (planFilter === 'plannable' && !plannable) return false;
      if (planFilter === 'unsupported' && plannable) return false;
      if (hemisphereFilter !== 'all' && hemi !== hemisphereFilter) return false;
      if (activeIndex !== 'all' && getIndexKey(item.key) !== activeIndex) return false;

      if (!searchNorm) return true;
      const names = getRegionNamePair(item.key, item, tr);
      const haystack = [
        normalizeText(item.key),
        normalizeText(names.en),
        normalizeText(names.cn),
        normalizeText(item.groupKey),
        normalizeText(item.groupName),
        normalizeText(item.groupNameCn),
        normalizeText(tr(CLINICAL_CLASS_LABEL_KEY[category] || 'clinicalClassPathway')),
      ].join(' ');
      return haystack.includes(searchNorm);
    });
  }, [
    activeCategory,
    activeIndex,
    hemisphereFilter,
    planFilter,
    regions,
    searchNorm,
    selectedRegions,
    selectionFilter,
    supportedSet,
    tr,
  ]);

  const groupedVisibleRegions = useMemo(() => {
    const grouped = new Map();
    for (const item of visibleRegions) {
      const meta = buildSectionMeta(item, tr, lang);
      if (!grouped.has(meta.sectionKey)) {
        grouped.set(meta.sectionKey, {
          sectionKey: meta.sectionKey,
          sectionLabel: meta.sectionLabel,
          sectionClassLabel: meta.sectionClassLabel,
          subgroupKey: meta.subgroupKey,
          baseCategory: meta.baseCategory,
          regions: [],
        });
      }
      grouped.get(meta.sectionKey).regions.push(item);
    }
    const sortRegions = (regionsInGroup) => [...regionsInGroup].sort((a, b) => {
      if (pinSelectedTop) {
        const aSel = selectedRegions.has(a.key) ? 0 : 1;
        const bSel = selectedRegions.has(b.key) ? 0 : 1;
        if (aSel !== bSel) return aSel - bSel;
      }
      return a.key.localeCompare(b.key);
    });

    const fixed = CATEGORY_ORDER
      .filter((cat) => cat !== 'all')
      .flatMap((cat) =>
        Array.from(grouped.values()).filter((group) => group.baseCategory === cat)
      )
      .map((group) => ({
        ...group,
        regions: sortRegions(group.regions),
      }))
      .sort((a, b) => {
        const ia = CATEGORY_ORDER.indexOf(a.baseCategory);
        const ib = CATEGORY_ORDER.indexOf(b.baseCategory);
        if (ia !== ib) return ia - ib;
        return a.sectionLabel.localeCompare(b.sectionLabel);
      });
    return fixed;
  }, [lang, pinSelectedTop, selectedRegions, tr, visibleRegions]);

  const selectedCount = selectedRegions.size;
  const coveredCount = coveredRegions.size;

  const isSectionExpanded = useCallback((sectionKey) => expandedSections[sectionKey] !== false, [expandedSections]);

  const flatItems = useMemo(() => {
    const out = [];
    for (const group of groupedVisibleRegions) {
      out.push({
        id: `section:${group.sectionKey}`,
        type: 'section',
        sectionKey: group.sectionKey,
        sectionLabel: group.sectionLabel,
        sectionClassLabel: group.sectionClassLabel,
        count: group.regions.length,
      });
      if (!isSectionExpanded(group.sectionKey)) continue;
      for (const reg of group.regions) {
        out.push({
          id: `row:${group.sectionKey}:${reg.key}`,
          type: 'row',
          sectionKey: group.sectionKey,
          sectionLabel: group.sectionLabel,
          sectionClassLabel: group.sectionClassLabel,
          reg,
        });
      }
    }
    return out;
  }, [groupedVisibleRegions, isSectionExpanded]);

  const layout = useMemo(() => {
    let top = 0;
    const sectionTop = {};
    const rowTop = {};
    const items = flatItems.map((item) => {
      const height = item.type === 'section' ? SECTION_HEIGHT : ROW_HEIGHT;
      const current = { ...item, top, height };
      if (item.type === 'section') {
        sectionTop[item.sectionKey] = top;
      } else {
        rowTop[item.reg.key] = top;
      }
      top += height;
      return current;
    });
    return { items, totalHeight: Math.max(top + 8, listHeight), sectionTop, rowTop };
  }, [flatItems, listHeight]);

  const visibleSlice = useMemo(() => {
    if (!layout.items.length) return [];
    const minY = Math.max(0, scrollTop - ROW_HEIGHT * VIRTUAL_OVERSCAN);
    const maxY = scrollTop + listHeight + ROW_HEIGHT * VIRTUAL_OVERSCAN;
    let start = 0;
    while (start < layout.items.length && layout.items[start].top + layout.items[start].height < minY) start += 1;
    let end = start;
    while (end < layout.items.length && layout.items[end].top < maxY) end += 1;
    return layout.items.slice(start, Math.min(layout.items.length, end + 1));
  }, [layout, listHeight, scrollTop]);

  const toggleSection = useCallback((sectionKey) => {
    setExpandedSections((prev) => ({
      ...prev,
      [sectionKey]: prev[sectionKey] === false,
    }));
  }, []);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return undefined;
    const update = () => setListHeight(el.clientHeight || 0);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!focusedRegion) return;
    const top = layout.rowTop[focusedRegion];
    if (!Number.isFinite(top) || !listRef.current) return;
    listRef.current.scrollTo({ top: Math.max(0, top - 40), behavior: 'smooth' });
  }, [focusedRegion, layout.rowTop]);

  return (
    <div style={S.root} className="bp-region-selector">
      <div style={S.toolbar}>
        <button style={S.toolBtn} onClick={() => onToggleRegion?.('__selectAll__')}>
          {tr('selectAll')}
        </button>
        <button style={S.toolBtn} onClick={() => onToggleRegion?.('__clear__')}>
          {tr('clearAll')}
        </button>
        <button
          style={{
            ...S.toolBtn,
            background: pinSelectedTop ? '#00d4ff22' : '#0a1828',
            border: pinSelectedTop ? '1px solid #00d4ff88' : '1px solid #1e3d5e',
            color: pinSelectedTop ? '#00d4ff' : '#90c8e4',
          }}
          onClick={() => setPinSelectedTop((v) => !v)}
        >
          {tr('regionPinSelectedTop')}
        </button>
      </div>

      <div style={S.searchWrap}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={tr('regionSearchPlaceholder')}
          style={S.searchInput}
        />
        {search ? (
          <button style={S.clearBtn} onClick={() => setSearch('')}>
            {tr('regionSearchClear')}
          </button>
        ) : null}
      </div>

      <div style={S.categoryRow} className="bp-scrollbar bp-scrollbar--thin">
        {CATEGORY_ORDER.map((cat) => {
          const count = categoryCounts[cat] || 0;
          if (cat !== 'all' && count <= 0) return null;
          const color = CATEGORY_COLOR[cat] || '#90c8e4';
          const active = activeCategory === cat;
          return (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              style={{
                ...S.categoryChip,
                border: active ? `1px solid ${color}` : '1px solid #1e3d5e',
                color: active ? color : '#90c8e4',
                background: active ? `${color}20` : '#0a1828',
              }}
            >
              {cat === 'all'
                ? tr('clinicalClassAll')
                : tr(CLINICAL_CLASS_LABEL_KEY[cat] || 'clinicalClassPathway')} ({count})
            </button>
          );
        })}
      </div>

      <div style={S.filterRow}>
        <span style={S.filterLabel}>{tr('regionFilterSelection')}</span>
        <select value={selectionFilter} onChange={(e) => setSelectionFilter(e.target.value)} style={S.filterSelect}>
          <option value="all">{tr('regionFilterAll')}</option>
          <option value="selected">{tr('regionFilterSelected')}</option>
          <option value="unselected">{tr('regionFilterUnselected')}</option>
        </select>

        <span style={S.filterLabel}>{tr('regionFilterPlan')}</span>
        <select value={planFilter} onChange={(e) => setPlanFilter(e.target.value)} style={S.filterSelect}>
          <option value="all">{tr('regionFilterAll')}</option>
          <option value="plannable">{tr('regionFilterPlannable')}</option>
          <option value="unsupported">{tr('regionFilterUnsupported')}</option>
        </select>

        <span style={S.filterLabel}>{tr('regionFilterHemisphere')}</span>
        <select value={hemisphereFilter} onChange={(e) => setHemisphereFilter(e.target.value)} style={S.filterSelect}>
          <option value="all">{tr('regionFilterAll')}</option>
          <option value="left">{tr('regionFilterHemiLeft')}</option>
          <option value="right">{tr('regionFilterHemiRight')}</option>
          <option value="unknown">{tr('regionFilterHemiUnknown')}</option>
        </select>
      </div>

      <div style={S.indexWrap}>
        <div style={S.indexLabel}>{tr('regionIndexLabel')}</div>
        <div style={S.indexRow} className="bp-scrollbar bp-scrollbar--thin">
          {indexedAvailable.map((idx) => (
            <button
              key={idx}
              onClick={() => setActiveIndex(idx)}
              style={{
                ...S.indexChip,
                border: activeIndex === idx ? '1px solid #00d4ff88' : '1px solid #1e3d5e',
                color: activeIndex === idx ? '#00d4ff' : '#90c8e4',
                background: activeIndex === idx ? '#00d4ff22' : '#0a1828',
              }}
            >
              {idx === 'all' ? tr('regionIndexAll') : idx}
            </button>
          ))}
        </div>
      </div>

      <div style={S.stats}>
        <div style={S.statChip}>{tr('selected')}: {selectedCount}</div>
        <div style={S.statChip}>{tr('coverage')}: {coveredCount}</div>
        {Number.isFinite(Number(catalogRegionCount)) ? (
          <div style={S.statChip}>{tr('catalogRegionCountLabel')}: {Number(catalogRegionCount)}</div>
        ) : null}
        {Number.isFinite(Number(presentRegionCount)) ? (
          <div style={S.statChip}>{tr('presentRegionCountLabel')}: {Number(presentRegionCount)}</div>
        ) : null}
      </div>

      <div
        ref={listRef}
        style={S.list}
        className="bp-scrollbar bp-scrollbar--thin bp-region-selector__list"
        data-testid="region-selector-list"
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      >
        {groupedVisibleRegions.length === 0 ? (
          <div style={S.empty}>{tr('regionNoMatch')}</div>
        ) : (
          <div style={{ ...S.virtualCanvas, height: layout.totalHeight }}>
            {visibleSlice.map((item) => {
              if (item.type === 'section') {
                const expanded = isSectionExpanded(item.sectionKey);
                return (
                  <button
                    type="button"
                    key={item.id}
                    style={{ ...S.sectionHdr, top: item.top }}
                    data-testid={`region-section-${item.sectionKey}`}
                    title={`${item.sectionClassLabel} / ${item.sectionLabel}`}
                    onClick={() => toggleSection(item.sectionKey)}
                    aria-expanded={expanded}
                  >
                    <span style={S.sectionHdrLeft}>
                      <span style={S.sectionChevron}>{expanded ? 'v' : '>'}</span>
                      <span style={S.sectionLabel}>{item.sectionClassLabel} / {item.sectionLabel}</span>
                    </span>
                    <span style={S.sectionCount}>{item.count}</span>
                  </button>
                );
              }

              const reg = item.reg;
              const key = reg.key;
              const selected = selectedRegions.has(key);
              const covered = coveredRegions.has(key);
              const selectedCovered = selected && covered;
              const inCatalog = !supportedSet || supportedSet.has(key);
              const plannerReady = reg.plannerReady !== false;
              const presentInVolume = reg.presentInVolume !== false;
              const supportState = !inCatalog ? 'ondemand' : (plannerReady ? 'plannable' : 'unplannable');
              const supported = supportState === 'plannable';
              const names = getRegionNamePair(key, reg, tr);
              const locale = String(lang || '').toLowerCase();
              const label = locale.startsWith('cn') ? (names.cn || key) : (names.en || key);
              const supportHint = supportState === 'unplannable'
                ? (presentInVolume ? tr('regionNotPlannerSupportedReason') : tr('regionNotPlannableReason'))
                : (supportState === 'ondemand' ? tr('regionWillAutoProvision') : '');

              return (
                <button
                  type="button"
                  key={item.id}
                  ref={(el) => {
                    if (el) rowRefs.current[key] = el;
                  }}
                  style={{
                    ...S.row,
                    top: item.top,
                    ...(covered ? S.rowCovered : null),
                    ...(selected ? S.rowOn : null),
                    ...(focusedRegion === key ? S.rowFocus : null),
                    ...(!supported ? S.rowUnsupported : null),
                  }}
                  onClick={() => onToggleRegion?.(key)}
                  title={`${label} (${key}) - ${item.sectionClassLabel} / ${item.sectionLabel}${supportHint ? ` - ${supportHint}` : ''}`}
                  data-region-key={key}
                  aria-pressed={selected}
                >
                  <div style={{ ...S.check, ...(selected ? S.checkOn : null) }}>{selected ? 'v' : ''}</div>

                  <div style={S.left}>
                    <div style={S.title}>{label}</div>
                    <div style={S.subtitle} data-region-subtitle>
                      {key} - {item.sectionClassLabel} / {item.sectionLabel}
                    </div>
                  </div>

                  <div
                    style={{
                      ...S.tag,
                      ...(selectedCovered ? S.tagSelectedCovered : null),
                      ...(covered ? S.tagCovered : null),
                      ...(selected ? S.tagOn : null),
                      ...(!supported ? S.tagUnsupported : null),
                    }}
                  >
                    {rowStatusLabel(tr, selected, covered, supportState)}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
