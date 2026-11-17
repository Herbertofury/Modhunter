use super::*;

fn application(path: &Path) {
    fs::create_dir_all(path).unwrap();
    // A deterministic x64 PE header is metadata only; no fixture is executed.
    let mut pe = [0_u8; 256];
    pe[..2].copy_from_slice(b"MZ");
    pe[60..64].copy_from_slice(&128_u32.to_le_bytes());
    pe[128..132].copy_from_slice(b"PE\0\0");
    pe[132..134].copy_from_slice(&0x8664_u16.to_le_bytes());
    fs::write(path.join("ModOrganizer.exe"), pe).unwrap();
}

fn qt_string(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn instance(path: &Path, game: &str) {
    fs::create_dir_all(path.join("profiles/Default")).unwrap();
    fs::write(
        path.join("ModOrganizer.ini"),
        format!("[General]\ngameName={game}\nselected_profile=@ByteArray(Default)\n"),
    )
    .unwrap();
    fs::write(path.join("profiles/Default/modlist.txt"), b"").unwrap();
}

fn deep(roots: &[PathBuf]) -> Inventory {
    discover(
        &ScanSpec {
            mode: ScanMode::Deep,
            roots: roots.to_vec(),
        },
        KnownSeeds::default(),
        &AtomicBool::new(false),
        |_, _| {},
    )
}

#[test]
fn portable_ini_is_sufficient_and_global_instances_are_shared_non_owning_choices() {
    let root = tempfile::tempdir().unwrap();
    let apps = root.path().join("applications");
    let first = apps.join("Portable but unlocked");
    let second = apps.join("Shared application");
    let locked = apps.join("Locked portable");
    for path in [&first, &second, &locked] {
        application(path);
    }
    instance(&first, "Skyrim Special Edition");
    instance(&locked, "Fallout 4");
    fs::write(locked.join("portable.txt"), b"").unwrap();
    let globals = root.path().join("current user global instances");
    instance(&globals.join("Skyrim global"), "Skyrim Special Edition");
    instance(&globals.join("Fallout global"), "Fallout 4");
    let mut seeds = KnownSeeds::for_roots(&[apps]);
    seeds.global_instance_roots.push(globals);
    let result = discover(
        &ScanSpec::default(),
        seeds,
        &AtomicBool::new(false),
        |_, _| {},
    );
    assert!(result.complete);
    assert_eq!(result.installations.len(), 3);
    assert_eq!(result.instances.len(), 4);
    let portable = result
        .installations
        .iter()
        .find(|app| app.directory.ends_with("Portable but unlocked"))
        .unwrap();
    assert!(!portable.portable_locked);
    assert_eq!(
        result
            .associations
            .iter()
            .filter(|association| association.installation_id == portable.id)
            .count(),
        3
    );
    let locked = result
        .installations
        .iter()
        .find(|app| app.portable_locked)
        .unwrap();
    let locked_associations: Vec<_> = result
        .associations
        .iter()
        .filter(|association| association.installation_id == locked.id)
        .collect();
    assert_eq!(locked_associations.len(), 1);
    assert_eq!(locked_associations[0].kind, "portable");
    for instance in result
        .instances
        .iter()
        .filter(|instance| instance.kind == "global")
    {
        let shared: Vec<_> = result
            .associations
            .iter()
            .filter(|association| association.instance_id == instance.id)
            .collect();
        assert_eq!(shared.len(), 2);
        assert!(
            shared
                .iter()
                .all(|association| association.kind == "availableGlobal")
        );
    }
}

#[test]
fn global_instances_without_an_application_remain_visible() {
    let root = tempfile::tempdir().unwrap();
    instance(&root.path().join("Skyrim"), "Skyrim Special Edition");
    let seeds = KnownSeeds {
        global_instance_roots: vec![root.path().to_path_buf()],
        ..KnownSeeds::default()
    };
    let result = discover(
        &ScanSpec::default(),
        seeds,
        &AtomicBool::new(false),
        |_, _| {},
    );
    assert!(result.complete);
    assert!(result.installations.is_empty());
    assert_eq!(result.instances.len(), 1);
    assert_eq!(result.instances[0].kind, "global");
    assert!(result.associations.is_empty());
}

#[test]
fn split_unicode_paths_qt_arrays_and_utf16_are_read_without_importing_mod_files() {
    let root = tempfile::tempdir().unwrap();
    let config = root.path().join("configuration 雪");
    let data = root.path().join("data outside application");
    let profiles = data.join("custom profiles");
    let profile = profiles.join("Profile 雪");
    fs::create_dir_all(&config).unwrap();
    fs::create_dir_all(&profile).unwrap();
    fs::write(profile.join("modlist.txt"), b"+No mod contents are needed").unwrap();
    let selected = "Profile 雪"
        .as_bytes()
        .iter()
        .map(|byte| format!("\\x{byte:02x}"))
        .collect::<String>();
    let text = format!(
        "[General]\ngameName=Skyrim Special Edition\ngamePath=\"{}\"\nselected_profile=@ByteArray({selected})\n\
         [Settings]\nbase_directory=\"{}\"\nprofiles_directory=%BASE_DIR%/custom profiles\n\
         mod_directory=%BASE_DIR%/mods not read\n",
        qt_string(&root.path().join("game 雪").to_string_lossy()),
        qt_string(&data.to_string_lossy()),
    );
    let bytes: Vec<_> = [0xff, 0xfe]
        .into_iter()
        .chain(text.encode_utf16().flat_map(u16::to_le_bytes))
        .collect();
    let ini = config.join("ModOrganizer.ini");
    fs::write(&ini, &bytes).unwrap();
    let seeds = KnownSeeds {
        instance_configs: vec![SeedPath::new(&ini, "importedProfile")],
        ..KnownSeeds::default()
    };
    let result = discover(
        &ScanSpec::default(),
        seeds,
        &AtomicBool::new(false),
        |_, _| {},
    );
    assert_eq!(result.instances.len(), 1);
    let value = &result.instances[0];
    assert_eq!(value.selected_profile.as_deref(), Some("Profile 雪"));
    assert_eq!(value.base_directory.as_deref(), Some(data.as_path()));
    assert_eq!(
        value.profiles_directory.as_deref(),
        Some(profiles.as_path())
    );
    assert_eq!(value.profiles.len(), 1);
    assert!(value.profiles[0].selected);
    assert_eq!(value.profiles[0].name, "Profile 雪");
    assert!(!value.mods_directory.as_ref().unwrap().exists());
    assert_eq!(fs::read(ini).unwrap(), bytes);
}

#[test]
fn deep_scan_has_no_live_session_count_or_depth_limit_and_deduplicates_overlap() {
    let root = tempfile::tempdir().unwrap();
    let mut deep_path = root.path().join("nested");
    for _ in 0..80 {
        deep_path = deep_path.join("d");
    }
    application(&deep_path);
    let applications = root.path().join("many");
    for index in 0..300 {
        application(&applications.join(format!("app-{index:03}")));
    }
    let result = deep(&[
        root.path().into(),
        applications.clone(),
        root.path().join("nested"),
    ]);
    assert!(result.complete);
    assert_eq!(result.installations.len(), 301);
    assert!(
        result
            .installations
            .iter()
            .all(|app| app.architecture.as_deref() == Some("x64"))
    );
    let ids: HashSet<_> = result.installations.iter().map(|app| &app.id).collect();
    assert_eq!(ids.len(), 301);
}

#[test]
fn known_scan_declares_shallow_scope_and_deep_scan_reaches_remaining_roots() {
    let root = tempfile::tempdir().unwrap();
    application(&root.path().join("Direct application"));
    application(&root.path().join("Collection/Another folder/Application"));
    let roots = [root.path().to_path_buf()];
    let known = discover(
        &ScanSpec::default(),
        KnownSeeds::for_roots(&roots),
        &AtomicBool::new(false),
        |_, _| {},
    );
    assert_eq!(known.installations.len(), 1);
    assert!(known.scope.contains("immediate children"));
    let result = discover(
        &ScanSpec {
            mode: ScanMode::Deep,
            roots: roots.to_vec(),
        },
        KnownSeeds {
            applications: vec![SeedPath::new(
                root.path().join("Direct application"),
                "remembered",
            )],
            ..KnownSeeds::default()
        },
        &AtomicBool::new(false),
        |_, _| {},
    );
    assert_eq!(result.installations.len(), 2);
}

#[test]
fn cancellation_keeps_found_records_and_never_reports_complete() {
    let root = tempfile::tempdir().unwrap();
    for index in 0..40 {
        application(&root.path().join(format!("MO2-{index}")));
    }
    let cancelled = AtomicBool::new(false);
    let mut final_counts = (0, true);
    let result = discover(
        &ScanSpec {
            mode: ScanMode::Deep,
            roots: vec![root.path().into()],
        },
        KnownSeeds::default(),
        &cancelled,
        |progress, inventory| {
            final_counts = (inventory.installations.len(), inventory.complete);
            if progress.installations_found >= 3 {
                cancelled.store(true, Ordering::Release);
            }
        },
    );
    assert!(!result.complete);
    assert_eq!(result.installations.len(), 3);
    assert_eq!(final_counts, (3, false));
    assert!(result.issues.iter().any(|issue| issue.kind == "cancelled"));
    let cancelled_before_start = discover(
        &ScanSpec {
            mode: ScanMode::Deep,
            roots: vec![root.path().into()],
        },
        KnownSeeds::default(),
        &cancelled,
        |progress, _| assert_eq!(progress.directories_visited, 0),
    );
    assert!(cancelled_before_start.installations.is_empty());
}

#[test]
fn malformed_and_oversized_instances_are_retained_with_explicit_issues() {
    let root = tempfile::tempdir().unwrap();
    let broken = root.path().join("broken");
    let huge = root.path().join("huge");
    application(&broken);
    application(&huge);
    fs::write(broken.join("ModOrganizer.ini"), b"[General\nbroken").unwrap();
    fs::write(
        huge.join("ModOrganizer.ini"),
        vec![b'x'; INI_LIMIT as usize + 1],
    )
    .unwrap();
    let result = deep(&[root.path().into()]);
    assert!(result.complete);
    assert_eq!(result.installations.len(), 2);
    assert_eq!(result.instances.len(), 2);
    assert!(
        result
            .instances
            .iter()
            .all(|instance| instance.kind == "portable" && instance.game_name.is_none())
    );
    assert_eq!(
        result
            .issues
            .iter()
            .filter(|issue| issue.kind == "invalidInstance")
            .count(),
        2
    );
}

#[test]
fn plugin_version_observation_is_explicit_and_application_identity_survives_upgrades() {
    let root = tempfile::tempdir().unwrap();
    application(root.path());
    let plugin = root.path().join("plugins/mo2_update_arsenal");
    let first = deep(&[root.path().into()]);
    assert_eq!(first.installations[0].plugin.status, "missing");
    fs::create_dir_all(&plugin).unwrap();
    fs::write(
        plugin.join("version.py"),
        format!("VERSION = '{}'\n", env!("CARGO_PKG_VERSION")),
    )
    .unwrap();
    let matching = deep(&[root.path().into()]);
    assert_eq!(matching.installations[0].plugin.status, "matching");
    fs::write(plugin.join("version.py"), "VERSION = '0.1.0'\n").unwrap();
    let previous = deep(&[root.path().into()]);
    assert_eq!(previous.installations[0].plugin.status, "different");
    assert_eq!(previous.installations[0].id, first.installations[0].id);
    fs::write(
        plugin.join("version.py"),
        "VERSION = '1.2.3'\nVERSION = '9.9.9'\n",
    )
    .unwrap();
    let invalid = deep(&[root.path().into()]);
    assert_eq!(invalid.installations[0].plugin.status, "invalid");
    assert!(invalid.installations[0].plugin.version.is_none());
}

#[test]
fn explicit_shortcut_association_does_not_assign_a_global_instance_exclusively() {
    let root = tempfile::tempdir().unwrap();
    let first = root.path().join("First application");
    let second = root.path().join("Second application");
    application(&first);
    application(&second);
    let globals = root.path().join("global");
    instance(&globals.join("Skyrim"), "Skyrim Special Edition");
    let mut seeds = KnownSeeds::for_roots(&[first.clone(), second.clone()]);
    seeds.global_instance_roots.push(globals);
    seeds.shortcuts.push(ShortcutSeed {
        path: root.path().join("SKSE.lnk"),
        executable: first.join("ModOrganizer.exe"),
        instance_name: Some("Skyrim".into()),
    });
    seeds.processes.push(RunningProcess {
        pid: 123,
        name: "ModOrganizer.exe".into(),
        executable: Some(first.join("ModOrganizer.exe")),
    });
    let result = discover(
        &ScanSpec::default(),
        seeds,
        &AtomicBool::new(false),
        |_, _| {},
    );
    assert_eq!(result.associations.len(), 2);
    assert!(
        result
            .associations
            .iter()
            .any(|association| association.kind == "shortcut")
    );
    assert!(
        result
            .associations
            .iter()
            .any(|association| association.kind == "availableGlobal")
    );
    let running = result
        .installations
        .iter()
        .find(|application| !application.running_pids.is_empty())
        .unwrap();
    assert_eq!(running.running_pids, [123]);
    // Process observation never becomes an invented authenticated session.
    let value = serde_json::to_value(&result).unwrap();
    assert!(value["installations"][0].get("endpoint").is_none());
}

#[test]
fn explicit_roots_do_not_walk_sibling_applications_and_unavailable_roots_are_reported() {
    let root = tempfile::tempdir().unwrap();
    let selected = root.path().join("selected");
    let outside = root.path().join("outside");
    application(&selected);
    application(&outside);
    let ordinary_file = root.path().join("not a folder");
    fs::write(&ordinary_file, b"").unwrap();
    let result = deep(&[
        selected.clone(),
        root.path().join("unavailable"),
        ordinary_file,
    ]);
    assert_eq!(result.installations.len(), 1);
    assert_eq!(
        result.installations[0].directory,
        fs::canonicalize(selected).unwrap()
    );
    assert!(
        result
            .issues
            .iter()
            .any(|issue| issue.kind == "unavailable")
    );
    assert!(
        result
            .issues
            .iter()
            .any(|issue| issue.kind == "notDirectory")
    );
}

#[test]
fn profile_list_retains_missing_modlists_and_unknown_placeholders_are_not_guessed() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("profiles/Empty")).unwrap();
    fs::write(
        root.path().join("ModOrganizer.ini"),
        "[General]\ngameName=Skyrim\n[Settings]\nmod_directory=%UNKNOWN%/mods\n",
    )
    .unwrap();
    let result = deep(&[root.path().into()]);
    assert_eq!(result.instances.len(), 1);
    assert_eq!(result.instances[0].profiles.len(), 1);
    assert_eq!(result.instances[0].profiles[0].name, "Empty");
    assert!(result.instances[0].mods_directory.is_none());
    assert!(
        result
            .issues
            .iter()
            .any(|issue| issue.kind == "profileModlist")
    );
    assert!(
        result
            .issues
            .iter()
            .any(|issue| issue.kind == "configuredPath")
    );
}

#[cfg(unix)]
#[test]
fn symlink_cycles_and_linked_config_files_are_not_followed() {
    use std::os::unix::fs::symlink;
    let root = tempfile::tempdir().unwrap();
    let selected = root.path().join("selected");
    let outside = root.path().join("outside");
    application(&selected);
    instance(&outside, "Not in scope");
    symlink(&selected, selected.join("cycle")).unwrap();
    symlink(&outside, selected.join("outside-link")).unwrap();
    symlink(
        outside.join("ModOrganizer.ini"),
        selected.join("ModOrganizer.ini"),
    )
    .unwrap();
    let result = deep(&[selected]);
    assert_eq!(result.installations.len(), 1);
    assert!(result.instances.is_empty());
    assert!(
        result
            .issues
            .iter()
            .filter(|issue| issue.kind == "linkedPath")
            .count()
            >= 2
    );
    assert!(
        result
            .issues
            .iter()
            .any(|issue| issue.kind == "invalidInstance")
    );
}

#[cfg(windows)]
#[test]
fn windows_junction_cycles_are_reported_without_following_them() {
    use std::os::windows::process::CommandExt;

    let root = tempfile::tempdir().unwrap();
    let application_path = root.path().join("MO2");
    application(&application_path);
    let link = application_path.join("cycle");
    let command = PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/cmd.exe");
    // cmd.exe parses its own command line and does not accept every path form
    // accepted by Rust's filesystem APIs. Keep all operands trusted literals;
    // only CreateProcess receives the owned, ordinary Win32 working directory.
    let directory = windows::fixture_shell_path(&fs::canonicalize(&application_path).unwrap());
    let result = std::process::Command::new(command)
        .current_dir(&directory)
        .args(["/d", "/c"])
        .raw_arg("mklink /J cycle .")
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "Junction fixture in {directory:?}: stdout={}, stderr={}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(linked(&fs::symlink_metadata(&link).unwrap()));
    assert_eq!(
        fs::canonicalize(&link).unwrap(),
        fs::canonicalize(&application_path).unwrap()
    );
    let result = deep(&[application_path]);
    assert_eq!(result.installations.len(), 1);
    assert!(result.issues.iter().any(|issue| issue.kind == "linkedPath"));
    // Remove the junction itself before TempDir removes its owned fixture tree.
    fs::remove_dir(link).unwrap();
}
