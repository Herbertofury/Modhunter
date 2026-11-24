//! Read-only inspection of the SSE NIF envelope and one explicitly typed block.
//! Format facts: https://github.com/niftools/nifxml/blob/970a6238218a106daaeb89a61bcda0eeaf9d08c4/nif.xml
use serde::Serialize;
use std::{
    collections::{BTreeMap, HashSet},
    fs::{self, File, Metadata, OpenOptions},
    io::{Cursor, Read, Seek, SeekFrom},
    path::Path,
};

const FILE_LIMIT: u64 = 256 * 1024 * 1024;
const HEADER_LIMIT: u64 = 4 * 1024 * 1024;
const BLOCK_LIMIT: usize = 65_536;
const TYPE_LIMIT: usize = 1024;
const TEXTURE_SET_LIMIT: usize = 128;
const TEXTURE_BLOCK_LIMIT: usize = 16 * 1024;
const SLOT_LIMIT: usize = 32;
const PATH_LIMIT: usize = 512;
const HEADER_LINE: &[u8] = b"Gamebryo File Format, Version 20.2.0.7";
const TEXTURE_SET: &str = "BSShaderTextureSet";
type Result<T> = std::result::Result<T, &'static str>;

struct Input<S> {
    source: S,
    pos: u64,
    end: u64,
}

impl<S: Read + Seek> Input<S> {
    fn new(source: S, end: u64) -> Self {
        Self {
            source,
            pos: 0,
            end,
        }
    }
    fn read(&mut self, destination: &mut [u8]) -> Result<()> {
        let len = destination.len() as u64;
        self.bound(self.pos + len)?;
        self.source
            .read_exact(destination)
            .map_err(|_| "Truncated or unreadable NIF data")?;
        self.pos += len;
        Ok(())
    }
    fn bound(&self, end: u64) -> Result<()> {
        if end > self.end {
            Err("NIF metadata or block extent exceeds selected bytes")
        } else {
            Ok(())
        }
    }
    fn seek(&mut self, end: u64) -> Result<()> {
        self.bound(end)?;
        self.source
            .seek(SeekFrom::Start(end))
            .map_err(|_| "Could not skip NIF data")?;
        self.pos = end;
        Ok(())
    }
    fn skip(&mut self, len: u64) -> Result<()> {
        self.seek(self.pos + len)
    }
    fn exact<const N: usize>(&mut self) -> Result<[u8; N]> {
        let mut bytes = [0; N];
        self.read(&mut bytes)?;
        Ok(bytes)
    }
    fn u8(&mut self) -> Result<u8> {
        Ok(self.exact::<1>()?[0])
    }
    fn u16(&mut self) -> Result<usize> {
        Ok(u16::from_le_bytes(self.exact()?) as usize)
    }
    fn u32(&mut self) -> Result<u32> {
        Ok(u32::from_le_bytes(self.exact()?))
    }
    fn bytes(&mut self, len: usize, limit: usize) -> Result<Vec<u8>> {
        if len > limit {
            return Err("NIF metadata string or block exceeds inspected limit");
        }
        let mut bytes = vec![0; len];
        self.read(&mut bytes)?;
        Ok(bytes)
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Report {
    scope: &'static str,
    format_version: &'static str,
    endian: &'static str,
    user_version: u32,
    stream_version: u32,
    file_size: u64,
    block_count: usize,
    block_types: BTreeMap<String, usize>,
    root_count: u32,
    texture_sets: Vec<TextureSet>,
    texture_sets_not_examined: usize,
    texture_evidence_scope: &'static str,
    dependencies_resolved: bool,
    geometry_inspected: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct TextureSet {
    block: usize,
    state: &'static str,
    slots: Option<Vec<TextureSlot>>,
}

#[derive(Debug, Serialize)]
struct TextureSlot {
    slot: usize,
    state: &'static str,
    path: Option<String>,
}

fn relative_texture(raw: &[u8]) -> Option<String> {
    let text = std::str::from_utf8(raw).ok()?;
    if !text
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b" _-.\\/()".contains(&b))
    {
        return None;
    }
    let mut parts = text.split(['\\', '/']);
    if !parts.next()?.eq_ignore_ascii_case("textures")
        || !text.to_ascii_lowercase().ends_with(".dds")
    {
        return None;
    }
    let mut count = 0;
    for part in parts {
        if part.is_empty() || matches!(part, "." | "..") || part.ends_with([' ', '.']) {
            return None;
        }
        count += 1;
    }
    (count != 0).then(|| text.to_owned())
}

fn texture_slots(bytes: Vec<u8>) -> Result<Vec<TextureSlot>> {
    let len = bytes.len() as u64;
    let mut input = Input::new(Cursor::new(bytes), len);
    let count = input.u32()? as usize;
    if count > SLOT_LIMIT {
        return Err("Texture set exceeds inspected slot limit");
    }
    let mut slots = Vec::with_capacity(count);
    for slot in 0..count {
        let size = input.u32()? as usize;
        if size > PATH_LIMIT {
            input.skip(size as u64)?;
            slots.push(TextureSlot {
                slot,
                state: "unreviewed_path",
                path: None,
            });
            continue;
        }
        let raw = input.bytes(size, PATH_LIMIT)?;
        let path = relative_texture(&raw);
        let state = if raw.is_empty() {
            "empty"
        } else if path.is_some() {
            "embedded_relative_dds"
        } else {
            "unreviewed_path"
        };
        slots.push(TextureSlot { slot, state, path });
    }
    if input.pos != len {
        return Err("Texture set layout was not fully understood");
    }
    Ok(slots)
}

fn inventory(source: impl Read + Seek, size: u64) -> Result<Report> {
    if size > FILE_LIMIT {
        return Err("Selected NIF exceeds 256 MiB");
    }
    let mut input = Input::new(source, size.min(HEADER_LIMIT));
    let mut line = Vec::new();
    loop {
        let b = input.u8()?;
        if b == b'\n' {
            break;
        }
        if line.len() == 96 {
            return Err("Unsupported or malformed NIF header line");
        }
        line.push(b);
    }
    if line.strip_suffix(b"\r").unwrap_or(&line) != HEADER_LINE
        || input.u32()? != 0x1402_0007
        || input.u8()? != 1
        || input.u32()? != 12
    {
        return Err(
            "Unsupported NIF tuple; expected Skyrim SE 20.2.0.7, little endian, user version 12",
        );
    }
    let block_count = input.u32()? as usize;
    if input.u32()? != 100 {
        return Err("Unsupported Bethesda stream; expected Skyrim SE stream version 100");
    }
    if block_count > BLOCK_LIMIT {
        return Err("NIF block count exceeds inspected limit");
    }
    // SSE stream 100: the three sized export strings are intentionally not returned.
    for _ in 0..3 {
        let len = input.u8()? as u64;
        input.skip(len)?;
    }
    let count = input.u16()?;
    if count > TYPE_LIMIT {
        return Err("NIF block type count exceeds inspected limit");
    }
    let mut names = Vec::with_capacity(count);
    let mut unique = HashSet::with_capacity(count);
    for _ in 0..count {
        let len = input.u32()? as usize;
        let bytes = input.bytes(len, 128)?;
        if bytes.is_empty()
            || !bytes
                .iter()
                .all(|b| b.is_ascii_alphanumeric() || *b == b'_')
        {
            return Err("Invalid NIF block type name");
        }
        let name = String::from_utf8(bytes).map_err(|_| "Invalid NIF block type encoding")?;
        if !unique.insert(name.clone()) {
            return Err("Duplicate NIF block type declaration");
        }
        names.push(name);
    }
    let mut indices = Vec::with_capacity(block_count);
    let mut counts = vec![0_usize; count];
    for _ in 0..block_count {
        let index = input.u16()?;
        let bucket = counts
            .get_mut(index)
            .ok_or("NIF block type index exceeds type table")?;
        *bucket += 1;
        indices.push(index);
    }
    let mut sizes = Vec::with_capacity(block_count);
    for _ in 0..block_count {
        sizes.push(input.u32()?);
    }
    let string_count = input.u32()? as usize;
    let max_string = input.u32()? as u64;
    if string_count > BLOCK_LIMIT || max_string > HEADER_LIMIT {
        return Err("NIF string table exceeds inspected limit");
    }
    for _ in 0..string_count {
        let len = input.u32()? as u64;
        if len > max_string {
            return Err("NIF string exceeds its declared maximum");
        }
        input.skip(len)?;
    }
    let groups = input.u32()? as usize;
    if groups > BLOCK_LIMIT {
        return Err("NIF group table exceeds inspected limit");
    }
    input.skip((groups * 4) as u64)?;
    let payload_start = input.pos;
    let payload_end = payload_start + sizes.iter().map(|&size| u64::from(size)).sum::<u64>();
    input.end = size;
    input.seek(payload_end)?;
    let root_count = input.u32()?;
    if root_count as usize > block_count || input.pos + u64::from(root_count) * 4 != size {
        return Err("NIF footer does not fit the declared block extents");
    }
    for _ in 0..root_count {
        let root = input.u32()?;
        if root != u32::MAX && root as usize >= block_count {
            return Err("NIF root index exceeds block table");
        }
    }

    let mut texture_sets = Vec::new();
    let mut texture_sets_not_examined = 0;
    let mut offset = payload_start;
    for (block, (index, block_size)) in indices.into_iter().zip(sizes).enumerate() {
        if names[index] == TEXTURE_SET {
            if texture_sets.len() == TEXTURE_SET_LIMIT || block_size as usize > TEXTURE_BLOCK_LIMIT
            {
                texture_sets_not_examined += 1;
            } else {
                input.seek(offset)?;
                let bytes = input.bytes(block_size as usize, TEXTURE_BLOCK_LIMIT)?;
                let slots = texture_slots(bytes).ok();
                texture_sets.push(TextureSet {
                    block,
                    state: if slots.is_some() {
                        "read_declared_slots"
                    } else {
                        "unreviewed_layout"
                    },
                    slots,
                });
            }
        }
        offset += u64::from(block_size);
    }
    Ok(Report {
        scope: "selected_skyrim_se_nif_header_and_limited_texture_slots",
        format_version: "20.2.0.7",
        endian: "little",
        user_version: 12,
        stream_version: 100,
        file_size: size,
        block_count,
        block_types: names
            .into_iter()
            .zip(counts)
            .filter(|(_, n)| *n != 0)
            .collect(),
        root_count,
        texture_sets,
        texture_sets_not_examined,
        texture_evidence_scope: "embedded_BSShaderTextureSet_only_owner_and_shader_use_unknown",
        dependencies_resolved: false,
        geometry_inspected: false,
    })
}

fn regular(m: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if m.file_attributes() & 0x400 != 0 {
            return false;
        }
    }
    m.is_file()
}

fn same_stamp(a: &Metadata, b: &Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if (a.dev(), a.ino(), a.ctime(), a.ctime_nsec())
            != (b.dev(), b.ino(), b.ctime(), b.ctime_nsec())
        {
            return false;
        }
    }
    regular(a)
        && regular(b)
        && a.len() == b.len()
        && a.modified().is_ok_and(|m| b.modified().ok() == Some(m))
}

#[cfg(windows)]
fn windows_local_syntax(path: &Path) -> bool {
    use std::path::{Component, Prefix};
    match path.components().next() {
        Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_)) && path.is_absolute() => {
        }
        Some(Component::Prefix(_) | Component::RootDir) | None => return false,
        Some(_) => {}
    }
    path.components().all(|part| match part {
        Component::Normal(s) => s.to_str().is_some_and(|s| {
            let base = s
                .split('.')
                .next()
                .unwrap_or_default()
                .trim_end_matches(' ')
                .to_ascii_uppercase();
            let device = matches!(
                base.as_str(),
                "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
            ) || base
                .strip_prefix("COM")
                .or_else(|| base.strip_prefix("LPT"))
                .is_some_and(|n| {
                    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "¹", "²", "³"].contains(&n)
                });
            !device
                && !s.ends_with([' ', '.'])
                && !s
                    .chars()
                    .any(|c| c.is_ascii_control() || r#"<>:"|?*"#.contains(c))
        }),
        _ => true,
    })
}

fn snapshot(path: &Path) -> Result<Report> {
    const INPUT: &str = "Select one explicitly named ordinary local .nif file";
    const DRIFT: &str = "Selected NIF changed during inspection; no result emitted";
    if !path
        .extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case("nif"))
    {
        return Err(INPUT);
    }
    #[cfg(windows)]
    if !windows_local_syntax(path) {
        return Err(INPUT);
    }
    let before = fs::symlink_metadata(path).map_err(|_| INPUT)?;
    if !regular(&before) {
        return Err(INPUT);
    }
    if before.len() > FILE_LIMIT {
        return Err("Selected NIF exceeds 256 MiB");
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x0020_0000).share_mode(0x0000_0001);
    }
    let mut file: File = options.open(path).map_err(|_| INPUT)?;
    let opened = file.metadata().map_err(|_| DRIFT)?;
    if !same_stamp(&before, &opened) {
        return Err(DRIFT);
    }
    let report = inventory(&mut file, opened.len())?;
    let after = file.metadata().map_err(|_| DRIFT)?;
    let chosen = fs::symlink_metadata(path).map_err(|_| DRIFT)?;
    if !same_stamp(&opened, &after) || !same_stamp(&after, &chosen) {
        return Err(DRIFT);
    }
    Ok(report)
}

pub(super) fn run(args: &[String]) -> std::result::Result<(), Box<dyn std::error::Error>> {
    let report = match args {
        [flag, path] if flag == "--file" => snapshot(Path::new(path))?,
        _ => return Err("nif-info requires --file PATH to one explicitly named .nif".into()),
    };
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
