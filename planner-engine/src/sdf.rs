use crate::EngineError;

#[derive(Clone, Debug)]
pub struct SdfGrid {
    pub dims: [usize; 3],
    pub spacing_mm: [f32; 3],
    pub origin_mm: [f32; 3],
    pub values: Vec<f32>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SdfSample {
    pub distance_mm: f32,
    pub inside_grid: bool,
}

impl SdfGrid {
    pub fn validate(&self) -> Result<(), EngineError> {
        let expected = self.dims[0]
            .checked_mul(self.dims[1])
            .and_then(|v| v.checked_mul(self.dims[2]))
            .ok_or(EngineError::InvalidGrid)?;
        if expected != self.values.len() {
            return Err(EngineError::LengthMismatch);
        }
        Ok(())
    }

    fn idx(&self, x: usize, y: usize, z: usize) -> usize {
        (x * self.dims[1] + y) * self.dims[2] + z
    }

    pub fn sample_voxel(&self, x: usize, y: usize, z: usize) -> SdfSample {
        if x >= self.dims[0] || y >= self.dims[1] || z >= self.dims[2] {
            return SdfSample {
                distance_mm: f32::INFINITY,
                inside_grid: false,
            };
        }
        let idx = self.idx(x, y, z);
        let Some(distance_mm) = self.values.get(idx).copied() else {
            return SdfSample {
                distance_mm: f32::NAN,
                inside_grid: false,
            };
        };
        SdfSample {
            distance_mm,
            inside_grid: distance_mm.is_finite(),
        }
    }

    pub fn trilinear_sample(&self, point_mm: [f32; 3]) -> SdfSample {
        if self.validate().is_err() {
            return SdfSample {
                distance_mm: f32::NAN,
                inside_grid: false,
            };
        }
        let fx = (point_mm[0] - self.origin_mm[0]) / self.spacing_mm[0];
        let fy = (point_mm[1] - self.origin_mm[1]) / self.spacing_mm[1];
        let fz = (point_mm[2] - self.origin_mm[2]) / self.spacing_mm[2];
        if fx < 0.0 || fy < 0.0 || fz < 0.0 {
            return SdfSample {
                distance_mm: f32::INFINITY,
                inside_grid: false,
            };
        }
        let x0 = fx.floor() as usize;
        let y0 = fy.floor() as usize;
        let z0 = fz.floor() as usize;
        if x0 + 1 >= self.dims[0] || y0 + 1 >= self.dims[1] || z0 + 1 >= self.dims[2] {
            return SdfSample {
                distance_mm: f32::INFINITY,
                inside_grid: false,
            };
        }
        let tx = fx - x0 as f32;
        let ty = fy - y0 as f32;
        let tz = fz - z0 as f32;
        let c000 = self.values[self.idx(x0, y0, z0)];
        let c100 = self.values[self.idx(x0 + 1, y0, z0)];
        let c010 = self.values[self.idx(x0, y0 + 1, z0)];
        let c110 = self.values[self.idx(x0 + 1, y0 + 1, z0)];
        let c001 = self.values[self.idx(x0, y0, z0 + 1)];
        let c101 = self.values[self.idx(x0 + 1, y0, z0 + 1)];
        let c011 = self.values[self.idx(x0, y0 + 1, z0 + 1)];
        let c111 = self.values[self.idx(x0 + 1, y0 + 1, z0 + 1)];
        let c00 = c000 * (1.0 - tx) + c100 * tx;
        let c10 = c010 * (1.0 - tx) + c110 * tx;
        let c01 = c001 * (1.0 - tx) + c101 * tx;
        let c11 = c011 * (1.0 - tx) + c111 * tx;
        let c0 = c00 * (1.0 - ty) + c10 * ty;
        let c1 = c01 * (1.0 - ty) + c11 * ty;
        SdfSample {
            distance_mm: c0 * (1.0 - tz) + c1 * tz,
            inside_grid: true,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::SdfGrid;

    #[test]
    fn trilinear_center_average() {
        let grid = SdfGrid {
            dims: [2, 2, 2],
            spacing_mm: [1.0, 1.0, 1.0],
            origin_mm: [0.0, 0.0, 0.0],
            values: vec![0.0, 2.0, 2.0, 4.0, 2.0, 4.0, 4.0, 6.0],
        };
        let sample = grid.trilinear_sample([0.5, 0.5, 0.5]);
        assert!(sample.inside_grid);
        assert!((sample.distance_mm - 3.0).abs() < 1e-6);
    }
}
