use serde_json::{Value, json};
use std::fs;
use std::path::Path;
use std::process::{Command, Output};

fn field(bytes: &[u8]) -> Vec<u8> {
    [(bytes.len() as u32).to_le_bytes().as_slice(), bytes].concat()
}

fn texture_set(paths: &[&[u8]]) -> Vec<u8> {
    let mut body = (paths.len() as u32).to_le_bytes().to_vec();
    for path in paths {
        body.extend(field(path));
    }
    body
}

fn mesh(stream: u32, blocks: &[(u16, &[u8])], root: u32) -> Vec<u8> {
    // SSE header, an intentional unused block type and ignored export/string metadata.
    let mut file = b"Gamebryo File Format, Version 20.2.0.7\n".to_vec();
    file.extend(0x14020007_u32.to_le_bytes());
    file.push(1);
    file.extend(12_u32.to_le_bytes());
    file.extend((blocks.len() as u32).to_le_bytes());
    file.extend(stream.to_le_bytes());
    file.extend([4, b'a', b'b', b'c', 0, 1, 0, 1, 0]);
    file.extend(3_u16.to_le_bytes());
    for name in [b"NiNode".as_slice(), b"BSShaderTextureSet", b"UnusedType"] {
        file.extend(field(name));
    }
    for (ty, _) in blocks {
        file.extend(ty.to_le_bytes());
    }
    for (_, block) in blocks {
        file.extend((block.len() as u32).to_le_bytes());
    }
    file.extend(1_u32.to_le_bytes());
    let note = b"textures/HeaderOnly.dds";
    file.extend((note.len() as u32).to_le_bytes());
    file.extend(field(note));
    file.extend(0_u32.to_le_bytes()); // groups
    for (_, block) in blocks {
        file.extend(*block);
    }
    file.extend(1_u32.to_le_bytes());
    file.extend(root.to_le_bytes());
    file
}

fn invoke(cwd: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .arg("nif-info")
        .args(args)
        .current_dir(cwd)
        .output()
        .unwrap()
}

fn good(out: Output) -> Value {
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.stderr.is_empty());
    serde_json::from_slice(&out.stdout).unwrap()
}

fn refused(out: Output) {
    assert!(!out.status.success());
    assert!(out.stdout.is_empty());
    assert!(!out.stderr.is_empty());
}

#[test]
fn block_headers_and_texture_slots_are_exact_and_not_guessed_from_other_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let dir = temp.path();
    let path = dir.join("Synthetic.NIF");
    let valid = texture_set(&[
        br"Textures\Synthetic\Test_D.dds",
        b"",
        br"C:\DontEchoOpaque\asset.dds",
        br"textures\..\NotCanonical.dds",
    ]);
    let second = texture_set(&[b"textures/Example/Other.DDS"]);
    let original = mesh(
        100,
        &[
            (0, b"textures/FakeInOpaqueBlock.dds"),
            (1, &valid),
            (1, &second),
        ],
        0,
    );
    fs::write(&path, &original).unwrap();
    let selection = ["--file", "Synthetic.NIF"];
    let first = invoke(dir, &selection);
    assert_eq!(first.stdout, invoke(dir, &selection).stdout);
    let answer = good(first);
    assert_eq!(
        answer["blockTypes"],
        json!({"NiNode":1,"BSShaderTextureSet":2})
    );
    assert_eq!(answer["blockCount"], 3);
    assert_eq!(answer["formatVersion"], "20.2.0.7");
    assert_eq!(answer["endian"], "little");
    assert_eq!(answer["userVersion"], 12);
    assert_eq!(answer["streamVersion"], 100);
    assert_eq!(answer["rootCount"], 1);
    assert_eq!(answer["dependenciesResolved"], false);
    assert_eq!(answer["geometryInspected"], false);
    let slots = &answer["textureSets"][0]["slots"];
    assert_eq!(answer["textureSets"][0]["block"], 1);
    assert_eq!(
        slots[0],
        json!({"slot":0, "state":"embedded_relative_dds", "path":r"Textures\Synthetic\Test_D.dds"})
    );
    assert_eq!(slots[1], json!({"slot":1, "state":"empty", "path":null}));
    assert_eq!(
        slots[2],
        json!({"slot":2, "state":"unreviewed_path", "path":null})
    );
    assert_eq!(slots[3]["state"], "unreviewed_path");
    assert_eq!(
        answer["textureSets"][1]["slots"][0]["path"],
        "textures/Example/Other.DDS"
    );
    for hidden in [
        "HeaderOnly",
        "DontEchoOpaque",
        "NotCanonical",
        "FakeInOpaqueBlock",
        dir.to_str().unwrap(),
    ] {
        assert!(!answer.to_string().contains(hidden));
    }
    assert_eq!(fs::read(&path).unwrap(), original);
    assert_eq!(fs::read_dir(dir).unwrap().count(), 1);
}

#[test]
fn invalid_envelope_refuses_while_unreviewed_texture_layout_is_distinct() {
    let temp = tempfile::tempdir().unwrap();
    let dir = temp.path();
    let path = dir.join("Synthetic.nif");
    // A table can be read soundly even when a specific typed block can't be decoded.
    let mut malformed_block = texture_set(&[b"textures/demo.dds"]);
    malformed_block.push(0xff);
    let unknown = mesh(
        100,
        &[(1, &malformed_block), (1, &vec![0_u8; 16 * 1024 + 1])],
        0,
    );
    fs::write(&path, &unknown).unwrap();
    let answer = good(invoke(dir, &["--file", "Synthetic.nif"]));
    assert_eq!(
        answer["textureSets"][0],
        json!({"block":0,"state":"unreviewed_layout","slots":null})
    );
    assert_eq!(answer["textureSetsNotExamined"], 1);

    let original = mesh(100, &[(1, &texture_set(&[b"textures/demo.dds"]))], 0);
    let mut truncated = original.clone();
    truncated.pop();
    let mut trailing = original.clone();
    trailing.push(0);
    let mut unsupported_endian = original.clone();
    unsupported_endian[b"Gamebryo File Format, Version 20.2.0.7\n".len() + 4] = 0;
    let mut inflated_block_extent = original.clone();
    let type_index_and_size = b"Gamebryo File Format, Version 20.2.0.7\n".len()
        + 4
        + 1
        + 4
        + 4
        + 4
        + 9
        + 2
        + (4 + "NiNode".len())
        + (4 + "BSShaderTextureSet".len())
        + (4 + "UnusedType".len());
    inflated_block_extent[type_index_and_size + 2..type_index_and_size + 6]
        .copy_from_slice(&u32::MAX.to_le_bytes());
    for bytes in [
        truncated,
        trailing,
        unsupported_endian,
        inflated_block_extent,
        mesh(83, &[(1, b"")], 0),
        mesh(100, &[(4, b"")], 0),
        mesh(100, &[(1, b"")], 3),
    ] {
        fs::write(&path, bytes).unwrap();
        refused(invoke(dir, &["--file", "Synthetic.nif"]));
    }
}

#[test]
fn bounded_named_selection_and_unsupported_arguments_are_refused() {
    let temp = tempfile::tempdir().unwrap();
    let dir = temp.path();
    let file = fs::File::create(dir.join("Oversize.nif")).unwrap();
    file.set_len(256 * 1024 * 1024 + 1).unwrap();
    for args in [
        vec!["--file", "Oversize.nif"],
        vec!["--file", "Other.txt"],
        vec!["--file", "NotSelected.nif"],
        vec!["--file", "Oversize.nif", "--extract"],
        vec!["--stdin"],
    ] {
        refused(invoke(dir, &args));
    }
}
