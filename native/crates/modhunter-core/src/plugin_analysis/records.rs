use super::{Eligibility, PluginAuditStats, PluginHeader, RecordSummary, Stamp};
use crate::{CoreError, io_error};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs::File;
use std::io::{BufReader, Read};

const HEADER_LIMIT: usize = 1_048_576;
const RECORD_LIMIT: usize = 2_000_000;
const GROUP_DEPTH_LIMIT: usize = 64;

pub(super) struct Parsed {
    pub header: PluginHeader,
    pub records: Option<RecordSummary>,
    pub sha256: Option<String>,
}

struct Input {
    reader: BufReader<File>,
    hash: Option<Sha256>,
    position: u64,
}
impl Input {
    fn exact(&mut self, bytes: &mut [u8]) -> Result<(), CoreError> {
        self.reader
            .read_exact(bytes)
            .map_err(|e| io_error("Read Bethesda plugin bytes", e))?;
        self.position += bytes.len() as u64;
        if let Some(hash) = self.hash.as_mut() {
            hash.update(bytes);
        }
        Ok(())
    }
    fn drain(&mut self, mut length: u64) -> Result<(), CoreError> {
        let mut scratch = [0_u8; 64 * 1024];
        while length > 0 {
            let count = length.min(scratch.len() as u64) as usize;
            self.exact(&mut scratch[..count])?;
            length -= count as u64;
        }
        Ok(())
    }
}

pub(super) fn scan(
    file: File,
    stamp: &Stamp,
    name: &str,
    game: &str,
    deep: bool,
    stats: &mut PluginAuditStats,
) -> Result<Parsed, CoreError> {
    let mut input = Input {
        reader: BufReader::new(file),
        hash: deep.then(Sha256::new),
        position: 0,
    };
    let result = (|| {
        let width = if matches!(game, "oblivion" | "oblivionremastered") {
            20
        } else {
            24
        };
        if stamp.size < width as u64 {
            return invalid("Plugin is shorter than its record header");
        }
        let mut raw = [0_u8; 24];
        input.exact(&mut raw[..width])?;
        if &raw[..4] != b"TES4" {
            return invalid("Plugin does not start with a TES4 header");
        }
        let payload_len = u32_at(&raw, 4) as usize;
        if payload_len > HEADER_LIMIT || payload_len as u64 + width as u64 > stamp.size {
            return invalid("TES4 header is truncated or exceeds the 1 MiB header limit");
        }
        let flags = u32_at(&raw, 8);
        if flags & 0x0004_0000 != 0 {
            return invalid("Compressed TES4 headers are unsupported");
        }
        let mut payload = vec![0_u8; payload_len];
        input.exact(&mut payload)?;
        let header = parse_header(&payload, &raw, name, game)?;
        let records = if deep && !matches!(game, "starfield" | "morrowind" | "unknown") {
            let mut records = scan_records(&mut input, stamp.size, width, header.masters.len())?;
            records.scan_complete = records.total_records + records.groups
                == header.declared_record_and_group_count as usize;
            Some(records)
        } else {
            if deep {
                input.drain(stamp.size - input.position)?;
            }
            None
        };
        let opened = input
            .reader
            .get_ref()
            .metadata()
            .map_err(|e| io_error("Recheck open plugin file", e))?;
        if Stamp::new(&opened) != *stamp {
            return invalid("Plugin changed during its read-only analysis");
        }
        let sha256 = input.hash.take().map(|h| format!("{:x}", h.finalize()));
        Ok(Parsed {
            header,
            records,
            sha256,
        })
    })();
    stats.bytes_read += input.position;
    if result.is_ok() && deep {
        stats.deep_scanned += 1;
    }
    result
}

fn parse_header(
    payload: &[u8],
    raw: &[u8; 24],
    name: &str,
    game: &str,
) -> Result<PluginHeader, CoreError> {
    let mut position = 0_usize;
    let mut extended = None;
    let mut version = None;
    let mut declared = 0;
    let mut masters = Vec::new();
    let mut seen_masters = HashSet::new();
    while position < payload.len() {
        if payload.len() - position < 6 {
            return invalid("Truncated TES4 subrecord header");
        }
        let tag = &payload[position..position + 4];
        let short = u16::from_le_bytes([payload[position + 4], payload[position + 5]]) as usize;
        position += 6;
        if tag == b"XXXX" {
            if short != 4 || extended.is_some() || payload.len() - position < 4 {
                return invalid("Malformed TES4 extended subrecord length");
            }
            extended = Some(u32_at(payload, position) as usize);
            position += 4;
            continue;
        }
        let size = extended.take().unwrap_or(short);
        let end = position
            .checked_add(size)
            .filter(|e| *e <= payload.len())
            .ok_or_else(|| CoreError::Invalid("TES4 subrecord exceeds its header bounds".into()))?;
        let data = &payload[position..end];
        match tag {
            b"HEDR" => {
                if size < 12 || version.is_some() {
                    return invalid("Missing fields or duplicate HEDR subrecord");
                }
                let value = f32::from_le_bytes(data[..4].try_into().expect("Size checked above"));
                if !value.is_finite() || !(0.0..=100.0).contains(&value) || value == 0.0 {
                    return invalid("Invalid TES4 HEDR version");
                }
                version = Some(value);
                declared = u32_at(data, 4);
            }
            b"MAST" => {
                if masters.len() >= 4096 || data.is_empty() || !data.contains(&0) {
                    return invalid("Invalid or excessive TES4 master names");
                }
                let end = data
                    .iter()
                    .position(|b| *b == 0)
                    .expect("Terminator checked above");
                let value = decode_plugin_string(&data[..end]);
                if super::plugin_name(&value).is_none()
                    || !seen_masters.insert(value.to_lowercase())
                {
                    return invalid("Path-like, duplicate, or invalid TES4 master name");
                }
                masters.push(value);
            }
            _ => {}
        }
        position = end;
    }
    if extended.is_some() {
        return invalid("Dangling TES4 extended subrecord length");
    }
    let version =
        version.ok_or_else(|| CoreError::Invalid("TES4 header has no HEDR version".into()))?;
    let flags = u32_at(raw, 8);
    let modern = matches!(
        game,
        "skyrimspecialedition" | "enderalspecialedition" | "skyrimvr" | "fallout4" | "fallout4vr"
    );
    let light_flag = if game == "starfield" {
        flags & 0x100 != 0
    } else {
        modern && flags & 0x200 != 0
    };
    let update_flag = game == "starfield" && flags & 0x200 != 0;
    let by_extension = name.to_ascii_lowercase().ends_with(".esl")
        && (modern || game == "starfield")
        && !(game == "starfield" && update_flag);
    Ok(PluginHeader {
        flags,
        version,
        record_header_version: u16::from_le_bytes([raw[20], raw[21]]),
        masters,
        light_flag,
        light_by_extension: by_extension,
        light_effective: light_flag || by_extension,
        master_flag: flags & 1 != 0,
        medium_flag: game == "starfield" && flags & 0x400 != 0,
        update_flag,
        declared_record_and_group_count: declared,
    })
}

fn scan_records(
    input: &mut Input,
    file_length: u64,
    width: usize,
    masters: usize,
) -> Result<RecordSummary, CoreError> {
    if masters > 253 {
        return invalid("This audit does not resolve plugins with more than 253 masters");
    }
    let mut summary = RecordSummary {
        scope: "record-headers".into(),
        ..Default::default()
    };
    let mut group_ends = vec![file_length];
    let mut seen_ids = HashSet::new();
    while input.position < file_length {
        while group_ends.len() > 1 && group_ends.last() == Some(&input.position) {
            group_ends.pop();
        }
        let parent_end = *group_ends.last().expect("File boundary is always retained");
        if parent_end.saturating_sub(input.position) < width as u64 {
            return invalid("Truncated record or group header");
        }
        let start = input.position;
        let mut raw = [0_u8; 24];
        input.exact(&mut raw[..width])?;
        let length = u32_at(&raw, 4) as u64;
        let group = &raw[..4] == b"GRUP";
        let end = if group {
            start.checked_add(length)
        } else {
            input.position.checked_add(length)
        };
        let end = end.filter(|e| *e <= parent_end).ok_or_else(|| {
            CoreError::Invalid("Plugin record exceeds its enclosing group or file".into())
        })?;
        if group {
            if length < width as u64 || group_ends.len() >= GROUP_DEPTH_LIMIT {
                return invalid("Invalid group size or excessive plugin group nesting");
            }
            summary.groups += 1;
            group_ends.push(end);
        } else {
            if group_ends.len() == 1
                || &raw[..4] == b"TES4"
                || !raw[..4]
                    .iter()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || *b == b'_')
            {
                return invalid("Invalid record signature or record outside a GRUP");
            }
            summary.total_records += 1;
            let id = u32_at(&raw, 12);
            let flags = u32_at(&raw, 8);
            if flags & 0x20 != 0 {
                summary.deleted_records += 1;
            }
            if flags & 0x0004_0000 != 0 {
                summary.compressed_records += 1;
            }
            if id == 0 {
                summary.null_form_ids += 1;
            } else {
                if !seen_ids.insert(id) {
                    summary.duplicate_form_ids += 1;
                }
                let index = (id >> 24) as usize;
                let local = id & 0x00FF_FFFF;
                if index > masters {
                    summary.invalid_master_indices += 1;
                } else if index == masters {
                    summary.new_records += 1;
                    summary.minimum_new_local_id =
                        Some(summary.minimum_new_local_id.map_or(local, |n| n.min(local)));
                    summary.maximum_new_local_id =
                        Some(summary.maximum_new_local_id.map_or(local, |n| n.max(local)));
                    if local == 0 {
                        summary.zero_new_local_ids += 1;
                    }
                    if local < 0x800 {
                        summary.new_ids_below_0800 += 1;
                    }
                    if local > 0xFFF {
                        summary.new_ids_above_0fff += 1;
                    }
                    *summary
                        .new_record_types
                        .entry(String::from_utf8_lossy(&raw[..4]).into_owned())
                        .or_default() += 1;
                    if summary.new_record_types.len() > 512 {
                        return invalid("Plugin exceeds the 512 record-signature audit limit");
                    }
                } else {
                    summary.override_or_injected_records += 1;
                }
            }
            input.drain(length)?;
        }
        if summary.total_records + summary.groups > RECORD_LIMIT {
            return invalid("Plugin exceeds the 2000000-record/group audit limit");
        }
    }
    summary.scan_complete = true;
    Ok(summary)
}

pub(super) fn assess(
    game: &str,
    runtime: Option<&str>,
    header: &PluginHeader,
    records: Option<&RecordSummary>,
) -> (Eligibility, String) {
    let supported_game = matches!(
        game,
        "skyrimspecialedition" | "enderalspecialedition" | "fallout4"
    );
    let runtime_support = runtime_support(game, runtime, header.version);
    let mut result = if header.light_effective {
        Eligibility::new(
            "already-light",
            "light-header",
            "The current file has a light flag or effective .esl extension. This does not prove a local conversion or runtime support.",
        )
    } else if !supported_game {
        Eligibility::new(
            "unsupported",
            "unsupported-game",
            "Conversion eligibility for this game is not implemented; no small/medium/master-index assumptions are applied.",
        )
    } else if records.is_none() {
        Eligibility::new(
            "unknown",
            "header-only",
            "Run the explicit record audit to check new FormIDs. The fast import reads only the header.",
        )
    } else {
        Eligibility::new(
            "unknown",
            "records-read",
            "Record IDs were read without changing this file.",
        )
    };
    if !supported_game {
        if game == "starfield" {
            result.add("starfield-master-resolution-not-assessed", "Starfield uses different small, medium and update flags; conversion needs complete master-scale resolution.");
        }
        if matches!(game, "skyrimvr" | "fallout4vr") {
            result.add(
                "vr-support-unverified",
                "A compatible VR extension and runtime must be verified separately.",
            );
        }
        return (result, runtime_support);
    }
    if (game == "fallout4" && header.version > 1.0) || (game != "fallout4" && header.version > 1.71)
    {
        if !header.light_effective {
            result.status = "unknown".into();
        }
        result.add("unrecognized-header-version", "This header is newer than the verified format rules; conversion eligibility is unknown.");
        return (result, runtime_support);
    }
    if let Some(records) = records {
        result.add("record-payloads-not-validated", "The bounded scan checks record framing and ObjectIDs; it does not decode or validate record payloads.");
        if !records.scan_complete {
            result.status = "unknown".into();
            result.add("declared-record-count-mismatch", "The observed record/group count does not match HEDR. The file may be truncated or have a stale header; inspect it in xEdit before considering conversion.");
            return (result, runtime_support);
        }
        let extended = if game == "fallout4" {
            header.version >= 1.0
        } else {
            header.version >= 1.71
        };
        // xEdit documents allocation from 0x001, while LOOT accepts local zero
        // for SSE 1.71. Local zero with a nonzero master index is not a null
        // full FormID; that boundary remains a manual-review case below.
        let minimum = if extended { 1 } else { 0x800 };
        let capacity = 0x1000 - minimum;
        let fits = records
            .minimum_new_local_id
            .is_none_or(|n| n >= minimum as u32)
            && records.maximum_new_local_id.is_none_or(|n| n <= 0xFFF);
        if records.invalid_master_indices > 0
            || records.duplicate_form_ids > 0
            || records.null_form_ids > 0
        {
            result.status = "blocked".into();
            result.add("invalid-record-identities", "Null, duplicate or unresolved master-index FormIDs require manual examination before any conversion.");
        } else if extended
            && game != "fallout4"
            && records.zero_new_local_ids > 0
            && records.new_ids_above_0fff == 0
            && records.new_records <= 4096
        {
            result.status = "unknown".into();
            result.add("zero-object-id-review", "This extended SSE plugin uses local ObjectID zero with a nonzero master index. Tool allocation rules differ at this boundary; manual xEdit review is required.");
        } else if records.new_records > capacity {
            result.status = "blocked".into();
            result.add(
                "too-many-new-records",
                "The number of new records exceeds this header's available light ObjectID range.",
            );
        } else if !fits && header.light_effective {
            result.status = "blocked".into();
            result.add("invalid-light-range", "This already-light file contains new ObjectIDs outside the range allowed by its header version.");
        } else if !fits {
            result.status = "requires-compaction".into();
            result.add("new-ids-need-remapping", "FormIDs must change. Use a conversion tool that patches dependent plugins and external references; existing saves are not assessed.");
        } else if !header.light_effective {
            result.status = "flag-only-candidate".into();
            result.add("new-ids-fit-light-range", "New ObjectIDs fit the header's light range without compaction. This is a conditional file-format candidate, not a safety certification.");
        }
        if records.new_record_types.contains_key("CELL")
            || records.new_record_types.contains_key("WRLD")
        {
            result.status = "blocked".into();
            result.add("new-cell-or-worldspace", "New CELL/WRLD records need game-specific conflict and runtime-fix review; this audit does not verify those fixes.");
        }
        if records.deleted_records > 0 {
            result.add("deleted-records", "Deleted records were found; cleaning and conflict behavior have not been assessed.");
        }
        if matches!(
            result.status.as_str(),
            "requires-compaction" | "flag-only-candidate"
        ) {
            result.add("external-references-not-assessed", "Scripts, DLLs, archives, FaceGen, voice, distribution files, dependent plugin references and save games are not analyzed by this record scan.");
        }
        if result.status == "requires-compaction"
            && ["NPC_", "INFO", "WTHR", "QUST", "NAVM"]
                .iter()
                .any(|kind| records.new_record_types.contains_key(*kind))
        {
            result.add("external-data-remapping-review", "This plugin adds record types that may have external or saved references; compaction requires dedicated tool review and patching.");
        }
    }
    if runtime_support != "supported" {
        result.add("runtime-support-unverified", "The configured runtime does not establish the required light/extended-light support. Backports are not inferred from filenames.");
        if result.status == "flag-only-candidate" {
            result.status = "unknown".into();
        }
    }
    (result, runtime_support)
}

fn runtime_support(game: &str, runtime: Option<&str>, header: f32) -> String {
    if !matches!(
        game,
        "skyrimspecialedition" | "enderalspecialedition" | "fallout4" | "starfield"
    ) {
        return "unknown".into();
    }
    if game == "starfield" {
        return "unknown".into();
    }
    let Some(parts) = runtime.and_then(parse_version) else {
        return "unknown".into();
    };
    let minimum = if game == "fallout4" {
        [1, 10, 163, 0]
    } else if header >= 1.71 {
        [1, 6, 1130, 0]
    } else {
        [1, 5, 97, 0]
    };
    if parts >= minimum {
        "supported"
    } else {
        "unknown"
    }
    .into()
}

fn parse_version(text: &str) -> Option<[u32; 4]> {
    let pieces: Vec<_> = text.trim().split('.').collect();
    if !(3..=4).contains(&pieces.len()) {
        return None;
    }
    let mut result = [0; 4];
    for (i, p) in pieces.iter().enumerate() {
        result[i] = p.parse().ok()?;
    }
    Some(result)
}

fn decode_plugin_string(bytes: &[u8]) -> String {
    if let Ok(text) = std::str::from_utf8(bytes) {
        return text.into();
    }
    const CP1252: [char; 32] = [
        '€', '\u{81}', '‚', 'ƒ', '„', '…', '†', '‡', 'ˆ', '‰', 'Š', '‹', 'Œ', '\u{8D}', 'Ž',
        '\u{8F}', '\u{90}', '‘', '’', '“', '”', '•', '–', '—', '˜', '™', 'š', '›', 'œ', '\u{9D}',
        'ž', 'Ÿ',
    ];
    bytes
        .iter()
        .map(|b| {
            if (0x80..=0x9F).contains(b) {
                CP1252[(b - 0x80) as usize]
            } else {
                char::from(*b)
            }
        })
        .collect()
}

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(
        bytes[offset..offset + 4]
            .try_into()
            .expect("Callers validate record/subrecord bounds"),
    )
}

fn invalid<T>(message: &str) -> Result<T, CoreError> {
    Err(CoreError::Invalid(message.into()))
}
