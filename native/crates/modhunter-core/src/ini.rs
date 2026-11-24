//! Small QSettings-compatible INI reader. Keeps sections separate so unrelated
//! plugin settings cannot override MO2's authoritative path and game settings.
use crate::CoreError;
use std::collections::HashMap;

#[derive(Debug, Clone, Default)]
pub struct Ini {
    sections: HashMap<String, HashMap<String, Vec<u8>>>,
}

impl Ini {
    pub fn parse(text: &str) -> Result<Self, CoreError> {
        Self::parse_internal(text, false)
    }

    pub(crate) fn parse_unique(text: &str) -> Result<Self, CoreError> {
        Self::parse_internal(text, true)
    }

    fn parse_internal(text: &str, reject_duplicates: bool) -> Result<Self, CoreError> {
        let mut parsed = Self::default();
        let mut section = String::from("general");
        for (n, raw) in text.trim_start_matches('\u{feff}').lines().enumerate() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with(';') || line.starts_with('#') {
                continue;
            }
            if line.starts_with('[') {
                let Some(end) = line.find(']') else {
                    return Err(CoreError::Invalid(format!(
                        "Malformed INI section at line {}",
                        n + 1
                    )));
                };
                let tail = line[end + 1..].trim();
                if !(tail.is_empty() || tail.starts_with(';') || tail.starts_with('#')) {
                    return Err(CoreError::Invalid(format!(
                        "Unexpected text after INI section at line {}",
                        n + 1
                    )));
                }
                section = line[1..end].trim().to_ascii_lowercase();
                if section.is_empty() {
                    return Err(CoreError::Invalid(format!(
                        "Empty INI section at line {}",
                        n + 1
                    )));
                }
                continue;
            }
            let Some((key, value)) = line.split_once('=') else {
                return Err(CoreError::Invalid(format!(
                    "INI entry has no '=' at line {}",
                    n + 1
                )));
            };
            let key = key.trim().to_ascii_lowercase();
            if key.is_empty() {
                return Err(CoreError::Invalid(format!(
                    "Empty INI key at line {}",
                    n + 1
                )));
            }
            let value = decode_bytes(value.trim())?;
            let previous = parsed
                .sections
                .entry(section.clone())
                .or_default()
                .insert(key, value);
            if reject_duplicates && previous.is_some() {
                return Err(CoreError::Invalid(format!(
                    "Duplicate INI key at line {}",
                    n + 1
                )));
            }
        }
        Ok(parsed)
    }

    pub fn get(&self, section: &str, key: &str) -> Option<&str> {
        self.text(section, key).ok().flatten()
    }

    /// An absent setting can use a default. A present binary setting cannot
    /// safely be interpreted as a path, game identity or runtime version.
    pub(crate) fn text(&self, section: &str, key: &str) -> Result<Option<&str>, CoreError> {
        self.get_canonical(&section.to_ascii_lowercase(), &key.to_ascii_lowercase())
            .map(|bytes| {
                std::str::from_utf8(bytes)
                    .ok()
                    .filter(|text| !text.contains('\0'))
                    .ok_or_else(|| {
                        CoreError::Invalid(format!("Expected text for [{section}] {key}"))
                    })
            })
            .transpose()
    }

    pub(crate) fn canonical_entries(&self) -> impl Iterator<Item = (&str, &str, &[u8])> {
        self.sections.iter().flat_map(|(section, entries)| {
            entries
                .iter()
                .map(move |(key, value)| (section.as_str(), key.as_str(), value.as_slice()))
        })
    }

    pub(crate) fn get_canonical(&self, section: &str, key: &str) -> Option<&[u8]> {
        self.sections.get(section)?.get(key).map(Vec::as_slice)
    }

    pub fn general(&self, key: &str) -> &str {
        self.get("general", key).unwrap_or("")
    }
}

pub fn decode_text(bytes: &[u8]) -> Result<String, CoreError> {
    if bytes.starts_with(&[0xff, 0xfe]) || bytes.starts_with(&[0xfe, 0xff]) {
        if !(bytes.len() - 2).is_multiple_of(2) {
            return Err(CoreError::Invalid("Truncated UTF-16 MO2 file".into()));
        }
        let little = bytes[0] == 0xff;
        let units: Vec<u16> = bytes[2..]
            .as_chunks::<2>()
            .0
            .iter()
            .map(|c| {
                if little {
                    u16::from_le_bytes([c[0], c[1]])
                } else {
                    u16::from_be_bytes([c[0], c[1]])
                }
            })
            .collect();
        String::from_utf16(&units).map_err(|_| CoreError::Invalid("Invalid UTF-16 MO2 file".into()))
    } else {
        let bytes = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(bytes);
        String::from_utf8(bytes.to_vec()).map_err(|_| {
            CoreError::Invalid("MO2 file must contain valid UTF-8 or BOM-tagged UTF-16".into())
        })
    }
}

/// Decode the strings written by Qt QSettings, including UTF-8 QByteArrays.
/// A single backslash before an unknown character is preserved rather than
/// silently damaging a manually supplied Windows path.
pub fn decode_value(raw: &str) -> Result<String, CoreError> {
    String::from_utf8(decode_bytes(raw)?)
        .map_err(|_| CoreError::Invalid("Invalid UTF-8 in Qt INI QByteArray".into()))
}

fn decode_bytes(raw: &str) -> Result<Vec<u8>, CoreError> {
    let mut raw = raw;
    if raw.starts_with('"') && raw.ends_with('"') && raw.len() >= 2 {
        raw = &raw[1..raw.len() - 1];
    }
    let byte_array = raw.starts_with("@ByteArray(") && raw.ends_with(')');
    if byte_array {
        raw = &raw[11..raw.len() - 1];
    }
    if let Some(s) = raw.strip_prefix("@@") {
        return Ok(format!("@{s}").into_bytes());
    }
    if !byte_array && raw.starts_with('@') {
        return Ok(raw.as_bytes().to_vec()); // Preserve Qt values whose type we do not interpret.
    }
    // MO2 writes escaped backslashes, but hand-edited INIs often contain plain
    // drive paths. In that unambiguous path shape preserve \n/\t literally.
    if !byte_array
        && raw.as_bytes().get(1) == Some(&b':')
        && raw.as_bytes().get(2) == Some(&b'\\')
        && !raw.contains("\\\\")
    {
        return Ok(raw.as_bytes().to_vec());
    }
    let mut out = Vec::<u8>::with_capacity(raw.len());
    let mut chars = raw.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\\' {
            let mut b = [0; 4];
            out.extend_from_slice(c.encode_utf8(&mut b).as_bytes());
            continue;
        }
        let Some(escaped) = chars.next() else {
            out.push(b'\\');
            break;
        };
        match escaped {
            '\\' => out.push(b'\\'),
            '"' => out.push(b'"'),
            'n' => out.push(b'\n'),
            'r' => out.push(b'\r'),
            't' => out.push(b'\t'),
            '0' => out.push(0),
            'x' => {
                let mut digits = String::new();
                let maximum = if byte_array { 2 } else { 4 };
                while digits.len() < maximum && chars.peek().is_some_and(char::is_ascii_hexdigit) {
                    digits.push(chars.next().unwrap());
                }
                if digits.is_empty() {
                    out.extend_from_slice(b"\\x");
                    continue;
                }
                let code = u32::from_str_radix(&digits, 16)
                    .map_err(|_| CoreError::Invalid("Invalid Qt INI escape".into()))?;
                if byte_array {
                    out.push(code as u8);
                } else if let Some(ch) = char::from_u32(code) {
                    let mut b = [0; 4];
                    out.extend_from_slice(ch.encode_utf8(&mut b).as_bytes());
                } else {
                    return Err(CoreError::Invalid("Invalid Unicode Qt INI escape".into()));
                }
            }
            other => {
                out.push(b'\\');
                let mut b = [0; 4];
                out.extend_from_slice(other.encode_utf8(&mut b).as_bytes());
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sections_cannot_override_general() {
        let i = Ini::parse("\u{feff}[General]\ngameName=Skyrim\n[Unrelated]\ngameName=Fallout 4\n[Settings]\nbase_directory=\"D:\\\\Modding\\\\MO2\"\n").unwrap();
        assert_eq!(i.general("gameName"), "Skyrim");
        assert_eq!(
            i.get("settings", "base_directory"),
            Some("D:\\Modding\\MO2")
        );
    }
    #[test]
    fn byte_array_and_unicode() {
        assert_eq!(
            decode_value(r"@ByteArray(A Hero \xc3\xa9)").unwrap(),
            "A Hero é"
        );
        assert_eq!(decode_value(r"\x65e5\x672c").unwrap(), "日本");
    }
    #[test]
    fn invalid_is_not_silently_ignored() {
        assert!(Ini::parse("[Settings\nmods=D:/mods").is_err());
        assert!(decode_text(&[0xff, 0xfe, 65]).is_err());
        assert_eq!(decode_text(&[0xff, 0xfe, 65, 0]).unwrap(), "A");
    }
}
