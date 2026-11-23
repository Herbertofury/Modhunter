// Call the pure emitter directly for its framing and metadata tests.
#[path = "../src/plugin_template/skyrim_se.rs"]
mod skyrim_se;

use modhunter_core::{PluginAuditRequest, PluginMod, audit_plugins};
use sha2::{Digest, Sha256};
use skyrim_se::empty_plugin;
use std::process::{Command, Output, Stdio};

#[test]
fn empty_template_has_exact_expected_tes4_framing_only() {
    let bytes = empty_plugin("", None).unwrap();
    // Independent complete frame: TES4 length 35, zero flags and FormID,
    // form version 44, HEDR 1.70/count 0/next 0x800, CNAM and INCC only.
    let expected = [
        b"TES4\x23\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\x2c\0\0\0".as_slice(),
        b"HEDR\x0c\0\x9a\x99\xd9\x3f\0\0\0\0\0\x08\0\0",
        b"CNAM\x01\0\0INCC\x04\0\0\0\0\0",
    ]
    .concat();
    assert_eq!(bytes, expected);
}

#[test]
fn selected_text_is_exact_and_length_counts_the_c_string_terminator() {
    let bytes = empty_plugin("Maker", Some("First draft")).unwrap();
    assert_eq!(
        &bytes[42..],
        b"CNAM\x06\0Maker\0SNAM\x0c\0First draft\0INCC\x04\0\0\0\0\0",
    );
    assert_eq!(&bytes[4..8], &58_u32.to_le_bytes());
    assert_eq!(bytes, empty_plugin("Maker", Some("First draft")).unwrap());
    assert_ne!(empty_plugin("", None), empty_plugin("", Some("")));
}

#[test]
fn accepts_511_ascii_bytes_each_in_printable_range() {
    let text = " !~".repeat(170) + "~";
    assert_eq!(text.len(), 511);
    let bytes = empty_plugin(&text, Some(&text)).unwrap();
    assert_eq!(bytes.len(), 1088);
    for (offset, signature) in [(42, b"CNAM"), (560, b"SNAM")] {
        assert_eq!(&bytes[offset..offset + 4], signature);
        assert_eq!(&bytes[offset + 4..offset + 6], &[0, 2]);
        assert_eq!(&bytes[offset + 6..offset + 517], text.as_bytes());
        assert_eq!(bytes[offset + 517], 0);
    }
}

#[test]
fn rejects_overlength_and_nonportable_text_before_output() {
    for bad in [
        "x".repeat(512),
        "\0".into(),
        "line\nbreak".into(),
        "\r\t".into(),
        "\u{7f}".into(),
        "résumé".into(),
    ] {
        assert_eq!(
            empty_plugin(&bad, None).unwrap_err(),
            "Author must contain at most 511 printable ASCII bytes",
        );
        assert_eq!(
            empty_plugin("", Some(&bad)).unwrap_err(),
            "Description must contain at most 511 printable ASCII bytes",
        );
    }
}

fn cli(cwd: &std::path::Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args(args)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .output()
        .unwrap()
}

#[test]
fn bad_cli_requests_are_rejected_with_empty_binary_stdout() {
    let root = tempfile::tempdir().unwrap();
    for malformed in [
        vec!["plugin-template", "--out", "unexpected.esp"],
        vec!["plugin-template", "unexpected.esp"],
        vec!["plugin-template", "--author"],
        vec!["plugin-template", "--author", "--description"],
        vec!["plugin-template", "--author", "A", "--author", "B"],
        vec![
            "plugin-template",
            "--description",
            "A",
            "--description",
            "B",
        ],
        vec!["plugin-template", "--description", "secret\nraw"],
        vec!["plugin-template", "--author", "é"],
    ] {
        let result = cli(root.path(), &malformed);
        assert!(!result.status.success(), "{malformed:?}");
        assert!(result.stdout.is_empty(), "{malformed:?}");
        assert!(!result.stderr.is_empty());
        assert!(!String::from_utf8_lossy(&result.stderr).contains("secret"));
        assert!(std::fs::read_dir(root.path()).unwrap().next().is_none());
    }
    let overflow = "x".repeat(512);
    let result = cli(
        root.path(),
        &["plugin-template", "--description", &overflow],
    );
    assert!(!result.status.success());
    assert!(result.stdout.is_empty());
    assert!(std::fs::read_dir(root.path()).unwrap().next().is_none());
}

#[test]
fn real_child_stdout_round_trips_through_native_modhunter_deep_plugin_reader() {
    let root = tempfile::tempdir().unwrap();
    let esp = root.path().join("Blank Template.esp");
    let output = cli(
        root.path(),
        &[
            "plugin-template",
            "--description",
            "No records or masters",
            "--author",
            "Fixture",
        ],
    );
    assert!(output.status.success(), "{:?}", output.stderr);
    assert!(output.stderr.is_empty());
    let bytes = output.stdout;
    assert_eq!(
        bytes,
        empty_plugin("Fixture", Some("No records or masters")).unwrap()
    );
    let default = cli(root.path(), &["plugin-template"]);
    assert!(default.status.success());
    assert_eq!(default.stdout, empty_plugin("", None).unwrap());
    assert!(std::fs::read_dir(root.path()).unwrap().next().is_none());
    // Only this test, not the CLI child, writes to a disposable fixture.
    std::fs::write(&esp, &bytes).unwrap();
    let result = audit_plugins(&PluginAuditRequest {
        game: "skyrimspecialedition".into(),
        deep: true,
        workers: Some(1),
        mods: vec![PluginMod {
            id: "template".into(),
            name: "Template Test".into(),
            path: root.path().into(),
            enabled: true,
            priority: 0,
            installed_version: None,
        }],
        ..Default::default()
    })
    .unwrap();
    assert!(result.inventory_complete);
    assert_eq!(result.stats.deep_scanned, 1);
    let [plugin] = result.mods["template"].plugins.as_slice() else {
        panic!("reader did not produce exactly one plugin");
    };
    assert!(plugin.errors.is_empty(), "{:?}", plugin.errors);
    let header = plugin.header.as_ref().unwrap();
    assert_eq!(header.flags, 0);
    assert_eq!(header.version, 1.70);
    assert_eq!(header.record_header_version, 44);
    assert_eq!(header.declared_record_and_group_count, 0);
    assert!(header.masters.is_empty());
    assert!(!header.light_effective && !header.master_flag && !header.medium_flag);
    assert!(!header.update_flag);
    assert!(plugin.master_issues.is_empty());
    let records = plugin.records.as_ref().unwrap();
    assert!(records.scan_complete);
    assert_eq!(records.total_records, 0);
    assert_eq!(records.groups, 0);
    assert_eq!(records.new_records, 0);
    assert_eq!(records.override_or_injected_records, 0);
    let expected = format!("{:x}", Sha256::digest(&bytes));
    assert_eq!(
        plugin.fingerprint.as_ref().unwrap().sha256.as_deref(),
        Some(expected.as_str())
    );
    assert_eq!(std::fs::read(esp).unwrap(), bytes);
}
