use super::*;
use std::sync::Arc;
use tempfile::TempDir;

fn fresh() -> (TempDir, WatchStore) {
    let dir = tempfile::tempdir().unwrap();
    let store = WatchStore::open(dir.path().join("watchtower.json"));
    (dir, store)
}

fn command(store: &WatchStore, action: &str, payload: Value, now: u64) -> Value {
    store.command(action, &payload, now).unwrap()
}

fn add(store: &WatchStore, url: &str, now: u64) -> String {
    command(store, "saveWatch", json!({"url":url}), now)["watchId"]
        .as_str()
        .unwrap()
        .to_owned()
}

fn capture(job: &CheckJob, text: &str) -> PageObservation {
    PageObservation {
        final_url: job.url.clone(),
        title: "A tracked page".into(),
        text: text.into(),
        ..Default::default()
    }
}

fn observe(store: &WatchStore, id: &str, text: &str, now: u64) -> Observation {
    let job = store.begin_check(id, now).unwrap();
    assert!(
        store
            .complete_check(&job, capture(&job, text), now + 1)
            .unwrap()
    );
    store.watch(id).unwrap().latest.unwrap()
}

fn row(store: &WatchStore, id: &str) -> Value {
    store.snapshot()["watches"]
        .as_array()
        .unwrap()
        .iter()
        .find(|w| w["id"] == id)
        .unwrap()
        .clone()
}

fn export_document(watches: Value) -> Value {
    json!({"kind":"modhunter-watchtower","schemaVersion":1,"watches":watches})
}

#[test]
fn baseline_unchanged_change_and_exact_seen_undo_remain_distinct() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/PackA", 100);
    assert_eq!(row(&store, &id)["needsCheck"], true);
    assert_eq!(row(&store, &id)["unread"], true);
    let first = observe(&store, &id, "Version 1\nA useful mod", 200);
    assert_eq!(row(&store, &id)["state"], "baseline");
    assert_eq!(row(&store, &id)["hasChanges"], false);
    assert_eq!(row(&store, &id)["unread"], false);
    let unchanged = observe(&store, &id, " Version   1\n\nA useful mod ", 300);
    assert_eq!(unchanged.id, first.id);
    assert!(unchanged.checked_at > first.checked_at);
    assert_eq!(store.watch(&id).unwrap().history.len(), 1);
    let second = observe(&store, &id, "Version 2\nA useful mod", 400);
    let third = observe(&store, &id, "Version 3\nA useful mod", 500);
    let seen = command(
        &store,
        "markSeen",
        json!({"id":id,"observationId":second.id}),
        600,
    );
    assert_eq!(store.watch(&id).unwrap().seen_hash, Some(second.hash));
    assert_eq!(
        row(&store, &id)["unread"],
        true,
        "seeing an older card must not acknowledge a newer result"
    );
    let newest = command(
        &store,
        "markSeen",
        json!({"id":id,"observationId":third.id}),
        700,
    );
    assert_eq!(row(&store, &id)["unread"], false);
    assert!(
        store
            .command("undoSeen", &json!({"eventId":seen["eventId"]}), 800)
            .is_err()
    );
    command(
        &store,
        "undoSeen",
        json!({"eventId":newest["eventId"]}),
        900,
    );
    assert_eq!(row(&store, &id)["unread"], true);
    command(&store, "undoSeen", json!({"eventId":seen["eventId"]}), 1000);
    assert_eq!(store.watch(&id).unwrap().seen_hash, Some(first.hash));
    assert!(
        store
            .command("undoSeen", &json!({"eventId":seen["eventId"]}), 1100)
            .is_err()
    );
}

#[test]
fn restoration_requires_a_fresh_owned_check_even_with_equal_or_rolled_back_clock() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/mod", 100);
    observe(&store, &id, "Version 1", 200);
    let old = store.begin_check(&id, 200).unwrap();
    command(&store, "removeWatch", json!({"id":id}), 200);
    command(&store, "restoreWatch", json!({"id":id}), 200);
    assert_eq!(row(&store, &id)["needsCheck"], true);
    assert!(
        !store
            .complete_check(&old, capture(&old, "stale old result"), 201)
            .unwrap()
    );
    assert!(
        store
            .command(
                "markSeen",
                &json!({"id":id,"observationId":"observation-2"}),
                201
            )
            .is_err()
    );
    let refreshed = observe(&store, &id, "Version 1", 150);
    assert_eq!(
        refreshed.started_at, 200,
        "logical freshness tolerates a backwards wall clock"
    );
    assert_eq!(row(&store, &id)["needsCheck"], false);
    assert_eq!(row(&store, &id)["unread"], false);
}

#[test]
fn canonical_duplicates_restore_existing_rows_without_lowercasing_paths() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://EXAMPLE.com:443/PackA#downloads", 100);
    let duplicate = command(
        &store,
        "saveWatch",
        json!({"url":"https://example.com/PackA"}),
        110,
    );
    assert_eq!(duplicate["watchId"], id);
    assert_eq!(duplicate["duplicate"], true);
    let lower = add(&store, "https://example.com/packa", 120);
    assert_ne!(lower, id);
    command(&store, "removeWatch", json!({"id":id}), 130);
    let restore = command(
        &store,
        "saveWatch",
        json!({"url":"https://example.com/PackA"}),
        140,
    );
    assert_eq!(restore["watchId"], id);
    assert_eq!(restore["restored"], true);
    assert_eq!(store.snapshot()["counts"]["all"], 2);
}

#[test]
fn edit_aba_pause_and_mute_invalidate_exact_check_ownership_without_stale_writes() {
    let (dir, store) = fresh();
    let id = add(&store, "https://example.com/A", 100);
    let old = store.begin_check(&id, 110).unwrap();
    command(
        &store,
        "saveWatch",
        json!({"id":id,"url":"https://example.com/B"}),
        120,
    );
    command(
        &store,
        "saveWatch",
        json!({"id":id,"url":"https://example.com/A"}),
        130,
    );
    let before = std::fs::read(dir.path().join("watchtower.json")).unwrap();
    let revision = store.snapshot()["revision"].clone();
    assert!(
        !store
            .complete_check(&old, capture(&old, "obsolete"), 140)
            .unwrap()
    );
    assert!(
        !store
            .fail_check(&old, "timeout", "obsolete failure", 141)
            .unwrap()
    );
    assert_eq!(store.snapshot()["revision"], revision);
    assert_eq!(
        std::fs::read(dir.path().join("watchtower.json")).unwrap(),
        before
    );
    let paused = store.begin_check(&id, 150).unwrap();
    command(
        &store,
        "pauseWatch",
        json!({"ids":[id,id],"enabled":false}),
        160,
    );
    assert!(!store.is_current(&paused));
    assert!(store.begin_check(&id, 170).is_err());
    command(&store, "pauseWatch", json!({"id":id,"enabled":true}), 180);
    let muted = store.begin_check(&id, 190).unwrap();
    command(
        &store,
        "settings",
        json!({"mutedOrigins":["https://example.com/path"]}),
        200,
    );
    assert!(!store.is_current(&muted));
    assert_eq!(row(&store, &id)["muted"], true);
    assert!(store.check_candidates(u64::MAX, false).is_empty());
    command(&store, "settings", json!({"mutedOrigins":[]}), 210);
    observe(&store, &id, "fresh", 220);
    assert_eq!(row(&store, &id)["unread"], false);
}

#[test]
fn historical_seen_actions_cannot_acknowledge_an_unrelated_page_context() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/A", 100);
    let old = observe(&store, &id, "old page", 110);
    command(
        &store,
        "saveWatch",
        json!({"id":id,"url":"https://example.com/B"}),
        120,
    );
    observe(&store, &id, "new page", 130);
    assert!(
        store
            .command("markSeen", &json!({"id":id,"observationId":old.id}), 140)
            .is_err()
    );
    assert_eq!(
        store.watch(&id).unwrap().history.len(),
        2,
        "review history is retained across edits"
    );
}

#[test]
fn failed_incomplete_login_and_challenge_captures_never_advance_the_baseline() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/download", 100);
    let good = observe(&store, &id, "Version 1", 200);
    for (index, kind) in [
        "truncated",
        "tooLarge",
        "offOrigin",
        "login",
        "challenge",
        "empty",
        "http",
        "status",
    ]
    .iter()
    .enumerate()
    {
        let now = 300 + index as u64 * 100;
        let job = store.begin_check(&id, now).unwrap();
        let mut result = capture(&job, "Untrusted replacement");
        let expected = match *kind {
            "truncated" => {
                result.truncated = true;
                "tooLarge"
            }
            "tooLarge" => {
                result.text = "x".repeat(MAX_CAPTURE_BYTES + 1);
                "tooLarge"
            }
            "offOrigin" => {
                result.final_url = "https://other.example.com/download".into();
                "unavailable"
            }
            "login" => {
                result.final_url = "https://example.com/login?return=download".into();
                "signInRequired"
            }
            "challenge" => {
                result.title = "Just a moment...".into();
                "challenge"
            }
            "empty" => {
                result.text = " \n\t ".into();
                "selectorMissing"
            }
            "http" => {
                result.http_status = Some(503);
                "unavailable"
            }
            _ => {
                result.status = "verificationRequired".into();
                result.message = "Verification needed".into();
                "challenge"
            }
        };
        assert!(store.complete_check(&job, result, now + 1).unwrap());
        let watch = store.watch(&id).unwrap();
        assert_eq!(watch.state, expected, "case {kind}");
        assert_eq!(watch.latest.as_ref().unwrap(), &good, "case {kind}");
        assert_eq!(watch.history.len(), 1);
        assert_eq!(watch.seen_hash.as_ref(), Some(&good.hash));
    }
}

#[test]
fn full_content_digest_catches_changes_beyond_the_bounded_preview() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/long", 100);
    let prefix = "a".repeat(MAX_PREVIEW_BYTES + 100);
    let first = observe(&store, &id, &format!("{prefix}\nold ending"), 110);
    let second = observe(&store, &id, &format!("{prefix}\nnew ending"), 120);
    assert!(second.preview_truncated);
    assert_eq!(second.text_preview.len(), MAX_PREVIEW_BYTES);
    assert_eq!(second.text_preview, first.text_preview);
    assert_ne!(second.hash, first.hash);
    assert_eq!(row(&store, &id)["unread"], true);
    assert!(
        store
            .watch(&id)
            .unwrap()
            .history
            .last()
            .unwrap()
            .summary
            .contains("outside the stored preview")
    );
    let compact = row(&store, &id);
    assert!(compact.get("history").is_none());
    assert!(compact["latest"].get("textPreview").is_none());
    assert!(
        command(&store, "detail", json!({"id":id}), 130)["watch"]["latest"]["textPreview"]
            .is_string()
    );
}

#[test]
fn checks_use_bounded_history_due_times_backoff_and_single_active_ownership() {
    let (_dir, store) = fresh();
    assert!(!store.settings().background_checking);
    assert!(!store.settings().check_on_launch);
    assert!(!store.settings().notifications);
    command(
        &store,
        "settings",
        json!({"historyLimit":2,"defaultIntervalMinutes":1}),
        10,
    );
    let id = add(&store, "https://example.com/scheduled", 100);
    let job = store.begin_check(&id, 200).unwrap();
    assert!(store.begin_check(&id, 201).is_err());
    assert!(store.check_candidates(202, false).is_empty());
    assert!(store.fail_check(&job, "timeout", "Timed out", 300).unwrap());
    assert!(store.check_candidates(60_299, true).is_empty());
    assert_eq!(store.check_candidates(60_300, true), vec![id.clone()]);
    let retry = store.begin_check(&id, 60_300).unwrap();
    assert!(
        store
            .fail_check(&retry, "timeout", "Timed out again", 60_400)
            .unwrap()
    );
    assert_eq!(store.watch(&id).unwrap().retry_at, 180_400);
    observe(&store, &id, "baseline", 61_000);
    assert_eq!(store.watch(&id).unwrap().consecutive_errors, 0);
    assert_eq!(store.watch(&id).unwrap().retry_at, 0);
    observe(&store, &id, "second", 62_000);
    observe(&store, &id, "third", 63_000);
    assert_eq!(store.watch(&id).unwrap().history.len(), 2);
    let cancel = store.begin_check(&id, 64_000).unwrap();
    command(&store, "cancel", json!({"id":id}), 64_100);
    assert!(!store.is_current(&cancel));
    assert_eq!(store.watch(&id).unwrap().state, "cancelled");
    assert!(store.check_candidates(64_101, true).is_empty());
    let cancelled = store.begin_check(&id, 65_000).unwrap();
    assert!(
        store
            .fail_check(
                &cancelled,
                "cancelled",
                "Cancelled before navigation",
                65_100
            )
            .unwrap()
    );
    assert_eq!(
        store.watch(&id).unwrap().consecutive_errors,
        0,
        "cancellation must not inflate later network backoff"
    );
}

#[test]
fn session_origin_rotation_and_removal_keep_account_watches_separate_and_paused() {
    let (_dir, store) = fresh();
    assert!(
        store
            .command(
                "saveSession",
                &json!({"origin":"http://example.com","label":"insecure"}),
                1
            )
            .is_err()
    );
    let session = command(
        &store,
        "saveSession",
        json!({"origin":"https://EXAMPLE.com/account","label":"Main","remember":true}),
        100,
    );
    let sid = session["sessionId"].as_str().unwrap();
    assert_eq!(session["session"]["origin"], "https://example.com");
    assert!(
        store
            .command(
                "saveWatch",
                &json!({"url":"https://another.example.com/","sessionId":sid}),
                110
            )
            .is_err()
    );
    let id = command(
        &store,
        "saveWatch",
        json!({"url":"https://example.com/private","sessionId":sid}),
        120,
    )["watchId"]
        .as_str()
        .unwrap()
        .to_owned();
    for (index, action) in ["sessionOpened", "confirmSession", "resetSession"]
        .iter()
        .enumerate()
    {
        let now = 200 + index as u64 * 100;
        let job = store.begin_check(&id, now).unwrap();
        command(&store, action, json!({"id":sid}), now + 1);
        assert!(
            !store.is_current(&job),
            "{action} invalidates the old hidden browser check"
        );
        assert!(
            !store
                .complete_check(&job, capture(&job, "obsolete"), now + 2)
                .unwrap()
        );
    }
    let generation = store.session(sid).unwrap().storage_generation;
    let reset = command(
        &store,
        "saveSession",
        json!({"id":sid,"remember":false,"storageGeneration":generation}),
        600,
    );
    assert_eq!(reset["storageReset"], true);
    assert_eq!(
        store.session(sid).unwrap().storage_generation,
        generation + 1
    );
    assert!(
        store
            .command(
                "saveSession",
                &json!({"id":sid,"remember":true,"storageGeneration":generation}),
                610
            )
            .is_err()
    );
    command(&store, "removeSession", json!({"id":sid}), 700);
    let watch = store.watch(&id).unwrap();
    assert!(watch.session_id.is_none());
    assert!(watch.requires_session);
    assert!(!watch.enabled);
    assert!(store.begin_check(&id, 710).is_err());
    assert!(
        store
            .command("pauseWatch", &json!({"id":id,"enabled":true}), 720)
            .is_err()
    );
    command(
        &store,
        "saveWatch",
        json!({"id":id,"sessionId":null,"requiresSession":false,"enabled":true}),
        730,
    );
    assert!(store.begin_check(&id, 740).is_ok());
}

#[test]
fn removing_multiple_accounts_for_the_same_page_preserves_all_watch_histories() {
    let (_dir, store) = fresh();
    for label in ["First account", "Second account"] {
        let sid = command(
            &store,
            "saveSession",
            json!({"origin":"https://example.com","label":label}),
            100,
        )["sessionId"]
            .as_str()
            .unwrap()
            .to_owned();
        let id = command(
            &store,
            "saveWatch",
            json!({"url":"https://example.com/private","sessionId":sid}),
            110,
        )["watchId"]
            .as_str()
            .unwrap()
            .to_owned();
        observe(&store, &id, label, 120);
        command(&store, "removeSession", json!({"id":sid}), 130);
    }
    assert_eq!(store.snapshot()["counts"]["all"], 2);
    assert_eq!(store.snapshot()["counts"]["paused"], 2);
    assert!(
        store.snapshot()["watches"]
            .as_array()
            .unwrap()
            .iter()
            .all(|w| w["latest"].is_object())
    );
}

#[test]
fn startup_recovers_interrupted_checks_and_rotates_only_ephemeral_browser_generations() {
    let (dir, store) = fresh();
    let remembered = command(
        &store,
        "saveSession",
        json!({"origin":"https://example.com","label":"Remembered","remember":true}),
        100,
    )["sessionId"]
        .as_str()
        .unwrap()
        .to_owned();
    let ephemeral = command(
        &store,
        "saveSession",
        json!({"origin":"https://example.com","label":"Ephemeral","remember":false}),
        110,
    )["sessionId"]
        .as_str()
        .unwrap()
        .to_owned();
    let id = command(
        &store,
        "saveWatch",
        json!({"url":"https://example.com/private","sessionId":ephemeral}),
        120,
    )["watchId"]
        .as_str()
        .unwrap()
        .to_owned();
    let job = store.begin_check(&id, 130).unwrap();
    drop(store);
    let restarted = WatchStore::open(dir.path().join("watchtower.json"));
    assert_eq!(
        restarted.session(&remembered).unwrap().storage_generation,
        1
    );
    assert_eq!(restarted.session(&ephemeral).unwrap().storage_generation, 2);
    assert_eq!(restarted.watch(&id).unwrap().state, "interrupted");
    assert!(!restarted.is_current(&job));
    assert_eq!(restarted.snapshot()["readOnly"], false);
}

#[test]
fn spectator_open_never_mutates_rotates_or_recovers_owned_files() {
    let (dir, store) = fresh();
    let sid = command(
        &store,
        "saveSession",
        json!({"origin":"https://example.com","label":"Private"}),
        100,
    )["sessionId"]
        .as_str()
        .unwrap()
        .to_owned();
    let id = add(&store, "https://example.com/page", 110);
    let job = store.begin_check(&id, 120).unwrap();
    let path = dir.path().join("watchtower.json");
    let saved = std::fs::read(&path).unwrap();
    let spectator = WatchStore::open_read_only(&path);
    assert_eq!(spectator.snapshot()["readOnly"], true);
    assert_eq!(spectator.session(&sid).unwrap().storage_generation, 1);
    assert!(spectator.check_candidates(u64::MAX, false).is_empty());
    assert!(!spectator.is_current(&job));
    assert!(
        spectator
            .command(
                "saveWatch",
                &json!({"url":"https://example.com/other"}),
                130
            )
            .is_err()
    );
    assert!(
        spectator
            .command("recover", &json!({"mode":"retry"}), 140)
            .is_err()
    );
    assert!(
        spectator
            .command("recover", &json!({"mode":"reset","confirm":"RESET"}), 150)
            .is_err()
    );
    assert_eq!(std::fs::read(path).unwrap(), saved);
    assert!(store.is_current(&job));
}

#[test]
fn corrupt_state_is_preserved_until_explicit_recovery_and_backup() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("watchtower.json");
    let original = b"{partial saved JSON";
    std::fs::write(&path, original).unwrap();
    let store = WatchStore::open(&path);
    assert_eq!(store.snapshot()["readOnly"], true);
    assert!(
        store
            .command("saveWatch", &json!({"url":"https://example.com/page"}), 100)
            .is_err()
    );
    assert!(
        store
            .command("recover", &json!({"mode":"reset"}), 100)
            .is_err()
    );
    assert_eq!(std::fs::read(&path).unwrap(), original);
    let recovery = command(
        &store,
        "recover",
        json!({"mode":"reset","confirm":"RESET"}),
        200,
    );
    assert_eq!(store.snapshot()["readOnly"], false);
    let backup = PathBuf::from(recovery["backupPath"].as_str().unwrap());
    assert_eq!(std::fs::read(backup).unwrap(), original);
    assert!(read_document(&path).is_ok());
    add(&store, "https://example.com/page", 300);
}

#[test]
fn recovery_retry_uses_a_valid_repaired_file_and_invalidates_interrupted_ownership() {
    let (dir, store) = fresh();
    let id = add(&store, "https://example.com/page", 100);
    let job = store.begin_check(&id, 110).unwrap();
    let path = dir.path().join("watchtower.json");
    let valid = std::fs::read(&path).unwrap();
    drop(store);
    std::fs::write(&path, b"broken").unwrap();
    let recovering = WatchStore::open(&path);
    std::fs::write(&path, valid).unwrap();
    command(&recovering, "recover", json!({"mode":"retry"}), 200);
    assert_eq!(recovering.watch(&id).unwrap().state, "interrupted");
    assert!(!recovering.is_current(&job));
}

#[test]
fn failed_atomic_save_never_publishes_a_partial_memory_state() {
    let (dir, store) = fresh();
    let id = add(&store, "https://example.com/page", 100);
    let original = store.snapshot();
    let path = dir.path().join("watchtower.json");
    let backup = dir.path().join("known-good.json");
    std::fs::rename(&path, &backup).unwrap();
    std::fs::create_dir(&path).unwrap();
    assert!(
        store
            .command(
                "saveWatch",
                &json!({"id":id,"title":"Should not publish"}),
                110
            )
            .is_err()
    );
    assert_eq!(store.snapshot(), original);
    assert_eq!(
        read_document(&backup).unwrap().watches[0].title,
        "example.com"
    );
    std::fs::remove_dir(&path).unwrap();
    std::fs::rename(&backup, &path).unwrap();
    command(
        &store,
        "saveWatch",
        json!({"id":id,"title":"Recovered save"}),
        120,
    );
}

#[test]
fn import_preview_is_read_only_and_commit_is_atomic_and_revision_guarded() {
    let (_dir, store) = fresh();
    let document = export_document(json!([
        {"url":"https://example.com/A","title":"A"},
        {"url":"https://127.0.0.1/secret","title":"Invalid"}
    ]));
    let before = store.snapshot();
    let preview = command(
        &store,
        "import",
        json!({"document":document,"preview":true}),
        100,
    );
    assert_eq!(preview["counts"]["new"], 1);
    assert_eq!(preview["counts"]["invalid"], 1);
    assert_eq!(store.snapshot(), before);
    assert!(
        store
            .command("import", &json!({"document":document,"preview":false}), 110)
            .is_err()
    );
    assert_eq!(store.snapshot(), before);
    let document = export_document(json!([{"url":"https://example.com/A"}]));
    let preview = command(
        &store,
        "import",
        json!({"document":document,"preview":true}),
        120,
    );
    add(&store, "https://example.com/B", 130);
    assert!(
        store
            .command(
                "import",
                &json!({"document":document,"preview":false,"previewRevision":preview["revision"]}),
                140
            )
            .is_err()
    );
    let revision = store.snapshot()["revision"].clone();
    let result = command(
        &store,
        "import",
        json!({"document":document,"preview":false,"previewRevision":revision}),
        150,
    );
    assert_eq!(result["added"], 1);
    assert_eq!(store.snapshot()["counts"]["all"], 2);
}

#[test]
fn import_restores_trash_deduplicates_and_pauses_account_pages_without_reusing_identity() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/A", 100);
    observe(&store, &id, "old report", 110);
    command(&store, "removeWatch", json!({"id":id}), 120);
    let document = export_document(json!([
        {"url":"https://example.com/A","title":"Do not overwrite existing title"},
        {"url":"https://example.com/A#same"},
        {"url":"https://example.com/private","enabled":true,"requiresSession":true,"sessionId":"session-999","observationReport":{"hash":"forged"}}
    ]));
    let preview = command(
        &store,
        "import",
        json!({"document":document,"preview":true}),
        130,
    );
    assert_eq!(
        preview["counts"],
        json!({"new":1,"duplicates":1,"restore":1,"invalid":0,"sessionRequired":1})
    );
    let commit = command(
        &store,
        "import",
        json!({"document":document,"preview":false}),
        140,
    );
    assert_eq!(commit["added"], 1);
    assert_eq!(commit["restored"], 1);
    assert_eq!(commit["skipped"], 1);
    assert_eq!(row(&store, &id)["title"], "example.com");
    assert_eq!(row(&store, &id)["needsCheck"], true);
    let private = store.snapshot()["watches"]
        .as_array()
        .unwrap()
        .iter()
        .find(|w| w["requiresSession"] == true)
        .unwrap()
        .clone();
    assert_eq!(private["enabled"], false);
    assert!(private["sessionId"].is_null());
    assert!(private["latest"].is_null());
}

#[test]
fn export_excludes_browser_sessions_and_csv_escapes_spreadsheet_formulas() {
    let (_dir, store) = fresh();
    let sid = command(
        &store,
        "saveSession",
        json!({"origin":"https://example.com","label":"Work","remember":true}),
        100,
    )["sessionId"]
        .as_str()
        .unwrap()
        .to_owned();
    command(
        &store,
        "saveWatch",
        json!({"url":"https://example.com/private","sessionId":sid,"title":"=HYPERLINK(\"https://example.com\")","notes":"line 1\nline 2, quoted \"yes\""}),
        110,
    );
    let document = command(&store, "export", json!({}), 120)["document"].clone();
    assert!(document.get("sessions").is_none());
    assert!(document["watches"][0]["sessionId"].is_null());
    assert!(document["watches"][0].get("observationReport").is_none());
    assert_eq!(document["watches"][0]["requiresSession"], true);
    assert!(document.to_string().find("storageGeneration").is_none());
    let csv = command(&store, "export", json!({"format":"csv"}), 130);
    assert_eq!(csv["mimeType"], "text/csv");
    let text = csv["text"].as_str().unwrap();
    assert!(text.contains("\"'=HYPERLINK(\"\"https://example.com\"\")\""));
    assert!(text.contains("line 1\nline 2, quoted \"\"yes\"\""));
    assert!(!text.contains(&sid));
}

#[test]
fn complete_list_and_bounded_history_survive_bulk_import_and_restart() {
    let (dir, store) = fresh();
    let watches: Vec<_> = (0..1501)
        .map(|i| json!({"url":format!("https://example.com/page-{i}"),"title":format!("Page {i}")}))
        .collect();
    let document = export_document(json!(watches));
    let result = command(
        &store,
        "import",
        json!({"document":document,"preview":false}),
        100,
    );
    assert_eq!(result["added"], 1501);
    assert_eq!(store.snapshot()["watches"].as_array().unwrap().len(), 1501);
    assert_eq!(store.check_candidates(101, false).len(), 1501);
    drop(store);
    let reopened = WatchStore::open(dir.path().join("watchtower.json"));
    assert_eq!(
        reopened.snapshot()["watches"].as_array().unwrap().len(),
        1501
    );
    assert_eq!(reopened.snapshot()["counts"]["new"], 1501);
}

#[test]
fn serialized_concurrent_writes_preserve_every_page_and_unique_operation_identity() {
    let (dir, store) = fresh();
    let store = Arc::new(store);
    let handles: Vec<_> = (0..8)
        .map(|index| {
            let store = Arc::clone(&store);
            std::thread::spawn(move || {
                let id = add(&store, &format!("https://example.com/thread-{index}"), 100);
                observe(&store, &id, &format!("page {index}"), 200)
            })
        })
        .collect();
    let ids: HashSet<_> = handles.into_iter().map(|h| h.join().unwrap().id).collect();
    assert_eq!(ids.len(), 8);
    assert_eq!(store.snapshot()["counts"]["total"], 8);
    let persisted = read_document(&dir.path().join("watchtower.json")).unwrap();
    assert_eq!(persisted.watches.len(), 8);
    assert!(persisted.watches.iter().all(|w| w.latest.is_some()));
}

#[test]
fn stale_completion_during_mutation_lock_recheck_is_a_true_no_op() {
    let (dir, store) = fresh();
    let id = add(&store, "https://example.com/page", 100);
    let job = store.begin_check(&id, 110).unwrap();
    assert!(store.is_current(&job));
    command(&store, "cancel", json!({"id":id}), 120);
    let before = store.snapshot();
    let bytes = std::fs::read(dir.path().join("watchtower.json")).unwrap();
    // Exercise the second ownership check after an already-successful fast check.
    let accepted = store
        .mutate_if(|doc| Ok(current(doc, &job)), |changed| *changed)
        .unwrap();
    assert!(!accepted);
    assert_eq!(store.snapshot(), before);
    assert_eq!(
        std::fs::read(dir.path().join("watchtower.json")).unwrap(),
        bytes
    );
}

#[test]
fn bulk_seen_is_atomic_and_preserves_newer_unread_observations() {
    let (_dir, store) = fresh();
    let first = add(&store, "https://example.com/first", 100);
    let second = add(&store, "https://example.com/second", 100);
    observe(&store, &first, "baseline first", 110);
    observe(&store, &second, "baseline second", 110);
    let first_displayed = observe(&store, &first, "displayed first", 120);
    let second_displayed = observe(&store, &second, "displayed second", 120);
    observe(&store, &first, "newer first", 130);
    let before = store.snapshot();
    assert!(
        store
            .command(
                "markSeen",
                &json!({"observations":[
                    {"id":first,"observationId":first_displayed.id},
                    {"id":second,"observationId":"observation-999999"}
                ]}),
                140
            )
            .is_err()
    );
    assert_eq!(
        store.snapshot(),
        before,
        "a stale mixed batch must roll back every seen mark"
    );
    let result = command(
        &store,
        "markSeen",
        json!({"observations":[
            {"id":first,"observationId":first_displayed.id},
            {"id":second,"observationId":second_displayed.id}
        ]}),
        150,
    );
    assert_eq!(result["eventIds"].as_array().unwrap().len(), 2);
    assert_eq!(
        result["revision"].as_u64().unwrap(),
        before["revision"].as_u64().unwrap() + 1
    );
    assert_eq!(row(&store, &first)["unread"], true);
    assert_eq!(row(&store, &second)["unread"], false);
}

#[test]
fn new_session_namespaces_survive_metadata_loss_and_reset_without_reusing_browser_identity() {
    let (dir, store) = fresh();
    let payload = json!({"origin":"https://example.com","label":"Account","remember":true});
    let first = command(&store, "saveSession", payload.clone(), 100)["sessionId"]
        .as_str()
        .unwrap()
        .to_owned();
    let path = dir.path().join("watchtower.json");
    drop(store);
    std::fs::remove_file(&path).unwrap();
    let replacement = WatchStore::open(&path);
    // Repeat the same timestamp and counter deliberately: wall-clock-based
    // identities alone would reattach the first remembered browser profile.
    let second = command(&replacement, "saveSession", payload.clone(), 100)["sessionId"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_ne!(first, second);
    assert_eq!(record_id(&first, "session-").unwrap(), 1);
    assert_eq!(record_id(&second, "session-").unwrap(), 1);
    drop(replacement);
    std::fs::write(&path, b"corrupt metadata").unwrap();
    let reset = WatchStore::open(&path);
    command(
        &reset,
        "recover",
        json!({"mode":"reset","confirm":"RESET"}),
        100,
    );
    let third = command(&reset, "saveSession", payload, 100)["sessionId"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_ne!(third, first);
    assert_ne!(third, second);
    assert_eq!(record_id(&third, "session-").unwrap(), 1);
    assert_eq!(reset.session(&third).unwrap().storage_generation, 1);
    drop(reset);
    assert!(
        WatchStore::open(&path).session(&third).is_some(),
        "the fresh identity must survive a normal restart"
    );
}

#[test]
fn legacy_session_ids_load_while_namespaced_ids_preserve_sequence_validation() {
    let (dir, store) = fresh();
    let result = command(
        &store,
        "saveSession",
        json!({"origin":"https://example.com","label":"Legacy","remember":true}),
        100,
    );
    assert!(
        result["sessionId"]
            .as_str()
            .unwrap()
            .starts_with("session-1-")
    );
    let path = dir.path().join("watchtower.json");
    drop(store);
    let mut saved: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    saved["sessions"][0]["id"] = json!("session-1");
    std::fs::write(&path, serde_json::to_vec(&saved).unwrap()).unwrap();
    let reopened = WatchStore::open(&path);
    assert_eq!(reopened.snapshot()["readOnly"], false);
    assert!(reopened.session("session-1").is_some());
    let new = command(
        &reopened,
        "saveSession",
        json!({"origin":"https://example.com","label":"New","remember":true}),
        100,
    )["sessionId"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(record_id(&new, "session-").unwrap(), 2);
    drop(reopened);
    let mut saved: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    saved["nextId"] = json!(2);
    std::fs::write(&path, serde_json::to_vec(&saved).unwrap()).unwrap();
    assert_eq!(
        WatchStore::open(&path).snapshot()["readOnly"],
        true,
        "the nonce cannot hide a reused sequence counter"
    );
}
