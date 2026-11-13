use modhunter_core::{
    CoreError, ImportRequest, PluginAnalyzer, PluginAuditRequest, PluginMod, PluginState,
    ProfileImporter,
};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use tempfile::TempDir;

struct Fixture {
    root: TempDir,
    request: PluginAuditRequest,
}
impl Fixture {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let request = PluginAuditRequest {
            game: "skyrimspecialedition".into(),
            game_runtime: Some("1.6.1170".into()),
            profile_id: Some("fixture-profile".into()),
            profile_name: Some("Test".into()),
            deep: true,
            workers: Some(2),
            ..Default::default()
        };
        Self { root, request }
    }
    fn add(&mut self, id: &str, priority: i64, enabled: bool) -> PathBuf {
        let path = self.root.path().join("mods").join(id);
        fs::create_dir_all(&path).unwrap();
        self.request.mods.push(PluginMod {
            id: id.into(),
            name: id.into(),
            path: path.clone(),
            enabled,
            priority,
            installed_version: Some("1.0".into()),
        });
        path
    }
    fn active(&mut self, names: &[&str]) {
        self.request.plugin_states = Some(
            names
                .iter()
                .enumerate()
                .map(|(i, n)| PluginState {
                    name: (*n).into(),
                    active: true,
                    load_order: Some(i as i64),
                    origin: None,
                    path: None,
                })
                .collect(),
        );
    }
}

fn plugin(flags: u32, version: f32, masters: &[&str], entries: &[(&str, u32)]) -> Vec<u8> {
    let mut payload = Vec::new();
    let mut hedr = Vec::new();
    hedr.extend(version.to_le_bytes());
    hedr.extend((entries.len() as u32 + u32::from(!entries.is_empty())).to_le_bytes());
    hedr.extend(0x800_u32.to_le_bytes());
    field(&mut payload, b"HEDR", &hedr);
    for master in masters {
        field(&mut payload, b"MAST", &[master.as_bytes(), &[0]].concat());
        field(&mut payload, b"DATA", &[0; 8]);
    }
    let mut result = record(b"TES4", flags, 0, &payload);
    if !entries.is_empty() {
        let mut body = Vec::new();
        for (signature, id) in entries {
            body.extend(record(
                signature.as_bytes().try_into().unwrap(),
                0,
                *id,
                b"",
            ));
        }
        let mut group = vec![0_u8; 24];
        group[..4].copy_from_slice(b"GRUP");
        group[4..8].copy_from_slice(&(body.len() as u32 + 24).to_le_bytes());
        group[8..12].copy_from_slice(b"MISC");
        result.extend(group);
        result.extend(body);
    }
    result
}
fn field(out: &mut Vec<u8>, tag: &[u8; 4], value: &[u8]) {
    out.extend(tag);
    out.extend((value.len() as u16).to_le_bytes());
    out.extend(value);
}
fn record(tag: &[u8; 4], flags: u32, id: u32, payload: &[u8]) -> Vec<u8> {
    let mut result = vec![0_u8; 24];
    result[..4].copy_from_slice(tag);
    result[4..8].copy_from_slice(&(payload.len() as u32).to_le_bytes());
    result[8..12].copy_from_slice(&flags.to_le_bytes());
    result[12..16].copy_from_slice(&id.to_le_bytes());
    result[20..22].copy_from_slice(&44_u16.to_le_bytes());
    result.extend(payload);
    result
}
fn hash(path: &Path) -> String {
    format!("{:x}", Sha256::digest(fs::read(path).unwrap()))
}

#[test]
fn canceled_deep_audit_is_not_a_partial_success_and_can_be_retried() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let mut f = Fixture::new();
    let directory = f.add("Source", 0, true);
    let path = directory.join("Large.esp");
    // An unsupported record grammar still requires fresh hashing; a large
    // payload proves cancellation reaches the bounded byte reader itself.
    f.request.game = "starfield".into();
    f.request.workers = Some(1);
    fs::write(&path, plugin(0, 1.0, &[], &[])).unwrap();
    fs::OpenOptions::new()
        .write(true)
        .open(&path)
        .unwrap()
        .set_len(8 * 1024 * 1024)
        .unwrap();
    let engine = PluginAnalyzer::new();
    let checks = AtomicUsize::new(0);
    let canceled =
        engine.audit_cancellable(&f.request, &|| checks.fetch_add(1, Ordering::Relaxed) >= 20);
    assert!(matches!(canceled, Err(CoreError::Cancelled)));
    let completed = engine.audit(&f.request).unwrap();
    let copy = &completed.mods["Source"].plugins[0];
    assert_eq!(
        copy.fingerprint.as_ref().unwrap().sha256.as_deref(),
        Some(hash(&path).as_str())
    );
    assert_eq!(completed.stats.deep_scanned, 1);
    assert!(copy.errors.is_empty());
}

fn restore_modified(path: &Path, modified: std::time::SystemTime) {
    #[cfg(windows)]
    let file = {
        use std::os::windows::fs::OpenOptionsExt;
        // FILE_WRITE_ATTRIBUTES plus BACKUP_SEMANTICS also permits setting
        // directory timestamps; no data write or inherited handle is needed.
        fs::OpenOptions::new()
            .access_mode(0x0000_0100)
            .custom_flags(0x0200_0000)
            .open(path)
            .unwrap()
    };
    #[cfg(not(windows))]
    let file = fs::File::open(path).unwrap();
    file.set_times(fs::FileTimes::new().set_modified(modified))
        .unwrap();
}

#[test]
fn fast_header_facts_are_cached_without_claiming_conversion_eligibility() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let bytes = plugin(0, 1.7, &["Skyrim.esm"], &[("MISC", 0x0100_0800)]);
    fs::write(root.join("Example.esp"), &bytes).unwrap();
    f.request.deep = false;
    let engine = PluginAnalyzer::new();
    let first = engine.audit(&f.request).unwrap();
    let p = &first.mods["Source"].plugins[0];
    assert_eq!(p.eligibility.status, "unknown");
    assert_eq!(p.eligibility.save_safety, "not-assessed");
    assert_eq!(p.header.as_ref().unwrap().masters, ["Skyrim.esm"]);
    assert_eq!(p.header.as_ref().unwrap().record_header_version, 44);
    assert!(p.records.is_none());
    assert!(p.fingerprint.as_ref().unwrap().sha256.is_none());
    assert!(first.stats.bytes_read < bytes.len() as u64);
    let warm = engine.audit(&f.request).unwrap();
    assert_eq!(warm.stats.header_cache_hits, 1);
    assert_eq!(warm.stats.directory_cache_hits, 1);
    assert_eq!(warm.stats.directories_read, 1);
    assert_eq!(warm.stats.files_read, 1);
    assert_eq!(warm.stats.bytes_read, first.stats.bytes_read);
    assert!(warm.stats.bytes_read > 0 && warm.stats.bytes_read < bytes.len() as u64);
    let old_time = fs::metadata(root.join("Example.esp"))
        .unwrap()
        .modified()
        .unwrap();
    let mut changed = bytes.clone();
    changed[8..12].copy_from_slice(&0x200_u32.to_le_bytes());
    fs::write(root.join("Example.esp"), &changed).unwrap();
    restore_modified(&root.join("Example.esp"), old_time);
    let refreshed = engine.audit(&f.request).unwrap();
    assert_eq!(
        refreshed.mods["Source"].plugins[0].eligibility.status,
        "already-light"
    );
    assert_eq!(refreshed.stats.header_cache_hits, 0);
    fs::write(root.join("Added.esl"), plugin(0, 1.7, &[], &[])).unwrap();
    assert_eq!(engine.audit(&f.request).unwrap().stats.plugin_copies, 2);
    assert_eq!(fs::read(root.join("Example.esp")).unwrap(), changed);
}

#[test]
fn payload_streaming_preserves_hashes_and_record_boundaries_for_every_buffer_size() {
    let mut f = Fixture::new();
    let root = f.add("Payloads", 0, true);
    let path = root.join("Payloads.esp");
    // Include empty, tiny, exactly buffered and multi-buffer payloads. Their
    // contents are opaque to the record audit, but every byte must be hashed.
    let sizes = [0, 1, 8191, 8192, 8193, 65535, 65536, 65537, 131077];
    let entries: Vec<_> = sizes
        .iter()
        .enumerate()
        .map(|(i, _)| ("MISC", 0x800 + i as u32))
        .collect();
    let mut bytes = plugin(0, 1.7, &[], &entries);
    let header_length = 24 + u32::from_le_bytes(bytes[4..8].try_into().unwrap()) as usize;
    bytes.truncate(header_length);
    let mut body = Vec::new();
    for (i, size) in sizes.into_iter().enumerate() {
        let payload: Vec<_> = (0..size).map(|j| ((j + i) % 251) as u8).collect();
        body.extend(record(b"MISC", 0, 0x800 + i as u32, &payload));
    }
    let mut group = vec![0_u8; 24];
    group[..4].copy_from_slice(b"GRUP");
    group[4..8].copy_from_slice(&(body.len() as u32 + 24).to_le_bytes());
    bytes.extend(group);
    bytes.extend(body);
    fs::write(&path, &bytes).unwrap();
    let old_time = fs::metadata(&path).unwrap().modified().unwrap();
    let engine = PluginAnalyzer::new();
    let result = engine.audit(&f.request).unwrap();
    let copy = &result.mods["Payloads"].plugins[0];
    let summary = copy.records.as_ref().unwrap();
    assert!(summary.scan_complete);
    assert_eq!(summary.total_records, sizes.len());
    assert_eq!(summary.new_record_types["MISC"], sizes.len());
    assert_eq!(result.stats.bytes_read, bytes.len() as u64);
    assert_eq!(copy.fingerprint.as_ref().unwrap().sha256, Some(hash(&path)));
    let old_hash = copy.fingerprint.as_ref().unwrap().sha256.clone();

    // An equal-length payload edit with restored mtime must still produce a
    // fresh hash, including data beyond the first buffer.
    let last = bytes.len() - 1;
    bytes[last] ^= 0x7f;
    fs::write(&path, &bytes).unwrap();
    restore_modified(&path, old_time);
    let changed = engine.audit(&f.request).unwrap();
    let copy = &changed.mods["Payloads"].plugins[0];
    assert_eq!(copy.fingerprint.as_ref().unwrap().sha256, Some(hash(&path)));
    assert_ne!(copy.fingerprint.as_ref().unwrap().sha256, old_hash);
    assert_eq!(copy.records.as_ref().unwrap().total_records, sizes.len());

    // Unsupported record formats hash the whole tail without claiming a
    // record audit or conditional ESL eligibility.
    f.request.game = "starfield".into();
    let tail_only = engine.audit(&f.request).unwrap();
    let copy = &tail_only.mods["Payloads"].plugins[0];
    assert!(copy.records.is_none());
    assert_eq!(copy.fingerprint.as_ref().unwrap().sha256, Some(hash(&path)));
    assert_eq!(tail_only.stats.bytes_read, bytes.len() as u64);
}

#[test]
fn fast_header_revalidation_reads_master_version_and_invalid_magic_with_preserved_mtime() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esp");
    let original = plugin(0, 1.7, &["Skyrim.esm"], &[("MISC", 0x0100_0800)]);
    fs::write(&path, &original).unwrap();
    let old_time = fs::metadata(&path).unwrap().modified().unwrap();
    f.request.deep = false;
    let engine = PluginAnalyzer::new();
    engine.audit(&f.request).unwrap();

    let changed = plugin(0, 1.71, &["Update.esm"], &[("MISC", 0x0100_0800)]);
    assert_eq!(changed.len(), original.len());
    fs::write(&path, &changed).unwrap();
    restore_modified(&path, old_time);
    let current = engine.audit(&f.request).unwrap();
    let copy = &current.mods["Source"].plugins[0];
    let header = copy.header.as_ref().unwrap();
    assert_eq!(header.version, 1.71);
    assert_eq!(header.masters, ["Update.esm"]);
    assert_eq!(current.stats.header_cache_hits, 0);
    assert!(copy.records.is_none());
    assert!(copy.fingerprint.as_ref().unwrap().sha256.is_none());

    let mut invalid = changed;
    invalid[..4].copy_from_slice(b"BAD!");
    fs::write(&path, invalid).unwrap();
    restore_modified(&path, old_time);
    let rejected = engine.audit(&f.request).unwrap();
    let copy = &rejected.mods["Source"].plugins[0];
    assert!(copy.header.is_none());
    assert_eq!(copy.eligibility.status, "unknown");
    assert!(!copy.errors.is_empty());
    assert_eq!(rejected.stats.header_cache_hits, 0);
}

#[test]
fn plugin_membership_changes_are_visible_with_preserved_directory_mtime() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    fs::write(root.join("Original.esp"), plugin(0, 1.7, &[], &[])).unwrap();
    let old_time = fs::metadata(&root).unwrap().modified().unwrap();
    f.request.deep = false;
    let engine = PluginAnalyzer::new();
    assert_eq!(engine.audit(&f.request).unwrap().stats.plugin_copies, 1);

    fs::write(root.join("Added.esl"), plugin(0, 1.7, &[], &[])).unwrap();
    restore_modified(&root, old_time);
    let added = engine.audit(&f.request).unwrap();
    assert_eq!(added.stats.plugin_copies, 2);
    assert_eq!(added.stats.directory_cache_hits, 0);

    fs::remove_file(root.join("Original.esp")).unwrap();
    restore_modified(&root, old_time);
    let removed = engine.audit(&f.request).unwrap();
    assert_eq!(removed.stats.plugin_copies, 1);
    assert_eq!(removed.mods["Source"].plugins[0].name, "Added.esl");
    assert_eq!(
        removed.mods["Source"].plugins[0].eligibility.status,
        "already-light"
    );

    fs::rename(root.join("Added.esl"), root.join("Renamed.esp")).unwrap();
    restore_modified(&root, old_time);
    let renamed = engine.audit(&f.request).unwrap();
    assert_eq!(renamed.stats.plugin_copies, 1);
    assert_eq!(renamed.mods["Source"].plugins[0].name, "Renamed.esp");
    assert_eq!(
        renamed.mods["Source"].plugins[0].eligibility.status,
        "unknown"
    );
    assert_eq!(renamed.stats.directory_cache_hits, 0);
}

#[test]
fn deep_record_changes_are_not_hidden_by_cached_headers_or_preserved_mtime() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esp");
    let original = plugin(0, 1.7, &["Skyrim.esm"], &[("MISC", 0x0100_0800)]);
    fs::write(&path, &original).unwrap();
    let old_time = fs::metadata(&path).unwrap().modified().unwrap();
    let engine = PluginAnalyzer::new();
    let first = engine.audit(&f.request).unwrap();
    let previous = &first.mods["Source"].plugins[0];
    assert_eq!(previous.eligibility.status, "flag-only-candidate");

    let changed = plugin(0, 1.7, &["Skyrim.esm"], &[("MISC", 0x0100_1000)]);
    assert_eq!(changed.len(), original.len());
    fs::write(&path, &changed).unwrap();
    restore_modified(&path, old_time);
    let current = engine.audit(&f.request).unwrap();
    let copy = &current.mods["Source"].plugins[0];
    assert_eq!(copy.eligibility.status, "requires-compaction");
    assert_eq!(
        copy.records.as_ref().unwrap().maximum_new_local_id,
        Some(0x1000)
    );
    assert_ne!(
        copy.fingerprint.as_ref().unwrap().sha256,
        previous.fingerprint.as_ref().unwrap().sha256
    );
    assert_eq!(current.stats.deep_scanned, 1);
    assert_eq!(current.stats.header_cache_hits, 0);
    assert_eq!(current.stats.bytes_read, changed.len() as u64);

    f.request.deep = false;
    let fast = engine.audit(&f.request).unwrap();
    let copy = &fast.mods["Source"].plugins[0];
    assert_eq!(copy.eligibility.status, "unknown");
    assert!(copy.records.is_none());
    assert!(copy.fingerprint.as_ref().unwrap().sha256.is_none());
    assert_eq!(fast.stats.header_cache_hits, 1);
}

#[cfg(windows)]
#[test]
fn active_windows_writer_prevents_a_mixed_plugin_snapshot() {
    use std::os::windows::fs::OpenOptionsExt;
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esp");
    fs::write(&path, plugin(0x200, 1.7, &[], &[])).unwrap();
    let writer = fs::OpenOptions::new()
        .write(true)
        .share_mode(0x0000_0003)
        .open(&path)
        .unwrap();
    let engine = PluginAnalyzer::new();
    let busy = engine.audit(&f.request).unwrap();
    let copy = &busy.mods["Source"].plugins[0];
    assert_eq!(copy.eligibility.status, "unknown");
    assert!(copy.header.is_none());
    assert!(!copy.errors.is_empty());
    drop(writer);
    let stable = engine.audit(&f.request).unwrap();
    assert_eq!(
        stable.mods["Source"].plugins[0].eligibility.status,
        "already-light"
    );
}

#[test]
fn new_formids_exclude_master_overrides_and_deep_audit_hashes_fresh_bytes() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esp");
    fs::write(
        &path,
        plugin(
            0,
            1.7,
            &["Skyrim.esm"],
            &[
                ("MISC", 0x0100_0800),
                ("WEAP", 0x0100_0FFF),
                ("MISC", 0x000A_BCDE),
            ],
        ),
    )
    .unwrap();
    let engine = PluginAnalyzer::new();
    let result = engine.audit(&f.request).unwrap();
    let p = &result.mods["Source"].plugins[0];
    assert_eq!(p.eligibility.status, "flag-only-candidate");
    let records = p.records.as_ref().unwrap();
    assert_eq!(records.new_records, 2);
    assert_eq!(records.override_or_injected_records, 1);
    assert_eq!(records.maximum_new_local_id, Some(0xFFF));
    assert_eq!(
        p.fingerprint.as_ref().unwrap().sha256.as_deref(),
        Some(hash(&path).as_str())
    );
    let old_time = fs::metadata(&path).unwrap().modified().unwrap();
    fs::write(
        &path,
        plugin(
            0x200,
            1.7,
            &["Skyrim.esm"],
            &[
                ("MISC", 0x0100_0800),
                ("WEAP", 0x0100_0FFF),
                ("MISC", 0x000A_BCDE),
            ],
        ),
    )
    .unwrap();
    fs::OpenOptions::new()
        .write(true)
        .open(&path)
        .unwrap()
        .set_times(fs::FileTimes::new().set_modified(old_time))
        .unwrap();
    let fresh = engine.audit(&f.request).unwrap();
    assert_eq!(fresh.stats.deep_scanned, 1);
    assert_eq!(
        fresh.mods["Source"].plugins[0].eligibility.status,
        "already-light"
    );
    assert_ne!(
        fresh.mods["Source"].plugins[0]
            .fingerprint
            .as_ref()
            .unwrap()
            .sha256,
        p.fingerprint.as_ref().unwrap().sha256
    );
}

#[test]
fn compaction_capacity_extended_range_and_runtime_are_distinct() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esp");
    let engine = PluginAnalyzer::new();
    fs::write(&path, plugin(0, 1.7, &[], &[("MISC", 0x1000)])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .eligibility
            .status,
        "requires-compaction"
    );
    let many: Vec<_> = (0x800..=0x1000).map(|id| ("MISC", id)).collect();
    fs::write(&path, plugin(0, 1.7, &[], &many)).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .eligibility
            .status,
        "blocked"
    );
    fs::write(&path, plugin(0, 1.71, &[], &[("MISC", 1), ("MISC", 0x7FF)])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .eligibility
            .status,
        "flag-only-candidate"
    );
    f.request.game_runtime = Some("1.5.97".into());
    let older = engine.audit(&f.request).unwrap();
    assert_eq!(
        older.mods["Source"].plugins[0].eligibility.status,
        "unknown"
    );
    assert_eq!(older.mods["Source"].plugins[0].runtime_support, "unknown");
    f.request.game_runtime = Some("1.6.1170".into());
    fs::write(
        &path,
        plugin(0, 1.71, &["Skyrim.esm"], &[("MISC", 0x0100_0000)]),
    )
    .unwrap();
    let zero_local = engine.audit(&f.request).unwrap();
    assert_eq!(
        zero_local.mods["Source"].plugins[0].eligibility.status,
        "unknown"
    );
    assert_eq!(
        zero_local.mods["Source"].plugins[0]
            .records
            .as_ref()
            .unwrap()
            .null_form_ids,
        0
    );
    assert_eq!(
        zero_local.mods["Source"].plugins[0]
            .records
            .as_ref()
            .unwrap()
            .zero_new_local_ids,
        1
    );
    f.request.game = "fallout4".into();
    f.request.game_runtime = Some("1.10.163".into());
    fs::write(&path, plugin(0, 1.0, &[], &[("MISC", 1)])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .eligibility
            .status,
        "flag-only-candidate"
    );
    fs::write(&path, plugin(0, 0.95, &[], &[("MISC", 1)])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .eligibility
            .status,
        "requires-compaction"
    );
}

#[test]
fn cell_worldspace_null_duplicate_and_invalid_light_records_never_get_green_candidates() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esp");
    let engine = PluginAnalyzer::new();
    for entries in [
        vec![("CELL", 0x800)],
        vec![("WRLD", 0x800)],
        vec![("MISC", 0)],
        vec![("MISC", 0x800), ("MISC", 0x800)],
        vec![("MISC", 0x0100_0800)],
    ] {
        fs::write(&path, plugin(0, 1.7, &[], &entries)).unwrap();
        assert_eq!(
            engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
                .eligibility
                .status,
            "blocked"
        );
    }
    fs::write(&path, plugin(0x200, 1.7, &[], &[("MISC", 0x1000)])).unwrap();
    let invalid = engine.audit(&f.request).unwrap();
    assert!(
        invalid.mods["Source"].plugins[0]
            .header
            .as_ref()
            .unwrap()
            .light_effective
    );
    assert_eq!(
        invalid.mods["Source"].plugins[0].eligibility.status,
        "blocked"
    );
}

#[test]
fn game_specific_flags_do_not_confuse_starfield_update_with_light() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esl");
    f.request.game = "starfield".into();
    let engine = PluginAnalyzer::new();
    fs::write(&path, plugin(0x200, 0.96, &[], &[])).unwrap();
    let update = engine.audit(&f.request).unwrap();
    let p = &update.mods["Source"].plugins[0];
    assert!(!p.header.as_ref().unwrap().light_effective);
    assert!(p.header.as_ref().unwrap().update_flag);
    assert_eq!(p.eligibility.status, "unsupported");
    assert_eq!(p.runtime_support, "unknown");
    fs::write(&path, plugin(0x100, 0.96, &[], &[])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .eligibility
            .status,
        "already-light"
    );
    f.request.game = "skyrimvr".into();
    fs::write(&path, plugin(0x200, 1.7, &[], &[])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0].runtime_support,
        "unknown"
    );
}

#[test]
fn winner_uses_enabled_priority_overwrite_and_authoritative_mo2_path() {
    let mut f = Fixture::new();
    let source = f.add("Source", 0, true);
    let output = f.add("Output", 5, true);
    let disabled = f.add("Disabled", 10, false);
    fs::write(source.join("Example.esp"), plugin(0, 1.7, &[], &[])).unwrap();
    fs::write(output.join("example.ESP"), plugin(0x200, 1.7, &[], &[])).unwrap();
    fs::write(disabled.join("Example.esp"), plugin(0, 1.7, &[], &[])).unwrap();
    f.active(&["EXAMPLE.esp"]);
    let engine = PluginAnalyzer::new();
    let result = engine.audit(&f.request).unwrap();
    let source_copy = &result.mods["Source"].plugins[0];
    assert_eq!(source_copy.winner_mod_id.as_deref(), Some("Output"));
    assert!(source_copy.light_override);
    assert_eq!(source_copy.active, Some(true));
    assert_eq!(source_copy.provenance.state, "unknown");
    let overwrite = f.root.path().join("custom overwrite");
    fs::create_dir(&overwrite).unwrap();
    fs::write(overwrite.join("Example.esp"), plugin(0, 1.7, &[], &[])).unwrap();
    f.request.overwrite_path = Some(overwrite);
    assert_eq!(
        engine.audit(&f.request).unwrap().plugins[0]
            .copy
            .owner_mod_id,
        "__overwrite__"
    );
    f.request.plugin_states.as_mut().unwrap()[0].path = Some(source.join("Example.esp"));
    assert_eq!(
        engine.audit(&f.request).unwrap().plugins[0]
            .copy
            .owner_mod_id,
        "Source"
    );
    f.request.plugin_states.as_mut().unwrap()[0].path =
        Some(f.root.path().join("unowned/Example.esp"));
    let mismatch = engine.audit(&f.request).unwrap();
    assert!(
        mismatch.plugins[0]
            .source_copies
            .iter()
            .all(|p| !p.winner && !p.winner_known)
    );
    assert!(!mismatch.warnings.is_empty());
}

#[test]
fn active_order_and_master_issues_come_from_profile_files() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    fs::write(
        root.join("Example.esp"),
        plugin(
            0,
            1.7,
            &["Disabled.esm", "Late.esm", "Missing.esm"],
            &[("MISC", 0x0300_0800)],
        ),
    )
    .unwrap();
    let data = f.root.path().join("Data");
    fs::create_dir(&data).unwrap();
    for name in ["Disabled.esm", "Late.esm"] {
        fs::write(data.join(name), plugin(1, 1.7, &[], &[])).unwrap();
    }
    f.request.game_data_path = Some(data);
    let plugins = f.root.path().join("plugins.txt");
    let order = f.root.path().join("loadorder.txt");
    let text = "# fixture\nDisabled.esm\n*Late.esm\n*Example.esp\n";
    let mut bytes = vec![0xFF, 0xFE];
    bytes.extend(text.encode_utf16().flat_map(u16::to_le_bytes));
    fs::write(&plugins, bytes).unwrap();
    fs::write(
        &order,
        "\u{feff}# order\nExample.esp\nLate.esm\nDisabled.esm\n",
    )
    .unwrap();
    f.request.plugins_path = Some(plugins);
    f.request.loadorder_path = Some(order);
    let result = PluginAnalyzer::new().audit(&f.request).unwrap();
    let p = &result.mods["Source"].plugins[0];
    let issues: Vec<_> = p
        .master_issues
        .iter()
        .map(|i| (i.name.as_str(), i.status.as_str()))
        .collect();
    assert_eq!(
        issues,
        [
            ("Disabled.esm", "inactive"),
            ("Late.esm", "loads-after"),
            ("Missing.esm", "missing")
        ]
    );
    assert_eq!(p.eligibility.status, "blocked");
    assert_eq!(p.load_order, Some(0));
    assert_eq!(result.activation_source, "plugins.txt");
}

#[test]
fn native_unscanned_masters_and_text_omissions_stay_unknown_instead_of_false_missing() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    fs::write(
        root.join("Example.esp"),
        plugin(0, 1.7, &["External.esm"], &[("MISC", 0x0100_0800)]),
    )
    .unwrap();
    let data = f.root.path().join("Data");
    fs::create_dir(&data).unwrap();
    f.request.game_data_path = Some(data.clone());
    f.active(&["External.esm", "Example.esp"]);
    f.request.plugin_states.as_mut().unwrap()[0].path =
        Some(f.root.path().join("outside/External.esm"));
    let engine = PluginAnalyzer::new();
    let unscanned = engine.audit(&f.request).unwrap();
    assert!(unscanned.mods["Source"].plugins[0].master_issues.is_empty());
    assert_eq!(
        unscanned.mods["Source"].plugins[0].unresolved_masters,
        ["External.esm"]
    );
    f.active(&["Example.esp"]);
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0].master_issues[0].status,
        "missing"
    );
    fs::write(data.join("External.esm"), plugin(1, 1.7, &[], &[])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0].master_issues[0].status,
        "inactive"
    );
    f.request.plugin_states = None;
    let plugins = f.root.path().join("plugins.txt");
    fs::write(&plugins, "*Example.esp\n").unwrap();
    f.request.plugins_path = Some(plugins);
    let textual = engine.audit(&f.request).unwrap();
    assert!(textual.mods["Source"].plugins[0].master_issues.is_empty());
    assert_eq!(
        textual.mods["Source"].plugins[0].unresolved_masters,
        ["External.esm"]
    );
}

#[test]
fn malformed_headers_group_lengths_and_file_truncation_fail_closed() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esp");
    let engine = PluginAnalyzer::new();
    let valid = plugin(0, 1.7, &[], &[("MISC", 0x800)]);
    let mut huge = valid.clone();
    huge[4..8].copy_from_slice(&u32::MAX.to_le_bytes());
    let mut bad_group = valid.clone();
    let start = 24 + 18;
    bad_group[start + 4..start + 8].copy_from_slice(&0_u32.to_le_bytes());
    let mut bad_header = valid.clone();
    bad_header[30..34].copy_from_slice(&f32::NAN.to_le_bytes());
    for bytes in [
        huge,
        bad_group,
        bad_header,
        valid[..valid.len() - 1].to_vec(),
        b"not a plugin".to_vec(),
    ] {
        fs::write(&path, bytes).unwrap();
        let result = engine.audit(&f.request).unwrap();
        let p = &result.mods["Source"].plugins[0];
        assert_eq!(p.eligibility.status, "unknown");
        assert!(!p.errors.is_empty());
        assert!(p.fingerprint.as_ref().unwrap().sha256.is_none());
    }
}

#[test]
fn truncation_at_a_valid_record_boundary_is_not_mistaken_for_an_empty_candidate() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let path = root.join("Example.esp");
    let engine = PluginAnalyzer::new();
    fs::write(&path, plugin(0, 1.7, &[], &[])).unwrap();
    let empty = engine.audit(&f.request).unwrap();
    assert_eq!(
        empty.mods["Source"].plugins[0].eligibility.status,
        "flag-only-candidate"
    );
    let mut truncated = plugin(0, 1.7, &[], &[("MISC", 0x800)]);
    truncated.truncate(42);
    fs::write(&path, truncated).unwrap();
    let result = engine.audit(&f.request).unwrap();
    let p = &result.mods["Source"].plugins[0];
    assert_eq!(p.eligibility.status, "unknown");
    assert!(!p.records.as_ref().unwrap().scan_complete);
    assert!(
        p.eligibility
            .reason_codes
            .iter()
            .any(|r| r == "declared-record-count-mismatch")
    );
    assert_eq!(
        p.header.as_ref().unwrap().declared_record_and_group_count,
        2
    );
}

#[test]
fn ghosts_are_visible_but_never_win_and_assets_are_not_traversed() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    fs::write(root.join("Ghost.esl.ghost"), plugin(0, 1.7, &[], &[])).unwrap();
    fs::create_dir(root.join("textures")).unwrap();
    fs::write(
        root.join("textures/NotAPlugin.esp"),
        plugin(0, 1.7, &[], &[]),
    )
    .unwrap();
    f.active(&["Ghost.esl"]);
    let result = PluginAnalyzer::new().audit(&f.request).unwrap();
    assert_eq!(result.stats.plugin_copies, 1);
    let p = &result.mods["Source"].plugins[0];
    assert!(p.ghosted);
    assert!(!p.winner);
    assert_eq!(p.active, Some(false));
}

fn provenance_fixture() -> (Fixture, PathBuf, PathBuf, PathBuf) {
    let mut f = Fixture::new();
    let source = f.add("Source", 0, true).join("Example.esp");
    let output = f.add("ESLifier Output", 1, true).join("Example.esp");
    fs::write(&source, plugin(0, 1.7, &[], &[("MISC", 0x800)])).unwrap();
    fs::write(&output, plugin(0x200, 1.7, &[], &[("MISC", 0x800)])).unwrap();
    f.active(&["Example.esp"]);
    let records = f.root.path().join("ESLifier_Data");
    fs::create_dir(&records).unwrap();
    fs::write(records.join("settings.json"),serde_json::to_vec(&json!({"mod_manager_mode":2,"mo2_profile":"Test","output_folder_path":f.root.path().join("mods"),"output_folder_name":"ESLifier Output"})).unwrap()).unwrap();
    fs::write(
        records.join("original_files.json"),
        serde_json::to_vec(&json!({"example.esp":[source,hash(&source)]})).unwrap(),
    )
    .unwrap();
    fs::write(
        records.join("new_file_hashes.json"),
        serde_json::to_vec(&json!({"example.esp":[hash(&output),false]})).unwrap(),
    )
    .unwrap();
    fs::write(records.join("esl_flagged.json"), "[\"Example.esp\"]").unwrap();
    f.request.eslifier_data_paths = vec![records.clone()];
    (f, source, output, records)
}

#[test]
fn recorded_conversion_is_current_only_when_owned_source_and_winning_output_hashes_match() {
    let (f, source, output, _) = provenance_fixture();
    let engine = PluginAnalyzer::new();
    let current = engine.audit(&f.request).unwrap();
    assert_eq!(
        current.mods["Source"].plugins[0].provenance.state,
        "current"
    );
    let original = fs::read(&source).unwrap();
    let out_original = fs::read(&output).unwrap();
    fs::write(&source, plugin(0, 1.7, &[], &[("MISC", 0x801)])).unwrap();
    let stale = engine.audit(&f.request).unwrap();
    assert_eq!(
        stale.mods["Source"].plugins[0].provenance.state,
        "source-changed"
    );
    assert_eq!(stale.mods["Source"].status, "conversion-stale");
    fs::write(&source, &original).unwrap();
    fs::write(&output, plugin(0x200, 1.7, &[], &[("MISC", 0x801)])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .provenance
            .state,
        "output-changed"
    );
    fs::write(&source, plugin(0, 1.7, &[], &[("MISC", 0x802)])).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .provenance
            .state,
        "source-and-output-changed"
    );
    fs::write(&source, &original).unwrap();
    fs::write(&output, &out_original).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .provenance
            .state,
        "current"
    );
}

#[test]
fn mutable_tool_flags_wrong_profile_and_missing_hashes_never_create_verified_conversion() {
    let (mut f, _, output, records) = provenance_fixture();
    let engine = PluginAnalyzer::new();
    let settings_path = records.join("settings.json");
    let settings_bytes = fs::read(&settings_path).unwrap();
    let mut settings: serde_json::Value = serde_json::from_slice(&settings_bytes).unwrap();
    for mode in [0, 1] {
        settings["mod_manager_mode"] = json!(mode);
        fs::write(&settings_path, serde_json::to_vec(&settings).unwrap()).unwrap();
        let rejected = engine.audit(&f.request).unwrap();
        assert_eq!(
            rejected.mods["Source"].plugins[0].provenance.state,
            "unknown"
        );
        assert!(
            rejected
                .warnings
                .iter()
                .any(|w| w.contains("not for Mod Organizer 2"))
        );
    }
    settings.as_object_mut().unwrap().remove("mod_manager_mode");
    settings["mode"] = json!(2);
    fs::write(&settings_path, serde_json::to_vec(&settings).unwrap()).unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .provenance
            .state,
        "unknown"
    );
    fs::write(&settings_path, settings_bytes).unwrap();
    fs::write(
        records.join("new_file_hashes.json"),
        serde_json::to_vec(&json!({"example.esp":[hash(&output),true]})).unwrap(),
    )
    .unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .provenance
            .state,
        "output-changed"
    );
    f.request.profile_name = Some("Different".into());
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .provenance
            .state,
        "unknown"
    );
    f.request.profile_name = Some("Test".into());
    f.request.deep = false;
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .provenance
            .state,
        "unknown"
    );
    f.request.deep = true;
    fs::write(
        records.join("original_files.json"),
        serde_json::to_vec(
            &json!({"example.esp":["/outside-owned-roots/Example.esp","a".repeat(64)]}),
        )
        .unwrap(),
    )
    .unwrap();
    assert_eq!(
        engine.audit(&f.request).unwrap().mods["Source"].plugins[0]
            .provenance
            .state,
        "unknown"
    );
}

#[test]
fn importer_integrates_custom_overwrite_priority_without_changing_mo2_files() {
    let root = tempfile::tempdir().unwrap();
    let profile = root.path().join("profiles/Test");
    let mods = root.path().join("mods");
    let overwrite = root.path().join("custom-overwrite");
    fs::create_dir_all(&profile).unwrap();
    fs::create_dir_all(mods.join("First")).unwrap();
    fs::create_dir_all(mods.join("Second")).unwrap();
    fs::create_dir(&overwrite).unwrap();
    fs::write(
        profile.join("modlist.txt"),
        "+Second\n+First\n*DLC: Foreign\n",
    )
    .unwrap();
    fs::write(profile.join("plugins.txt"), "*Example.esp\n").unwrap();
    fs::write(root.path().join("ModOrganizer.ini"),"[General]\ngameName=Skyrim Special Edition\ngameVersion=1.6.1170\n[Settings]\noverwrite_directory=%BASE_DIR%/custom-overwrite\n").unwrap();
    fs::write(
        mods.join("First/Example.esp"),
        plugin(0, 1.7, &[], &[("MISC", 0x800)]),
    )
    .unwrap();
    fs::write(
        mods.join("Second/Example.esp"),
        plugin(0x200, 1.7, &[], &[("MISC", 0x800)]),
    )
    .unwrap();
    fs::write(
        overwrite.join("Example.esp"),
        plugin(0, 1.7, &[], &[("MISC", 0x800)]),
    )
    .unwrap();
    let request = ImportRequest {
        modlist_path: profile.join("modlist.txt"),
        ini_path: None,
        mods_path: None,
        workers: Some(2),
    };
    let before = fs::read(profile.join("modlist.txt")).unwrap();
    let engine = ProfileImporter::new();
    let fast = engine.import(&request).unwrap();
    assert_eq!(fast.catalog["pluginAnalysis"]["depth"], "header");
    assert_eq!(
        fast.catalog["pluginAnalysis"]["plugins"][0]["ownerModId"],
        "__overwrite__"
    );
    let deep = engine.import_with_analysis(&request, true, &[]).unwrap();
    assert_eq!(
        deep.catalog["items"][1]["localAnalysis"]["plugins"][0]["eligibility"]["status"],
        "flag-only-candidate"
    );
    assert_eq!(deep.catalog["pluginAnalysis"]["depth"], "records");
    assert_eq!(
        deep.catalog["items"][0]["localAnalysis"]["inventoryComplete"],
        false
    );
    assert_eq!(
        deep.catalog["items"][0]["localAnalysis"]["status"],
        "unknown"
    );
    assert_eq!(
        deep.catalog["items"][0]["localAnalysis"]["depth"],
        "records"
    );
    assert!(deep.catalog["items"][0]["localAnalysis"]["checkedAt"].is_string());
    assert!(fast.catalog["items"][0]["localAnalysis"]["checkedAt"].is_null());
    assert_eq!(fs::read(profile.join("modlist.txt")).unwrap(), before);
}

#[test]
fn cli_accepts_bounded_json_stdin_with_optional_fields_and_returns_contract() {
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    fs::write(root.join("Example.esl"), plugin(0, 1.7, &[], &[])).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args(["audit-plugins", "--request", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(&serde_json::to_vec(&f.request).unwrap())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["schemaVersion"], 1);
    assert_eq!(
        value["mods"]["Source"]["plugins"][0]["eligibility"]["status"],
        "already-light"
    );
    assert!(value["checkedAt"].as_str().unwrap().ends_with('Z'));
}

#[cfg(unix)]
#[test]
fn symlink_plugins_and_source_folders_are_rejected_without_following_them() {
    use std::os::unix::fs::symlink;
    let mut f = Fixture::new();
    let root = f.add("Source", 0, true);
    let external = f.root.path().join("external.esp");
    fs::write(&external, plugin(0x200, 1.7, &[], &[])).unwrap();
    symlink(&external, root.join("Example.esp")).unwrap();
    let engine = PluginAnalyzer::new();
    let rejected = engine.audit(&f.request).unwrap();
    assert_eq!(
        rejected.mods["Source"].plugins[0].eligibility.status,
        "unknown"
    );
    assert!(rejected.mods["Source"].plugins[0].header.is_none());
    let linked = f.root.path().join("linked");
    symlink(&root, &linked).unwrap();
    f.request.mods[0].path = linked;
    let rejected = engine.audit(&f.request).unwrap();
    assert!(!rejected.inventory_complete);
    assert!(rejected.mods["Source"].plugins.is_empty());
}
