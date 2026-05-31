use crate::EngineError;
use memmap2::{Mmap, MmapOptions};
use serde::Deserialize;
use std::{
    collections::{HashMap, HashSet},
    fs::File,
    io::{Read, Seek},
    path::Path,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VolumeScalarType {
    U8,
    U16,
    U32,
    F32,
}

#[derive(Clone, Debug)]
pub struct RawVolumeSpec {
    pub dims: [usize; 3],
    pub spacing_um: [f32; 3],
    pub origin_um: [f32; 3],
    pub scalar_type: VolumeScalarType,
    pub data_offset: usize,
}

#[derive(Debug)]
pub struct MmapVolume {
    spec: RawVolumeSpec,
    mmap: Mmap,
}

#[derive(Clone, Debug)]
pub struct RegionEntry {
    pub id: u32,
    pub canonical_acronym: String,
    pub aliases: Vec<String>,
}

#[derive(Clone, Debug, Default)]
pub struct RegionLookup {
    entries: Vec<RegionEntry>,
    alias_to_id: HashMap<String, u32>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RegionMapFile {
    entries: Vec<RegionMapEntryFile>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RegionMapEntryFile {
    canonical_acronym: String,
    aliases: Vec<String>,
    id: u32,
}

impl RegionLookup {
    pub fn from_json(path: impl AsRef<Path>) -> Result<Self, EngineError> {
        let text = std::fs::read_to_string(path).map_err(|_| EngineError::OutOfBounds)?;
        let parsed: RegionMapFile =
            serde_json::from_str(&text).map_err(|_| EngineError::InvalidGrid)?;
        let mut alias_to_id = HashMap::new();
        let entries = parsed
            .entries
            .into_iter()
            .map(|entry| {
                let mut aliases = entry.aliases;
                aliases.push(entry.canonical_acronym.clone());
                for alias in &aliases {
                    alias_to_id.insert(normalize_region_key(alias), entry.id);
                }
                RegionEntry {
                    id: entry.id,
                    canonical_acronym: entry.canonical_acronym,
                    aliases,
                }
            })
            .collect();
        Ok(Self {
            entries,
            alias_to_id,
        })
    }

    pub fn descendant_ids_for(&self, key: &str) -> Vec<u32> {
        let normalized = normalize_region_key(key);
        let mut ids = HashSet::new();
        if let Some(id) = self.alias_to_id.get(&normalized) {
            ids.insert(*id);
        }
        for entry in &self.entries {
            if normalize_region_key(&entry.canonical_acronym).starts_with(&normalized) {
                ids.insert(entry.id);
            }
            if entry
                .aliases
                .iter()
                .any(|alias| normalize_region_key(alias).starts_with(&normalized))
            {
                ids.insert(entry.id);
            }
        }
        let mut out = ids.into_iter().collect::<Vec<_>>();
        out.sort_unstable();
        out
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }
}

impl MmapVolume {
    pub fn open_raw(path: impl AsRef<Path>, spec: RawVolumeSpec) -> Result<Self, EngineError> {
        let expected = voxel_count(spec.dims)?;
        let bytes_per_voxel = bytes_per_voxel(spec.scalar_type);
        let file = File::open(path).map_err(|_| EngineError::OutOfBounds)?;
        let mmap = unsafe {
            MmapOptions::new()
                .map(&file)
                .map_err(|_| EngineError::OutOfBounds)?
        };
        if mmap.len() < spec.data_offset + expected * bytes_per_voxel {
            return Err(EngineError::LengthMismatch);
        }
        Ok(Self { spec, mmap })
    }

    pub fn open_npy(
        path: impl AsRef<Path>,
        spacing_um: [f32; 3],
        origin_um: [f32; 3],
    ) -> Result<Self, EngineError> {
        let mut file = File::open(path.as_ref()).map_err(|_| EngineError::OutOfBounds)?;
        let parsed = parse_npy_header_from_reader(&mut file)?;
        if parsed.fortran_order || parsed.dims.len() != 3 {
            return Err(EngineError::InvalidGrid);
        }
        let dims = [parsed.dims[0], parsed.dims[1], parsed.dims[2]];
        Self::open_raw(
            path,
            RawVolumeSpec {
                dims,
                spacing_um,
                origin_um,
                scalar_type: parsed.scalar_type,
                data_offset: parsed.data_offset,
            },
        )
    }

    pub fn spec(&self) -> RawVolumeSpec {
        self.spec.clone()
    }

    fn flat_index(&self, x: usize, y: usize, z: usize) -> Option<usize> {
        if x >= self.spec.dims[0] || y >= self.spec.dims[1] || z >= self.spec.dims[2] {
            return None;
        }
        Some((x * self.spec.dims[1] + y) * self.spec.dims[2] + z)
    }

    pub fn sample_u8(&self, x: usize, y: usize, z: usize) -> Option<u8> {
        if !matches!(self.spec.scalar_type, VolumeScalarType::U8) {
            return None;
        }
        let offset = self.spec.data_offset + self.flat_index(x, y, z)?;
        self.mmap.get(offset).copied()
    }

    pub fn sample_u16(&self, x: usize, y: usize, z: usize) -> Option<u16> {
        if !matches!(self.spec.scalar_type, VolumeScalarType::U16) {
            return None;
        }
        let offset = self.spec.data_offset + self.flat_index(x, y, z)? * 2;
        let bytes = self.mmap.get(offset..offset + 2)?;
        Some(u16::from_le_bytes([bytes[0], bytes[1]]))
    }

    pub fn sample_u32(&self, x: usize, y: usize, z: usize) -> Option<u32> {
        if !matches!(self.spec.scalar_type, VolumeScalarType::U32) {
            return None;
        }
        let offset = self.spec.data_offset + self.flat_index(x, y, z)? * 4;
        let bytes = self.mmap.get(offset..offset + 4)?;
        Some(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
    }

    pub fn sample_f32(&self, x: usize, y: usize, z: usize) -> Option<f32> {
        if !matches!(self.spec.scalar_type, VolumeScalarType::F32) {
            return None;
        }
        let offset = self.spec.data_offset + self.flat_index(x, y, z)? * 4;
        let bytes = self.mmap.get(offset..offset + 4)?;
        Some(f32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
    }

    pub fn trilinear_sample_um(&self, point_um: [f32; 3]) -> Option<f32> {
        if !matches!(self.spec.scalar_type, VolumeScalarType::F32) {
            return None;
        }
        let fx = (point_um[0] - self.spec.origin_um[0]) / self.spec.spacing_um[0];
        let fy = (point_um[1] - self.spec.origin_um[1]) / self.spec.spacing_um[1];
        let fz = (point_um[2] - self.spec.origin_um[2]) / self.spec.spacing_um[2];
        if fx < 0.0 || fy < 0.0 || fz < 0.0 {
            return None;
        }
        let x0 = fx.floor() as usize;
        let y0 = fy.floor() as usize;
        let z0 = fz.floor() as usize;
        if x0 + 1 >= self.spec.dims[0] || y0 + 1 >= self.spec.dims[1] || z0 + 1 >= self.spec.dims[2]
        {
            return None;
        }
        let tx = fx - x0 as f32;
        let ty = fy - y0 as f32;
        let tz = fz - z0 as f32;
        let c000 = self.sample_f32(x0, y0, z0)?;
        let c100 = self.sample_f32(x0 + 1, y0, z0)?;
        let c010 = self.sample_f32(x0, y0 + 1, z0)?;
        let c110 = self.sample_f32(x0 + 1, y0 + 1, z0)?;
        let c001 = self.sample_f32(x0, y0, z0 + 1)?;
        let c101 = self.sample_f32(x0 + 1, y0, z0 + 1)?;
        let c011 = self.sample_f32(x0, y0 + 1, z0 + 1)?;
        let c111 = self.sample_f32(x0 + 1, y0 + 1, z0 + 1)?;
        let c00 = c000 * (1.0 - tx) + c100 * tx;
        let c10 = c010 * (1.0 - tx) + c110 * tx;
        let c01 = c001 * (1.0 - tx) + c101 * tx;
        let c11 = c011 * (1.0 - tx) + c111 * tx;
        let c0 = c00 * (1.0 - ty) + c10 * ty;
        let c1 = c01 * (1.0 - ty) + c11 * ty;
        Some(c0 * (1.0 - tz) + c1 * tz)
    }

    pub fn sample_label(&self, x: usize, y: usize, z: usize) -> Option<u32> {
        match self.spec.scalar_type {
            VolumeScalarType::U8 => self.sample_u8(x, y, z).map(u32::from),
            VolumeScalarType::U16 => self.sample_u16(x, y, z).map(u32::from),
            VolumeScalarType::U32 => self.sample_u32(x, y, z),
            VolumeScalarType::F32 => None,
        }
    }
}

#[derive(Debug)]
pub struct ParsedNpyHeader {
    pub scalar_type: VolumeScalarType,
    pub dims: Vec<usize>,
    pub fortran_order: bool,
    pub data_offset: usize,
}

pub fn parse_npy_header_from_reader<R: Read + Seek>(
    reader: &mut R,
) -> Result<ParsedNpyHeader, EngineError> {
    let mut magic = [0u8; 6];
    reader
        .read_exact(&mut magic)
        .map_err(|_| EngineError::InvalidGrid)?;
    if &magic != b"\x93NUMPY" {
        return Err(EngineError::InvalidGrid);
    }
    let mut version = [0u8; 2];
    reader
        .read_exact(&mut version)
        .map_err(|_| EngineError::InvalidGrid)?;
    let header_len = if version[0] == 1 {
        let mut len = [0u8; 2];
        reader
            .read_exact(&mut len)
            .map_err(|_| EngineError::InvalidGrid)?;
        u16::from_le_bytes(len) as usize
    } else {
        let mut len = [0u8; 4];
        reader
            .read_exact(&mut len)
            .map_err(|_| EngineError::InvalidGrid)?;
        u32::from_le_bytes(len) as usize
    };
    let preamble = if version[0] == 1 { 10 } else { 12 };
    let mut header_bytes = vec![0u8; header_len];
    reader
        .read_exact(&mut header_bytes)
        .map_err(|_| EngineError::InvalidGrid)?;
    let header = std::str::from_utf8(&header_bytes).map_err(|_| EngineError::InvalidGrid)?;
    let descr = extract_quoted_field(header, "descr").ok_or(EngineError::InvalidGrid)?;
    let scalar_type = match descr.as_str() {
        "|u1" => VolumeScalarType::U8,
        "<u2" | "|u2" => VolumeScalarType::U16,
        "<u4" | "|u4" => VolumeScalarType::U32,
        "<f4" | "|f4" => VolumeScalarType::F32,
        _ => return Err(EngineError::InvalidGrid),
    };
    let fortran_order =
        header.contains("'fortran_order': True") || header.contains("\"fortran_order\": true");
    let dims = extract_shape(header).ok_or(EngineError::InvalidGrid)?;
    Ok(ParsedNpyHeader {
        scalar_type,
        dims,
        fortran_order,
        data_offset: preamble + header_len,
    })
}

pub fn f32_vec_from_npy_bytes(bytes: &[u8]) -> Result<(Vec<f32>, [usize; 3]), EngineError> {
    let mut cursor = std::io::Cursor::new(bytes);
    let parsed = parse_npy_header_from_reader(&mut cursor)?;
    if parsed.fortran_order || parsed.scalar_type != VolumeScalarType::F32 || parsed.dims.len() != 3
    {
        return Err(EngineError::InvalidGrid);
    }
    let count = voxel_count([parsed.dims[0], parsed.dims[1], parsed.dims[2]])?;
    let start = parsed.data_offset;
    let expected = start + count * 4;
    if bytes.len() < expected {
        return Err(EngineError::LengthMismatch);
    }
    let mut out = Vec::with_capacity(count);
    for chunk in bytes[start..expected].chunks_exact(4) {
        out.push(f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]));
    }
    Ok((out, [parsed.dims[0], parsed.dims[1], parsed.dims[2]]))
}

pub fn load_f32_npy_from_npz(
    path: impl AsRef<Path>,
    channel: &str,
) -> Result<(Vec<f32>, [usize; 3]), EngineError> {
    let file = File::open(path).map_err(|_| EngineError::OutOfBounds)?;
    let mut archive = zip::ZipArchive::new(file).map_err(|_| EngineError::InvalidGrid)?;
    let name = format!("{channel}.npy");
    let mut member = archive
        .by_name(&name)
        .map_err(|_| EngineError::OutOfBounds)?;
    let mut bytes = Vec::with_capacity(member.size() as usize);
    member
        .read_to_end(&mut bytes)
        .map_err(|_| EngineError::InvalidGrid)?;
    f32_vec_from_npy_bytes(&bytes)
}

fn bytes_per_voxel(scalar_type: VolumeScalarType) -> usize {
    match scalar_type {
        VolumeScalarType::U8 => 1,
        VolumeScalarType::U16 => 2,
        VolumeScalarType::U32 | VolumeScalarType::F32 => 4,
    }
}

fn voxel_count(dims: [usize; 3]) -> Result<usize, EngineError> {
    dims[0]
        .checked_mul(dims[1])
        .and_then(|v| v.checked_mul(dims[2]))
        .ok_or(EngineError::InvalidGrid)
}

fn extract_quoted_field(header: &str, key: &str) -> Option<String> {
    let needle = format!("'{key}':");
    let start = header.find(&needle)? + needle.len();
    let tail = header[start..].trim_start();
    let quote = tail.chars().next()?;
    if quote != '\'' && quote != '"' {
        return None;
    }
    let end = tail[1..].find(quote)? + 1;
    Some(tail[1..end].to_string())
}

fn extract_shape(header: &str) -> Option<Vec<usize>> {
    let start = header.find("'shape':")? + "'shape':".len();
    let tail = header[start..].trim_start();
    let open = tail.find('(')?;
    let close = tail[open..].find(')')? + open;
    let inner = &tail[open + 1..close];
    let dims = inner
        .split(',')
        .filter_map(|part| {
            let trimmed = part.trim();
            if trimmed.is_empty() {
                None
            } else {
                trimmed.parse::<usize>().ok()
            }
        })
        .collect::<Vec<_>>();
    if dims.is_empty() {
        None
    } else {
        Some(dims)
    }
}

fn normalize_region_key(value: &str) -> String {
    value.trim().to_ascii_uppercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_npy_header() {
        let header = "{'descr': '<u4', 'fortran_order': False, 'shape': (2, 3, 4), }";
        let padding = 64 - ((10 + header.len() + 1) % 64);
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"\x93NUMPY");
        bytes.extend_from_slice(&[1, 0]);
        let header_len = header.len() + padding + 1;
        bytes.extend_from_slice(&(header_len as u16).to_le_bytes());
        bytes.extend_from_slice(header.as_bytes());
        bytes.extend(std::iter::repeat(b' ').take(padding));
        bytes.push(b'\n');
        let parsed = parse_npy_header_from_reader(&mut std::io::Cursor::new(bytes)).unwrap();
        assert_eq!(parsed.scalar_type, VolumeScalarType::U32);
        assert_eq!(parsed.dims, vec![2, 3, 4]);
        assert!(!parsed.fortran_order);
    }
}
