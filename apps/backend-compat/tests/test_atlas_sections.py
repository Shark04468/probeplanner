"""Real section pixels and coordinates, independent of Unity and registration."""
import base64
import hashlib
import json
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from backend.atlas_sections import AtlasSectionSource, SectionError, slice_array


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def profile(tmp_path):
    shape = (7, 5, 9)
    a, d, m = np.indices(shape)
    intensity = (a * 100 + d * 10 + m + 1).astype(np.float32)
    labels = np.zeros(shape, np.uint32)
    labels[2:5, 1:4, 3:6] = 17
    np.save(tmp_path / 'annotation.npy', labels)
    np.save(tmp_path / 'jacobian.npy', np.ones(shape, np.float32))
    image = nib.Nifti1Image(intensity, np.diag([-.025, -.025, .025, 1.]))
    image.header.set_xyzt_units('mm')
    nib.save(image, tmp_path / 'mri.nii.gz')
    (tmp_path / 'surfaces.json').write_text(json.dumps({'coordinateOrder': 'AP_um,DV_um,ML_um'}))
    artifacts = {key: {'path': filename, 'sha256': digest(tmp_path / filename), 'required': True}
                 for key, filename in [('annotation', 'annotation.npy'), ('warped_mri', 'mri.nii.gz'),
                                       ('jacobian', 'jacobian.npy'), ('mri_surface_meshes', 'surfaces.json')]}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'schemaVersion': 'probeplanner.mri_ccf.artifact_manifest.v1',
        'atlasShape': shape, 'voxelSizeUm': 25, 'artifacts': artifacts}))
    return manifest, intensity, labels


@pytest.mark.parametrize('axis,index,expected', [
    ('coronal', 2, lambda v: v[2, :, :]),
    ('horizontal', 3, lambda v: v[:, 3, :]),
    ('sagittal', 4, lambda v: v[:, :, 4].T),
])
def test_asymmetric_slice_axes(axis, index, expected, profile):
    _, volume, _ = profile
    np.testing.assert_array_equal(slice_array(volume, axis, index), expected(volume))


@pytest.mark.parametrize('axis,index,origin,u,v', [
    ('x', 4, [-5.6, .4525, 5.4125], [0, 0, -.175], [0, -.125, 0]),
    ('y', 3, [-5.7125, .365, 5.4125], [.225, 0, 0], [0, 0, -.175]),
    ('z', 2, [-5.7125, .4525, 5.35], [.225, 0, 0], [0, -.125, 0]),
])
def test_section_world_edges_and_raw_rows(axis, index, origin, u, v, profile):
    manifest, volume, _ = profile
    source = AtlasSectionSource(manifest)
    coordinate = {'x': index * .025 - 5.7, 'y': .44 - index * .025, 'z': 5.4 - index * .025}[axis]
    result = source.section(axis, coordinate, 'mri_only')
    np.testing.assert_allclose(result['origin'], origin, atol=1e-6)
    np.testing.assert_allclose(result['u'], u, atol=1e-6)
    np.testing.assert_allclose(result['v'], v, atol=1e-6)
    assert result['labelIndex'] == index
    assert result['rowOrder'] == 'origin_to_v'
    assert result['sourceHashes']['warped_mri'] == digest(manifest.parent / 'mri.nii.gz')
    rgba = np.frombuffer(base64.b64decode(result['rgbaBase64']), np.uint8).reshape(result['height'], result['width'], 4)
    actual = slice_array(volume, axis, index)
    lo, hi = result['intensityWindow']
    expected = np.rint(np.clip((actual - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    np.testing.assert_array_equal(rgba[:, :, 0], expected)
    np.testing.assert_array_equal(rgba[:, :, 0], rgba[:, :, 1])
    np.testing.assert_array_equal(rgba[:, :, 3], (expected > 0).astype(np.uint8) * 255)
    assert result['supportRule'] == 'display_window_nonzero_or_ccf_labels_in_overlay'


def test_interpolates_intensity_not_label_ids(profile):
    manifest, _, _ = profile
    source = AtlasSectionSource(manifest)
    result = source.section('z', 5.4 - 2.25 * .025, 'mri_only')
    assert result['intensityInterpolation'] == 'linear_normal_to_plane'
    assert result['sampleIndex'] == pytest.approx(2.25)
    assert result['labelIndex'] == 2
    assert result['planeWorldMm'] == pytest.approx(5.34375)


def test_checkerboard_really_contains_mri_and_no_fake_jacobian(profile):
    manifest, _, _ = profile
    source = AtlasSectionSource(manifest)
    pixels, info = source.qc_image('coronal', 3, 'checkerboard')
    assert info['comparison'] == 'warped_mri_intensity_vs_ccf_labels'
    assert np.any((pixels[:, :, 0] == pixels[:, :, 1]) & (pixels[:, :, 1] == pixels[:, :, 2]) & (pixels[:, :, 0] > 0))
    data = json.loads(manifest.read_text())
    del data['artifacts']['jacobian']
    manifest.write_text(json.dumps(data))
    source = AtlasSectionSource(manifest)
    with pytest.raises(SectionError, match='jacobian'):
        source.qc_image('coronal', 3, 'jacobian')


@pytest.mark.parametrize('mutation', ['hash', 'shape', 'affine', 'order', 'missing_mri'])
def test_untrusted_profile_fails_closed(profile, mutation):
    manifest, _, _ = profile
    data = json.loads(manifest.read_text())
    if mutation == 'hash':
        data['artifacts']['warped_mri']['sha256'] = '0' * 64
    elif mutation == 'shape':
        data['atlasShape'] = [5, 7, 9]
    elif mutation == 'affine':
        path = manifest.parent / 'mri.nii.gz'
        image = nib.load(path)
        nib.save(nib.Nifti1Image(image.get_fdata(), np.diag([.025, .025, .025, 1.])), path)
        data['artifacts']['warped_mri']['sha256'] = digest(path)
    elif mutation == 'order':
        path = manifest.parent / 'surfaces.json'
        path.write_text(json.dumps({'coordinateOrder': 'ML_um,DV_um,AP_um'}))
        data['artifacts']['mri_surface_meshes']['sha256'] = digest(path)
    else:
        del data['artifacts']['warped_mri']
    manifest.write_text(json.dumps(data))
    with pytest.raises(SectionError):
        AtlasSectionSource(manifest)


def test_changed_input_invalidates_loaded_pixels(profile):
    manifest, _, _ = profile
    source = AtlasSectionSource(manifest)
    source.section('z', 5.325, 'mri_only')
    with (manifest.parent / 'mri.nii.gz').open('ab') as stream:
        stream.write(b'changed input')
    with pytest.raises(SectionError, match='changed'):
        source.section('z', 5.325, 'mri_only')


def test_black_window_background_does_not_make_an_opaque_rectangle(profile):
    source = AtlasSectionSource(profile[0])
    result = source.section('z', 5.4, 'mri_only')
    rgba = np.frombuffer(base64.b64decode(result['rgbaBase64']), np.uint8).reshape(result['height'], result['width'], 4)
    assert np.any(rgba[:, :, 0] == 0)
    assert np.all(rgba[:, :, 3][rgba[:, :, 0] == 0] == 0)
    assert np.all(rgba[:, :, 3][rgba[:, :, 0] > 0] == 255), 'No CCF mask may censor visible MRI'


@pytest.mark.parametrize('axis,coordinate,mode', [('q', 0, 'mri_only'), ('z', float('nan'), 'mri_only'),
    ('z', 5.325, 'invented'), ('z', 100, 'mri_only')])
def test_invalid_section_rejected(axis, coordinate, mode, profile):
    source = AtlasSectionSource(profile[0])
    with pytest.raises(SectionError):
        source.section(axis, coordinate, mode)
