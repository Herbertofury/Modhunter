#[path = "../src/bin/bsa_directory/mod.rs"]
#[allow(dead_code)]
mod bsa_directory;
use std::io::{self, Cursor, Read};

// Independent tiny fixture serializer for the documented header, records and names.
// Its data bytes are opaque sentinels, neither game assets nor valid codecs.
fn fixture(revision: u32, flags: u32, groups: &[(&str, &[&str])]) -> (Vec<u8>, usize) {
    let u32le = |out: &mut Vec<u8>, x: usize| out.extend((x as u32).to_le_bytes());
    let files = groups.iter().map(|(_, names)| names.len()).sum::<usize>();
    let file_names = groups
        .iter()
        .flat_map(|(_, names)| *names)
        .map(|name| name.len() + 1)
        .sum::<usize>();
    let folder_names = groups
        .iter()
        .map(|(folder, _)| folder.len() + 1)
        .sum::<usize>();
    let width = if revision == 105 { 24 } else { 16 };
    let mut block_start = 36 + width * groups.len();
    let metadata_bytes = block_start + folder_names + groups.len() + files * 16 + file_names;
    let mut out = b"BSA\0".to_vec();
    for n in [
        revision as usize,
        36,
        flags as usize,
        groups.len(),
        files,
        folder_names,
        file_names,
        0,
    ] {
        u32le(&mut out, n);
    }
    for (folder, names) in groups {
        out.extend([0; 8]); // Hashes are not consumed by the named inventory.
        u32le(&mut out, names.len());
        if revision == 105 {
            out.extend([0; 4]);
            out.extend(((block_start + file_names) as u64).to_le_bytes());
        } else {
            u32le(&mut out, block_start + file_names);
        }
        block_start += folder.len() + 2 + names.len() * 16;
    }
    let mut ordinal = 0;
    for (folder, names) in groups {
        out.push((folder.len() + 1) as u8);
        out.extend(folder.as_bytes());
        out.push(0);
        for _ in *names {
            out.extend([0; 8]);
            u32le(&mut out, 1 | (if ordinal % 2 == 0 { 1 << 30 } else { 0 }));
            u32le(&mut out, metadata_bytes + ordinal);
            ordinal += 1;
        }
    }
    for (_, names) in groups {
        for name in *names {
            out.extend(name.as_bytes());
            out.push(0);
        }
    }
    assert_eq!(out.len(), metadata_bytes);
    out.resize(out.len() + files, 87);
    (out, metadata_bytes)
}

#[test]
fn standard_named_pc_revisions_list_multiple_folders_in_stored_order() {
    for revision in [103, 104, 105] {
        let (bytes, end) = fixture(
            revision,
            0x87,
            &[
                ("meshes\\Example", &["first.nif", "second.nif"]),
                ("textures", &["readme.txt"]),
            ],
        );
        let found = bsa_directory::inventory(Cursor::new(&bytes), bytes.len() as u64).unwrap();
        assert_eq!(found.version, revision);
        assert_eq!(found.metadata_bytes, end);
        assert_eq!(
            found.paths,
            [
                "meshes/Example/first.nif",
                "meshes/Example/second.nif",
                "textures/readme.txt"
            ]
        );
    }
}
#[test]
fn se_file_name_table_accepts_only_zero_alignment_padding() {
    let (mut bytes, end) = fixture(105, 3, &[("meshes", &["one.nif"])]);
    let extra = 8;
    bytes.splice(end..end, [0; 8]);
    bytes[28..32].copy_from_slice(&16u32.to_le_bytes());
    let folder_offset = u64::from_le_bytes(bytes[52..60].try_into().unwrap());
    bytes[52..60].copy_from_slice(&(folder_offset + extra).to_le_bytes());
    let entry_start = 60 + 1 + "meshes".len() + 1;
    bytes[entry_start + 12..entry_start + 16]
        .copy_from_slice(&((end + extra as usize) as u32).to_le_bytes());
    assert_eq!(
        bsa_directory::inventory(&bytes[..], bytes.len() as u64)
            .unwrap()
            .paths,
        ["meshes/one.nif"]
    );
    bytes[end] = 1;
    assert!(bsa_directory::inventory(&bytes[..], bytes.len() as u64).is_err());
}

#[test]
fn intentionally_unavailable_payload_is_never_read() {
    struct MetadataOnly<'a>(Cursor<&'a [u8]>, u64);
    impl Read for MetadataOnly<'_> {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            assert!(
                self.0.position() + buf.len() as u64 <= self.1,
                "Read request exceeds metadata"
            );
            self.0.read(buf)
        }
    }
    let (bytes, end) = fixture(105, 0x107, &[("meshes", &["body.nif"])]);
    let mut source = MetadataOnly(Cursor::new(bytes[..end].as_ref()), end as u64);
    let found = bsa_directory::inventory(&mut source, bytes.len() as u64).unwrap();
    assert_eq!(found.paths, ["meshes/body.nif"]);
    assert_eq!(source.0.position(), end as u64);
}
#[test]
fn known_empty_and_fully_named_root_are_supported() {
    let (empty, end) = fixture(105, 3, &[]);
    assert_eq!(end, 36);
    assert!(
        bsa_directory::inventory(&empty[..], empty.len() as u64)
            .unwrap()
            .paths
            .is_empty()
    );
    let (root, _) = fixture(104, 3, &[("", &["readme.txt"])]);
    assert_eq!(
        bsa_directory::inventory(&root[..], root.len() as u64)
            .unwrap()
            .paths,
        ["readme.txt"]
    );
}

#[test]
fn whole_folder_dot_is_a_root_marker_but_dot_components_are_not_paths() {
    for revision in [103, 104, 105] {
        let (root, _) = fixture(revision, 3, &[(".", &["root.txt"])]);
        assert_eq!(
            bsa_directory::inventory(&root[..], root.len() as u64)
                .unwrap()
                .paths,
            ["root.txt"]
        );
        for unsafe_path in ["./subdir", "subdir/.", "subdir/../other", ".."] {
            let (bad, _) = fixture(revision, 3, &[(unsafe_path, &["root.txt"])]);
            assert!(bsa_directory::inventory(&bad[..], bad.len() as u64).is_err());
        }
        let (duplicate, _) = fixture(revision, 3, &[("", &["root.txt"]), (".", &["ROOT.TXT"])]);
        assert!(bsa_directory::inventory(&duplicate[..], duplicate.len() as u64).is_err());
    }
}

#[test]
fn unsupported_versions_encodings_and_ambiguity_are_not_empty_successes() {
    for (rev, flags, folders) in [
        (106, 3, vec![("meshes", &["body.nif"][..])]),
        (105, 2, vec![("meshes", &["body.nif"][..])]),
        (105, 0x43, vec![("meshes", &["body.nif"][..])]),
        (105, 3, vec![("meshes", &["café.nif"][..])]),
        (105, 3, vec![("meshes", &["body.nif", "Body.nif"][..])]),
        (104, 3, vec![("meshes//example", &["body.nif"][..])]),
        (104, 3, vec![("meshes", &["dir/body.nif"][..])]),
    ] {
        let (bytes, _) = fixture(rev, flags, &folders);
        assert!(bsa_directory::inventory(&bytes[..], bytes.len() as u64).is_err());
    }
    let (mut bytes, _) = fixture(105, 3, &[("meshes", &["body.nif"])]);
    bytes[..4].copy_from_slice(b"BTDX");
    assert!(bsa_directory::inventory(&bytes[..], bytes.len() as u64).is_err());
}
#[test]
fn truncated_directory_and_out_of_bound_file_length_refuse_atomically() {
    let (mut bytes, end) = fixture(105, 3, &[("meshes", &["body.nif", "tail.nif"])]);
    assert!(bsa_directory::inventory(&bytes[..end - 1], bytes.len() as u64).is_err());
    assert!(bsa_directory::inventory(&bytes[..], end as u64).is_err()); // No declared stored payload.
    bytes[20..24].copy_from_slice(&100_001u32.to_le_bytes());
    let mut source = Cursor::new(&bytes);
    assert!(bsa_directory::inventory(&mut source, bytes.len() as u64).is_err());
    assert_eq!(source.position(), 36); // Reject the announced resource bound before asking for directory bytes.
}
#[test]
fn repeated_data_extents_do_not_imply_duplicate_paths() {
    let (mut bytes, end) = fixture(105, 3, &[("meshes", &["one.nif", "two.nif"])]);
    let first_entry = 36 + 24 + 8;
    bytes[first_entry + 16 + 12..first_entry + 16 + 16]
        .copy_from_slice(&(end as u32).to_le_bytes());
    bytes.pop(); // Both distinct names now legitimately reuse the same byte extent.
    assert_eq!(
        bsa_directory::inventory(&bytes[..], bytes.len() as u64)
            .unwrap()
            .paths
            .len(),
        2
    );
}

#[test]
fn repeated_folder_prefixes_cannot_expand_the_index_beyond_its_output_limit() {
    let folder = "m".repeat(240);
    let names: Vec<_> = (0..70_000).map(|i| format!("asset-{i:05}.nif")).collect();
    let refs: Vec<_> = names.iter().map(String::as_str).collect();
    let (data, metadata_end) = fixture(105, 3, &[(folder.as_str(), refs.as_slice())]);
    assert!(metadata_end < 3_000_000);
    let result = bsa_directory::inventory(&data[..], data.len() as u64);
    assert_eq!(result, Err("Expanded BSA index exceeds the output limit"));
}

fn cli(path: &std::path::Path, current_dir: &std::path::Path) -> std::process::Output {
    std::process::Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args(["bsa-directory", "--archive"])
        .arg(path)
        .current_dir(current_dir)
        .output()
        .unwrap()
}

#[test]
fn official_cli_inventories_absolute_and_relative_selection_without_path_leak() {
    use std::io::{Seek, SeekFrom, Write};

    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("selected private fixture.BSA");
    let (mut bytes, end) = fixture(105, 3, &[("meshes", &["sample.nif"])]);
    let offset = 64 * 1024 * 1024u32;
    bytes[36 + 24 + 8 + 12..36 + 24 + 8 + 16].copy_from_slice(&offset.to_le_bytes());
    let mut file = std::fs::File::create(&path).unwrap();
    file.write_all(&bytes[..end]).unwrap();
    file.seek(SeekFrom::Start(u64::from(offset))).unwrap();
    file.write_all(&[1]).unwrap();
    drop(file);

    let before = path.metadata().unwrap();
    assert_eq!(before.len(), u64::from(offset) + 1);
    for selected in [&path, std::path::Path::new(path.file_name().unwrap())] {
        let output = cli(selected, temp.path());
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(output.stderr.is_empty());
        let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(
            report,
            serde_json::json!({
                "scope":"named-bsa-directory-only","version":105,
                "metadataBytesRead":end,"entryCount":1,"paths":["meshes/sample.nif"]
            })
        );
    }
    let after = path.metadata().unwrap();
    assert_eq!(before.len(), after.len());
    assert_eq!(before.modified().unwrap(), after.modified().unwrap());
}

#[test]
fn official_cli_refuses_non_input_and_incomplete_file_without_location_in_outputs() {
    let temp = tempfile::tempdir().unwrap();
    let directory = temp.path().join("private directory.bsa");
    std::fs::create_dir(&directory).unwrap();
    let (data, end) = fixture(105, 3, &[("meshes", &["sample.nif"])]);
    let truncated = temp.path().join("private incomplete.bsa");
    std::fs::write(&truncated, &data[..end]).unwrap();
    let extension = temp.path().join("private other.bin");
    std::fs::write(&extension, &data).unwrap();
    let missing = temp.path().join("private missing.bsa");
    for path in [&directory, &truncated, &extension, &missing] {
        let output = cli(path, temp.path());
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(!String::from_utf8_lossy(&output.stderr).contains("private "));
    }
    assert_eq!(std::fs::read(truncated).unwrap(), &data[..end]);
}

#[cfg(unix)]
#[test]
fn official_cli_refuses_link_leaf_and_nonregular_pipe() {
    let temp = tempfile::tempdir().unwrap();
    let ordinary = temp.path().join("original.bsa");
    let link = temp.path().join("link.bsa");
    let pipe = temp.path().join("pipe.bsa");
    let (data, _) = fixture(103, 3, &[]);
    std::fs::write(&ordinary, &data).unwrap();
    std::os::unix::fs::symlink(&ordinary, &link).unwrap();
    let pipe_path = std::ffi::CString::new(pipe.as_os_str().as_encoded_bytes()).unwrap();
    // Owned, inert filesystem object to test ordinary-file rejection on Unix.
    assert_eq!(unsafe { libc::mkfifo(pipe_path.as_ptr(), 0o600) }, 0);
    for path in [&link, &pipe] {
        let output = cli(path, temp.path());
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(!String::from_utf8_lossy(&output.stderr).contains(temp.path().to_str().unwrap()));
    }
}

fn compare_cli(left: &std::path::Path, right: &std::path::Path) -> std::process::Output {
    std::process::Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args(["bsa-compare", "--left"])
        .arg(left)
        .arg("--right")
        .arg(right)
        .output()
        .unwrap()
}

#[test]
fn compare_collapses_ascii_case_but_retains_both_observed_names_and_counts() {
    let temp = tempfile::tempdir().unwrap();
    let left = temp.path().join("Private left.bsa");
    let right = temp.path().join("Private right.bsa");
    let (lhs, left_meta) = fixture(
        104,
        3,
        &[
            ("Meshes\\Garden", &["Common.NIF", "LeftOnly.nif"]),
            ("Textures/Leaves", &["Leaf.dds"]),
        ],
    );
    let (mut rhs, right_meta) = fixture(
        105,
        3,
        &[
            ("textures\\leaves", &["LEAF.DDS"]),
            (
                "meshes/Garden",
                &["common.nif", "rightOnly.nif", "other.nif"],
            ),
        ],
    );
    // Explicitly different opaque payload bytes aren't compared or read.
    rhs[right_meta..].fill(19);
    std::fs::write(&left, lhs).unwrap();
    std::fs::write(&right, rhs).unwrap();
    let output = compare_cli(&left, &right);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        report,
        serde_json::json!({
            "scope":"named-bsa-directory-overlap-only",
            "left":{"version":104,"metadataBytesRead":left_meta,"entryCount":3},
            "right":{"version":105,"metadataBytesRead":right_meta,"entryCount":4},
            "leftOnlyCount":1, "rightOnlyCount":2, "overlapCount":2,
            "overlaps":[
                {"leftPath":"Meshes/Garden/Common.NIF","rightPath":"meshes/Garden/common.nif"},
                {"leftPath":"Textures/Leaves/Leaf.dds","rightPath":"textures/leaves/LEAF.DDS"}
            ]
        })
    );
}

#[test]
fn compare_empty_is_distinct_from_missing_or_partial_and_does_not_leak_path() {
    let temp = tempfile::tempdir().unwrap();
    let one = temp.path().join("Private source.bsa");
    let empty = temp.path().join("Private empty.bsa");
    let bad = temp.path().join("Private incomplete.bsa");
    let (one_bytes, one_meta) = fixture(105, 3, &[("meshes", &["one.nif"])]);
    let (empty_bytes, _) = fixture(103, 3, &[]);
    std::fs::write(&one, &one_bytes).unwrap();
    std::fs::write(&empty, empty_bytes).unwrap();
    std::fs::write(&bad, &one_bytes[..one_meta]).unwrap();

    let response = compare_cli(&empty, &one);
    assert!(response.status.success());
    let report: serde_json::Value = serde_json::from_slice(&response.stdout).unwrap();
    assert_eq!(report["overlapCount"], 0);
    assert_eq!(report["overlaps"], serde_json::json!([]));
    assert_eq!(report["rightOnlyCount"], 1);
    assert_eq!(report["leftOnlyCount"], 0);
    for (left, right) in [(&bad, &one), (&one, &bad)] {
        let failed = compare_cli(left, right);
        assert!(!failed.status.success());
        assert!(failed.stdout.is_empty());
        assert!(!String::from_utf8_lossy(&failed.stderr).contains("Private "));
    }
}
