import json
from io import BytesIO

import numpy as np
from PIL import Image

from test_optimizer_server import server
from test_atlas_sections import profile


def configure(server, monkeypatch, profile):
    manifest, _, _ = profile
    monkeypatch.setattr(server, 'ACTIVE_ATLAS_MODE', 'mri_ccf_hybrid')
    monkeypatch.setattr(server, 'MRI_CCF_MANIFEST_PATH', str(manifest))
    # The legacy route used this payload and silently ignored warped MRI.
    monkeypatch.setattr(server, '_mri_payload', lambda: {
        'annotationPath': str(manifest.parent / 'annotation.npy'),
        'warpedMriPath': str(manifest.parent / 'mri.nii.gz'),
        'manifestPath': str(manifest), 'manifestValid': True})
    return server.app.test_client()


def test_qc_pixels_depend_on_mri_not_only_labels(server, monkeypatch, profile):
    client = configure(server, monkeypatch, profile)
    response = client.get('/api/mri/registration/qc/overlay_image?mode=checkerboard&axis=horizontal&index=2')
    assert response.status_code == 200
    rgb = np.array(Image.open(BytesIO(response.data)))
    assert rgb.shape[:2] == (7, 9), 'Horizontal cuts DV, not ML'
    assert response.headers.get('X-Section-Comparison') == 'warped_mri_intensity_vs_ccf_labels'
    assert np.any((rgb[:, :, 0] == rgb[:, :, 1]) & (rgb[:, :, 1] == rgb[:, :, 2]) & (rgb[:, :, 0] > 0))


def test_qc_missing_mri_is_not_replaced_by_gray_annotation(server, monkeypatch, profile):
    client = configure(server, monkeypatch, profile)
    manifest = profile[0]
    data = json.loads(manifest.read_text())
    del data['artifacts']['warped_mri']
    manifest.write_text(json.dumps(data))
    response = client.get('/api/mri/registration/qc/overlay_image?mode=checkerboard')
    assert response.status_code == 409
    assert response.json['code'] == 'section_source_unavailable'


def test_real_section_endpoint_and_changed_file_rejection(server, monkeypatch, profile):
    client = configure(server, monkeypatch, profile)
    url = '/api/mri/section?axis=z&coordinate=5.325&mode=mri_only'
    response = client.get(url)
    assert response.status_code == 200
    assert response.json['width'] == 9 and response.json['height'] == 5
    assert response.headers['Cache-Control'] == 'no-store'
    with (profile[0].parent / 'mri.nii.gz').open('ab') as stream:
        stream.write(b'mutated')
    assert client.get(url).status_code == 409


def test_section_invalid_coordinate_and_inactive_atlas(server, monkeypatch, profile):
    client = configure(server, monkeypatch, profile)
    assert client.get('/api/mri/section?axis=y&coordinate=nan').status_code == 400
    monkeypatch.setattr(server, 'ACTIVE_ATLAS_MODE', 'allen_sdk')
    assert client.get('/api/mri/section?axis=y&coordinate=0').status_code == 409
