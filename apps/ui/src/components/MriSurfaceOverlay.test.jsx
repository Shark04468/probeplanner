import { expect, it } from 'vitest';
import * as THREE from 'three';
import { parseSurfaceObj, cameraPositionFromState, synchronizeCamera, updateUnityProjection, buildClippingPlanes, surfaceLodMetadata } from './MriSurfaceOverlay';
import { buildMriAppearancePayload, getCameraBasis, projectPoint } from './Brain3DView';
import { ccfUmToViewer } from './brain3d/ccfCoordinates';

it('reports actual fallback surface LOD and never replaces missing provenance with the requested LOD', () => {
  expect(surfaceLodMetadata(new Headers({ 'X-Lod': '0', 'X-Cache-Source': 'memory' }), 2))
    .toEqual({ requestedLod: 2, resolvedLod: 0, cacheSource: 'memory' });
  expect(surfaceLodMetadata(new Headers(), 2).resolvedLod).toBeNull();
  expect(surfaceLodMetadata(new Headers({ 'X-Lod': '-1' }), 2).resolvedLod).toBeNull();
});

it('maps actual OBJ vertices to the same CCF frame as the atlas and probes', () => {
  const vertices = [[8127, 440, 3466], [8127, 5684.1, 2541.3], [5400, 440, 5700]];
  const obj = vertices.map(v => `v ${v.join(' ')}`).join('\n') + '\nf 1 2 3\n';
  const geometry = parseSurfaceObj(obj);
  vertices.forEach((point, index) => {
    const actual = geometry.getAttribute('position');
    const expected = ccfUmToViewer(point);
    expect(actual.getX(index)).toBeCloseTo(expected.x, 5);
    expect(actual.getY(index)).toBeCloseTo(expected.y, 5);
    expect(actual.getZ(index)).toBeCloseTo(expected.z, 5);
  });
  geometry.dispose();
});

it('tracks the actual orbit pivot instead of a fixed brain-center camera', () => {
  const state = { theta: 0, phi: Math.PI / 2, distance: 10, pivot: { x: 2, y: -3, z: -2 } };
  expect(cameraPositionFromState(state).toArray()).toEqual([12, -3 + 10 * Math.cos(Math.PI / 2), -2]);
});

it('aligns the overlay camera target and field of view after a focus operation', () => {
  const camera = new THREE.PerspectiveCamera(38, 1.5, 0.05, 100);
  const state = { theta: 0.7, phi: 1.2, distance: 12, pivot: { x: 2, y: -3, z: -2 }, fov: 45 };
  synchronizeCamera(camera, state);
  expect(camera.fov).toBe(45);
  const projectedPivot = new THREE.Vector3(2, -3, -2).project(camera);
  expect(projectedPivot.x).toBeCloseTo(0, 6);
  expect(projectedPivot.y).toBeCloseTo(0, 6);
});

it('assigns anatomical meshes to one renderer without disabling CCF labels', () => {
  expect(buildMriAppearancePayload(true, { brainSurface: true, overlay: true, ccfLabels: true }, 'mri_ccf_hybrid', true))
    .toMatchObject({ enabled: true, surfaceRenderer: 'unity', showBrainSurface: true, showCcfLabels: true });
});

it.each([0.5, 1.5])('matches Unity screen handedness for MRI and labels at aspect=%s', aspect => {
  const camera = new THREE.PerspectiveCamera(38, aspect, 0.05, 100);
  const state = { theta: -Math.PI / 2, phi: Math.PI / 2, distance: 10, pivot: { x: 0, y: 0, z: 0 }, fov: 38 };
  synchronizeCamera(camera, state);
  const projected = new THREE.Vector3(1, 0, 0).project(camera);
  // Unity WorldToViewportPoint(+X) is right of center when looking from (0,0,-10).
  expect(projected.x).toBeGreaterThan(0);
  const point = projectPoint({ x: 1, y: 0, z: 0 }, getCameraBasis(state), aspect * 1000, 1000);
  expect(point.x / (aspect * 1000)).toBeCloseTo((projected.x + 1) / 2, 8);
  updateUnityProjection(camera);
  expect(new THREE.Vector3(1, 0, 0).project(camera).x).toBeCloseTo(projected.x, 8);
});

it.each(['left', 'right'])('applies the %s hemisphere filter together with the native slice plane', hemisphere => {
  const planes = buildClippingPlanes({ enabled: true, axis: 'y', coord: -4, mode: 'below' }, hemisphere);
  const x = hemisphere === 'left' ? -1 : 1;
  const retained = point => planes.every(plane => plane.distanceToPoint(new THREE.Vector3(...point)) >= 0);
  expect(retained([x, -5, 0])).toBe(true);
  expect(retained([-x, -5, 0])).toBe(false);
  expect(retained([x, -3, 0])).toBe(false);
  expect(buildClippingPlanes({ enabled: false }, hemisphere)).toHaveLength(1);
  expect(buildClippingPlanes({ enabled: false }, 'all')).toHaveLength(0);
});
