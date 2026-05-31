import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';

const BREGMA_UM = { ap: 5400, dv: 440, ml: 5700 };
const CAMERA_PIVOT = new THREE.Vector3(0, -2.8, -0.8);
const UNITY_FOV_DEG = 38;
const LOD_SETTLE_DELAY_MS = 700;

const SURFACE_LAYERS = [
  {
    uiKey: 'brainSurface',
    apiLayer: 'brain_surface',
    color: 0x6fb6ff,
    opacity: 0.1,
    renderOrder: 1,
  },
  {
    uiKey: 'outerBoundary',
    apiLayer: 'outer_tissue',
    color: 0x55d6c7,
    opacity: 0.12,
    renderOrder: 2,
  },
  {
    uiKey: 'skull',
    apiLayer: 'skull',
    color: 0xe6d8bf,
    opacity: 0.24,
    renderOrder: 3,
  },
];

function clamp(value, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return min;
  return Math.min(max, Math.max(min, n));
}

function normalizeBaseUrl(baseUrl) {
  return String(baseUrl || '').trim().replace(/\/+$/, '');
}

function apiUrl(baseUrl, path) {
  const clean = normalizeBaseUrl(baseUrl);
  return clean ? `${clean}${path}` : path;
}

function fetchOptions(authToken, signal) {
  const token = String(authToken || '').trim();
  return {
    signal,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  };
}

function parseObjIndex(raw, size) {
  const idx = Number.parseInt(raw, 10);
  if (!Number.isFinite(idx) || idx === 0) return null;
  return idx > 0 ? idx - 1 : size + idx;
}

function atlasUmToUnityWorld(apUm, dvUm, mlUm) {
  const apMm = (BREGMA_UM.ap - apUm) / 1000;
  const dvMm = (dvUm - BREGMA_UM.dv) / 1000;
  const mlMm = (mlUm - BREGMA_UM.ml) / 1000;
  return [mlMm, -dvMm, -apMm];
}

function atlasNormalToUnityWorld(ap, dv, ml) {
  const nx = ml;
  const ny = -dv;
  const nz = -ap;
  const len = Math.hypot(nx, ny, nz) || 1;
  return [nx / len, ny / len, nz / len];
}

function parseSurfaceObj(text) {
  const vertices = [];
  const normals = [];
  const positions = [];
  const normalValues = [];
  const lines = String(text || '').split(/\r?\n/);

  for (const line of lines) {
    if (!line || line[0] === '#') continue;
    const parts = line.trim().split(/\s+/);
    if (parts[0] === 'v' && parts.length >= 4) {
      const ap = Number(parts[1]);
      const dv = Number(parts[2]);
      const ml = Number(parts[3]);
      if (Number.isFinite(ap) && Number.isFinite(dv) && Number.isFinite(ml)) {
        vertices.push(atlasUmToUnityWorld(ap, dv, ml));
      }
      continue;
    }
    if (parts[0] === 'vn' && parts.length >= 4) {
      const ap = Number(parts[1]);
      const dv = Number(parts[2]);
      const ml = Number(parts[3]);
      if (Number.isFinite(ap) && Number.isFinite(dv) && Number.isFinite(ml)) {
        normals.push(atlasNormalToUnityWorld(ap, dv, ml));
      }
      continue;
    }
    if (parts[0] !== 'f' || parts.length < 4) continue;
    const face = parts.slice(1).map((token) => {
      const [vRaw, , nRaw] = token.split('/');
      return {
        vi: parseObjIndex(vRaw, vertices.length),
        ni: nRaw ? parseObjIndex(nRaw, normals.length) : null,
      };
    }).filter((item) => item.vi !== null && vertices[item.vi]);
    if (face.length < 3) continue;
    for (let i = 1; i < face.length - 1; i += 1) {
      [face[0], face[i], face[i + 1]].forEach((item) => {
        positions.push(...vertices[item.vi]);
        const normal = item.ni !== null && normals[item.ni] ? normals[item.ni] : null;
        if (normal) normalValues.push(...normal);
      });
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  if (normalValues.length === positions.length) {
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normalValues, 3));
  } else {
    geometry.computeVertexNormals();
  }
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}

function makeMaterial(layer) {
  return new THREE.MeshStandardMaterial({
    color: layer.color,
    transparent: true,
    opacity: layer.opacity,
    roughness: 0.9,
    metalness: 0,
    depthWrite: false,
    depthTest: true,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  });
}

function disposeMesh(mesh) {
  if (!mesh) return;
  mesh.geometry?.dispose?.();
  mesh.material?.dispose?.();
}

function cameraPositionFromState(cameraState) {
  const theta = Number.isFinite(cameraState?.theta) ? Number(cameraState.theta) : 0.55;
  const phi = clamp(cameraState?.phi, 0.05, Math.PI - 0.05);
  const distance = clamp(cameraState?.distance, 4, 55);
  const sinPhi = Math.sin(phi);
  return new THREE.Vector3(
    CAMERA_PIVOT.x + distance * sinPhi * Math.cos(theta),
    CAMERA_PIVOT.y + distance * Math.cos(phi),
    CAMERA_PIVOT.z + distance * sinPhi * Math.sin(theta),
  );
}

function buildClippingPlanes(slice) {
  if (!slice?.enabled) return [];
  const coord = Number(slice.coord);
  if (!Number.isFinite(coord)) return [];
  const axis = ['x', 'y', 'z'].includes(slice.axis) ? slice.axis : 'y';
  const normal = new THREE.Vector3(
    axis === 'x' ? 1 : 0,
    axis === 'y' ? 1 : 0,
    axis === 'z' ? 1 : 0,
  );
  const thickness = Math.max(0.02, Number(slice.thicknessMm) || 0.3);
  if (slice.mode === 'slab') {
    const half = thickness / 2;
    return [
      new THREE.Plane(normal.clone(), -(coord - half)),
      new THREE.Plane(normal.clone().negate(), coord + half),
    ];
  }
  const keepAbove = slice.mode === 'above';
  return keepAbove
    ? [new THREE.Plane(normal, -coord)]
    : [new THREE.Plane(normal.negate(), coord)];
}

export default function MriSurfaceOverlay({
  active,
  layers,
  backendBaseUrl,
  authToken,
  cameraState,
  viewportSize,
  interactive,
  slice,
}) {
  const mountRef = useRef(null);
  const rendererRef = useRef(null);
  const sceneRef = useRef(null);
  const cameraRef = useRef(null);
  const groupRef = useRef(null);
  const frameRef = useRef(0);
  const cacheRef = useRef(new Map());
  const loadingRef = useRef(new Map());
  const visibleLayerRef = useRef(new Map());
  const [lodLevel, setLodLevel] = useState(2);

  const desiredLod = useMemo(() => (interactive ? 2 : 1), [interactive]);
  const activeLayers = useMemo(
    () => SURFACE_LAYERS.filter((layer) => Boolean(active && layers?.[layer.uiKey])),
    [active, layers],
  );

  useEffect(() => {
    setLodLevel(2);
    const tid = window.setTimeout(() => {
      setLodLevel(desiredLod);
    }, desiredLod === 2 ? 0 : LOD_SETTLE_DELAY_MS);
    return () => window.clearTimeout(tid);
  }, [desiredLod]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return undefined;
    const cache = cacheRef.current;
    const loading = loadingRef.current;

    const renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance',
    });
    renderer.setClearColor(0x000000, 0);
    renderer.localClippingEnabled = true;
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.pointerEvents = 'none';
    renderer.domElement.dataset.mriSurfaceOverlay = 'true';

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(UNITY_FOV_DEG, 1, 0.05, 100);
    const group = new THREE.Group();
    const ambient = new THREE.AmbientLight(0xffffff, 0.62);
    const key = new THREE.DirectionalLight(0xffffff, 1.35);
    key.position.set(2.5, 4.5, 3.5);
    const rim = new THREE.DirectionalLight(0xb8ecff, 0.55);
    rim.position.set(-4, 1.8, -3);
    scene.add(ambient, key, rim, group);

    mount.appendChild(renderer.domElement);
    rendererRef.current = renderer;
    sceneRef.current = scene;
    cameraRef.current = camera;
    groupRef.current = group;

    const render = () => {
      frameRef.current = window.requestAnimationFrame(render);
      if (!active) return;
      renderer.render(scene, camera);
    };
    render();

    return () => {
      window.cancelAnimationFrame(frameRef.current);
      cache.forEach((mesh) => disposeMesh(mesh));
      cache.clear();
      loading.clear();
      renderer.dispose();
      if (renderer.domElement.parentElement === mount) {
        mount.removeChild(renderer.domElement);
      }
      rendererRef.current = null;
      sceneRef.current = null;
      cameraRef.current = null;
      groupRef.current = null;
    };
  }, [active]);

  useEffect(() => {
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    const mount = mountRef.current;
    if (!renderer || !camera || !mount) return;
    const width = Math.max(1, Math.floor(Number(viewportSize?.width) || mount.clientWidth || 1));
    const height = Math.max(1, Math.floor(Number(viewportSize?.height) || mount.clientHeight || 1));
    renderer.setSize(width, height, false);
    renderer.setPixelRatio(Math.min(1.75, window.devicePixelRatio || 1));
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }, [viewportSize?.height, viewportSize?.width]);

  useEffect(() => {
    const camera = cameraRef.current;
    if (!camera) return;
    camera.position.copy(cameraPositionFromState(cameraState));
    camera.up.set(0, 1, 0);
    camera.lookAt(CAMERA_PIVOT);
    camera.updateMatrixWorld();
  }, [cameraState]);

  useEffect(() => {
    const planes = buildClippingPlanes(slice);
    cacheRef.current.forEach((mesh) => {
      if (mesh.material) {
        mesh.material.clippingPlanes = planes;
        mesh.material.needsUpdate = true;
      }
    });
  }, [slice]);

  useEffect(() => {
    const group = groupRef.current;
    if (!group) return undefined;
    let cancelled = false;

    const applyVisibility = () => {
      const visibleByLayer = visibleLayerRef.current;
      cacheRef.current.forEach((mesh, key) => {
        mesh.visible = Boolean(active && visibleByLayer.get(mesh.userData.apiLayer) === key);
      });
    };

    const addMeshToGroup = (mesh) => {
      if (mesh.parent !== group) group.add(mesh);
    };

    const showCachedFallback = (layer, requestedKey) => {
      const fallbackKeys = [
        requestedKey,
        `${layer.apiLayer}:2`,
        `${layer.apiLayer}:1`,
        `${layer.apiLayer}:0`,
      ];
      const fallbackKey = fallbackKeys.find((key) => cacheRef.current.has(key));
      if (!fallbackKey) return false;
      const mesh = cacheRef.current.get(fallbackKey);
      addMeshToGroup(mesh);
      visibleLayerRef.current.set(layer.apiLayer, fallbackKey);
      return true;
    };

    const loadLayer = async (layer) => {
      const lod = lodLevel;
      const requestedKey = `${layer.apiLayer}:${lod}`;
      if (cacheRef.current.has(requestedKey)) {
        addMeshToGroup(cacheRef.current.get(requestedKey));
        visibleLayerRef.current.set(layer.apiLayer, requestedKey);
        applyVisibility();
        return;
      }
      showCachedFallback(layer, requestedKey);
      applyVisibility();
      if (loadingRef.current.has(requestedKey)) return;

      const task = fetch(
        apiUrl(backendBaseUrl, `/api/mri_surface_mesh_lod/${encodeURIComponent(layer.apiLayer)}?lod=${lod}`),
        fetchOptions(authToken),
      )
        .then((res) => {
          if (!res.ok) throw new Error(`MRI surface ${layer.apiLayer} LOD${lod} HTTP ${res.status}`);
          return res.text();
        })
        .then((objText) => {
          if (cancelled) return null;
          const geometry = parseSurfaceObj(objText);
          const material = makeMaterial(layer);
          material.clippingPlanes = buildClippingPlanes(slice);
          const mesh = new THREE.Mesh(geometry, material);
          mesh.name = `mri-surface-${layer.apiLayer}-lod${lod}`;
          mesh.renderOrder = layer.renderOrder;
          mesh.userData = {
            apiLayer: layer.apiLayer,
            lod,
            cacheKey: requestedKey,
          };
          cacheRef.current.set(requestedKey, mesh);
          addMeshToGroup(mesh);
          visibleLayerRef.current.set(layer.apiLayer, requestedKey);
          applyVisibility();
          return mesh;
        })
        .catch((err) => {
          if (err?.name !== 'AbortError') {
            // Keep Unity as the primary renderer; overlay failures should not block the 3D view.
            console.warn('[MriSurfaceOverlay] surface load failed', err);
          }
          return null;
        })
        .finally(() => {
          loadingRef.current.delete(requestedKey);
        });
      loadingRef.current.set(requestedKey, task);
    };

    const wantedLayerNames = new Set(activeLayers.map((layer) => layer.apiLayer));
    SURFACE_LAYERS.forEach((layer) => {
      if (!wantedLayerNames.has(layer.apiLayer)) {
        visibleLayerRef.current.delete(layer.apiLayer);
      }
    });
    activeLayers.forEach((layer) => {
      loadLayer(layer);
    });
    applyVisibility();

    return () => {
      cancelled = true;
    };
  }, [active, activeLayers, authToken, backendBaseUrl, lodLevel, slice]);

  const display = active && activeLayers.length > 0 ? 'block' : 'none';

  return (
    <div
      ref={mountRef}
      aria-hidden="true"
      data-mri-surface-overlay={display === 'block' ? 'active' : 'hidden'}
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 2,
        display,
        pointerEvents: 'none',
      }}
    />
  );
}
