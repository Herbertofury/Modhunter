//! Produce bytes for a new, empty, full Skyrim SE .esp candidate.
//! The caller owns any future export; this module has no path or I/O API.

const TES4_HEADER_BYTES: usize = 24;
const TEXT_LIMIT: usize = 511;
const FORM_VERSION: u16 = 44;
const HEADER_VERSION: f32 = 1.70;
const FIRST_OBJECT: u32 = 0x800;

fn text_fits(text: &str) -> bool {
    text.len() <= TEXT_LIMIT && text.bytes().all(|byte| (0x20..=0x7e).contains(&byte))
}

fn subrecord_header(bytes: &mut Vec<u8>, signature: &[u8; 4], size: u16) {
    bytes.extend_from_slice(signature);
    bytes.extend_from_slice(&size.to_le_bytes());
}

fn c_string(bytes: &mut Vec<u8>, signature: &[u8; 4], text: &str) {
    // Inputs were bounded before allocation; the terminator is counted.
    subrecord_header(bytes, signature, (text.len() + 1) as u16);
    bytes.extend_from_slice(text.as_bytes());
    bytes.push(0);
}

/// A deterministic header-only template with no masters, overrides or records.
/// Uses the conservative 1.70 object range (not 1.71 extended IDs). Metadata is
/// printable ASCII only until encoding is qualified independently. This proves
/// no game, save, installer or live load-order compatibility.
pub fn empty_plugin(author: &str, description: Option<&str>) -> Result<Vec<u8>, &'static str> {
    if !text_fits(author) {
        return Err("Author must contain at most 511 printable ASCII bytes");
    }
    if description.is_some_and(|text| !text_fits(text)) {
        return Err("Description must contain at most 511 printable ASCII bytes");
    }

    let description_extent = description.map_or(0, |text| 7 + text.len());
    let mut bytes = Vec::with_capacity(59 + author.len() + description_extent);
    bytes.extend_from_slice(b"TES4");
    // Placeholder payload length; no flags, FormID, or version-control stamp.
    bytes.extend_from_slice(&[0; 16]);
    bytes.extend_from_slice(&FORM_VERSION.to_le_bytes());
    bytes.extend_from_slice(&[0; 2]);

    subrecord_header(&mut bytes, b"HEDR", 12);
    bytes.extend_from_slice(&HEADER_VERSION.to_le_bytes());
    bytes.extend_from_slice(&0_u32.to_le_bytes()); // Total records and groups.
    bytes.extend_from_slice(&FIRST_OBJECT.to_le_bytes());
    c_string(&mut bytes, b"CNAM", author);
    if let Some(description) = description {
        c_string(&mut bytes, b"SNAM", description);
    }
    subrecord_header(&mut bytes, b"INCC", 4);
    bytes.extend_from_slice(&0_u32.to_le_bytes()); // No interior cells.

    let payload_size = (bytes.len() - TES4_HEADER_BYTES) as u32;
    bytes[4..8].copy_from_slice(&payload_size.to_le_bytes());
    Ok(bytes)
}
