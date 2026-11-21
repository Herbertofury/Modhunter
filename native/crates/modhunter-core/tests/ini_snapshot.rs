use serde_json::Value;
use std::{fs, path::Path, process::Command};

fn compare(before: &Path, after: &Path) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .arg("ini-compare")
        .arg("--before")
        .arg(before)
        .arg("--after")
        .arg(after)
        .output()
        .unwrap()
}

#[test]
fn real_cli_reports_selected_copies_only_without_disclosing_values_or_paths() {
    let d = tempfile::tempdir().unwrap();
    let before = d
        .path()
        .join("Private Windows settings before snapshot.ini");
    let after = d
        .path()
        .join("Private Windows settings changed snapshot.ini");
    let outside = d.path().join("Do not follow this setting value.ini");
    fs::write(&outside, "CANARY SHOULD NOT CHANGE").unwrap();
    let input = [
        format!("[General]\ngameName=Private Game\n[Settings]\nbase_directory={}\ncustomPrivateName=SensitiveBefore\n", outside.display()),
        "[general]\ngameName=Private Game\ngameVersion=privateRuntime\n[Settings]\nbase_directory=Z:\\do-not-log\notherPrivateName=SensitiveAfter\n".into()
    ];
    fs::write(&before, &input[0]).unwrap();
    fs::write(&after, &input[1]).unwrap();
    let stamps = [&before, &after].map(|p| fs::metadata(p).unwrap().modified().unwrap());
    let result = compare(&before, &after);
    assert!(result.status.success());
    assert!(result.stderr.is_empty());
    let printed = String::from_utf8(result.stdout).unwrap();
    let json: Value = serde_json::from_str(&printed).unwrap();
    assert_eq!(json["schemaVersion"], 1);
    assert_eq!(json["profileIdentity"], "notVerified");
    assert_eq!(json["editorAttribution"], "unknown");
    assert_eq!(json["orderEvidence"], "callerSupplied");
    assert_eq!(json["values"], "notIncluded");
    assert_eq!(json["changes"]["added"], 2);
    assert_eq!(json["changes"]["removed"], 1);
    assert_eq!(json["changes"]["changed"], 1);
    assert_eq!(json["unlabeledChanges"]["added"], 1);
    assert_eq!(json["unlabeledChanges"]["removed"], 1);
    assert_eq!(json["labeledChanges"][0]["setting"], "gameRuntimeVersion");
    assert_eq!(
        json["labeledChanges"][1]["setting"],
        "instanceBaseDirectory"
    );
    for redacted in [
        "SensitiveBefore",
        "SensitiveAfter",
        "customPrivateName",
        "otherPrivateName",
        "do-not-log",
        "privateRuntime",
        "Private Game",
        "Private Windows",
        outside.to_str().unwrap(),
    ] {
        assert!(!printed.contains(redacted));
    }
    assert_eq!(
        fs::read_to_string(&outside).unwrap(),
        "CANARY SHOULD NOT CHANGE"
    );
    for (i, path) in [&before, &after].into_iter().enumerate() {
        assert_eq!(fs::read_to_string(path).unwrap(), input[i]);
        assert_eq!(fs::metadata(path).unwrap().modified().unwrap(), stamps[i]);
    }
    assert_eq!(fs::read_dir(d.path()).unwrap().count(), 3);
}

#[test]
fn refusals_are_complete_and_do_not_print_private_paths_or_raw_keys() {
    let d = tempfile::tempdir().unwrap();
    let before = d.path().join("before.ini");
    let after = d.path().join("CONFIDENTIAL_CHOSEN_FILE.ini");
    fs::write(&before, "[Settings]\ncustom=test").unwrap();
    let assert_refusal = |output: std::process::Output, expected: &str| {
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        let error = String::from_utf8(output.stderr).unwrap();
        assert!(error.contains(expected), "{error}");
        assert!(!error.contains(d.path().to_str().unwrap()));
        assert!(!error.contains("CONFIDENTIAL_CHOSEN_FILE"));
        assert!(!error.contains("CUSTOM_PRIVATE_KEY"));
    };
    assert_refusal(compare(&before, &after), "After snapshot");
    fs::write(
        &after,
        "[Settings]\nCUSTOM_PRIVATE_KEY=1\ncustom_private_key=2",
    )
    .unwrap();
    assert_refusal(compare(&before, &after), "Duplicate INI key");
    fs::write(&after, vec![b'#'; 256_001]).unwrap();
    assert_refusal(compare(&before, &after), "256000-byte");
    fs::write(&after, [0xff, 0xfe, b'X']).unwrap();
    assert_refusal(compare(&before, &after), "not a supported INI encoding");
    #[cfg(unix)]
    {
        let symlink = d.path().join("linked.ini");
        std::os::unix::fs::symlink(&before, &symlink).unwrap();
        assert_refusal(compare(&before, &symlink), "Linked or non-regular");
    }
}

#[test]
fn argument_order_is_explicit_and_ambiguous_duplicate_flags_fail() {
    let unused = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args([
            "ini-compare",
            "--before",
            "untouched.ini",
            "--before",
            "second.ini",
        ])
        .output()
        .unwrap();
    assert!(!unused.status.success());
    assert!(unused.stdout.is_empty());
    assert!(
        String::from_utf8_lossy(&unused.stderr).contains("requires --before PATH --after PATH")
    );
}

#[cfg(unix)]
#[test]
fn non_unicode_argument_fails_without_panic_or_path_disclosure() {
    use std::{ffi::OsString, os::unix::ffi::OsStringExt};
    let mut raw = b"PRIVATE_BAD_FILE_NAME_".to_vec();
    raw.push(0xff);
    let output = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args(["ini-compare", "--before"])
        .arg(OsString::from_vec(raw))
        .args(["--after", "unused.ini"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    let error = String::from_utf8(output.stderr).unwrap();
    assert_eq!(error, "Command arguments must be valid Unicode\n");
}
