# Immutable risk-class-volume snapshots

This post-hoc execution change preserves risk-class values and query semantics.
The builder publishes a uint8 three-dimensional class map backed by a new immutable
bytes buffer once per model. Readers acquire its reference under risk_model_lock
alongside the matching tree and summary snapshot. The reference retains the old
model data if another model is published while a diagnostic is running.

Only an ndarray with a wholly read-only ndarray base chain ending in bytes is
reused. Writable arrays, read-only views of mutable owners/buffers, and other
injected array types retain the old copy behavior. Merely setting an owned array's
WRITEABLE flag toFalse is not sufficient, because prior writable aliases may exist.

This trades one publication-time copy for removal of repeated full-volume copies.
It does not remove the sampled index-array copies, change any risk-class priority,
alter the atlas mask, or imply a measured end-to-end improvement. Query workers,
candidate pool, selection and clinical claim boundaries are unchanged. Tests and
an actual fresh-request experiment are required before any performance claim.

Pinned references:
- https://numpy.org/doc/1.23/reference/generated/numpy.frombuffer.html
- https://numpy.org/doc/1.23/reference/generated/numpy.ndarray.setflags.html
