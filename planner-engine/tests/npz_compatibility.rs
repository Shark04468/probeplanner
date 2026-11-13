use planner_engine::{atlas::load_f32_npy_from_npz, EngineError};
use std::path::PathBuf;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/npz")
        .join(name)
}

fn assert_brain(name: &str) {
    let (values, shape) = load_f32_npy_from_npz(fixture(name), "brain").unwrap();
    assert_eq!(shape, [2, 2, 2]);
    assert_eq!(values, vec![0.0, 0.25, -0.5, 1.0, 2.0, 3.5, 10.0, 20.0]);
}

#[test]
fn reads_numpy_stored_array() {
    assert_brain("stored.npz");
}

#[test]
fn reads_numpy_deflated_array() {
    assert_brain("deflated.npz");
}

#[test]
fn selects_the_requested_member_not_the_first_member() {
    let (values, shape) = load_f32_npy_from_npz(fixture("deflated.npz"), "auxiliary").unwrap();
    assert_eq!(shape, [2, 1, 3]);
    assert_eq!(values, vec![100.0, 101.0, 102.0, 103.0, 104.0, 105.0]);
}

#[test]
fn rejects_missing_member() {
    assert!(matches!(
        load_f32_npy_from_npz(fixture("stored.npz"), "absent"),
        Err(EngineError::OutOfBounds)
    ));
}

#[test]
fn rejects_missing_archive() {
    let path = fixture("absent.npz");
    assert!(!path.exists());
    assert!(matches!(
        load_f32_npy_from_npz(path, "brain"),
        Err(EngineError::OutOfBounds)
    ));
}

#[test]
fn rejects_invalid_archive() {
    assert!(matches!(
        load_f32_npy_from_npz(fixture("invalid_archive.npz"), "brain"),
        Err(EngineError::InvalidGrid)
    ));
}

#[test]
fn rejects_invalid_numpy_magic() {
    assert!(matches!(
        load_f32_npy_from_npz(fixture("invalid_npy.npz"), "brain"),
        Err(EngineError::InvalidGrid)
    ));
}

#[test]
fn rejects_truncated_numpy_payload() {
    assert!(matches!(
        load_f32_npy_from_npz(fixture("truncated_npy.npz"), "brain"),
        Err(EngineError::LengthMismatch)
    ));
}

#[test]
fn rejects_float64_channel() {
    assert!(matches!(
        load_f32_npy_from_npz(fixture("float64.npz"), "brain"),
        Err(EngineError::InvalidGrid)
    ));
}

#[test]
fn rejects_fortran_order_channel() {
    assert!(matches!(
        load_f32_npy_from_npz(fixture("fortran.npz"), "brain"),
        Err(EngineError::InvalidGrid)
    ));
}

#[test]
fn rejects_non_volume_channel() {
    assert!(matches!(
        load_f32_npy_from_npz(fixture("two_dimensional.npz"), "brain"),
        Err(EngineError::InvalidGrid)
    ));
}
