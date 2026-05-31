# Python native extension

This crate exposes phase-1 hot kernels to Python through PyO3.

Planned use in the current Flask backend:

```python
from native_kernel_adapter import sample_trajectory_voxels_native_or_raise
```

Build once Rust is available:

```powershell
cd bindings\python
python -m pip install maturin
maturin develop --release
```

The extension currently exposes:

- `sample_trajectory_voxels_native`
- `sdf_trilinear_sample_native`
- `conflict_pairs_native`
