"""Hash-bound sections of the legacy AP,DV,ML CCF index grid, not native anatomy.

NIfTI axes in this legacy profile describe an origin/direction-neutral SimpleITK
grid. Reorienting them to anatomical codes would disagree with its CCF labels.
The surface contract and neutral affine are therefore checked explicitly.
"""
import base64
import hashlib
import json
from pathlib import Path

import nibabel as nib
import numpy as np


class SectionError(ValueError):
    pass


AXES = {'coronal': 0, 'ap': 0, 'z': 0, 'horizontal': 1, 'dv': 1, 'y': 1,
        'sagittal': 2, 'ml': 2, 'x': 2}
BREGMA_MM = np.array([5.4, .44, 5.7])


def axis_index(axis):
    try:
        return AXES[axis]
    except (KeyError, TypeError) as exc:
        raise SectionError('Unsupported section axis') from exc


def slice_array(volume, axis, index):
    dimension = axis_index(axis)
    if not isinstance(index, (int, np.integer)) or not 0 <= index < volume.shape[dimension]:
        raise SectionError('Section index outside volume')
    plane = volume[index, :, :] if dimension == 0 else volume[:, index, :] if dimension == 1 else volume[:, :, index]
    return plane.T if dimension == 2 else plane


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def edges(labels):
    mask = np.zeros(labels.shape, bool)
    mask[1:, :] |= labels[1:, :] != labels[:-1, :]
    mask[:-1, :] |= labels[1:, :] != labels[:-1, :]
    mask[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    mask[:, :-1] |= labels[:, 1:] != labels[:, :-1]
    return mask & (labels != 0)


def label_rgb(labels):
    values = np.asarray(labels, np.uint32)
    rgb = np.stack([((values * a + b) % 255).astype(np.uint8)
                    for a, b in [(37, 23), (67, 71), (97, 113)]], axis=-1)
    rgb[values == 0] = 0
    return rgb


class AtlasSectionSource:
    """One immutable verified profile, with one MRI array and mapped label arrays."""

    def __init__(self, manifest_path, expected_manifest_hash=None):
        self.manifest_path = Path(manifest_path).resolve()
        self.paths, self.hashes, self.stamps = {}, {}, {}
        try:
            self.stamps[self.manifest_path] = fingerprint(self.manifest_path)
            self.manifest_hash = sha256(self.manifest_path)
            if expected_manifest_hash and expected_manifest_hash != self.manifest_hash:
                raise SectionError('Bound section manifest hash mismatch')
            data = json.loads(self.manifest_path.read_text(encoding='utf-8'))
            if data.get('schemaVersion') != 'probeplanner.mri_ccf.artifact_manifest.v1' or data.get('valid') is False:
                raise SectionError('Unsupported or invalid section grid contract')
            artifacts = data.get('artifacts', {})
            roles = {'annotation', 'warped_mri', 'mri_surface_meshes'}
            if not roles.issubset(artifacts):
                raise SectionError('Section requires bound annotation, warped_mri and surface coordinate contract')
            for key, record in artifacts.items():
                if not record.get('required') and key not in roles | {'jacobian'}:
                    continue
                path = (self.manifest_path.parent / record['path']).resolve()
                if self.manifest_path.parent not in path.parents:
                    raise SectionError('Section artifact path escapes profile')
                self.stamps[path] = fingerprint(path)
                actual = sha256(path)
                if actual != record.get('sha256'):
                    raise SectionError('Section artifact hash mismatch: ' + key)
                self.paths[key], self.hashes[key] = path, actual
            surface = json.loads(self.paths['mri_surface_meshes'].read_text(encoding='utf-8'))
            if surface.get('coordinateOrder') != 'AP_um,DV_um,ML_um':
                raise SectionError('Unsupported section coordinate order')
            self.spacing = float(data['voxelSizeUm']) / 1000
            if not np.isfinite(self.spacing) or self.spacing <= 0:
                raise SectionError('Invalid section voxel spacing')
            self.annotation = np.load(self.paths['annotation'], mmap_mode='r', allow_pickle=False)
            self.shape = tuple(data['atlasShape'])
            if len(self.shape) != 3 or self.annotation.shape != self.shape or self.annotation.dtype.kind not in 'ui':
                raise SectionError('Section annotation shape or type mismatch')
            image = nib.load(self.paths['warped_mri'])
            expected_affine = np.diag([-self.spacing, -self.spacing, self.spacing, 1.])
            if image.shape != self.shape or not np.allclose(image.affine, expected_affine, atol=1e-7, rtol=0):
                raise SectionError('Section MRI shape or legacy index-grid affine mismatch')
            if image.header.get_xyzt_units()[0] != 'mm':
                raise SectionError('Section MRI grid units must be mm')
            self.mri = image.get_fdata(dtype=np.float32, caching='unchanged')
            flat = self.mri.ravel(order='K')
            if any(not np.isfinite(flat[start:start + 1048576]).all() for start in range(0, flat.size, 1048576)):
                raise SectionError('Section MRI contains nonfinite intensities')
            # A bounded deterministic sample gives a shared window across all planes.
            sample = flat[::max(1, flat.size // 1000000)]
            sample = sample[sample != 0]
            if not sample.size:
                raise SectionError('Section MRI has no nonzero intensity samples')
            lo, hi = np.percentile(sample, [1, 99])
            if hi <= lo:
                lo, hi = float(sample.min()), float(sample.max())
            if hi <= lo:
                hi = lo + 1.
            self.window = float(lo), float(hi)
            self.verify_unchanged()
        except SectionError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise SectionError('Section source unavailable or invalid') from exc

    def verify_unchanged(self):
        try:
            if any(fingerprint(path) != stamp for path, stamp in self.stamps.items()):
                raise SectionError('Section source changed after verification')
        except OSError as exc:
            raise SectionError('Section source changed or missing after verification') from exc

    def world_coordinate(self, axis, index):
        dimension = axis_index(axis)
        value = float(index) * self.spacing - BREGMA_MM[dimension]
        return value if dimension == 2 else -value

    def _pixels(self, axis, coordinate, mode):
        self.verify_unchanged()
        dimension = axis_index(axis)
        if mode not in {'mri_only', 'mri_ccf_overlay', 'ccf_only'}:
            raise SectionError('Unsupported section display mode')
        if not np.isfinite(coordinate):
            raise SectionError('Section coordinate must be finite')
        idx = ((coordinate if dimension == 2 else -coordinate) + BREGMA_MM[dimension]) / self.spacing
        if idx < -.5 - 1e-5 or idx > self.shape[dimension] - .5 + 1e-5:
            raise SectionError('Section plane outside volume')
        sample_idx = float(np.clip(idx, 0, self.shape[dimension] - 1))
        low, high = int(np.floor(sample_idx)), int(np.ceil(sample_idx))
        fraction = sample_idx - low
        intensity = np.asarray(slice_array(self.mri, axis, low), np.float32)
        if high != low:
            intensity = intensity * (1 - fraction) + slice_array(self.mri, axis, high) * fraction
        label_idx = int(np.clip(np.floor(sample_idx + .5), 0, self.shape[dimension] - 1))
        labels = slice_array(self.annotation, axis, label_idx)
        lo, hi = self.window
        gray = np.rint(np.clip((intensity - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
        rgb = label_rgb(labels) if mode == 'ccf_only' else np.repeat(gray[:, :, None], 3, axis=2)
        if mode == 'mri_ccf_overlay':
            rgb[edges(labels)] = [105, 176, 181]
        support = labels != 0 if mode == 'ccf_only' else gray > 0
        if mode == 'mri_ccf_overlay':
            support = support | (labels != 0)
        rgba = np.concatenate([rgb, (support.astype(np.uint8) * 255)[:, :, None]], axis=2)
        return rgba, labels, sample_idx, label_idx

    def section(self, axis, coordinate, mode='mri_ccf_overlay'):
        rgba, _, sample_idx, label_idx = self._pixels(axis, float(coordinate), mode)
        ap0, dv0, ml0 = -.5 * self.spacing - BREGMA_MM
        ap_size, dv_size, ml_size = np.array(self.shape) * self.spacing
        dimension = axis_index(axis)
        origin, u, v = {
            0: ([ml0, -dv0, coordinate], [ml_size, 0, 0], [0, -dv_size, 0]),
            1: ([ml0, coordinate, -ap0], [ml_size, 0, 0], [0, 0, -ap_size]),
            2: ([coordinate, -dv0, -ap0], [0, 0, -ap_size], [0, -dv_size, 0]),
        }[dimension]
        self.verify_unchanged()
        return {'schemaVersion': 'probeplanner.atlas_section.v1', 'width': rgba.shape[1], 'height': rgba.shape[0],
                'rgbaBase64': base64.b64encode(rgba.tobytes()).decode('ascii'), 'pixelFormat': 'RGBA32',
                'rowOrder': 'origin_to_v', 'origin': origin, 'u': u, 'v': v,
                'planeWorldMm': coordinate, 'axis': ['z', 'y', 'x'][dimension], 'mode': mode,
                'sampleIndex': sample_idx, 'labelIndex': label_idx,
                'intensityInterpolation': 'linear_normal_to_plane', 'labelInterpolation': 'nearest',
                'intensityWindow': self.window, 'windowMethod': 'global_nonzero_sample_p1_p99',
                'supportRule': 'display_window_nonzero_or_ccf_labels_in_overlay',
                'sourceHashes': {key: self.hashes[key] for key in ('warped_mri', 'annotation')},
                'manifestSha256': self.manifest_hash, 'coordinateOrder': 'AP_um,DV_um,ML_um',
                'scope': 'population_atlas_display_not_anatomical_validation'}

    def qc_image(self, axis, index, mode):
        dimension = axis_index(axis)
        index = self.shape[dimension] // 2 if index is None else index
        if not isinstance(index, (int, np.integer)) or not 0 <= index < self.shape[dimension]:
            raise SectionError('Section index outside volume')
        coordinate = self.world_coordinate(axis, index)
        rgba, labels, _, _ = self._pixels(axis, coordinate, 'mri_ccf_overlay' if mode == 'boundary' else 'mri_only')
        rgb = rgba[:, :, :3].copy()
        comparison = 'warped_mri_intensity_with_ccf_label_boundaries'
        if mode == 'checkerboard':
            yy, xx = np.indices(labels.shape)
            tile = max(1, min(64, min(labels.shape) // 8))
            alternate = ((yy // tile + xx // tile) % 2) != 0
            rgb[alternate] = label_rgb(labels)[alternate]
            comparison = 'warped_mri_intensity_vs_ccf_labels'
        elif mode == 'jacobian':
            if 'jacobian' not in self.paths:
                raise SectionError('Bound jacobian is unavailable')
            volume = np.load(self.paths['jacobian'], mmap_mode='r', allow_pickle=False)
            if volume.shape != self.shape:
                raise SectionError('Section jacobian shape mismatch')
            plane = slice_array(volume, axis, index)
            if not np.isfinite(plane).all():
                raise SectionError('Section jacobian contains nonfinite values')
            # Fixed range preserves comparability between slices; folds are black.
            deviation = np.clip(plane - 1, -1, 1)
            neutral = np.full((*plane.shape, 3), 245., np.float32)
            colour = np.where((deviation >= 0)[..., None], [178, 68, 64], [60, 112, 169])
            rgb = np.rint(neutral + np.abs(deviation)[..., None] * (colour - neutral)).astype(np.uint8)
            rgb[plane <= 0] = 0
            comparison = 'jacobian_determinant_fixed_range_0_to_2_folds_black'
        elif mode != 'boundary':
            raise SectionError('Unsupported QC overlay mode')
        self.verify_unchanged()
        return rgb, {'comparison': comparison, 'axis': ['coronal', 'horizontal', 'sagittal'][dimension],
                     'index': index, 'manifestSha256': self.manifest_hash,
                     'intensityWindow': self.window, 'sourceHashes': self.hashes,
                     'rowOrder': 'origin_to_v', 'scope': 'display_not_anatomical_validation'}
