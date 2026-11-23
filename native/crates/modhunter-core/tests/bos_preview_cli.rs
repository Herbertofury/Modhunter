use std::io::Write;
use std::process::{Command, Output, Stdio};

fn call(args: &[&str], input: &[u8]) -> Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(input).unwrap();
    child.wait_with_output().unwrap()
}

fn preview(bytes: &[u8]) -> serde_json::Value {
    let result = call(&["bos-preview", "--name", "Owned Objects_SWAP.Ini"], bytes);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    serde_json::from_slice(&result.stdout).unwrap()
}

#[test]
fn physical_order_and_repeated_selectors_distinguish_forms_references_and_aliases() {
    let report = preview(
        b"\xef\xbb\xbf; owned file\r\n[Forms]\r\n0xA~Owned.esp | InkCabinet\r\n\
          0X000A~owned.ESP|0x42~Fixture.esl\r\nInkLamp|InkBench\r\n\
          [References]\r\n0xA~Owned.esp|InkArmoire\r\n\
          [Forms]\r\nInkLamp|InkTable\r\ninkLamp|InkChandelier\r\n0xA|InkClock\r\n",
    );
    assert_eq!(report["sourceName"], "Owned Objects_SWAP.Ini");
    assert_eq!(report["runtimeStatus"], "not_evaluated");
    assert_eq!(report["declaredPairs"], 7);
    assert_eq!(report["unreviewedRows"], 0);
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(
        rows.iter()
            .map(|r| r["line"].as_u64().unwrap())
            .collect::<Vec<_>>(),
        [3, 4, 5, 7, 9, 10, 11]
    );
    assert_eq!(rows[0]["section"], "forms");
    assert_eq!(rows[1]["replacement"]["plugin"], "Fixture.esl");
    assert_eq!(rows[3]["section"], "references");
    assert_eq!(rows[6]["base"]["kind"], "form_id");
    assert!(rows[6]["base"]["plugin"].is_null());
    let repeats = report["repeatedSelectors"].as_array().unwrap();
    assert_eq!(repeats.len(), 2);
    assert_eq!(repeats[0]["selector"]["value"], "0xA");
    assert_eq!(repeats[0]["selector"]["plugin"], "Owned.esp");
    assert_eq!(repeats[0]["lines"], serde_json::json!([3, 4]));
    assert_eq!(repeats[1]["selector"]["value"], "InkLamp");
    assert_eq!(repeats[1]["lines"], serde_json::json!([5, 9]));
}

#[test]
fn labels_unreviewed_syntax_and_keeps_only_safe_typed_declarations() {
    let report = preview(
        b"[Forms|PrivateRegion]\nInkLamp|InkTable\n[Properties]\nInkLamp|secret-raw-property\n\
          [Forms]\nInkLamp,InkFire|InkBed,InkDoor\nDEADBEEF|InkShelf\n\
          0x12~folder/secret.esp|InkFloor\nInkFrame|InkMirror|private-option|private-chance\n\
          InkFrame|InkMirror=key-value\n[Forms\nInkFrame|InkMirror\n[forms]\nInkLamp|InkChair\n\
          [References]\nInkPost\nInkPost|InkDesk|a|b|three\n",
    );
    assert_eq!(report["runtimeStatus"], "not_evaluated");
    assert_eq!(report["declaredPairs"], 1);
    assert_eq!(report["unreviewedRows"], 10);
    assert!(report["repeatedSelectors"].as_array().unwrap().is_empty());
    let rows = report["rows"].as_array().unwrap();
    assert_eq!(rows[0]["section"], "conditional");
    assert_eq!(rows[1]["section"], "properties");
    assert_eq!(rows[2]["reason"], "selector_set_not_reviewed");
    assert_eq!(rows[3]["reason"], "unsupported_selector");
    assert_eq!(rows[4]["reason"], "unsupported_selector");
    assert_eq!(rows[5]["state"], "declared_pair");
    assert_eq!(rows[5]["hasOptionalFields"], true);
    assert_eq!(rows[6]["reason"], "ambiguous_entry");
    assert_eq!(rows[7]["section"], "unreviewed");
    assert_eq!(rows[8]["section"], "unreviewed");
    assert_eq!(rows[9]["reason"], "no_paired_selectors");
    assert_eq!(rows[10]["reason"], "extra_fields_not_reviewed");
    let output = report.to_string();
    for omitted in [
        "PrivateRegion",
        "secret",
        "private-option",
        "private-chance",
        "key-value",
    ] {
        assert!(!output.contains(omitted), "{omitted}");
    }
}

#[test]
fn rejects_unsupported_scope_utf8_and_limits_without_partial_stdout() {
    for args in [
        vec!["bos-preview", "--file", "Inputs_SWAP.ini"],
        vec!["bos-preview", "--name", "folder/Inputs_SWAP.ini"],
        vec!["bos-preview", "--name", r"C:\Inputs_SWAP.ini"],
        vec!["bos-preview", "--name", "Inputs_DISTR.ini"],
        vec!["bos-preview", "--name", "_SWAP.ini"],
        vec!["bos-preview", "--name", "OK_SWAP.ini", "extra"],
    ] {
        let result = call(&args, b"");
        assert!(
            !result.status.success() && result.stdout.is_empty(),
            "{args:?}"
        );
    }
    for bad in [
        b"[Forms]\nInkTable|InkBed\n\xff".to_vec(),
        vec![b'x'; 512 * 1024 + 1],
        vec![b'\n'; 4097],
        vec![b'x'; 2049],
    ] {
        let result = call(&["bos-preview", "--name", "Owned_SWAP.ini"], &bad);
        assert!(!result.status.success() && result.stdout.is_empty());
    }
}

#[test]
fn empty_or_comment_only_does_not_assert_no_runtime_swaps() {
    let report = preview(b"; deliberately blank\n# comment\n\n[Forms]\n");
    assert_eq!(report["runtimeStatus"], "not_evaluated");
    assert_eq!(report["declaredPairs"], 0);
    assert_eq!(report["rows"], serde_json::json!([]));
    assert_eq!(report["repeatedSelectors"], serde_json::json!([]));
}
