use modhunter_core::{CoreError, ImportRequest, ProfileImporter};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use tempfile::TempDir;

struct Fixture {
    root: TempDir,
    profile: PathBuf,
    mods: PathBuf,
}
impl Fixture {
    fn new(modlist: &str) -> Self {
        let root = tempfile::tempdir().unwrap();
        let profile = root.path().join("profiles/Héro 日本");
        let mods = root.path().join("mods");
        fs::create_dir_all(&profile).unwrap();
        fs::create_dir_all(&mods).unwrap();
        fs::write(profile.join("modlist.txt"), modlist).unwrap();
        fs::write(
            root.path().join("ModOrganizer.ini"),
            "[General]\ngameName=Skyrim Special Edition\ngameVersion=1.6.1170\n",
        )
        .unwrap();
        Self {
            root,
            profile,
            mods,
        }
    }
    fn add(&self, name: &str, meta: &str) {
        let directory = self.mods.join(name);
        fs::create_dir_all(&directory).unwrap();
        fs::write(directory.join("meta.ini"), meta).unwrap();
    }
    fn request(&self) -> ImportRequest {
        ImportRequest {
            modlist_path: self.profile.join("modlist.txt"),
            ini_path: None,
            mods_path: None,
            workers: Some(4),
        }
    }
}
fn item<'a>(catalog: &'a Value, name: &str) -> &'a Value {
    catalog["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|m| m["name"] == name)
        .unwrap()
}
fn names(catalog: &Value) -> Vec<String> {
    catalog["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| m["name"].as_str().unwrap().to_owned())
        .collect()
}

#[test]
fn canceled_import_stops_traversal_and_the_same_engine_can_refresh_again() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let modlist = (0..100).map(|n| format!("+Mod{n}\n")).collect::<String>();
    let fixture = Fixture::new(&modlist);
    for n in 0..100 {
        fixture.add(&format!("Mod{n}"), "[General]\nversion=1.0\n");
    }
    let engine = ProfileImporter::new();
    let checks = AtomicUsize::new(0);
    let canceled = engine.import_with_analysis_cancellable(&fixture.request(), false, &[], &|| {
        checks.fetch_add(1, Ordering::Relaxed) >= 8
    });
    assert!(matches!(canceled, Err(CoreError::Cancelled)));
    assert_eq!(checks.load(Ordering::Relaxed), 9);
    let completed = engine.import(&fixture.request()).unwrap();
    assert_eq!(completed.stats.mods, 100);
    assert_eq!(completed.catalog["items"].as_array().unwrap().len(), 100);
}

#[test]
fn already_canceled_import_does_not_open_an_absent_profile() {
    let request = ImportRequest {
        modlist_path: PathBuf::from("missing/modlist.txt"),
        ini_path: None,
        mods_path: None,
        workers: Some(1),
    };
    assert!(matches!(
        ProfileImporter::new().import_with_analysis_cancellable(&request, true, &[], &|| true,),
        Err(CoreError::Cancelled)
    ));
}

#[test]
fn actual_mo2_priority_separators_foreign_and_unlisted_are_preserved() {
    let f = Fixture::new(
        "\u{feff}# MO2 generated\n+Third\n+Side Missions_separator\n-Second\n+First\n*DLC: Example\n*DLCExample\n",
    );
    f.add("First", "[General]\nmodid=44\nversion=1.0\nnewestVersion=2.0\nauthor=Actual Author\nurl=https://github.com/example/mod/releases?token=secret\n");
    f.add("Second", "[General]\nmodid=55\nversion=3.0\n");
    f.add("Third", "[General]\n");
    f.add("Side Missions_separator", "[General]\n");
    f.add("Unlisted", "[General]\n");
    fs::create_dir_all(f.mods.join("First/SKSE/Plugins")).unwrap();
    fs::write(f.mods.join("First/SKSE/Plugins/addon.dll"), "fixture").unwrap();
    let before = fs::read(f.profile.join("modlist.txt")).unwrap();
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    assert_eq!(
        names(&result.catalog),
        [
            "DLCExample",
            "DLC: Example",
            "First",
            "Second",
            "Third",
            "Unlisted"
        ]
    );
    assert_eq!(
        item(&result.catalog, "First")["flags"],
        serde_json::json!(["DLL"])
    );
    assert_eq!(item(&result.catalog, "First")["author"], "Actual Author");
    assert_eq!(
        item(&result.catalog, "First")["source"]["url"],
        "https://github.com/example/mod/releases"
    );
    assert_eq!(item(&result.catalog, "Second")["enabled"], false);
    assert_eq!(item(&result.catalog, "Third")["group"], "Side Missions");
    assert_eq!(item(&result.catalog, "Unlisted")["notInProfile"], true);
    assert_eq!(item(&result.catalog, "DLCExample")["foreign"], true);
    assert_eq!(item(&result.catalog, "DLCExample")["updateEligible"], false);
    assert_eq!(result.stats.foreign_mods, 2);
    assert_eq!(fs::read(f.profile.join("modlist.txt")).unwrap(), before);
    assert!(
        !result
            .catalog
            .to_string()
            .contains(f.root.path().to_str().unwrap())
    );
}

#[test]
fn metadata_cache_verifies_fresh_bytes_before_reusing_parsed_values() {
    let f = Fixture::new("+First\n");
    f.add("First", "[General]\nversion=1.0\n");
    let engine = ProfileImporter::new();
    let cold = engine.import(&f.request()).unwrap();
    let warm = engine.import(&f.request()).unwrap();
    assert_eq!(cold.stats.metadata_cache_hits, 0);
    assert_eq!(warm.stats.metadata_cache_hits, 1);
    assert_eq!(warm.stats.files_read, 3);
    assert_eq!(warm.stats.bytes_read, cold.stats.bytes_read);
    assert_eq!(cold.catalog["items"], warm.catalog["items"]);
    f.add("First", "[General]\nversion=2.0\n");
    let changed = engine.import(&f.request()).unwrap();
    assert_eq!(item(&changed.catalog, "First")["installedVersion"], "2.0");
    assert_eq!(changed.stats.metadata_cache_hits, 0);
    fs::remove_file(f.mods.join("First/meta.ini")).unwrap();
    let removed = engine.import(&f.request()).unwrap();
    assert_eq!(item(&removed.catalog, "First")["installedVersion"], "?");
}

#[test]
fn equal_length_metadata_edits_with_restored_mtime_refresh_versions_and_source_identity() {
    let f = Fixture::new("+First\n");
    let original =
        "[General]\nversion=1.0\nnewestVersion=2.0\nmodid=0\nurl=https://github.com/owner/first\n";
    let changed =
        "[General]\nversion=2.0\nnewestVersion=3.0\nmodid=0\nurl=https://github.com/owner/other\n";
    assert_eq!(original.len(), changed.len());
    f.add("First", original);
    let path = f.mods.join("First/meta.ini");
    let modified = fs::metadata(&path).unwrap().modified().unwrap();
    let engine = ProfileImporter::new();
    let first = engine.import(&f.request()).unwrap();
    assert_eq!(item(&first.catalog, "First")["installedVersion"], "1.0");

    let replace = |bytes: &[u8]| {
        fs::write(&path, bytes).unwrap();
        fs::OpenOptions::new()
            .write(true)
            .open(&path)
            .unwrap()
            .set_times(fs::FileTimes::new().set_modified(modified))
            .unwrap();
    };
    replace(changed.as_bytes());
    let current = engine.import(&f.request()).unwrap();
    let row = item(&current.catalog, "First");
    assert_eq!(row["installedVersion"], "2.0");
    assert_eq!(row["latestKnownVersion"], "3.0");
    assert_eq!(row["source"]["url"], "https://github.com/owner/other");
    assert_eq!(current.stats.metadata_cache_hits, 0);
    assert_eq!(
        engine
            .import(&f.request())
            .unwrap()
            .stats
            .metadata_cache_hits,
        1
    );

    let mut malformed = changed.as_bytes().to_vec();
    malformed[8] = b'[';
    replace(&malformed);
    let rejected = engine.import(&f.request()).unwrap();
    let row = item(&rejected.catalog, "First");
    assert_eq!(row["installedVersion"], "?");
    assert_ne!(row["source"]["url"], "https://github.com/owner/other");
    assert_eq!(rejected.stats.metadata_cache_hits, 0);
    assert!(!rejected.warnings.is_empty());
}

#[cfg(windows)]
#[test]
fn active_windows_metadata_writer_cannot_reuse_a_previous_installed_version() {
    use std::os::windows::fs::OpenOptionsExt;
    let f = Fixture::new("+First\n");
    f.add("First", "[General]\nversion=1.0\n");
    let engine = ProfileImporter::new();
    engine.import(&f.request()).unwrap();
    let writer = fs::OpenOptions::new()
        .write(true)
        .share_mode(0x0000_0003)
        .open(f.mods.join("First/meta.ini"))
        .unwrap();
    let busy = engine.import(&f.request()).unwrap();
    assert_eq!(item(&busy.catalog, "First")["installedVersion"], "?");
    assert_eq!(busy.stats.metadata_cache_hits, 0);
    assert!(!busy.warnings.is_empty());
    drop(writer);
    let stable = engine.import(&f.request()).unwrap();
    assert_eq!(item(&stable.catalog, "First")["installedVersion"], "1.0");
    assert_eq!(stable.stats.metadata_cache_hits, 1);
}

#[test]
fn dll_change_is_seen_even_when_metadata_cache_hits() {
    let f = Fixture::new("+First\n");
    f.add("First", "[General]\nversion=1.0\n");
    let engine = ProfileImporter::new();
    engine.import(&f.request()).unwrap();
    fs::create_dir_all(f.mods.join("First/SKSE/Plugins")).unwrap();
    fs::write(f.mods.join("First/SKSE/Plugins/new.dll"), "fixture").unwrap();
    let refreshed = engine.import(&f.request()).unwrap();
    assert_eq!(refreshed.stats.metadata_cache_hits, 1);
    assert_eq!(
        item(&refreshed.catalog, "First")["flags"],
        serde_json::json!(["DLL"])
    );
    fs::remove_file(f.mods.join("First/SKSE/Plugins/new.dll")).unwrap();
    let removed = engine.import(&f.request()).unwrap();
    assert_eq!(
        item(&removed.catalog, "First")["flags"],
        serde_json::json!([])
    );
}

#[test]
fn split_paths_and_utf16_ini_are_resolved_without_guessing() {
    let f = Fixture::new("+First\n");
    f.add("First", "[General]\nmodid=1\n");
    let config = f.root.path().join("config");
    fs::create_dir(&config).unwrap();
    let text = format!(
        "[General]\ngameName=Fallout 4\nselected_profile=@ByteArray(Hero)\n[Settings]\nbase_directory={}\nmod_directory=%BASE_DIR%/mods\nprofiles_directory=%BASE_DIR%/profiles\n",
        f.root.path().display()
    );
    let mut bytes = vec![0xff, 0xfe];
    for unit in text.encode_utf16() {
        bytes.extend_from_slice(&unit.to_le_bytes());
    }
    let ini = config.join("ModOrganizer.ini");
    fs::write(&ini, bytes).unwrap();
    let mut request = f.request();
    request.ini_path = Some(ini);
    let result = ProfileImporter::new().import(&request).unwrap();
    assert_eq!(result.catalog["game"], "fallout4");
    assert_eq!(
        item(&result.catalog, "First")["source"]["url"],
        "https://www.nexusmods.com/fallout4/mods/1?tab=files"
    );
}

#[test]
fn explicit_mods_override_supports_unusual_profile_directory_name() {
    let f = Fixture::new("+First\n");
    f.add("First", "[General]\nversion=1.0\n");
    let other_profile = f.root.path().join("custom-named-profile-storage/Hero");
    fs::create_dir_all(&other_profile).unwrap();
    fs::write(other_profile.join("modlist.txt"), "+First\n").unwrap();
    let mut request = f.request();
    request.modlist_path = other_profile.join("modlist.txt");
    request.mods_path = Some(f.mods.clone());
    let result = ProfileImporter::new().import(&request).unwrap();
    assert_eq!(result.stats.mods, 1);
}

#[test]
fn ini_profile_mismatch_fails_instead_of_importing_wrong_instance() {
    let f = Fixture::new("+First\n");
    f.add("First", "[General]\n");
    let other = f.root.path().join("other-profiles");
    fs::create_dir(&other).unwrap();
    fs::write(
        f.root.path().join("ModOrganizer.ini"),
        format!("[Settings]\nprofiles_directory={}\n", other.display()),
    )
    .unwrap();
    assert!(
        ProfileImporter::new()
            .import(&f.request())
            .unwrap_err()
            .to_string()
            .contains("does not belong")
    );
}

#[test]
fn unknown_and_cross_game_metadata_do_not_invent_same_game_update_identity() {
    let f = Fixture::new("+First\n+Unknown\n");
    f.add(
        "First",
        "[General]\ngameName=Fallout 4\nmodid=44\nversion=1.0\n",
    );
    f.add("Unknown", "[General]\nmodid=5\n");
    fs::write(
        f.root.path().join("ModOrganizer.ini"),
        "[General]\ngameName=Unsupported Game\n",
    )
    .unwrap();
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    assert_eq!(result.catalog["game"], "unknown");
    assert_eq!(
        item(&result.catalog, "Unknown")["source"]["verified"],
        false
    );
    assert_eq!(
        item(&result.catalog, "First")["source"]["url"],
        "https://www.nexusmods.com/fallout4/mods/44?tab=files"
    );
    assert_eq!(item(&result.catalog, "First")["updateEligible"], false);
}

#[test]
fn corrupted_or_oversized_metadata_keeps_row_with_warning() {
    let f = Fixture::new("+Bad\n+Large\n");
    f.add("Bad", "[General\nversion=5\n");
    f.add("Large", &"x".repeat(256_001));
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    assert_eq!(result.stats.mods, 2);
    assert_eq!(result.warnings.len(), 2);
    assert_eq!(item(&result.catalog, "Bad")["installedVersion"], "?");
}

#[test]
fn an_explicit_unknown_mod_game_never_falls_back_to_profile_nexus_identity() {
    let f = Fixture::new("+Different Game\n");
    f.add(
        "Different Game",
        "[General]\ngameName=Unrecognized Other Game\nmodid=44\n",
    );
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    let imported = item(&result.catalog, "Different Game");
    assert_eq!(imported["source"]["verified"], false);
    assert_eq!(imported["sourceGame"], "unknown");
    assert_eq!(imported["updateEligible"], false);
}

#[test]
fn meaningful_custom_source_queries_survive_credential_and_tracking_redaction() {
    let f = Fixture::new("+Forum\n+File\n");
    f.add("Forum", "[General]\nhasCustomURL=true\nurl=https://www.loverslab.com/index.php?/topic/123-exact-mod/&utm_source=test&%74oken=SECRET\n");
    f.add("File", "[General]\nhasCustomURL=true\nurl=https://example.com/download.php?id=123&edition=se&api_key=SECRET&gclid=TRACKING\n");
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    assert_eq!(
        item(&result.catalog, "Forum")["source"]["url"],
        "https://www.loverslab.com/index.php?/topic/123-exact-mod/"
    );
    assert_eq!(
        item(&result.catalog, "File")["source"]["url"],
        "https://example.com/download.php?id=123&edition=se"
    );
    assert!(!result.catalog.to_string().contains("SECRET"));
}

#[test]
fn notes_links_cannot_override_authoritative_nexus_identity() {
    let f = Fixture::new("+Native\n+Advisory\n");
    f.add("Native", "[General]\nhasCustomURL=false\nurl=https://example.com/stale\nmodid=44\nnotes=Requires https://github.com/dependency/other-mod\n");
    f.add(
        "Advisory",
        "[General]\nhasCustomURL=false\nnotes=See https://example.com/troubleshooting?id=4\n",
    );
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    assert_eq!(
        item(&result.catalog, "Native")["source"]["url"],
        "https://www.nexusmods.com/skyrimspecialedition/mods/44?tab=files"
    );
    let advisory = &item(&result.catalog, "Advisory")["source"];
    assert_eq!(advisory["url"], "https://example.com/troubleshooting?id=4");
    assert_eq!(advisory["verified"], false);
    assert_eq!(advisory["kind"], "advisory");
}

#[test]
fn no_arbitrary_names_or_secret_urls_are_imported() {
    let f = Fixture::new("+Valid\n+../evil\n+C:\\private\n+Valid\n");
    f.add("Valid", "[General]\nurl=https://127.0.0.1/private\n");
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    assert_eq!(names(&result.catalog), ["Valid"]);
    assert_eq!(item(&result.catalog, "Valid")["source"]["verified"], false);
}

#[test]
fn profile_id_is_stable_and_different_profiles_are_separate() {
    let f = Fixture::new("+First\n");
    f.add("First", "[General]\n");
    let engine = ProfileImporter::new();
    let one = engine.import(&f.request()).unwrap();
    let same = engine.import(&f.request()).unwrap();
    assert_eq!(one.catalog["profileId"], same.catalog["profileId"]);
    let other = f.root.path().join("profiles/Other");
    fs::create_dir(&other).unwrap();
    fs::write(other.join("modlist.txt"), "+First\n").unwrap();
    let mut request = f.request();
    request.modlist_path = other.join("modlist.txt");
    let two = engine.import(&request).unwrap();
    assert_ne!(one.catalog["profileId"], two.catalog["profileId"]);
    assert_ne!(
        item(&one.catalog, "First")["id"],
        item(&two.catalog, "First")["id"]
    );
}

#[cfg(unix)]
#[test]
fn symlinked_mods_metadata_and_native_plugins_are_not_followed() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new("+LinkedMod\n+LinkedMeta\n+LinkedPlugin\n");
    let outside = tempfile::tempdir().unwrap();
    fs::write(
        outside.path().join("meta.ini"),
        "[General]\nversion=secret\n",
    )
    .unwrap();
    symlink(outside.path(), f.mods.join("LinkedMod")).unwrap();
    fs::create_dir(f.mods.join("LinkedMeta")).unwrap();
    symlink(
        outside.path().join("meta.ini"),
        f.mods.join("LinkedMeta/meta.ini"),
    )
    .unwrap();
    f.add("LinkedPlugin", "[General]\n");
    fs::write(outside.path().join("evil.dll"), "fixture").unwrap();
    fs::create_dir(f.mods.join("LinkedPlugin/SKSE")).unwrap();
    symlink(outside.path(), f.mods.join("LinkedPlugin/SKSE/Plugins")).unwrap();
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    assert_eq!(result.stats.mods, 3);
    assert!(!result.catalog.to_string().contains("secret"));
    assert_eq!(
        item(&result.catalog, "LinkedPlugin")["flags"],
        serde_json::json!([])
    );
}

#[test]
fn large_profile_has_no_silent_truncation_and_workers_are_bounded() {
    let count = 700;
    let f = Fixture::new(
        &(0..count)
            .rev()
            .map(|n| format!("+Mod {n}\n"))
            .collect::<String>(),
    );
    for n in 0..count {
        f.add(&format!("Mod {n}"), "[General]\nversion=1.0\n");
    }
    let result = ProfileImporter::new().import(&f.request()).unwrap();
    assert_eq!(result.stats.mods, count);
    assert_eq!(item(&result.catalog, "Mod 0")["priority"], 0);
    let mut invalid = f.request();
    invalid.workers = Some(33);
    assert!(ProfileImporter::new().import(&invalid).is_err());
    invalid.workers = Some(0);
    assert!(ProfileImporter::new().import(&invalid).is_err());
    invalid.modlist_path = Path::new("plugins.txt").to_path_buf();
    assert!(ProfileImporter::new().import(&invalid).is_err());
}
