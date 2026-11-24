use serde_json::{Value, json};
use std::io::Write;
use std::path::Path;
use std::process::{Command, Output, Stdio};

fn call(args: &[&str], bytes: &[u8], cwd: Option<&Path>) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"));
    command
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(root) = cwd {
        command
            .current_dir(root)
            .env("HOME", root)
            .env("USERPROFILE", root)
            .env("APPDATA", root)
            .env("LOCALAPPDATA", root)
            .env("XDG_DATA_HOME", root);
    }
    let mut child = command.spawn().expect("start native CLI");
    let write = child.stdin.take().unwrap().write_all(bytes);
    let output = child.wait_with_output().unwrap();
    if output.status.success() {
        write.unwrap();
    }
    output
}

fn preview(bytes: &[u8]) -> Output {
    call(&["kid-preview", "--name", "My Rules_KID.INI"], bytes, None)
}

fn report(output: &Output) -> Value {
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    serde_json::from_slice(&output.stdout).unwrap()
}

#[test]
fn reports_source_physical_lines_and_distinct_authored_id_shapes_in_order() {
    let out = preview(b"\xef\xbb\xbf; authored comment\r\n\r\nKeyword = WorkshopDoor|Talking Activator\r\nKeyword = 0x1a2~Test Keywords.ESL|Armor|PrivateFilter,0xFA~Fixture.esm|NONE|75.5\r\nKeyword = 0x02001234|Magic Effect|NONE\r\nKeyword = WorkshopDoor|Talking Activator");
    let j = report(&out);
    assert_eq!(j["sourceName"], "My Rules_KID.INI");
    assert_eq!(j["runtimeStatus"], "not_evaluated");
    let rows = j["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 4);
    assert_eq!(
        rows[0],
        json!({"status":"declaration","line":3,"itemType":"Talking Activator","keyword":{"kind":"editor_id","editorId":"WorkshopDoor"},"unevaluatedSlots":0})
    );
    assert_eq!(
        rows[1],
        json!({"status":"declaration","line":4,"itemType":"Armor","keyword":{"kind":"plugin_form","plugin":"Test Keywords.ESL","localFormHex":"1A2"},"unevaluatedSlots":3})
    );
    assert_eq!(
        rows[2],
        json!({"status":"declaration","line":5,"itemType":"Magic Effect","keyword":{"kind":"unqualified_form","formHex":"02001234"},"unevaluatedSlots":1})
    );
    assert_eq!(rows[3]["line"], 6);
    assert_eq!(rows[3]["keyword"], rows[0]["keyword"]);
    let transcript = String::from_utf8_lossy(&out.stdout);
    assert!(!transcript.contains("PrivateFilter"));
    assert!(!transcript.contains("Fixture.esm"));
    assert!(!transcript.contains("75.5"));
}

#[test]
fn unreviewed_syntax_never_yields_a_keyword_guess() {
    let input = b"ExclusiveGroup = NotShown|Armor\nKeyword = SecretNoType\nKeyword = SecretWrongType|armor\nKeyword = 0x1000001~Fixture.esp|Armor\nKeyword = 0x1~../Fixture.esp|Armor\nKeyword = ABCDE|Armor\nKeyword = SecretExtra|Armor|NONE|NONE|50|1\nKeyword = SecretEmpty|Armor||NONE|50\nKeyword = SecretCompound|Armor|One+Two|NONE|100\nKeyword = SecretTrait|Armor|NONE|UnsupportedTrait|99\nKeyword = SecretChance|Armor|NONE|NONE|101\nKeyword = SecretExponent|Armor|NONE|NONE|1e2\nKeyword = Broken\n[Group]\nKeyword = SecretSection|Armor\n";
    let out = preview(input);
    let j = report(&out);
    let rows = j["rows"].as_array().unwrap();
    let reasons = [
        "unsupported_directive",
        "unsupported_slot_shape",
        "unsupported_item_type",
        "unsupported_keyword_identifier",
        "unsupported_keyword_identifier",
        "unsupported_keyword_identifier",
        "unsupported_slot_shape",
        "unsupported_slot_shape",
        "unsupported_optional_syntax",
        "unsupported_optional_syntax",
        "unsupported_optional_syntax",
        "unsupported_optional_syntax",
        "unsupported_slot_shape",
        "unsupported_section",
        "unsupported_section",
    ];
    assert_eq!(rows.len(), reasons.len());
    for (i, (row, reason)) in rows.iter().zip(reasons).enumerate() {
        assert_eq!(
            *row,
            json!({"status":"unreviewed","line":i+1,"reason":reason})
        );
    }
    assert!(!String::from_utf8_lossy(&out.stdout).contains("Secret"));
}

#[test]
fn accepts_named_defaults_and_bounded_decimal_edges_without_evaluating_them() {
    let out = preview(b"Keyword = FixtureKeyword|Ammo|none|NONE|0\nKeyword = FixtureKeyword|Spell|NONE|NONE|100.0\nKeyword = FixtureKeyword|Soul Gem|NONE|NONE|NoNe\n");
    let j = report(&out);
    let rows = j["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 3);
    assert!(
        rows.iter()
            .all(|x| x["status"] == "declaration" && x["unevaluatedSlots"] == 3)
    );
}

#[test]
fn command_and_input_bounds_refuse_atomically() {
    let valid = "one_KID.ini";
    for (args, input) in [
        (
            vec!["kid-preview", "--file", valid],
            b"Keyword = Fixture|Armor".to_vec(),
        ),
        (vec!["kid-preview", "--name", "one_DISTR.ini"], vec![]),
        (vec!["kid-preview", "--name", "../one_KID.ini"], vec![]),
        (vec!["kid-preview", "--name", "C:\\one_KID.ini"], vec![]),
        (vec!["kid-preview", "--name", valid, "two_KID.ini"], vec![]),
        (vec!["kid-preview", "--name", valid], vec![0xff]),
        (
            vec!["kid-preview", "--name", valid],
            b"Keyword = Fixture|Ammo\n\0".to_vec(),
        ),
        (
            vec!["kid-preview", "--name", valid],
            b"Keyword = Fixture|Ammo\rKeyword = Second|Ammo".to_vec(),
        ),
        (
            vec!["kid-preview", "--name", valid],
            vec![b' '; 512 * 1024 + 1],
        ),
        (vec!["kid-preview", "--name", valid], vec![b' '; 8193]),
        (
            vec!["kid-preview", "--name", valid],
            b"Keyword = Fixture|Ammo\n".repeat(4097),
        ),
    ] {
        let output = call(&args, &input, None);
        assert!(!output.status.success(), "{args:?}");
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
    }
}

#[test]
fn basename_is_a_label_not_a_file_read_or_write_and_empty_is_not_clear() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    std::fs::create_dir(root.join("Data")).unwrap();
    std::fs::create_dir(root.join("profiles")).unwrap();
    std::fs::write(root.join("named_KID.ini"), "Keyword = DiskOnly|Armor").unwrap();
    std::fs::write(root.join("profiles/marker.txt"), b"keep bytes").unwrap();
    let result = call(
        &["kid-preview", "--name", "named_KID.ini"],
        b"; supplied bytes only\n",
        Some(root),
    );
    let j = report(&result);
    assert_eq!(j["sourceName"], "named_KID.ini");
    assert_eq!(j["runtimeStatus"], "not_evaluated");
    assert_eq!(j["rows"], json!([]));
    assert_eq!(
        std::fs::read(root.join("named_KID.ini")).unwrap(),
        b"Keyword = DiskOnly|Armor"
    );
    assert_eq!(
        std::fs::read(root.join("profiles/marker.txt")).unwrap(),
        b"keep bytes"
    );
    assert_eq!(std::fs::read_dir(root).unwrap().count(), 3);
    assert_eq!(std::fs::read_dir(root.join("profiles")).unwrap().count(), 1);
    assert_eq!(std::fs::read_dir(root.join("Data")).unwrap().count(), 0);
}
