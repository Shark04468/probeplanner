"""Immutable class-volume publication and conservative read snapshots."""
import numpy as np


def publish_class_volume(volume):
    if not isinstance(volume, np.ndarray) or volume.dtype != np.uint8 or volume.ndim != 3:
        raise ValueError("Risk class volume must be a three-dimensional uint8 array")
    # Copy once into immutable storage; old writable aliases cannot affect it.
    return np.frombuffer(volume.tobytes(order="C"), dtype=np.uint8).reshape(volume.shape)


def snapshot_class_volume(volume):
    if not isinstance(volume, np.ndarray):
        return None
    owner = volume
    while type(owner) is np.ndarray and not owner.flags.writeable:
        owner = owner.base
    if type(owner) is bytes:
        return volume
    # Read-only views of mutable buffers/owners are not immutable snapshots.
    return volume.copy()
