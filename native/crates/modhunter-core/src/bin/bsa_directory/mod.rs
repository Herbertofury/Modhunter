//! Bounded metadata-only inventory of named PC BSA revisions 103–105.
//! No BSA extraction, compression decoding or filesystem path construction.
use std::{
    collections::{HashMap, HashSet},
    io::Read,
};

const HEADER_LEN: usize = 36;
const MAX_FOLDERS: usize = 16_384;
const MAX_FILES: usize = 100_000;
const MAX_METADATA: usize = 16 * 1024 * 1024;
const MAX_RESULT_BYTES: usize = 16 * 1024 * 1024;
const FILE_ROW: usize = 16;
const COMPRESS_TOGGLE: u32 = 1 << 30;

#[derive(Debug, PartialEq, Eq)]
pub struct Directory {
    pub version: u32,
    pub metadata_bytes: usize,
    pub paths: Vec<String>,
}

fn number(bytes: &[u8], i: usize) -> u32 {
    u32::from_le_bytes(bytes[i..i + 4].try_into().expect("fixed-width record"))
}

fn safe_component(part: &str) -> bool {
    if part.is_empty()
        || part.len() > 255
        || part.starts_with(' ')
        || part.ends_with([' ', '.'])
        || !part
            .bytes()
            .all(|c| (b' '..=b'~').contains(&c) && !b"<>:\"/\\|?*".contains(&c))
    {
        return false;
    }
    let stem = part
        .split('.')
        .next()
        .unwrap_or_default()
        .trim_end()
        .to_ascii_uppercase();
    !matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        && !(stem.len() == 4
            && (stem.starts_with("COM") || stem.starts_with("LPT"))
            && stem.as_bytes()[3].is_ascii_digit())
}

fn folder_path(raw: &[u8]) -> Result<String, &'static str> {
    let raw = raw
        .strip_suffix(b"\0")
        .ok_or("Folder name is not terminated")?;
    // Some PC packers write a sole dot as the archive's root marker.
    // Accept it only for the whole directory name, never inside a path.
    if raw.is_empty() || raw == b"." {
        return Ok(String::new());
    }
    let name = std::str::from_utf8(raw).map_err(|_| "Archive name encoding is unsupported")?;
    if !name.split(['/', '\\']).all(safe_component) {
        return Err("Unsupported or unsafe archive directory name");
    }
    Ok(name.replace('\\', "/"))
}

fn take<'a>(bytes: &'a [u8], next: &mut usize, size: usize) -> Result<&'a [u8], &'static str> {
    let end = next
        .checked_add(size)
        .ok_or("BSA metadata length overflow")?;
    let out = bytes.get(*next..end).ok_or("Incomplete BSA metadata")?;
    *next = end;
    Ok(out)
}

/// Reads exactly the announced metadata prefix. `archive_bytes` is from the
/// selected opened file, not from archive metadata or from an entry name.
pub fn inventory(mut input: impl Read, archive_bytes: u64) -> Result<Directory, &'static str> {
    let mut header = [0; HEADER_LEN];
    input
        .read_exact(&mut header)
        .map_err(|_| "Cannot read BSA header")?;
    if &header[..4] != b"BSA\0" {
        return Err("Unsupported archive signature; expected a named TES4-family BSA");
    }
    let version = number(&header, 4);
    let folder_width = match version {
        103 | 104 => 16,
        105 => 24,
        _ => return Err("Unsupported BSA revision; expected 103, 104 or 105"),
    };
    if number(&header, 8) as usize != HEADER_LEN {
        return Err("Unsupported BSA folder table position");
    }
    let flags = number(&header, 12);
    if flags & 3 != 3 {
        return Err(
            "A named directory and file table are required; hash-only inventory is unsupported",
        );
    }
    if flags & (0x40 | 0x200) != 0 {
        return Err("Console/XMem BSA variants are unsupported");
    }
    let dirs = number(&header, 16) as usize;
    let files = number(&header, 20) as usize;
    let dir_names = number(&header, 24) as usize;
    let file_names = number(&header, 28) as usize;
    if dirs > MAX_FOLDERS
        || files > MAX_FILES
        || (files == 0 && dirs != 0)
        || file_names < files
        || dir_names < dirs
    {
        return Err("BSA directory counts or bounds are unsupported");
    }
    let folder_table = folder_width * dirs;
    let blocks_len = dir_names
        .checked_add(dirs + files * FILE_ROW)
        .ok_or("BSA metadata length overflow")?;
    let entries_len = folder_table
        .checked_add(blocks_len)
        .and_then(|len| len.checked_add(file_names))
        .ok_or("BSA metadata length overflow")?;
    let metadata_bytes = HEADER_LEN
        .checked_add(entries_len)
        .ok_or("BSA metadata length overflow")?;
    if metadata_bytes > MAX_METADATA || metadata_bytes as u64 > archive_bytes {
        return Err("BSA metadata exceeds byte limits or the selected file");
    }
    let mut metadata = vec![0; entries_len];
    input
        .read_exact(&mut metadata)
        .map_err(|_| "Cannot read the complete BSA directory")?;
    let (folders, rest) = metadata.split_at(folder_table);
    let (blocks, names) = rest.split_at(blocks_len);
    let mut block_cursor = 0usize;
    let mut name_cursor = 0usize;
    let mut found_dir_names = 0usize;
    let mut expanded_bytes = 0usize;
    let mut paths = Vec::with_capacity(files);
    let mut used = HashSet::with_capacity(files);

    for record in folders.chunks_exact(folder_width) {
        let entry_count = number(record, 8) as usize;
        if entry_count == 0 || entry_count > files - paths.len() {
            return Err("Folder counts do not match the BSA header");
        }
        let encoded_pos = if version == 105 {
            u64::from_le_bytes(record[16..24].try_into().expect("v105 folder record"))
        } else {
            u64::from(number(record, 12))
        };
        // Canonical BSA offsets include the size of the later file-name table.
        let expected_pos = HEADER_LEN + folder_table + block_cursor + file_names;
        if encoded_pos != expected_pos as u64 {
            return Err("Folder records do not identify the canonical BSA directory layout");
        }
        let len = take(blocks, &mut block_cursor, 1)?[0] as usize;
        found_dir_names += len;
        let dir = folder_path(take(blocks, &mut block_cursor, len)?)?;
        for _ in 0..entry_count {
            let info = take(blocks, &mut block_cursor, FILE_ROW)?;
            let stored_size = u64::from(number(info, 8) & !COMPRESS_TOGGLE);
            let file_pos = u64::from(number(info, 12));
            if file_pos < metadata_bytes as u64 || file_pos + stored_size > archive_bytes {
                return Err("A BSA entry points outside the selected file's data area");
            }
            let len = names[name_cursor..]
                .iter()
                .take(256)
                .position(|b| *b == 0)
                .ok_or("File name is too long or not terminated")?;
            let basename = std::str::from_utf8(take(names, &mut name_cursor, len + 1)?)
                .map_err(|_| "Archive name encoding is unsupported")?
                .strip_suffix('\0')
                .expect("announced terminator");
            if !safe_component(basename) {
                return Err("Unsupported or unsafe archive file name");
            }
            let path = if dir.is_empty() {
                basename.to_owned()
            } else {
                format!("{dir}/{basename}")
            };
            expanded_bytes += path.len() + 1;
            if expanded_bytes > MAX_RESULT_BYTES {
                return Err("Expanded BSA index exceeds the output limit");
            }
            if !used.insert(path.to_ascii_lowercase()) {
                return Err("Ambiguous duplicate BSA entry name");
            }
            paths.push(path);
        }
    }
    if paths.len() != files
        || block_cursor != blocks.len()
        || name_cursor != names.len()
        || found_dir_names != dir_names
    {
        return Err("BSA directory counts or table sizes do not reconcile");
    }
    Ok(Directory {
        version,
        metadata_bytes,
        paths,
    })
}

use serde::Serialize;
use std::{
    fs::{self, File, Metadata, OpenOptions},
    io::{self, Write},
    path::Path,
};

const USAGE: &str = "bsa-directory requires --archive PATH to one explicitly selected .bsa";
const COMPARE_USAGE: &str =
    "bsa-compare requires --left PATH --right PATH to two explicitly selected .bsa files";
const FILE_REQUIRED: &str = "Select one ordinary .bsa file (not a link or special file)";
const FILE_CHANGED: &str = "The selected BSA changed during inspection; choose a stable copy";

fn regular(metadata: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return false; // FILE_ATTRIBUTE_REPARSE_POINT
        }
    }
    metadata.is_file()
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
        && a.modified()
            .is_ok_and(|stamp| b.modified().ok() == Some(stamp))
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Report {
    scope: &'static str,
    version: u32,
    metadata_bytes_read: usize,
    entry_count: usize,
    paths: Vec<String>,
}

pub(super) fn run(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let [mode, path] = args else {
        return Err(USAGE.into());
    };
    if mode != "--archive" {
        return Err(USAGE.into());
    }
    let directory = snapshot(Path::new(path))?;
    write_report(&Report {
        scope: "named-bsa-directory-only",
        version: directory.version,
        metadata_bytes_read: directory.metadata_bytes,
        entry_count: directory.paths.len(),
        paths: directory.paths,
    })
}

/// Snapshot one human-selected archive; the read handle is closed on return.
pub(super) fn snapshot(path: &Path) -> Result<Directory, &'static str> {
    let mut archive = Selection::open(path)?;
    let directory = archive.read()?;
    archive.recheck()?;
    Ok(directory)
}

struct Selection<'a> {
    path: &'a Path,
    file: File,
    opened: Metadata,
}

impl<'a> Selection<'a> {
    fn open(path: &'a Path) -> Result<Self, &'static str> {
        if !path
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("bsa"))
        {
            return Err(FILE_REQUIRED);
        }
        #[cfg(windows)]
        {
            use std::path::{Component, Prefix};
            // Only ordinary relative paths and drive-qualified absolute paths.
            // In particular don't turn this explicit local read into a UNC probe.
            if matches!(path.components().next(), Some(Component::Prefix(prefix))
            if !matches!(prefix.kind(), Prefix::Disk(_)))
                || (path.has_root() && !path.is_absolute())
                || (matches!(path.components().next(), Some(Component::Prefix(_)))
                    && !path.is_absolute())
                || path.components().any(|part| {
                    matches!(part, Component::Normal(name)
                if name.to_string_lossy().contains(':'))
                })
            {
                return Err(FILE_REQUIRED);
            }
        }

        let before = fs::symlink_metadata(path).map_err(|_| FILE_REQUIRED)?;
        if !regular(&before) {
            return Err(FILE_REQUIRED);
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
            // Open without following a final reparse point, and share only with
            // other readers (same flags as ModHunter's local metadata importer).
            options.custom_flags(0x0020_0000).share_mode(0x0000_0001);
        }
        let file = options.open(path).map_err(|_| FILE_REQUIRED)?;
        let opened = file.metadata().map_err(|_| FILE_CHANGED)?;
        if !same_stamp(&before, &opened) {
            return Err(FILE_CHANGED);
        }
        Ok(Self { path, file, opened })
    }

    fn read(&mut self) -> Result<Directory, &'static str> {
        // Unbuffered file; reads stop exactly before payload.
        inventory(&mut self.file, self.opened.len())
    }

    fn recheck(&self) -> Result<(), &'static str> {
        let after = self.file.metadata().map_err(|_| FILE_CHANGED)?;
        let selected = fs::symlink_metadata(self.path).map_err(|_| FILE_CHANGED)?;
        if !same_stamp(&self.opened, &after) || !same_stamp(&after, &selected) {
            return Err(FILE_CHANGED);
        }
        Ok(())
    }
}

fn write_report(report: &impl Serialize) -> Result<(), Box<dyn std::error::Error>> {
    let stdout = io::stdout().lock();
    let mut out = io::BufWriter::new(stdout);
    serde_json::to_writer(&mut out, report)?;
    writeln!(out)?;
    out.flush()?;
    Ok(())
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SourceIndex {
    version: u32,
    metadata_bytes_read: usize,
    entry_count: usize,
}

impl SourceIndex {
    fn of(directory: &Directory) -> Self {
        Self {
            version: directory.version,
            metadata_bytes_read: directory.metadata_bytes,
            entry_count: directory.paths.len(),
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Overlap {
    left_path: String,
    right_path: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Comparison {
    scope: &'static str,
    left: SourceIndex,
    right: SourceIndex,
    left_only_count: usize,
    right_only_count: usize,
    overlap_count: usize,
    overlaps: Vec<Overlap>,
}

fn compare(left: Directory, right: Directory) -> Comparison {
    let left_summary = SourceIndex::of(&left);
    let right_summary = SourceIndex::of(&right);
    // Input is already bounded, ASCII and case-deduplicated. Move out only the
    // matching original path strings; no payload is requested for comparison.
    let mut right_by_key: HashMap<_, _> = right
        .paths
        .into_iter()
        .map(|path| (path.to_ascii_lowercase(), path))
        .collect();
    let overlaps: Vec<_> = left
        .paths
        .into_iter()
        .filter_map(|left_path| {
            let right_path = right_by_key.remove(&left_path.to_ascii_lowercase())?;
            Some(Overlap {
                left_path,
                right_path,
            })
        })
        .collect();
    Comparison {
        scope: "named-bsa-directory-overlap-only",
        left_only_count: left_summary.entry_count - overlaps.len(),
        right_only_count: right_by_key.len(),
        overlap_count: overlaps.len(),
        left: left_summary,
        right: right_summary,
        overlaps,
    }
}

fn inspect_pair(
    left: &mut Selection<'_>,
    right: &mut Selection<'_>,
) -> Result<Comparison, &'static str> {
    let report = compare(left.read()?, right.read()?);
    left.recheck()?;
    right.recheck()?;
    Ok(report)
}

pub(super) fn run_compare(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let [left_flag, left_path, right_flag, right_path] = args else {
        return Err(COMPARE_USAGE.into());
    };
    if left_flag != "--left" || right_flag != "--right" {
        return Err(COMPARE_USAGE.into());
    }
    let mut left = Selection::open(Path::new(left_path))?;
    let mut right = Selection::open(Path::new(right_path))?;
    write_report(&inspect_pair(&mut left, &mut right)?)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn either_rebound_selection_refuses_to_publish_comparison() {
        let fixture = tempfile::tempdir().unwrap();
        let empty: Vec<_> = [0x00415342u32, 105, 36, 3, 0, 0, 0, 0, 0]
            .into_iter()
            .flat_map(u32::to_le_bytes)
            .collect();
        let left_path = fixture.path().join("left.bsa");
        let right_path = fixture.path().join("right.bsa");
        for changed in [&left_path, &right_path] {
            fs::write(&left_path, &empty).unwrap();
            fs::write(&right_path, &empty).unwrap();
            let mut left = Selection::open(&left_path).unwrap();
            let mut right = Selection::open(&right_path).unwrap();
            // Both original read handles still return valid, identical metadata.
            // Replace exactly one selected name with a new, identical inode.
            let replacement = fixture.path().join("replacement.bsa");
            fs::write(&replacement, &empty).unwrap();
            fs::rename(replacement, changed).unwrap();
            assert!(matches!(
                inspect_pair(&mut left, &mut right),
                Err(FILE_CHANGED)
            ));
        }
    }
}
