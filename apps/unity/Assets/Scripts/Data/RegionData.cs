// RegionData.cs
// Allen CCFv3 脑区数据：structure ID、配色、Bregma 相对坐标
// 与 optimizer_server.py PLANNER_REGIONS 严格一致

using System.Collections.Generic;
using UnityEngine;

namespace BrainPlanner.Data
{
    [System.Serializable]
    public class RegionInfo
    {
        public string key;
        public int    structureId;
        public Color  color;
        public float  ap;    // mm, anterior positive
        public float  dv;    // mm, ventral positive
        public float  ml;    // mm, right positive
        public string category;
        public string groupKey;
        public string groupName;
        public string groupNameCn;
        public string displayName;
        public int voxelCount;
        public int nSubregions;
        public bool volumeAvailabilityKnown;
        public bool presentInVolume;

        // Unity world position (Bregma = origin)
        public Vector3 WorldPos => new Vector3(ml, -dv, ap);
    }

    public static class RegionData
    {
        // ── 全量脑区定义 ────────────────────────────────────────────────────
        public static readonly List<RegionInfo> All = new List<RegionInfo>
        {
            // Visual
            new RegionInfo { key="VISp",  structureId=385,  color=Hex("08858C"), ap= 2.10f, dv=2.30f, ml=-0.90f, category="visual",       displayName="Primary visual area" },
            new RegionInfo { key="VISl",  structureId=394,  color=Hex("1BA3A3"), ap= 2.30f, dv=2.50f, ml=-1.60f, category="visual",       displayName="Lateral visual area" },
            new RegionInfo { key="VISpm", structureId=533,  color=Hex("069080"), ap= 2.80f, dv=2.00f, ml=-0.40f, category="visual",       displayName="Posteromedial visual area" },
            new RegionInfo { key="VISal", structureId=402,  color=Hex("1A9999"), ap= 1.50f, dv=2.10f, ml=-1.60f, category="visual",       displayName="Anterolateral visual area" },
            new RegionInfo { key="VISrl", structureId=417,  color=Hex("2AA0A0"), ap= 1.90f, dv=2.20f, ml=-1.10f, category="visual",       displayName="Rostrolateral visual area" },
            new RegionInfo { key="LGd",   structureId=170,  color=Hex("6666DD"), ap= 1.80f, dv=4.00f, ml=-1.60f, category="visual",       displayName="Lateral geniculate (dorsal)" },
            // Hippocampus
            new RegionInfo { key="CA1",   structureId=382,  color=Hex("FF8070"), ap= 1.80f, dv=3.80f, ml=-1.20f, category="hippocampus",  displayName="Field CA1" },
            new RegionInfo { key="CA3",   structureId=463,  color=Hex("FF5566"), ap= 1.60f, dv=3.60f, ml=-1.40f, category="hippocampus",  displayName="Field CA3" },
            new RegionInfo { key="DG",    structureId=726,  color=Hex("FF4466"), ap= 2.00f, dv=4.20f, ml=-1.10f, category="hippocampus",  displayName="Dentate gyrus" },
            new RegionInfo { key="ENTl",  structureId=28,   color=Hex("9999CC"), ap= 3.50f, dv=4.50f, ml=-2.00f, category="hippocampus",  displayName="Entorhinal area (lateral)" },
            new RegionInfo { key="RSP",   structureId=254,  color=Hex("D4B800"), ap= 2.50f, dv=2.60f, ml=-0.50f, category="hippocampus",  displayName="Retrosplenial area" },
            // Thalamus
            new RegionInfo { key="LP",    structureId=218,  color=Hex("AA44FF"), ap= 1.50f, dv=3.80f, ml=-1.30f, category="thalamus",     displayName="Lateral posterior nucleus" },
            new RegionInfo { key="VPM",   structureId=733,  color=Hex("88BBFF"), ap= 0.80f, dv=4.20f, ml=-1.50f, category="thalamus",     displayName="Ventral posteromedial nucleus" },
            new RegionInfo { key="VPL",   structureId=718,  color=Hex("99CCFF"), ap= 0.90f, dv=4.00f, ml=-1.90f, category="thalamus",     displayName="Ventral posterolateral nucleus" },
            new RegionInfo { key="MD",    structureId=362,  color=Hex("8855CC"), ap= 0.20f, dv=3.70f, ml=-0.40f, category="thalamus",     displayName="Mediodorsal nucleus" },
            new RegionInfo { key="PO",    structureId=1020, color=Hex("9966DD"), ap= 1.80f, dv=3.90f, ml=-1.40f, category="thalamus",     displayName="Posterior complex" },
            // Frontal
            new RegionInfo { key="MOp",   structureId=985,  color=Hex("44BB66"), ap=-0.50f, dv=1.70f, ml=-1.20f, category="frontal",      displayName="Primary motor area" },
            new RegionInfo { key="MOs",   structureId=993,  color=Hex("33AA55"), ap=-0.70f, dv=1.50f, ml=-1.10f, category="frontal",      displayName="Secondary motor area" },
            new RegionInfo { key="SSp",   structureId=322,  color=Hex("22AA55"), ap= 0.20f, dv=2.00f, ml=-1.50f, category="frontal",      displayName="Primary somatosensory area" },
            new RegionInfo { key="PFC",   structureId=184,  color=Hex("22DD66"), ap=-1.70f, dv=2.00f, ml=-0.40f, category="frontal",      displayName="Prefrontal cortex" },
            new RegionInfo { key="ACA",   structureId=31,   color=Hex("33CC88"), ap=-1.50f, dv=1.60f, ml=-0.20f, category="frontal",      displayName="Anterior cingulate area" },
            new RegionInfo { key="PL",    structureId=667,  color=Hex("55DD99"), ap=-1.20f, dv=2.40f, ml=-0.40f, category="frontal",      displayName="Prelimbic area" },
            new RegionInfo { key="ILA",   structureId=44,   color=Hex("22CCAA"), ap=-1.80f, dv=2.60f, ml=-0.30f, category="frontal",      displayName="Infralimbic area" },
            // Midbrain
            new RegionInfo { key="SCsg",  structureId=834,  color=Hex("FF3311"), ap= 3.30f, dv=4.00f, ml=-0.40f, category="midbrain",     displayName="Superior colliculus (zonal)" },
            new RegionInfo { key="SCm",   structureId=294,  color=Hex("FF4422"), ap= 3.50f, dv=4.50f, ml=-0.40f, category="midbrain",     displayName="Superior colliculus (motor)" },
            new RegionInfo { key="MRN",   structureId=128,  color=Hex("FF2200"), ap= 3.40f, dv=4.80f, ml=-0.40f, category="midbrain",     displayName="Midbrain reticular nucleus" },
            new RegionInfo { key="SNr",   structureId=536,  color=Hex("CC2200"), ap= 3.20f, dv=4.60f, ml=-1.00f, category="midbrain",     displayName="Substantia nigra (reticular)" },
            // Basal Ganglia
            new RegionInfo { key="CP",    structureId=672,  color=Hex("CC9900"), ap=-0.30f, dv=3.20f, ml=-1.80f, category="basal_ganglia",displayName="Caudoputamen" },
            new RegionInfo { key="ACB",   structureId=56,   color=Hex("FF9900"), ap=-0.90f, dv=3.60f, ml=-0.60f, category="basal_ganglia",displayName="Nucleus accumbens" },
            new RegionInfo { key="GPe",   structureId=1022, color=Hex("FFBB22"), ap= 0.20f, dv=4.00f, ml=-1.60f, category="basal_ganglia",displayName="Globus pallidus (external)" },
            new RegionInfo { key="GPi",   structureId=1031, color=Hex("FFAA11"), ap= 0.10f, dv=4.20f, ml=-1.40f, category="basal_ganglia",displayName="Globus pallidus (internal)" },
        };

        // ── 快速查找字典 ────────────────────────────────────────────────────
        private static Dictionary<string, RegionInfo> _byKey;
        private static Dictionary<int,    RegionInfo> _byId;

        public static Dictionary<string, RegionInfo> ByKey
        {
            get
            {
                if (_byKey == null)
                {
                    _byKey = new Dictionary<string, RegionInfo>();
                    foreach (var r in All) _byKey[r.key] = r;
                }
                return _byKey;
            }
        }

        public static Dictionary<int, RegionInfo> ById
        {
            get
            {
                if (_byId == null)
                {
                    _byId = new Dictionary<int, RegionInfo>();
                    foreach (var r in All) _byId[r.structureId] = r;
                }
                return _byId;
            }
        }

        public static RegionInfo Get(string key)
            => ByKey.TryGetValue(key, out var r) ? r : null;

        // ── Hex → Color ──────────────────────────────────────────────────────
        private static Color Hex(string hex)
        {
            ColorUtility.TryParseHtmlString("#" + hex, out Color c);
            c.a = 1f;
            return c;
        }
    }
}
