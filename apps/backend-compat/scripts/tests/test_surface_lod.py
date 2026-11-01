"""Display-mesh fidelity tests; fixtures do not represent biological evidence."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from vtkmodules.vtkFiltersCore import vtkPolyDataConnectivityFilter
from vtkmodules.vtkIOGeometry import vtkOBJReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_mri_surface_meshes as builder


def component_count(path):
    reader = vtkOBJReader()
    reader.SetFileName(str(path))
    reader.Update()
    connectivity = vtkPolyDataConnectivityFilter()
    connectivity.SetInputData(reader.GetOutput())
    connectivity.SetExtractionModeToAllRegions()
    connectivity.Update()
    return connectivity.GetNumberOfExtractedRegions()


def arguments(tmp_path, method=None):
    mask = np.zeros((24, 24, 24), dtype=np.uint8)
    mask[8:14, 8:14, 8:14] = 1
    mask[4, 2:18, 2:18] = 1
    mask[18, 18, 18] = 1
    np.save(tmp_path / 'skull_mask.npy', mask)
    (tmp_path / 'artifact_manifest.json').write_text(json.dumps({'atlasShape': [24, 24, 24]}))
    kwargs = dict(profile_dir=tmp_path, annotation=None, skull_mask=None,
        outer_tissue_mask=None, output_dir=tmp_path / 'surfaces', layers='skull',
        lod_steps='1,2,4', voxel_size_um=25.0, update_artifact_manifest=False)
    if method is not None:
        kwargs['lod_method'] = method
    return SimpleNamespace(**kwargs)


def test_default_lods_preserve_thin_membrane_and_single_voxel_components(tmp_path):
    args = arguments(tmp_path)
    original = (tmp_path / 'skull_mask.npy').read_bytes()
    manifest = builder.build(args)
    counts = [component_count(tmp_path / r['path']) for r in manifest['layers']['skull']['lods'].values()]
    assert counts == [3, 3, 3]
    assert (tmp_path / 'skull_mask.npy').read_bytes() == original


def test_explicit_legacy_lods_are_retained_and_identified(tmp_path):
    args = arguments(tmp_path, 'legacy_steps')
    manifest = builder.build(args)
    assert manifest['lodMethod'] == 'legacy_steps'
    counts = [component_count(tmp_path / r['path']) for r in manifest['layers']['skull']['lods'].values()]
    assert counts[0] == 3 and counts[1] < 3 and counts[2] < 3


def test_invalid_lod_method_fails_before_writing_meshes(tmp_path):
    args = arguments(tmp_path, 'unknown')
    with pytest.raises(ValueError, match='LOD method'):
        builder.build(args)
    assert not args.output_dir.exists()


def test_new_lod_manifest_records_source_and_fidelity_controls(tmp_path):
    args = arguments(tmp_path)
    manifest = builder.build(args)
    assert manifest['lodMethod'] == 'preserve_topology'
    assert manifest['layers']['skull']['sourceSha256'] == builder.sha256_file(tmp_path / 'skull_mask.npy')
    for i, record in enumerate(manifest['layers']['skull']['lods'].values()):
        assert record['stepSize'] == 1
        assert record['method'] == ('marching_cubes_full_resolution' if i == 0 else 'vtkDecimatePro')
        assert record['topology']['components'] == 3
        assert record['topology']['boundaryEdges'] == 0
        assert record['topology']['nonManifoldEdges'] == 0
        if i:
            assert record['preserveTopology'] is True
            assert record['splitting'] is False
            assert record['boundaryVertexDeletion'] is False
            assert record['actualReduction'] <= record['requestedReduction'] + 0.01


def test_no_implicit_coarse_fallback_when_optional_builder_is_missing(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    monkeypatch.setitem(sys.modules, 'surface_mesh_lod', None)
    with pytest.raises(ImportError):
        builder.build(args)
    assert not args.output_dir.exists()


def test_preserving_lods_reject_non_full_resolution_reference(tmp_path):
    args = arguments(tmp_path)
    args.lod_steps = '2,4'
    with pytest.raises(ValueError, match='full resolution'):
        builder.build(args)
    assert not args.output_dir.exists()
