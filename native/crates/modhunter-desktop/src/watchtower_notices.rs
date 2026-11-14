//! Discreet notifications carry an opaque event identity, never target paths,
//! credentials, page addresses, or authority to install anything.
use super::*;
use modhunter_core::acquisition::{atomic_json, reject_link};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use tauri_plugin_notification::{Action, ActionType, NotificationExt};

#[path = "watchtower_notice_smoke.rs"]
mod smoke_fixture;
pub(super) fn smoke_check(app: &tauri::AppHandle, manager: &WebviewWindow) -> Result<Value> {
    smoke_fixture::check(app, manager)
}

const OWNER: &str = "com.modhunter.desktop.notifications";
const ACTION_TYPE: &str = "modhunter-review-current-item";
const MAX_NOTICES: usize = 256;
const MAX_DEDUP: usize = 10_000;
const MAX_DOCUMENT_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct NativeContext {
    profile_id: String,
    game: String,
    game_runtime: String,
    execution_epoch: u64,
    native_lease: String,
    native_generation: u64,
}
impl NativeContext {
    fn read(value: &Value) -> Result<Self> {
        let result = Self {
            profile_id: text_field(value, "profileId", 1000)?,
            game: text_field(value, "game", 160)?,
            game_runtime: value["gameRuntime"]
                .as_str()
                .filter(|value| value.len() <= 240 && !value.chars().any(char::is_control))
                .ok_or("The current native game runtime is unavailable.")?
                .into(),
            execution_epoch: value["executionEpoch"]
                .as_u64()
                .ok_or("Missing native epoch.")?,
            native_lease: text_field(value, "nativeLease", 32)?,
            native_generation: value["nativeGeneration"]
                .as_u64()
                .ok_or("Missing native generation.")?,
        };
        if !hex(&result.native_lease, 32) {
            return Err("The current native service identity is unavailable.".into());
        }
        Ok(result)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
enum Target {
    Page {
        watch_id: String,
        generation: u64,
        context_generation: u64,
        observation_id: String,
        hash: String,
    },
    Updater {
        endpoint: String,
        connection_epoch: u64,
        context: NativeContext,
        item_id: String,
        proof_digest: String,
    },
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Notice {
    id: String,
    content_key: String,
    target: Target,
    created_at: u64,
    seen: bool,
    dismissed: bool,
    os_id: i32,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Document {
    owner: String,
    schema: u32,
    next_os_id: i32,
    last_os_at: u64,
    notices: Vec<Notice>,
    deduplicated: BTreeMap<String, u64>,
}
impl Default for Document {
    fn default() -> Self {
        Self {
            owner: OWNER.into(),
            schema: 1,
            next_os_id: 1,
            last_os_at: 0,
            notices: Vec::new(),
            deduplicated: BTreeMap::new(),
        }
    }
}
struct Ledger {
    path: PathBuf,
    document: Document,
    error: String,
    status: String,
    focus: Option<(String, u64)>,
    focus_sequence: u64,
}
struct Notices {
    ledger: Mutex<Ledger>,
    refreshing: AtomicBool,
    last_refresh: Mutex<Option<Instant>>,
}
fn hex(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}
fn text_field(value: &Value, key: &str, max: usize) -> Result<String> {
    value[key]
        .as_str()
        .filter(|value| {
            !value.is_empty() && value.len() <= max && !value.chars().any(char::is_control)
        })
        .map(str::to_owned)
        .ok_or_else(|| format!("Missing or invalid notification {key}."))
}
fn digest(value: &impl Serialize) -> Result<String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).map_err(err)?)
    ))
}
fn event_id(target: &Target) -> Result<String> {
    Ok(format!("notice-{}", digest(target)?))
}
fn target_slot(target: &Target) -> String {
    match target {
        Target::Page { watch_id, .. } => format!("page:{watch_id}"),
        Target::Updater {
            context, item_id, ..
        } => format!("update:{}:{item_id}", context.profile_id),
    }
}
fn validate_document(document: &Document) -> Result<()> {
    if document.schema != 1
        || document.owner != OWNER
        || document.next_os_id < 1
        || document.notices.len() > MAX_NOTICES
        || document.deduplicated.len() > MAX_DEDUP
    {
        return Err("Notification history is unsupported; its original file was preserved.".into());
    }
    let mut ids = BTreeSet::new();
    for notice in &document.notices {
        if notice.id != event_id(&notice.target)?
            || !ids.insert(&notice.id)
            || !hex(&notice.content_key, 64)
            || notice.os_id < 1
        {
            return Err("Notification history has an ambiguous identity and was preserved.".into());
        }
    }
    if document.deduplicated.keys().any(|key| !hex(key, 64)) {
        return Err("Notification deduplication history is invalid and was preserved.".into());
    }
    Ok(())
}
fn load(path: &Path) -> Result<Document> {
    if !path.exists() {
        return Ok(Document::default());
    }
    reject_link(path)?;
    let mut bytes = Vec::new();
    File::open(path)
        .map_err(err)?
        .take(MAX_DOCUMENT_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(err)?;
    if bytes.len() as u64 > MAX_DOCUMENT_BYTES {
        return Err("Notification history exceeds its complete bound.".into());
    }
    let document = serde_json::from_slice(&bytes).map_err(err)?;
    validate_document(&document)?;
    Ok(document)
}
fn commit(ledger: &mut Ledger, draft: Document) -> Result<()> {
    if !ledger.error.is_empty() {
        return Err(ledger.error.clone());
    }
    validate_document(&draft)?;
    atomic_json(&ledger.path, &draft)?;
    ledger.document = draft;
    Ok(())
}
fn register_action_type(app: &tauri::AppHandle) -> Result<()> {
    app.notification()
        .register_action_types(vec![
            ActionType::builder(ACTION_TYPE)
                .actions(vec![
                    Action::builder("review", super::localized(app, "Review current item")).build(),
                ])
                .build(),
        ])
        .map_err(err)
}
pub(super) fn setup(app: &tauri::AppHandle, path: PathBuf) {
    let (document, error) = match load(&path) {
        Ok(document) => (document, String::new()),
        Err(error) => (Document::default(), error),
    };
    app.manage(Notices {
        ledger: Mutex::new(Ledger {
            path,
            document,
            error,
            status: String::new(),
            focus: None,
            focus_sequence: 0,
        }),
        refreshing: AtomicBool::new(false),
        last_refresh: Mutex::new(None),
    });
    if let Err(error) = register_action_type(app) {
        set_status(
            app,
            &format!("Desktop notification actions are unavailable: {error}"),
        );
    }
    let handle = app.clone();
    if let Err(error) = app.notification().on_action(move |action| {
        if !matches!(action.action_id(), "tap" | "review") { return; }
        let Some(notification) = action.notification() else { return; };
        let Some(id) = notification.extra().get("modhunterNotice").and_then(Value::as_str) else { return; };
        let os_id = notification.id();
        let app = handle.clone();
        let id = id.to_owned();
        std::thread::spawn(move || {
            let result = (|| -> Result<()> {
                let notice = find(&app, &id)?;
                if notice.os_id != os_id { return Err("This desktop notification was superseded.".into()); }
                request_focus(&app, &notice)
            })();
            if result.is_err() {
                set_status(&app, "This notification is no longer current. Refresh notices to review current facts.");
                notify(&app);
            }
        });
    }) {
        set_status(app, &format!("Desktop click handling is unavailable; use the notices list: {error}"));
    }
}
fn set_status(app: &tauri::AppHandle, status: &str) {
    if let Some(state) = app.try_state::<Notices>() {
        state
            .ledger
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .status = status.into();
    }
}
pub(super) fn snapshot(app: &tauri::AppHandle) -> Value {
    let Some(state) = app.try_state::<Notices>() else {
        return json!({"rows":[]});
    };
    let ledger = state.ledger.lock().unwrap_or_else(|e| e.into_inner());
    json!({"rows":ledger.document.notices.iter().rev().filter(|notice|!notice.dismissed).map(|notice| {
        let page = matches!(notice.target,Target::Page{..});
        json!({"id":notice.id,"kind":if page{"page"}else{"updater"},"createdAt":notice.created_at,"seen":notice.seen,
            "title":if page{"A watched page changed"}else{"A version candidate was recorded"},
            "body":"Open to recheck and review the exact current item.","actionLabel":"Review current item"})
    }).collect::<Vec<_>>(),"error":ledger.error,"status":ledger.status,
        "refreshing":state.refreshing.load(Ordering::Acquire),
        "focusId":ledger.focus.as_ref().map(|focus|&focus.0),"focusSequence":ledger.focus.as_ref().map(|focus|focus.1),
        "scope":"Desktop notices omit names and addresses. Every action rechecks its exact item; it never installs or confirms file access.",
        "desktopActionScope":"Windows forwards clicks while the toast is on screen. Notices remain actionable here after the toast expires."})
}
fn add(
    document: &mut Document,
    target: Target,
    content_key: String,
    at: u64,
) -> Result<Option<(Notice, bool)>> {
    let id = event_id(&target)?;
    if document.notices.iter().any(|notice| notice.id == id) {
        return Ok(None);
    }
    let fresh_content = !document.deduplicated.contains_key(&content_key);
    let slot = target_slot(&target);
    document
        .notices
        .retain(|notice| target_slot(&notice.target) != slot);
    let notice = Notice {
        id,
        content_key: content_key.clone(),
        target,
        created_at: at,
        seen: false,
        dismissed: false,
        os_id: document.next_os_id,
    };
    document.next_os_id = document
        .next_os_id
        .checked_add(1)
        .filter(|value| *value > 0)
        .ok_or("Notification identities exhausted.")?;
    document.notices.push(notice.clone());
    if document.notices.len() > MAX_NOTICES {
        document.notices.remove(0);
    }
    document.deduplicated.insert(content_key, at);
    if document.deduplicated.len() > MAX_DEDUP {
        let oldest = document
            .deduplicated
            .iter()
            .min_by_key(|(_, at)| **at)
            .map(|(key, _)| key.clone())
            .unwrap();
        document.deduplicated.remove(&oldest);
    }
    Ok(Some((notice, fresh_content)))
}
fn record(app: &tauri::AppHandle, targets: Vec<(Target, String)>) -> Result<usize> {
    app.state::<WatchRuntime>().writable()?;
    let state = app.state::<Notices>();
    let enabled =
        app.state::<WatchRuntime>().store.settings().notifications && !privacy::is_hidden(app);
    let at = now();
    let mut ledger = state.ledger.lock().map_err(err)?;
    let mut draft = ledger.document.clone();
    let mut count = 0;
    let mut desktop = None;
    for (target, key) in targets {
        if let Some((notice, fresh)) = add(&mut draft, target, key, at)? {
            count += 1;
            if desktop.is_none()
                && enabled
                && fresh
                && at.saturating_sub(draft.last_os_at) >= 60_000
            {
                desktop = Some(notice);
                draft.last_os_at = at;
            }
        }
    }
    if count > 0 {
        commit(&mut ledger, draft)?;
    }
    drop(ledger);
    if let Some(notice) = desktop
        && !privacy::is_hidden(app)
        && app.state::<WatchRuntime>().store.settings().notifications
        && validate_target(app, &notice.target).is_ok()
    {
        // The vendor delivers on-screen Windows clicks through on_action. show()
        // only acknowledges dispatch, so this does not claim OS display or click.
        // Registration replaces this owned type, keeping action captions in the
        // current locale even after preferences changed during this app run.
        if register_action_type(app).is_err()
            || app
                .notification()
                .builder()
                .id(notice.os_id)
                .title(super::localized(app, "Modhunter"))
                .body(super::localized(
                    app,
                    "A verified change is ready to review. Open its current item in Modhunter.",
                ))
                .action_type_id(ACTION_TYPE)
                .extra("modhunterNotice", &notice.id)
                .show()
                .is_err()
        {
            set_status(
                app,
                "The desktop notice could not be prepared. The exact item remains available here.",
            );
        }
    }
    if count > 0 {
        notify(app);
    }
    Ok(count)
}
fn page_target(store: &WatchStore, watch_id: &str) -> Result<Target> {
    if !store
        .check_candidates(now(), false)
        .iter()
        .any(|id| id == watch_id)
    {
        return Err("This page is paused, muted, removed, or no longer eligible.".into());
    }
    let watch = store
        .watch(watch_id)
        .ok_or("This watched page was removed.")?;
    let observation = watch
        .latest
        .as_ref()
        .ok_or("This page has no complete observation.")?;
    if watch.fresh_check_required || !matches!(watch.state.as_str(), "changed" | "unchanged") {
        return Err(
            "This page needs a successful current check before its notification can open.".into(),
        );
    }
    Ok(Target::Page {
        watch_id: watch.id,
        generation: watch.generation,
        context_generation: watch.context_generation,
        observation_id: observation.id.clone(),
        hash: observation.hash.clone(),
    })
}
pub(super) fn page_changed(app: &tauri::AppHandle, job: &CheckJob, previous: Option<&str>) {
    let Some(previous) = previous else {
        return;
    };
    let result = (|| -> Result<()> {
        let target = page_target(&app.state::<WatchRuntime>().store, &job.watch_id)?;
        let Target::Page {
            generation,
            context_generation,
            hash,
            ..
        } = &target
        else {
            unreachable!()
        };
        if *generation != job.generation || previous == hash {
            return Ok(());
        }
        let key = digest(
            &json!({"kind":"page","watchId":job.watch_id,"contextGeneration":context_generation,"hash":hash}),
        )?;
        record(app, vec![(target, key)])?;
        Ok(())
    })();
    if let Err(error) = result {
        set_status(app, &format!("Page notice was not recorded: {error}"));
    }
}
fn updater_targets(
    reply: &Value,
    endpoint: &str,
    connection_epoch: u64,
    at: u64,
) -> Result<Vec<(Target, String)>> {
    if reply["ok"] != true {
        return Err("Current updater facts are unavailable.".into());
    }
    let context = NativeContext::read(reply)?;
    let facts = reply["facts"]
        .as_array()
        .filter(|facts| facts.len() <= 10_000)
        .ok_or("Updater facts exceed their complete bound.")?;
    let mut seen = BTreeSet::new();
    let mut targets = Vec::new();
    for fact in facts {
        let item_id = text_field(fact, "itemId", 256)?;
        let proof = text_field(fact, "proofDigest", 64)?;
        if !item_id.starts_with("live-")
            || !hex(&proof, 64)
            || !seen.insert(item_id.clone())
            || fact["kind"] != "newerVersionCandidate"
            || fact["profileId"] != context.profile_id
            || fact["game"] != context.game
            || fact["gameRuntime"] != context.game_runtime
            || fact["checkedAt"]
                .as_u64()
                .is_none_or(|checked| checked == 0 || checked > at.saturating_add(300_000))
            || fact["expiresAt"]
                .as_u64()
                .is_none_or(|expires| expires <= at)
            || fact["checkedAt"].as_u64() >= fact["expiresAt"].as_u64()
        {
            return Err("Updater facts changed, expired, or contain ambiguous identities.".into());
        }
        let key = digest(
            &json!({"kind":"updater","profileId":context.profile_id,"itemId":item_id,"proofDigest":proof}),
        )?;
        targets.push((
            Target::Updater {
                endpoint: endpoint.into(),
                connection_epoch,
                context: context.clone(),
                item_id,
                proof_digest: proof,
            },
            key,
        ));
    }
    Ok(targets)
}
fn refresh_updater(app: &tauri::AppHandle) -> Result<usize> {
    let (client, endpoint, epoch) = crate::windows::mo2_connection(app)?;
    let reply = client
        .native_content(&endpoint, "notification-facts", &json!({}))
        .map_err(err)?;
    if !crate::windows::mo2_connection_current(app, &endpoint, epoch) {
        return Err("MO2 connection changed during notification refresh.".into());
    }
    let targets = updater_targets(&reply, &endpoint, epoch, now())?;
    let count = record(app, targets)?;
    set_status(
        app,
        "Current native updater facts checked. Old notices are always rechecked when opened.",
    );
    Ok(count)
}
pub(super) fn tick(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<Notices>() else {
        return;
    };
    if !app.state::<WatchRuntime>().store.settings().notifications
        && app.get_webview_window("watchtower").is_none()
    {
        return;
    }
    let mut last = state
        .last_refresh
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    if last.is_some_and(|last| last.elapsed() < Duration::from_secs(60))
        || state.refreshing.swap(true, Ordering::AcqRel)
    {
        return;
    }
    *last = Some(Instant::now());
    drop(last);
    let handle = app.clone();
    std::thread::spawn(move || {
        if refresh_updater(&handle).is_err() {
            set_status(
                &handle,
                "Current MO2 updater facts are unavailable. Page notices remain available; every item is rechecked on open.",
            );
        }
        handle
            .state::<Notices>()
            .refreshing
            .store(false, Ordering::Release);
        notify(&handle);
    });
}
fn find(app: &tauri::AppHandle, id: &str) -> Result<Notice> {
    if !id.starts_with("notice-") || !hex(&id[7..], 64) {
        return Err("Invalid notification identity.".into());
    }
    app.state::<Notices>()
        .ledger
        .lock()
        .map_err(err)?
        .document
        .notices
        .iter()
        .find(|notice| notice.id == id && !notice.dismissed)
        .cloned()
        .ok_or("This notification was dismissed or superseded.".into())
}
fn same_page_fact(current: &Target, saved: &Target) -> bool {
    match (current, saved) {
        (
            Target::Page {
                watch_id: a_id,
                generation: a_generation,
                context_generation: a_context,
                hash: a_hash,
                ..
            },
            Target::Page {
                watch_id: b_id,
                generation: b_generation,
                context_generation: b_context,
                hash: b_hash,
                ..
            },
        ) => {
            a_id == b_id
                && a_generation == b_generation
                && a_context == b_context
                && a_hash == b_hash
        }
        _ => false,
    }
}
fn validate_target(app: &tauri::AppHandle, target: &Target) -> Result<Value> {
    match target {
        Target::Page {
            watch_id,
            observation_id,
            ..
        } => {
            let current = page_target(&app.state::<WatchRuntime>().store, watch_id)?;
            if !same_page_fact(&current, target) {
                return Err(
                    "This page observation was superseded. Review its current notice.".into(),
                );
            }
            let Target::Page {
                observation_id: current_observation,
                ..
            } = current
            else {
                unreachable!()
            };
            Ok(
                json!({"kind":"page","watchId":watch_id,"observationId":current_observation,"noticeObservationId":observation_id}),
            )
        }
        Target::Updater {
            endpoint,
            connection_epoch,
            context,
            item_id,
            ..
        } => {
            let (client, current_endpoint, current_epoch) = crate::windows::mo2_connection(app)?;
            if current_endpoint != *endpoint || current_epoch != *connection_epoch {
                return Err("The native MO2 connection changed. Refresh notices.".into());
            }
            let reply = client
                .native_content(endpoint, "notification-facts", &json!({"itemId":item_id}))
                .map_err(err)?;
            if !crate::windows::mo2_connection_current(app, endpoint, *connection_epoch)
                || NativeContext::read(&reply)? != *context
            {
                return Err(
                    "The native profile, game or item generation changed. Refresh notices.".into(),
                );
            }
            let targets = updater_targets(&reply, endpoint, *connection_epoch, now())?;
            if targets.len() != 1 || targets[0].0 != *target {
                return Err("This update fact is no longer current. Refresh notices.".into());
            }
            let fact = &reply["facts"][0];
            Ok(
                json!({"kind":"updater","itemId":item_id,"profileId":context.profile_id,"game":context.game,
                "itemName":text_field(fact,"itemName",2048)?,"installedVersion":fact["installedVersion"],
                "latestVersion":fact["latestVersion"],"provider":fact["provider"],"checkedAt":fact["checkedAt"],
                "sourceUrl":fact["sourceUrl"],"scope":fact["scope"],"proofDigest":fact["proofDigest"]}),
            )
        }
    }
}
fn request_focus(app: &tauri::AppHandle, notice: &Notice) -> Result<()> {
    if privacy::is_hidden(app) {
        return Err("Modhunter is hidden by the privacy shortcut.".into());
    }
    validate_target(app, &notice.target)?;
    {
        let state = app.state::<Notices>();
        let mut ledger = state.ledger.lock().map_err(err)?;
        ledger.focus_sequence = ledger
            .focus_sequence
            .checked_add(1)
            .ok_or("Notification focus identity exhausted.")?;
        ledger.focus = Some((notice.id.clone(), ledger.focus_sequence));
    }
    super::open(app, None)?;
    notify(app);
    Ok(())
}
pub(super) fn execute(app: &tauri::AppHandle, kind: &str, value: &Value) -> Result<Value> {
    match kind {
        "notices" => Ok(json!({"notices":snapshot(app)})),
        "refreshNotices" => {
            let state = app.state::<Notices>();
            if state.refreshing.swap(true, Ordering::AcqRel) {
                return Err("Notification facts are already being refreshed.".into());
            }
            let result = refresh_updater(app);
            state.refreshing.store(false, Ordering::Release);
            *state.last_refresh.lock().map_err(err)? = Some(Instant::now());
            if result.is_err() {
                set_status(
                    app,
                    "Current MO2 updater facts are unavailable. Existing notices are rechecked on open.",
                );
            }
            Ok(
                json!({"notices":snapshot(app),"message":if result.is_ok(){"Current notification facts refreshed."}else{"MO2 facts are unavailable; page notices remain available."}}),
            )
        }
        "activateNotice" => {
            let notice = find(app, &text_field(value, "id", 80)?)?;
            let action = validate_target(app, &notice.target)?;
            let state = app.state::<Notices>();
            let mut ledger = state.ledger.lock().map_err(err)?;
            let mut draft = ledger.document.clone();
            draft
                .notices
                .iter_mut()
                .find(|row| row.id == notice.id && !row.dismissed)
                .ok_or("This notification changed during review.")?
                .seen = true;
            commit(&mut ledger, draft)?;
            if ledger
                .focus
                .as_ref()
                .is_some_and(|focus| focus.0 == notice.id)
            {
                ledger.focus = None;
            }
            Ok(json!({"noticeAction":action,"message":"Current item identity verified."}))
        }
        "dismissNotice" => {
            let notice = find(app, &text_field(value, "id", 80)?)?;
            let state = app.state::<Notices>();
            let mut ledger = state.ledger.lock().map_err(err)?;
            let mut draft = ledger.document.clone();
            draft
                .notices
                .iter_mut()
                .find(|row| row.id == notice.id)
                .unwrap()
                .dismissed = true;
            commit(&mut ledger, draft)?;
            if ledger
                .focus
                .as_ref()
                .is_some_and(|focus| focus.0 == notice.id)
            {
                ledger.focus = None;
            }
            Ok(json!({"message":"Notification dismissed."}))
        }
        _ => Err("Unknown notification command.".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn page(generation: u64, observation: &str, hash: &str) -> Target {
        Target::Page {
            watch_id: "watch-1".into(),
            generation,
            context_generation: 1,
            observation_id: observation.into(),
            hash: hash.into(),
        }
    }
    #[test]
    fn dedup_survives_restart_without_renewing_old_event_authority() {
        let mut document = Document::default();
        let key = "a".repeat(64);
        let first = add(&mut document, page(1, "first", "hash"), key.clone(), 1)
            .unwrap()
            .unwrap();
        assert!(first.1);
        assert!(
            add(&mut document, first.0.target.clone(), key.clone(), 2)
                .unwrap()
                .is_none()
        );
        let mut document: Document =
            serde_json::from_slice(&serde_json::to_vec(&document).unwrap()).unwrap();
        let next = add(&mut document, page(2, "second", "hash"), key, 3)
            .unwrap()
            .unwrap();
        assert!(!next.1);
        assert_ne!(first.0.id, next.0.id);
        assert_eq!(document.notices.len(), 1);
        assert_eq!(document.notices[0].id, next.0.id);
        validate_document(&document).unwrap();
    }
    #[test]
    fn repeated_complete_capture_keeps_the_same_fact_but_context_and_content_changes_do_not() {
        assert!(same_page_fact(
            &page(1, "second", "hash"),
            &page(1, "first", "hash")
        ));
        assert!(!same_page_fact(
            &page(2, "second", "hash"),
            &page(1, "first", "hash")
        ));
        assert!(!same_page_fact(
            &page(1, "second", "different"),
            &page(1, "first", "hash")
        ));
    }
    #[test]
    fn failed_identity_and_duplicate_fact_reject_the_entire_batch() {
        let at = 1_000_000;
        let row = json!({"itemId":"live-one","proofDigest":"b".repeat(64),"kind":"newerVersionCandidate",
            "profileId":"profile","game":"game","gameRuntime":"runtime","checkedAt":900_000,"expiresAt":1_200_000});
        let mut reply = json!({"ok":true,"profileId":"profile","game":"game","gameRuntime":"runtime","executionEpoch":1,
            "nativeLease":"a".repeat(32),"nativeGeneration":1,"facts":[row.clone()]});
        assert_eq!(
            updater_targets(&reply, "http://127.0.0.1:1/", 1, at)
                .unwrap()
                .len(),
            1
        );
        reply["facts"] = json!([row.clone(), row]);
        assert!(updater_targets(&reply, "http://127.0.0.1:1/", 1, at).is_err());
        reply["facts"].as_array_mut().unwrap().pop();
        reply["facts"][0]["expiresAt"] = json!(at);
        assert!(updater_targets(&reply, "http://127.0.0.1:1/", 1, at).is_err());
        reply["nativeLease"] = json!("");
        assert!(updater_targets(&reply, "http://127.0.0.1:1/", 1, at).is_err());
    }
    #[test]
    fn opaque_event_identity_changes_for_native_aba_and_service_restart() {
        let context = NativeContext {
            profile_id: "profile".into(),
            game: "game".into(),
            game_runtime: "runtime".into(),
            execution_epoch: 1,
            native_lease: "a".repeat(32),
            native_generation: 1,
        };
        let mut target = Target::Updater {
            endpoint: "http://127.0.0.1:1/".into(),
            connection_epoch: 1,
            context,
            item_id: "live-one".into(),
            proof_digest: "b".repeat(64),
        };
        let first = event_id(&target).unwrap();
        if let Target::Updater { context, .. } = &mut target {
            context.native_generation += 1;
        }
        assert_ne!(first, event_id(&target).unwrap());
        if let Target::Updater { context, .. } = &mut target {
            context.native_generation = 1;
            context.native_lease = "c".repeat(32);
        }
        assert_ne!(first, event_id(&target).unwrap());
    }
}
