use super::*;
use tempfile::TempDir;
fn fresh() -> (TempDir, WatchStore) {
    let dir = tempfile::tempdir().unwrap();
    let store = WatchStore::open(dir.path().join("watchtower.json"));
    (dir, store)
}
fn run(store: &WatchStore, action: &str, value: Value, now: u64) -> Value {
    store.command(action, &value, now).unwrap()
}
fn add(store: &WatchStore, url: &str, profile: &str, item: &str) -> String {
    run(store,"saveWatch",json!({"url":url,"association":{"profileId":profile,"itemId":item,"name":"Exact mod","sourceUrl":url}}),10)["watchId"].as_str().unwrap().into()
}
fn source(
    store: &WatchStore,
    id: &str,
    creator: Option<&str>,
    preferred: bool,
    runtime: &str,
    now: u64,
) {
    let generation = store.watch(id).unwrap().generation;
    run(
        store,
        "saveSourceBinding",
        json!({"watchId":id,"generation":generation,"creatorId":creator,"game":"SkyrimSE","runtime":runtime,"variant":"default","compatibilityConfirmed":true,"contentIntent":"update","preferred":preferred}),
        now,
    );
}
fn choice(store: &WatchStore, id: &str, key: &str, kind: &str, ignored: bool, now: u64) {
    run(
        store,
        "saveFileDecision",
        json!({"watchId":id,"generation":store.watch(id).unwrap().generation,"sourceFileKey":key,"name":"Release.zip","kind":kind,"ignored":ignored}),
        now,
    );
}
fn baseline(store: &WatchStore, id: &str, now: u64) {
    let job = store.begin_check(id, now).unwrap();
    assert!(
        store
            .complete_check(
                &job,
                PageObservation {
                    final_url: job.url.clone(),
                    title: "Release".into(),
                    text: "A complete original release description".into(),
                    ..Default::default()
                },
                now + 1
            )
            .unwrap()
    );
}
fn session(store: &WatchStore, label: &str) -> String {
    run(
        store,
        "saveSession",
        json!({"origin":"https://www.patreon.com","label":label,"remember":true}),
        20,
    )["sessionId"]
        .as_str()
        .unwrap()
        .into()
}
fn link_session(store: &WatchStore, id: &str, session: &str) {
    let watch = store.watch(id).unwrap();
    run(
        store,
        "saveWatch",
        json!({"id":id,"url":watch.url,"sessionId":session,"requiresSession":true}),
        30,
    );
}
#[test]
fn preferred_source_is_scoped_to_exact_profile_mod_game_runtime_and_variant() {
    let (_dir, store) = fresh();
    let a = add(&store, "https://a.example.com/mod", "profile-a", "mod");
    let b = add(&store, "https://b.example.com/mod", "profile-a", "mod");
    let different = add(&store, "https://c.example.com/mod", "profile-b", "mod");
    source(&store, &a, None, true, "1.6", 20);
    source(&store, &different, None, true, "1.6", 21);
    let old = store.begin_check(&a, 22).unwrap();
    source(&store, &b, None, true, "1.6", 23);
    assert!(!store.is_current(&old));
    assert!(!store.source_binding(&a).unwrap().preferred);
    assert!(store.source_binding(&b).unwrap().preferred);
    assert!(store.source_binding(&different).unwrap().preferred);
    source(&store, &a, None, true, "1.5", 24);
    assert!(store.source_binding(&b).unwrap().preferred);
}
#[test]
fn newer_incompatible_or_locked_source_never_replaces_explicit_preference() {
    let (_dir, store) = fresh();
    let preferred = add(&store, "https://a.example.com/mod", "profile", "mod");
    let other = add(&store, "https://b.example.com/mod", "profile", "mod");
    source(&store, &preferred, None, true, "1.5", 20);
    source(&store, &other, None, true, "1.6", 21);
    baseline(&store, &preferred, 30);
    baseline(&store, &other, 9999);
    let result = run(&store, "detail", json!({"id":preferred}), 10001);
    let rows = result["watch"]["sourceAlternatives"].as_array().unwrap();
    assert_eq!(rows.len(), 2);
    let row = rows.iter().find(|r| r["watchId"] == other).unwrap();
    assert_eq!(row["compatible"], false);
    assert!(store.source_binding(&preferred).unwrap().preferred);
    let job = store.begin_check(&other, 10002).unwrap();
    store
        .fail_check(&job, "signInRequired", "Locked page", 10003)
        .unwrap();
    assert!(store.source_binding(&preferred).unwrap().preferred);
}
#[test]
fn exact_file_ignore_and_new_content_classification_survive_restart_without_inheriting_names() {
    let (dir, store) = fresh();
    let id = add(&store, "https://example.com/mod", "profile", "mod");
    let key = "a".repeat(64);
    let new_key = "b".repeat(64);
    baseline(&store, &id, 20);
    let latest = store.latest_hash(&id);
    choice(&store, &id, &key, "update", true, 30);
    choice(&store, &id, &new_key, "newContent", false, 31);
    assert_eq!(store.latest_hash(&id), latest);
    assert!(store.source_file_decision(&id, &key).unwrap().ignored);
    assert!(
        store
            .source_file_decision(&id, &new_key)
            .unwrap()
            .association
            .is_none()
    );
    assert!(store.source_file_decision(&id, &"c".repeat(64)).is_none());
    drop(store);
    let store = WatchStore::open(dir.path().join("watchtower.json"));
    assert!(!store.snapshot()["readOnly"].as_bool().unwrap());
    assert!(store.source_file_decision(&id, &key).unwrap().ignored);
    assert_eq!(
        store.source_file_decision(&id, &new_key).unwrap().kind,
        "newContent"
    );
}
#[test]
fn source_rebind_preserves_but_never_reuses_old_update_ownership() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/mod", "profile", "mod");
    let key = "a".repeat(64);
    source(&store, &id, None, true, "1.6", 20);
    choice(&store, &id, &key, "update", false, 21);
    let old = store.begin_check(&id, 22).unwrap();
    run(
        &store,
        "saveWatch",
        json!({"id":id,"url":"https://example.com/mod","association":{"profileId":"profile","itemId":"different","name":"Similar name","sourceUrl":""}}),
        23,
    );
    assert!(!store.is_current(&old));
    assert!(store.source_binding(&id).is_none());
    assert!(store.source_file_decision(&id, &key).is_none());
    let details = run(&store, "detail", json!({"id":id}), 24);
    assert_eq!(details["watch"]["sourceFiles"].as_array().unwrap().len(), 1);
}
#[test]
fn creator_site_mute_is_exact_and_cancels_in_flight_work_without_erasing_observations() {
    let (_dir, store) = fresh();
    let a = add(&store, "https://a.example.com/mod", "profile", "mod");
    let b = add(&store, "https://b.example.com/mod", "profile", "mod");
    let creator = run(
        &store,
        "saveCreator",
        json!({"profileId":"profile","name":"Creator","offerNewContent":false,"mutedOrigins":[]}),
        20,
    )["creatorId"]
        .as_str()
        .unwrap()
        .to_owned();
    source(&store, &a, Some(&creator), false, "1.6", 21);
    source(&store, &b, Some(&creator), false, "1.6", 22);
    baseline(&store, &a, 30);
    let old = store.begin_check(&a, 40).unwrap();
    let previous = store.latest_hash(&a);
    run(
        &store,
        "saveCreator",
        json!({"id":creator,"revision":1,"profileId":"profile","name":"Creator","offerNewContent":true,"mutedOrigins":["https://a.example.com/path"]}),
        50,
    );
    assert!(!store.is_current(&old));
    assert!(!store.watch_eligible(&a));
    assert!(store.watch_eligible(&b));
    assert_eq!(store.latest_hash(&a), previous);
    assert!(store.creator_offers_new_content(&b));
}
#[test]
fn account_verification_holds_shared_work_and_confirmation_never_asserts_login() {
    let (_dir, store) = fresh();
    let a = add(
        &store,
        "https://www.patreon.com/posts/111",
        "profile",
        "one",
    );
    let b = add(
        &store,
        "https://www.patreon.com/posts/222",
        "profile",
        "two",
    );
    let c = add(
        &store,
        "https://www.patreon.com/posts/333",
        "profile",
        "three",
    );
    let session_a = session(&store, "Primary");
    let session_b = session(&store, "Other");
    link_session(&store, &a, &session_a);
    link_session(&store, &b, &session_a);
    link_session(&store, &c, &session_b);
    baseline(&store, &a, 40);
    let previous = store.latest_hash(&a);
    let first = store.begin_check(&a, 50).unwrap();
    let second = store.begin_check(&b, 51).unwrap();
    assert!(store.fail_check(&first, "challenge", "Verify", 52).unwrap());
    assert!(!store.is_current(&second));
    assert!(!store.watch_eligible(&a));
    assert!(!store.watch_eligible(&b));
    assert!(store.watch_eligible(&c));
    assert_eq!(store.latest_hash(&a), previous);
    run(&store, "sessionOpened", json!({"id":session_a}), 59);
    assert!(!store.watch_eligible(&a));
    run(&store, "confirmSession", json!({"id":session_a}), 60);
    assert!(store.watch_eligible(&a));
    let snapshot = store.snapshot();
    let access = &snapshot["sourcePreferences"]["accounts"][0];
    assert_eq!(access["loginState"], "unknown");
    assert_eq!(access["verificationHold"], false);
}
#[test]
fn account_observations_reject_old_generations_and_do_not_treat_login_as_entitlement() {
    let (_dir, store) = fresh();
    let session = session(&store, "Primary");
    let generation = store.session(&session).unwrap().storage_generation;
    assert!(
        store
            .observe_session_access(&session, generation, "signedIn", "restricted", "", 50)
            .unwrap()
    );
    let snapshot = store.snapshot();
    assert_eq!(
        snapshot["sourcePreferences"]["accounts"][0]["status"],
        "restricted"
    );
    assert!(
        !store
            .observe_session_access(&session, generation, "signedIn", "pageAccessible", "", 49)
            .unwrap()
    );
    assert!(
        !store
            .observe_session_access(
                &session,
                generation + 1,
                "signedIn",
                "pageAccessible",
                "",
                51
            )
            .unwrap()
    );
    run(&store, "resetSession", json!({"id":session}), 60);
    assert!(
        !store
            .observe_session_access(&session, generation, "signedIn", "pageAccessible", "", 61)
            .unwrap()
    );
    assert!(
        store.snapshot()["sourcePreferences"]["accounts"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}
#[test]
fn a_repeated_same_session_access_observation_does_not_rewrite_the_store() {
    let (_dir, store) = fresh();
    let session = session(&store, "Primary");
    let generation = store.session(&session).unwrap().storage_generation;
    assert!(
        store
            .observe_session_access(&session, generation, "signedIn", "pageAccessible", "", 50)
            .unwrap()
    );
    let revision = store.snapshot()["revision"].clone();
    assert!(
        !store
            .observe_session_access(&session, generation, "signedIn", "pageAccessible", "", 51)
            .unwrap()
    );
    assert_eq!(store.snapshot()["revision"], revision);
}
#[test]
fn source_choices_require_current_generation_and_preserve_every_file_decision() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/mod", "profile", "mod");
    let generation = store.watch(&id).unwrap().generation;
    choice(&store, &id, &"a".repeat(64), "newContent", false, 30);
    assert!(store.command("saveFileDecision",&json!({"watchId":id,"generation":generation,"sourceFileKey":"b".repeat(64),"name":"Same name.zip","kind":"update","ignored":false}),31).is_err());
    assert_eq!(
        store.snapshot()["sourcePreferences"]["fileDecisionCount"],
        1
    );
    run(
        &store,
        "clearFileDecision",
        json!({"watchId":id,"generation":store.watch(&id).unwrap().generation,"sourceFileKey":"a".repeat(64)}),
        32,
    );
    assert!(store.source_file_decision(&id, &"a".repeat(64)).is_none());
}

#[test]
fn native_review_requires_confirmed_matching_game_and_runtime() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/mod", "profile", "mod");
    assert!(
        store
            .validate_source_compatibility(&id, "skyrimspecialedition", "1.6.1170")
            .is_err()
    );
    source(&store, &id, None, false, "1.6.1170", 20);
    assert!(
        store
            .validate_source_compatibility(&id, "skyrimspecialedition", "1.6.1170")
            .is_ok()
    );
    assert!(
        store
            .validate_source_compatibility(&id, "skyrimvr", "1.6.1170")
            .is_err()
    );
    assert!(
        store
            .validate_source_compatibility(&id, "skyrimspecialedition", "1.5.97")
            .is_err()
    );
    source(&store, &id, None, false, "Not applicable", 21);
    assert!(
        store
            .validate_source_compatibility(&id, "skyrimspecialedition", "1.6.1170")
            .is_err()
    );
    assert!(
        store
            .validate_source_compatibility(&id, "skyrimspecialedition", "unknown")
            .is_ok()
    );
}

#[test]
fn export_import_preserves_creator_groups_and_exact_file_choices_without_account_observations() {
    let (_dir, store) = fresh();
    let a = add(&store, "https://a.example.com/mod", "profile", "mod");
    let b = add(&store, "https://b.example.com/mod", "profile", "mod");
    let creator=run(&store,"saveCreator",json!({"profileId":"profile","name":"Creator","offerNewContent":true,"mutedOrigins":["https://a.example.com"]}),20)["creatorId"].as_str().unwrap().to_owned();
    source(&store, &a, Some(&creator), true, "1.6", 21);
    source(&store, &b, Some(&creator), false, "1.6", 22);
    choice(&store, &a, &"a".repeat(64), "update", true, 23);
    let exported = run(&store, "export", json!({}), 30)["document"].clone();
    assert!(exported.get("accounts").is_none());
    let (_dir2, imported) = fresh();
    let preview = run(
        &imported,
        "import",
        json!({"document":exported,"preview":true}),
        31,
    );
    assert_eq!(preview["counts"]["invalid"], 0);
    run(
        &imported,
        "import",
        json!({"document":exported,"preview":false}),
        32,
    );
    let snap = imported.snapshot();
    assert_eq!(
        snap["sourcePreferences"]["creators"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(snap["sourcePreferences"]["fileDecisionCount"], 1);
    let id = snap["watches"]
        .as_array()
        .unwrap()
        .iter()
        .find(|w| w["url"] == "https://a.example.com/mod")
        .unwrap()["id"]
        .as_str()
        .unwrap();
    assert!(
        imported
            .source_file_decision(id, &"a".repeat(64))
            .unwrap()
            .ignored
    );
    assert!(imported.creator_offers_new_content(id));
    assert!(!imported.watch_eligible(id));
}

#[test]
fn hidden_resource_policy_change_preserves_last_good_until_a_fresh_complete_baseline() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/mod", "profile", "mod");
    baseline(&store, &id, 20);
    let original = store.watch(&id).unwrap().latest.unwrap();
    let old = store.begin_check(&id, 30).unwrap();
    run(
        &store,
        "settings",
        json!({"filterHiddenResources":true}),
        31,
    );
    assert!(!store.is_current(&old));
    assert_eq!(store.watch(&id).unwrap().latest.unwrap(), original);
    let failed = store.begin_check(&id, 32).unwrap();
    assert!(failed.filtered_resources);
    store
        .fail_check(&failed, "incomplete", "Required resource failed", 33)
        .unwrap();
    assert_eq!(store.watch(&id).unwrap().latest.unwrap(), original);
    let next = store.begin_check(&id, 34).unwrap();
    store
        .complete_check(
            &next,
            PageObservation {
                final_url: next.url.clone(),
                text: "A different complete filtered description".into(),
                filtered_resources: true,
                ..Default::default()
            },
            35,
        )
        .unwrap();
    let watch = store.watch(&id).unwrap();
    assert_eq!(watch.state, "baseline");
    assert_eq!(watch.history.last().unwrap().kind, "baseline");
    assert!(watch.latest.as_ref().unwrap().filtered_resources);
    assert!(!has_changes(&watch));
}
#[test]
fn capture_under_a_different_resource_policy_cannot_advance_the_baseline() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/mod", "profile", "mod");
    baseline(&store, &id, 20);
    let old = store.latest_hash(&id);
    let next = store.begin_check(&id, 30).unwrap();
    store
        .complete_check(
            &next,
            PageObservation {
                final_url: next.url.clone(),
                text: "Wrong browser policy".into(),
                filtered_resources: true,
                ..Default::default()
            },
            31,
        )
        .unwrap();
    assert_eq!(store.latest_hash(&id), old);
}

#[test]
fn a_restricted_release_does_not_advance_the_baseline_or_hold_the_account_as_signed_out() {
    let (_dir, store) = fresh();
    let a = add(
        &store,
        "https://www.patreon.com/posts/111",
        "profile",
        "one",
    );
    let b = add(
        &store,
        "https://www.patreon.com/posts/222",
        "profile",
        "two",
    );
    let account = session(&store, "Primary");
    link_session(&store, &a, &account);
    link_session(&store, &b, &account);
    baseline(&store, &a, 40);
    let original = store.latest_hash(&a);
    let job = store.begin_check(&a, 50).unwrap();
    store
        .complete_check(
            &job,
            PageObservation {
                final_url: job.url.clone(),
                status: "accessRestricted".into(),
                message: "Membership access is restricted".into(),
                text: "Join to unlock".into(),
                ..Default::default()
            },
            51,
        )
        .unwrap();
    assert_eq!(store.latest_hash(&a), original);
    assert_eq!(store.watch(&a).unwrap().state, "unavailable");
    assert!(store.watch_eligible(&b));
    assert!(
        store.snapshot()["sourcePreferences"]["accounts"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}

#[test]
fn resource_policy_aba_establishes_a_new_context_instead_of_reusing_old_observation_ownership() {
    let (_dir, store) = fresh();
    let id = add(&store, "https://example.com/mod", "profile", "mod");
    baseline(&store, &id, 20);
    let original = store.watch(&id).unwrap().latest.unwrap().id;
    run(
        &store,
        "settings",
        json!({"filterHiddenResources":true}),
        30,
    );
    run(
        &store,
        "settings",
        json!({"filterHiddenResources":false}),
        31,
    );
    baseline(&store, &id, 32);
    let watch = store.watch(&id).unwrap();
    assert_ne!(watch.latest.as_ref().unwrap().id, original);
    assert_eq!(
        watch.history.last().unwrap().context_generation,
        watch.context_generation
    );
    assert_eq!(watch.state, "baseline");
}
