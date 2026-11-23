use serde_json::Value;
use std::io::Write;
use std::process::{Command, Output, Stdio};

fn call(args: &[&str], bytes: &[u8]) -> Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("start native CLI");
    // An invalid operation can refuse before consuming any stdin.
    let write = child.stdin.take().unwrap().write_all(bytes);
    let output = child.wait_with_output().unwrap();
    if output.status.success() {
        write.unwrap();
    }
    output
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
fn preserves_physical_and_authored_order_and_identifies_only_primary_forms() {
    let output = call(&["spid-preview", "--name", "synthetic_DISTR.ini"],
        b"\xef\xbb\xbf; comment\r\n\r\nSpell = 0x1a2~Demo Patch.ESL||SyntheticFilter|NONE|NONE|1|15\r\nSpell = SampleSpell\r\nSpell = SampleSpell\r\n");
    let json = report(&output);
    assert_eq!(json["sourceName"], "synthetic_DISTR.ini");
    assert_eq!(json["runtimeStatus"], "not_evaluated");
    assert_eq!(
        json["rows"][0],
        serde_json::json!({
            "status": "declaration", "line": 3, "formType": "Spell",
            "target": {"kind":"plugin_form", "plugin":"Demo Patch.ESL", "localFormHex":"1A2"},
            "unevaluatedSlots": 6
        })
    );
    assert_eq!(json["rows"][1]["target"]["editorId"], "SampleSpell");
    assert_eq!(json["rows"][2]["line"], 5);
    assert_eq!(json["rows"][2]["target"], json["rows"][1]["target"]);
    let transcript = String::from_utf8_lossy(&output.stdout);
    assert!(!transcript.contains("SyntheticFilter"));
}

#[test]
fn never_guesses_unknown_syntax_or_legacy_or_incidental_tokens() {
    let output = call(&["spid-preview", "--name", "not-an-alias_DISTR.INI"],
        b"FinalOutfit = SyntheticSecret\nSpell = 0x0100001a~Demo.esp\nPerk = 0x1~../Invalid.esp\nItem = 00001\nSpell = SampleSpell|1|2|3|4|5|6|7\n[Example]\nSpell = SectionScoped\n");
    let json = report(&output);
    let rows = json["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 7);
    assert!(
        rows.iter()
            .all(|r| r["status"] == "unreviewed" && r.get("target").is_none())
    );
    assert_eq!(rows[0]["reason"], "unsupported_directive");
    assert_eq!(rows[4]["reason"], "unsupported_slot_shape");
    assert_eq!(rows[6]["reason"], "unsupported_section");
    assert!(!String::from_utf8_lossy(&output.stdout).contains("SyntheticSecret"));
}

#[test]
fn invalid_scope_input_or_bounds_emit_no_partial_report() {
    for (args, input) in [
        (
            vec!["spid-preview", "--name", "one.ini"],
            b"Spell = Synthetic".to_vec(),
        ),
        (vec!["spid-preview", "--name", "../one_DISTR.ini"], vec![]),
        (
            vec!["spid-preview", "--name", "one_DISTR.ini", "other_DISTR.ini"],
            vec![],
        ),
        (vec!["spid-preview", "--name", "one_DISTR.ini"], vec![0xff]),
        (
            vec!["spid-preview", "--name", "one_DISTR.ini"],
            vec![b' '; 512 * 1024 + 1],
        ),
        (
            vec!["spid-preview", "--name", "one_DISTR.ini"],
            vec![b' '; 8193],
        ),
        (
            vec!["spid-preview", "--name", "one_DISTR.ini"],
            b"Spell = Synthetic\n".repeat(4097),
        ),
    ] {
        let output = call(&args, &input);
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
    }
}

#[test]
fn empty_and_only_comments_do_not_assert_a_clean_load_order() {
    let json = report(&call(
        &["spid-preview", "--name", "comments_DISTR.ini"],
        b"; no declarations\n",
    ));
    assert_eq!(json["runtimeStatus"], "not_evaluated");
    assert_eq!(json["rows"], serde_json::json!([]));
}
