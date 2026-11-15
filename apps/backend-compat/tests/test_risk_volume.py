import concurrent.futures
import gc
import json
import threading

import numpy as np
import pytest

from test_optimizer_server import server
from backend.risk_volume import publish_class_volume, snapshot_class_volume


@pytest.mark.parametrize("layout", ["C", "F", "strided", "empty"])
def test_publication_is_independent_and_irreversibly_readonly(layout):
    original = np.arange(120, dtype=np.uint8).reshape(4, 5, 6)
    if layout == "F":
        original = np.asfortranarray(original)
    elif layout == "strided":
        original = original[::-1, ::2, :]
    elif layout == "empty":
        original = original[:0]
    expected = original.copy()
    alias = original.view()
    published = publish_class_volume(original)
    alias[...] = 255
    np.testing.assert_array_equal(published, expected)
    assert published.shape == expected.shape and published.dtype == expected.dtype
    assert not np.shares_memory(published, original)
    assert snapshot_class_volume(published) is published
    with pytest.raises(ValueError):
        published.setflags(write=True)
    with pytest.raises(ValueError):
        published[...] = 1
    with pytest.raises(ValueError):
        published.base.setflags(write=True)


@pytest.mark.parametrize("value", [None, [1], np.zeros((2, 3), dtype=np.uint8),
                                  np.zeros((2, 3, 4), dtype=np.int16),
                                  np.zeros((2, 3, 4), dtype=object)])
def test_invalid_publication_is_rejected(value):
    with pytest.raises(ValueError, match="three-dimensional uint8"):
        publish_class_volume(value)


@pytest.mark.parametrize("kind", ["writable", "readonly_owner", "readonly_view", "mutable_buffer", "readonly_memoryview"])
def test_unknown_or_mutable_backing_retains_copy_fallback(kind):
    owner = np.arange(27, dtype=np.uint8).reshape(3, 3, 3).copy()
    alias = owner.view()
    value = owner
    if kind == "readonly_owner":
        owner.setflags(write=False)
    elif kind == "readonly_view":
        value = owner.view()
        value.setflags(write=False)
    elif kind in {"mutable_buffer", "readonly_memoryview"}:
        backing = bytearray(owner.tobytes())
        source = backing if kind == "mutable_buffer" else memoryview(backing).toreadonly()
        value = np.frombuffer(source, dtype=np.uint8).reshape(owner.shape)
    before = value.copy()
    snapshot = snapshot_class_volume(value)
    assert snapshot is not value and not np.shares_memory(snapshot, value)
    if kind in {"mutable_buffer", "readonly_memoryview"}:
        backing[0] = 99
    else:
        alias[0, 0, 0] = 99
    np.testing.assert_array_equal(snapshot, before)
    assert value[0, 0, 0] == 99


def test_snapshot_absent_and_published_views():
    assert snapshot_class_volume(None) is None
    assert snapshot_class_volume([1]) is None
    value = publish_class_volume(np.arange(27, dtype=np.uint8).reshape(3, 3, 3))
    view = value[::-1, ::2, :]
    assert snapshot_class_volume(view) is view
    with pytest.raises(ValueError):
        view.setflags(write=True)


def test_concurrent_readers_retain_old_snapshot_after_replacement():
    current = publish_class_volume(np.ones((10, 10, 10), dtype=np.uint8))
    guard = threading.Lock()
    ready = threading.Barrier(9)
    replaced = threading.Event()
    def reader(_):
        with guard:
            snapshot = snapshot_class_volume(current)
        ready.wait(timeout=10)
        assert replaced.wait(10)
        return int(snapshot.sum()), snapshot.flags.writeable
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(reader, i) for i in range(8)]
        ready.wait(timeout=10)
        with guard:
            current = publish_class_volume(np.full((10, 10, 10), 2, dtype=np.uint8))
        gc.collect()
        replaced.set()
        assert [f.result(timeout=10) for f in futures] == [(1000, False)] * 8
    assert int(current.sum()) == 2000


def build_model(server, monkeypatch):
    monkeypatch.setattr(server, "_resolve_risk_mask_path", lambda *args: (None, None))
    monkeypatch.setattr(server, "DISABLE_ATLAS_KEYWORD_RISK", True)
    shape = (12, 12, 12)
    vessel = np.zeros(shape, dtype=bool)
    vessel[:, 4:6, :] = True
    ventricle = np.zeros(shape, dtype=bool)
    ventricle[:, 8:10, :] = True
    masks = {"vessel": vessel, "ventricle": ventricle}
    before = {k: v.copy() for k, v in masks.items()}
    server._initialize_risk_model(np.zeros(shape, dtype=np.uint32), extra_source_masks=masks,
                                 precomputed_signed_sdf_mm=np.full(shape, .25, dtype=np.float32))
    for k, v in masks.items():
        np.testing.assert_array_equal(v, before[k])
    return shape


@pytest.mark.parametrize("radius", [0., 20.])
def test_real_builder_and_diagnostic_equal_legacy_copy(server, monkeypatch, radius):
    shape = build_model(server, monkeypatch)
    published = server.risk_class_volume
    assert published.shape == shape and not published.flags.writeable
    assert snapshot_class_volume(published) is published
    for k, code in server.RISK_CLASS_CODE.items():
        assert server.risk_model_summary['classCoverageVoxels'][k] == int(np.count_nonzero(published == code))
    trajectory = [(5., float(i), 5.) for i in range(2, 10)]
    actual = server._risk_distance_diagnostics_from_voxels(trajectory, threshold_mm=.3, probe_radius_um=radius)
    monkeypatch.setattr(server, "snapshot_class_volume", lambda v: v.copy() if isinstance(v, np.ndarray) else None)
    expected = server._risk_distance_diagnostics_from_voxels(trajectory, threshold_mm=.3, probe_radius_um=radius)
    assert actual is not None
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_actual_diagnostic_holds_one_epoch_when_model_replaced(server, monkeypatch):
    build_model(server, monkeypatch)
    trajectory = [(5., float(i), 5.) for i in range(2, 10)]
    expected = server._risk_distance_diagnostics_from_voxels(trajectory, threshold_mm=.3)
    old = server.risk_class_volume
    original_sampler = server._build_swept_probe_samples_vox
    def replace_after_snapshot(*args, **kwargs):
        with server.risk_model_lock:
            server.risk_class_volume = publish_class_volume(np.zeros(old.shape, dtype=np.uint8))
            server.risk_model_summary = {"enabled": False}
        return original_sampler(*args, **kwargs)
    monkeypatch.setattr(server, "_build_swept_probe_samples_vox", replace_after_snapshot)
    observed = server._risk_distance_diagnostics_from_voxels(trajectory, threshold_mm=.3)
    assert json.dumps(observed, sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert server.risk_class_volume is not old
    assert server._risk_distance_diagnostics_from_voxels(trajectory) is None
    assert server._risk_distance_diagnostics_from_voxels([]) is None
