use super::*;

pub(super) fn apply(
    doc: &mut Document,
    action: &str,
    payload: &Value,
    now: u64,
) -> Result<Value, String> {
    let mut result = match action {
        "saveWatch" => save_watch(doc, payload, now)?,
        "saveCreator" | "saveSourceBinding" | "saveFileDecision" | "clearFileDecision" => {
            sources::apply(doc, action, payload, now)?
        }
        "removeWatch" | "restoreWatch" | "pauseWatch" | "cancel" => {
            change_watch(doc, action, payload, now)?
        }
        "markSeen" => mark_seen(doc, payload, now)?,
        "undoSeen" => undo_seen(doc, payload, now)?,
        "saveSession" => save_session(doc, payload, now)?,
        "resetSession" | "removeSession" | "sessionOpened" | "confirmSession" => {
            change_session(doc, action, payload, now)?
        }
        "settings" => save_settings(doc, payload, now)?,
        "import" => commit_import(doc, payload, now)?,
        _ => return Err("Unknown Watchtower command.".into()),
    };
    result["revision"] = json!(doc.revision.saturating_add(1));
    Ok(result)
}

fn text(payload: &Value, key: &str, previous: &str, limit: usize) -> Result<String, String> {
    let value = match payload.get(key) {
        None => previous,
        Some(v) => v.as_str().ok_or_else(|| format!("{key} must be text."))?,
    };
    if value.len() > limit || value.chars().any(|c| c == '\0') {
        return Err(format!(
            "{key} exceeds its {limit}-byte limit or contains an invalid character."
        ));
    }
    Ok(value.trim().to_owned())
}
fn boolean(payload: &Value, key: &str, previous: bool) -> Result<bool, String> {
    match payload.get(key) {
        None => Ok(previous),
        Some(v) => v
            .as_bool()
            .ok_or_else(|| format!("{key} must be true or false.")),
    }
}
fn strings(
    payload: &Value,
    key: &str,
    previous: &[String],
    count: usize,
    limit: usize,
) -> Result<Vec<String>, String> {
    let Some(value) = payload.get(key) else {
        return Ok(previous.to_vec());
    };
    let values = value
        .as_array()
        .ok_or_else(|| format!("{key} must be a list."))?;
    if values.len() > count {
        return Err(format!("{key} exceeds its {count}-entry limit."));
    }
    let mut output = Vec::new();
    for value in values {
        let value = value
            .as_str()
            .ok_or_else(|| format!("{key} entries must be text."))?
            .trim();
        if value.len() > limit || value.chars().any(char::is_control) {
            return Err(format!("An entry in {key} is invalid or too long."));
        }
        if !value.is_empty() && !output.iter().any(|s: &String| s == value) {
            output.push(value.to_owned());
        }
    }
    Ok(output)
}
fn optional_id(
    payload: &Value,
    key: &str,
    previous: &Option<String>,
) -> Result<Option<String>, String> {
    match payload.get(key) {
        None => Ok(previous.clone()),
        Some(Value::Null) => Ok(None),
        Some(v) => {
            let id = v
                .as_str()
                .ok_or_else(|| format!("{key} must be text or null."))?;
            if id.len() > 100 {
                return Err(format!("Invalid {key}."));
            }
            Ok(if id.is_empty() {
                None
            } else {
                Some(id.to_owned())
            })
        }
    }
}
fn interval(payload: &Value, key: &str, previous: u32) -> Result<u32, String> {
    match payload.get(key) {
        None => Ok(previous),
        Some(v) => v
            .as_u64()
            .filter(|n| (1..=10080).contains(n))
            .map(|n| n as u32)
            .ok_or_else(|| format!("{key} must be 1 to 10,080 whole minutes.")),
    }
}
fn config(
    doc: &Document,
    payload: &Value,
    previous: Option<&Watch>,
    now: u64,
) -> Result<Watch, String> {
    if !payload.is_object() {
        return Err("Each watch must be an object.".into());
    }
    let mut watch = previous.cloned().unwrap_or_else(|| Watch {
        id: String::new(),
        url: String::new(),
        title: String::new(),
        folder: String::new(),
        tags: Vec::new(),
        notes: String::new(),
        enabled: true,
        session_id: None,
        requires_session: false,
        selector: String::new(),
        ignore_selectors: Vec::new(),
        interval_minutes: doc.settings.default_interval_minutes,
        association: None,
        created_at: now,
        updated_at: now,
        added_at: now,
        removed_at: None,
        generation: 1,
        context_generation: 1,
        fresh_check_required: true,
        operation_id: 0,
        state: "new".into(),
        last_attempt_at: None,
        last_success_at: None,
        next_check_at: 0,
        retry_at: 0,
        consecutive_errors: 0,
        last_error: String::new(),
        latest: None,
        seen_hash: None,
        seen_observation_id: None,
        seen_event_id: None,
        history: Vec::new(),
    });
    watch.url = canonical_page_url(&text(payload, "url", &watch.url, 4096)?)?;
    watch.title = text(payload, "title", &watch.title, 512)?;
    if watch.title.is_empty() {
        watch.title = url::Url::parse(&watch.url)
            .ok()
            .and_then(|u| u.host_str().map(str::to_owned))
            .unwrap_or_else(|| "Tracked page".into());
    }
    watch.folder = text(payload, "folder", &watch.folder, 200)?;
    watch.tags = strings(payload, "tags", &watch.tags, 32, 80)?;
    watch.notes = text(payload, "notes", &watch.notes, 8192)?;
    watch.enabled = boolean(payload, "enabled", watch.enabled)?;
    watch.selector = text(payload, "selector", &watch.selector, 2048)?;
    watch.ignore_selectors = strings(
        payload,
        "ignoreSelectors",
        &watch.ignore_selectors,
        20,
        2048,
    )?;
    watch.interval_minutes = interval(payload, "intervalMinutes", watch.interval_minutes)?;
    watch.session_id = optional_id(payload, "sessionId", &watch.session_id)?;
    watch.requires_session = boolean(
        payload,
        "requiresSession",
        if payload.get("sessionId").is_some() {
            watch.session_id.is_some()
        } else {
            watch.requires_session
        },
    )?;
    if let Some(id) = &watch.session_id {
        let session = doc
            .sessions
            .iter()
            .find(|s| &s.id == id)
            .ok_or("Choose an existing sign-in session.")?;
        if page_origin(&watch.url)? != session.origin {
            return Err("The selected sign-in session belongs to a different page origin.".into());
        }
        watch.requires_session = true;
    }
    if watch.requires_session && watch.session_id.is_none() {
        watch.enabled = false;
    }
    if let Some(association) = payload.get("association") {
        watch.association = if association.is_null() {
            None
        } else {
            let parsed: ModAssociation = serde_json::from_value(association.clone())
                .map_err(|_| "Invalid linked-mod association.".to_owned())?;
            if parsed.profile_id.is_empty()
                || parsed.item_id.is_empty()
                || parsed.profile_id.len() > 1000
                || parsed.item_id.len() > 1000
                || parsed.name.len() > 512
                || parsed.source_url.len() > 4096
            {
                return Err("Invalid linked-mod association fields.".into());
            }
            if !parsed.source_url.is_empty() {
                canonical_page_url(&parsed.source_url)?;
            }
            Some(parsed)
        };
    }
    Ok(watch)
}
fn same_page(a: &Watch, b: &Watch) -> bool {
    a.url == b.url && a.session_id == b.session_id && a.requires_session == b.requires_session
}
fn observation_context_changed(a: &Watch, b: &Watch) -> bool {
    a.url != b.url
        || a.session_id != b.session_id
        || a.requires_session != b.requires_session
        || a.selector != b.selector
        || a.ignore_selectors != b.ignore_selectors
}
fn idle_state(w: &Watch) -> String {
    if w.removed_at.is_some() {
        "removed"
    } else if !w.enabled {
        "paused"
    } else if needs_check(w) {
        "new"
    } else if has_changes(w) {
        "changed"
    } else {
        "unchanged"
    }
    .into()
}

fn save_watch(doc: &mut Document, payload: &Value, now: u64) -> Result<Value, String> {
    let id = payload
        .get("id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty());
    let index = id
        .map(|id| {
            doc.watches
                .iter()
                .position(|w| w.id == id)
                .ok_or_else(|| "This page watch no longer exists.".to_owned())
        })
        .transpose()?;
    if let Some(index) = index
        && let Some(generation) = payload.get("generation")
        && generation.as_u64() != Some(doc.watches[index].generation)
    {
        return Err("This page was edited elsewhere. Reload its settings before saving.".into());
    }
    let previous = index.map(|i| &doc.watches[i]);
    let mut watch = config(doc, payload, previous, now)?;
    if let Some(duplicate) = doc
        .watches
        .iter()
        .position(|w| Some(w.id.as_str()) != id && same_page(w, &watch))
    {
        if id.is_some() {
            return Err(
                "That page is already tracked with this session. Open its existing watch instead."
                    .into(),
            );
        }
        let restored = doc.watches[duplicate].removed_at.is_some();
        if restored {
            restore(&mut doc.watches[duplicate], now)?;
        }
        return Ok(
            json!({"ok":true,"watchId":doc.watches[duplicate].id,"duplicate":!restored,"restored":restored}),
        );
    }
    if let Some(index) = index {
        if observation_context_changed(&doc.watches[index], &watch) {
            watch.latest = None;
            watch.seen_hash = None;
            watch.seen_observation_id = None;
            watch.seen_event_id = None;
            watch.last_success_at = None;
            watch.added_at = now;
            watch.fresh_check_required = true;
            watch.context_generation = watch
                .context_generation
                .checked_add(1)
                .ok_or("Page context generation limit reached.")?;
        }
        watch.generation = watch
            .generation
            .checked_add(1)
            .ok_or("Page generation limit reached.")?;
        watch.updated_at = now;
        watch.last_error.clear();
        watch.consecutive_errors = 0;
        watch.retry_at = 0;
        watch.next_check_at = 0;
        watch.state = idle_state(&watch);
        let id = watch.id.clone();
        doc.watches[index] = watch;
        Ok(json!({"ok":true,"watchId":id,"duplicate":false,"restored":false}))
    } else {
        watch.id = format!("watch-{}", sequence(doc)?);
        watch.state = idle_state(&watch);
        let id = watch.id.clone();
        doc.watches.push(watch);
        Ok(json!({"ok":true,"watchId":id,"duplicate":false,"restored":false}))
    }
}
fn restore(w: &mut Watch, now: u64) -> Result<(), String> {
    if w.removed_at.is_none() {
        return Ok(());
    }
    w.removed_at = None;
    w.added_at = now;
    w.updated_at = now;
    w.generation = w
        .generation
        .checked_add(1)
        .ok_or("Page generation limit reached.")?;
    w.fresh_check_required = true;
    w.seen_event_id = None;
    w.next_check_at = 0;
    w.retry_at = 0;
    w.last_error.clear();
    w.state = idle_state(w);
    Ok(())
}
fn change_watch(
    doc: &mut Document,
    action: &str,
    payload: &Value,
    now: u64,
) -> Result<Value, String> {
    let mut ids: Vec<String> = if let Some(values) = payload.get("ids") {
        values
            .as_array()
            .ok_or("ids must be a list.")?
            .iter()
            .map(|v| {
                v.as_str()
                    .filter(|s| !s.is_empty() && s.len() <= 100)
                    .map(str::to_owned)
                    .ok_or_else(|| "Invalid watch ID.".to_owned())
            })
            .collect::<Result<_, _>>()?
    } else {
        vec![required(payload, "id")?.to_owned()]
    };
    if ids.len() > 10_000 {
        return Err("Select at most 10,000 pages for one operation.".into());
    }
    let mut seen = HashSet::new();
    ids.retain(|id| seen.insert(id.clone()));
    let indexes: Vec<_> = ids
        .iter()
        .map(|id| {
            doc.watches
                .iter()
                .position(|w| &w.id == id)
                .ok_or_else(|| format!("Watch {id} no longer exists."))
        })
        .collect::<Result<_, _>>()?;
    for index in indexes {
        let w = &mut doc.watches[index];
        if action == "restoreWatch" {
            restore(w, now)?;
            continue;
        }
        w.generation = w
            .generation
            .checked_add(1)
            .ok_or("Page generation limit reached.")?;
        w.updated_at = now;
        match action {
            "removeWatch" => {
                w.removed_at = Some(now);
                w.state = "removed".into();
            }
            "pauseWatch" => {
                w.enabled = boolean(payload, "enabled", !w.enabled)?;
                if w.requires_session && w.session_id.is_none() && w.enabled {
                    return Err(
                        "Choose a sign-in session or public checking before resuming this page."
                            .into(),
                    );
                }
                w.state = idle_state(w);
            }
            "cancel" if w.state == "checking" => {
                w.state = "cancelled".into();
                w.last_error = "Check cancelled; the last complete observation is retained.".into();
                w.next_check_at = now.saturating_add(u64::from(w.interval_minutes) * 60_000);
            }
            _ => {}
        }
    }
    Ok(json!({"ok":true,"watchIds":ids}))
}

fn observation_hash(watch: &Watch, observation_id: &str) -> Result<String, String> {
    if needs_check(watch) || watch.removed_at.is_some() {
        return Err("Check this page before marking its observation as seen.".into());
    }
    watch
        .latest
        .as_ref()
        .filter(|o| o.id == observation_id)
        .map(|o| o.hash.clone())
        .or_else(|| {
            watch
                .history
                .iter()
                .find(|h| {
                    h.observation_id == observation_id
                        && h.context_generation == watch.context_generation
                })
                .map(|h| h.hash.clone())
        })
        .ok_or_else(|| {
            "The selected observation is no longer retained. Review the current page change.".into()
        })
}
fn record_seen(
    doc: &mut Document,
    index: usize,
    observation_id: &str,
    hash: String,
    now: u64,
) -> Result<String, String> {
    let event_id = format!("seen-{}", sequence(doc)?);
    let watch = &mut doc.watches[index];
    let event = SeenEvent {
        id: event_id.clone(),
        watch_id: watch.id.clone(),
        title: watch.title.clone(),
        at: now,
        observation_id: observation_id.into(),
        hash: hash.clone(),
        previous_hash: watch.seen_hash.clone(),
        previous_observation_id: watch.seen_observation_id.clone(),
        previous_event_id: watch.seen_event_id.clone(),
        undone: false,
    };
    watch.seen_hash = Some(hash);
    watch.seen_observation_id = Some(observation_id.into());
    watch.seen_event_id = Some(event_id.clone());
    watch.updated_at = now;
    if matches!(watch.state.as_str(), "baseline" | "changed" | "unchanged") {
        watch.state = idle_state(watch);
    }
    doc.seen_history.push(event);
    Ok(event_id)
}
fn mark_seen(doc: &mut Document, payload: &Value, now: u64) -> Result<Value, String> {
    let result = if let Some(observations) = payload.get("observations") {
        let entries = observations
            .as_array()
            .filter(|items| !items.is_empty() && items.len() <= 5000)
            .ok_or("Choose 1 to 5,000 exact page observations for one seen operation.")?;
        // Validate the entire displayed selection first. A stale, missing, or
        // unrelated row aborts the transaction instead of acknowledging a subset.
        let indexes: std::collections::HashMap<_, _> = doc
            .watches
            .iter()
            .enumerate()
            .map(|(index, w)| (w.id.as_str(), index))
            .collect();
        let mut selected = HashSet::new();
        let mut prepared = Vec::with_capacity(entries.len());
        for entry in entries {
            let id = required(entry, "id")?;
            if !selected.insert(id) {
                return Err("Select each page only once in a seen operation.".into());
            }
            let index = *indexes
                .get(id)
                .ok_or("A selected page watch no longer exists.")?;
            let observation_id = required(entry, "observationId")?;
            let hash = observation_hash(&doc.watches[index], observation_id)?;
            prepared.push((index, observation_id.to_owned(), hash));
        }
        let mut event_ids = Vec::with_capacity(prepared.len());
        let mut watch_ids = Vec::with_capacity(prepared.len());
        for (index, observation_id, hash) in prepared {
            watch_ids.push(doc.watches[index].id.clone());
            event_ids.push(record_seen(doc, index, &observation_id, hash, now)?);
        }
        json!({"ok":true,"eventIds":event_ids,"watchIds":watch_ids})
    } else {
        let id = required(payload, "id")?;
        let index = doc
            .watches
            .iter()
            .position(|w| w.id == id)
            .ok_or("This page watch no longer exists.")?;
        let observation_id = required(payload, "observationId")?;
        let hash = observation_hash(&doc.watches[index], observation_id)?;
        let event_id = record_seen(doc, index, observation_id, hash, now)?;
        json!({"ok":true,"eventId":event_id,"watchId":id})
    };
    if doc.seen_history.len() > 500 {
        doc.seen_history.drain(..doc.seen_history.len() - 500);
    }
    Ok(result)
}

fn undo_seen(doc: &mut Document, payload: &Value, now: u64) -> Result<Value, String> {
    let event_id = required(payload, "eventId")?;
    let index = doc
        .seen_history
        .iter()
        .position(|e| e.id == event_id)
        .ok_or("This seen action is no longer retained.")?;
    let event = doc.seen_history[index].clone();
    if event.undone {
        return Err("This seen action was already undone.".into());
    }
    let watch = doc
        .watches
        .iter_mut()
        .find(|w| w.id == event.watch_id)
        .ok_or("This page watch no longer exists.")?;
    if watch.seen_event_id.as_deref() != Some(event_id) {
        return Err("A later seen action or page edit superseded this action. It cannot be undone over newer work.".into());
    }
    watch.seen_hash = event.previous_hash;
    watch.seen_observation_id = event.previous_observation_id;
    watch.seen_event_id = event.previous_event_id;
    watch.updated_at = now;
    if matches!(watch.state.as_str(), "baseline" | "changed" | "unchanged") {
        watch.state = idle_state(watch);
    }
    doc.seen_history[index].undone = true;
    Ok(json!({"ok":true,"watchId":watch.id,"eventId":event_id}))
}

fn save_session(doc: &mut Document, payload: &Value, now: u64) -> Result<Value, String> {
    let id = payload
        .get("id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty());
    let index = id
        .map(|id| {
            doc.sessions
                .iter()
                .position(|s| s.id == id)
                .ok_or_else(|| "This sign-in session no longer exists.".to_owned())
        })
        .transpose()?;
    let previous = index.map(|i| doc.sessions[i].clone());
    let origin = page_origin(&text(
        payload,
        "origin",
        previous.as_ref().map_or("", |s| s.origin.as_str()),
        4096,
    )?)?;
    if !origin.starts_with("https://") {
        return Err("Sign-in sessions require an HTTPS origin.".into());
    }
    let mut label = text(
        payload,
        "label",
        previous.as_ref().map_or("", |s| s.label.as_str()),
        200,
    )?;
    if label.is_empty() {
        label = origin.clone();
    }
    let remember = boolean(
        payload,
        "remember",
        previous.as_ref().is_some_and(|s| s.remember),
    )?;
    if let Some(index) = index {
        let previous = previous.as_ref().unwrap();
        if payload
            .get("storageGeneration")
            .is_some_and(|v| v.as_u64() != Some(previous.storage_generation))
        {
            return Err(
                "This sign-in session was reset elsewhere. Reload before editing it.".into(),
            );
        }
        if origin != previous.origin
            && doc
                .watches
                .iter()
                .any(|w| w.session_id.as_deref() == Some(&previous.id))
        {
            return Err("This session is linked to page watches. Create a separate session for a different origin.".into());
        }
        let reset = origin != previous.origin || remember != previous.remember;
        let session = &mut doc.sessions[index];
        session.label = label;
        session.origin = origin;
        session.remember = remember;
        session.updated_at = now;
        if reset {
            session.storage_generation = session
                .storage_generation
                .checked_add(1)
                .ok_or("Session generation limit reached.")?;
            session.state = "reset".into();
            session.last_opened_at = None;
            session.last_confirmed_at = None;
        }
        let result =
            json!({"ok":true,"sessionId":session.id,"session":session,"storageReset":reset});
        if reset {
            invalidate_session_watches(doc, id.unwrap(), now, false)?;
        }
        Ok(result)
    } else {
        if let Some(s) = doc
            .sessions
            .iter()
            .find(|s| s.origin == origin && s.label == label)
        {
            return Ok(
                json!({"ok":true,"sessionId":s.id,"session":s,"duplicate":true,"storageReset":false}),
            );
        }
        let session = Session {
            id: new_session_id(doc)?,
            label,
            origin,
            remember,
            state: "notOpened".into(),
            storage_generation: 1,
            created_at: now,
            updated_at: now,
            last_opened_at: None,
            last_confirmed_at: None,
        };
        let result =
            json!({"ok":true,"sessionId":session.id,"session":session,"storageReset":false});
        doc.sessions.push(session);
        Ok(result)
    }
}
fn invalidate_session_watches(
    doc: &mut Document,
    id: &str,
    now: u64,
    removed: bool,
) -> Result<(), String> {
    let generation=doc.sessions.iter().find(|session|session.id==id).map(|session|session.storage_generation);
    if removed || doc.source_preferences.accounts.iter().any(|account|account.session_id==id && Some(account.storage_generation)!=generation) { sources::forget_account(doc,id); }
    for watch in doc
        .watches
        .iter_mut()
        .filter(|w| w.session_id.as_deref() == Some(id))
    {
        watch.generation = watch
            .generation
            .checked_add(1)
            .ok_or("Page generation limit reached.")?;
        watch.updated_at = now;
        if removed {
            watch.session_id = None;
            watch.requires_session = true;
            watch.enabled = false;
        }
        watch.last_error=if removed{"The sign-in session was removed. Choose another session or explicitly select public checking."}else{"The sign-in session changed. Open or confirm it before checking again."}.into();
        watch.state = if watch.removed_at.is_some() {
            "removed"
        } else if !watch.enabled {
            "paused"
        } else {
            "signInRequired"
        }
        .into();
        watch.next_check_at = 0;
        watch.retry_at = 0;
    }
    Ok(())
}
fn change_session(
    doc: &mut Document,
    action: &str,
    payload: &Value,
    now: u64,
) -> Result<Value, String> {
    let id = required(payload, "id")?;
    if action == "confirmSession" {
        sources::release_hold(doc, id);
    }
    let index = doc
        .sessions
        .iter()
        .position(|s| s.id == id)
        .ok_or("This sign-in session no longer exists.")?;
    if action == "removeSession" {
        let retired = doc.sessions.remove(index);
        invalidate_session_watches(doc, id, now, true)?;
        return Ok(json!({"ok":true,"sessionId":id,"retiredSession":retired}));
    }
    let session = &mut doc.sessions[index];
    session.updated_at = now;
    match action {
        "resetSession" => {
            session.storage_generation = session
                .storage_generation
                .checked_add(1)
                .ok_or("Session generation limit reached.")?;
            session.state = "reset".into();
            session.last_opened_at = None;
            session.last_confirmed_at = None;
        }
        "sessionOpened" => {
            session.state = "opened".into();
            session.last_opened_at = Some(now);
        }
        "confirmSession" => {
            session.state = "confirmed".into();
            session.last_confirmed_at = Some(now);
        }
        _ => {}
    }
    let result = json!({"ok":true,"sessionId":id,"session":session});
    // Opening or confirming can change cookies while an old hidden read runs.
    // Recheck under a new ownership generation rather than accepting that read.
    invalidate_session_watches(doc, id, now, false)?;
    Ok(result)
}

pub(super) fn validate_settings(settings: &WatchSettings) -> Result<(), String> {
    if !(1..=10080).contains(&settings.default_interval_minutes)
        || !(1..=100).contains(&settings.history_limit)
        || settings.muted_origins.len() > 1000
        || settings.muted_folders.len() > 1000
        || settings.muted_folders.iter().any(|s| s.len() > 200)
    {
        return Err("Invalid Watchtower settings or retention limits.".into());
    }
    for origin in &settings.muted_origins {
        if page_origin(origin)? != *origin {
            return Err("Muted sites must be normalized origins.".into());
        }
    }
    Ok(())
}
fn save_settings(doc: &mut Document, payload: &Value, now: u64) -> Result<Value, String> {
    let payload = payload.get("settings").unwrap_or(payload);
    if !payload.is_object() {
        return Err("Watchtower settings must be an object.".into());
    }
    let old = doc.settings.clone();
    let mut settings = old.clone();
    settings.check_on_launch = boolean(payload, "checkOnLaunch", settings.check_on_launch)?;
    settings.background_checking =
        boolean(payload, "backgroundChecking", settings.background_checking)?;
    settings.notifications = boolean(payload, "notifications", settings.notifications)?;
    settings.hide_titles = boolean(payload, "hideTitles", settings.hide_titles)?;
    settings.privacy_screen = boolean(payload, "privacyScreen", settings.privacy_screen)?;
    settings.default_interval_minutes = interval(
        payload,
        "defaultIntervalMinutes",
        settings.default_interval_minutes,
    )?;
    if let Some(value) = payload.get("historyLimit") {
        settings.history_limit = value
            .as_u64()
            .filter(|n| (1..=100).contains(n))
            .ok_or("History retention must be 1 to 100 observations per page.")?
            as usize;
    }
    settings.muted_origins = strings(payload, "mutedOrigins", &settings.muted_origins, 1000, 4096)?
        .iter()
        .map(|s| page_origin(s))
        .collect::<Result<_, _>>()?;
    settings.muted_origins.sort();
    settings.muted_origins.dedup();
    settings.muted_folders = strings(payload, "mutedFolders", &settings.muted_folders, 1000, 200)?;
    validate_settings(&settings)?;
    for watch in &mut doc.watches {
        if muted(watch, &settings) && !muted(watch, &old) {
            watch.generation = watch
                .generation
                .checked_add(1)
                .ok_or("Page generation limit reached.")?;
            watch.updated_at = now;
            if watch.state == "checking" {
                watch.state = "cancelled".into();
            }
        }
        if watch.history.len() > settings.history_limit {
            watch
                .history
                .drain(..watch.history.len() - settings.history_limit);
        }
    }
    doc.settings = settings;
    Ok(json!({"ok":true,"settings":doc.settings}))
}

pub(super) fn export_document(doc: &Document, history: bool, now: u64) -> Value {
    let watches:Vec<_>=doc.watches.iter().filter(|w|w.removed_at.is_none()).map(|w| {
        let mut value=json!({"url":w.url,"title":w.title,"folder":w.folder,"tags":w.tags,"notes":w.notes,"enabled":w.enabled,
            "sessionId":Value::Null,"requiresSession":w.requires_session,"selector":w.selector,"ignoreSelectors":w.ignore_selectors,
            "intervalMinutes":w.interval_minutes,"association":w.association});
        if let Some(session)=w.session_id.as_ref().and_then(|id|doc.sessions.iter().find(|s|&s.id==id)){value["sessionLabel"]=json!(session.label);value["sessionOrigin"]=json!(session.origin);}
        value["sourceChoices"]=sources::export_choices(doc,w);
        if history{value["observationReport"]=json!({"latest":w.latest,"history":w.history,"seenObservationId":w.seen_observation_id});}
        value
    }).collect();
    json!({"kind":"modhunter-watchtower","schemaVersion":SCHEMA_VERSION,"exportedAt":now,"watches":watches,
        "settings":doc.settings,"notice":"Browser data and sign-in sessions are excluded. Imported account pages stay paused until a session is chosen. Imports always establish fresh page baselines; optional observation reports are for reference."})
}
pub(super) fn export_csv(doc: &Document) -> Value {
    // CSV is a readable configuration report; JSON is the lossless import format.
    // Protect spreadsheet users from formulas in user-controlled titles/notes.
    fn cell(raw: &str) -> String {
        let value = if raw.trim_start().starts_with(['=', '+', '-', '@'])
            || raw.starts_with(['\t', '\r', '\n'])
        {
            format!("'{raw}")
        } else {
            raw.to_owned()
        };
        format!("\"{}\"", value.replace('"', "\"\""))
    }
    let mut text="url,title,folder,tags,notes,enabled,requiresSession,selector,ignoreSelectors,intervalMinutes,profileId,itemId\r\n".to_owned();
    for watch in doc.watches.iter().filter(|w| w.removed_at.is_none()) {
        let fields = [
            watch.url.clone(),
            watch.title.clone(),
            watch.folder.clone(),
            watch.tags.join("; "),
            watch.notes.clone(),
            watch.enabled.to_string(),
            watch.requires_session.to_string(),
            watch.selector.clone(),
            watch.ignore_selectors.join("; "),
            watch.interval_minutes.to_string(),
            watch
                .association
                .as_ref()
                .map(|a| a.profile_id.clone())
                .unwrap_or_default(),
            watch
                .association
                .as_ref()
                .map(|a| a.item_id.clone())
                .unwrap_or_default(),
        ];
        text.push_str(&fields.iter().map(|s| cell(s)).collect::<Vec<_>>().join(","));
        text.push_str("\r\n");
    }
    json!({"ok":true,"text":text,"mimeType":"text/csv","fileName":"modhunter-watchtower.csv"})
}
fn import_values(payload: &Value) -> Result<&Vec<Value>, String> {
    let document = payload
        .get("document")
        .ok_or("Choose a Watchtower export document.")?;
    if serde_json::to_vec(document)
        .map_err(|e| e.to_string())?
        .len()
        > MAX_IMPORT_BYTES
    {
        return Err("Watchtower imports are limited to 8 MiB per operation.".into());
    }
    if document.get("kind").and_then(Value::as_str) != Some("modhunter-watchtower")
        || document.get("schemaVersion").and_then(Value::as_u64) != Some(u64::from(SCHEMA_VERSION))
    {
        return Err("This is not a supported Modhunter Watchtower export.".into());
    }
    let values = document
        .get("watches")
        .and_then(Value::as_array)
        .ok_or("The export has no watch list.")?;
    if values.len() > 10_000 {
        return Err("Import at most 10,000 watches per operation.".into());
    }
    Ok(values)
}
fn import_config(doc: &Document, value: &Value) -> Result<Watch, String> {
    if !value.is_object() {
        return Err("Each imported watch must be an object.".into());
    }
    let mut clean = value.clone();
    clean.as_object_mut().unwrap().remove("id");
    clean["sessionId"] = Value::Null;
    let requires = boolean(value, "requiresSession", false)?
        || optional_id(value, "sessionId", &None)?.is_some();
    clean["requiresSession"] = json!(requires);
    if requires {
        clean["enabled"] = json!(false);
    }
    let watch=config(doc, &clean, None, 0)?;
    sources::validate_import_choices(&watch,value)?;
    Ok(watch)
}
pub(super) fn import_preview(doc: &Document, payload: &Value) -> Result<Value, String> {
    let values = import_values(payload)?;
    let mut items = Vec::new();
    let mut new = 0;
    let mut duplicates = 0;
    let mut restore = 0;
    let mut invalid = 0;
    let mut session_required = 0;
    let mut seen = HashSet::new();
    for (index, value) in values.iter().enumerate() {
        match import_config(doc, value) {
            Ok(watch) => {
                let key = (watch.url.clone(), watch.requires_session);
                let existing = doc.watches.iter().find(|w| same_page(w, &watch));
                let status =
                    if !seen.insert(key) || existing.is_some_and(|w| w.removed_at.is_none()) {
                        duplicates += 1;
                        "duplicate"
                    } else if existing.is_some() {
                        restore += 1;
                        "restore"
                    } else {
                        new += 1;
                        "new"
                    };
                if watch.requires_session {
                    session_required += 1;
                }
                items.push(json!({"index":index,"status":status,"url":watch.url,"title":watch.title,"reason":"","sessionRequired":watch.requires_session}));
            }
            Err(error) => {
                invalid += 1;
                items.push(json!({"index":index,"status":"invalid","url":value.get("url").and_then(Value::as_str).map(|s|bounded_text(s,4096)).unwrap_or_default(),"title":value.get("title").and_then(Value::as_str).map(|s|bounded_text(s,512)).unwrap_or_default(),"reason":error,"sessionRequired":false}));
            }
        }
    }
    Ok(
        json!({"ok":true,"preview":true,"revision":doc.revision,"items":items,"counts":{"new":new,"duplicates":duplicates,"restore":restore,"invalid":invalid,"sessionRequired":session_required},
        "warnings":["Import adds watches without overwriting existing active pages. Account pages require a new session assignment. New pages retain their exact source, creator and file choices. Existing source preferences are preserved. Fresh checks establish baselines; account observations, observation reports and settings are not applied automatically."]}),
    )
}
fn commit_import(doc: &mut Document, payload: &Value, now: u64) -> Result<Value, String> {
    if payload
        .get("previewRevision")
        .is_some_and(|r| r.as_u64() != Some(doc.revision))
    {
        return Err(
            "Watchtower changed after the import preview. Preview again before importing.".into(),
        );
    }
    let preview = import_preview(doc, payload)?;
    if preview["counts"]["invalid"].as_u64().unwrap_or(1) > 0 {
        return Err("The import contains invalid watches. Correct them before importing; no watches were changed.".into());
    }
    let values = import_values(payload)?;
    let mut added = 0;
    let mut restored = 0;
    let mut skipped = 0;
    let mut ids = Vec::new();
    let mut imported_creators=sources::ImportedCreators::new();
    for value in values {
        let mut watch = import_config(doc, value)?;
        if let Some(index) = doc.watches.iter().position(|w| same_page(w, &watch)) {
            if doc.watches[index].removed_at.is_some() {
                restore(&mut doc.watches[index], now)?;
                restored += 1;
                ids.push(doc.watches[index].id.clone());
            } else {
                skipped += 1;
            }
            continue;
        }
        watch.id = format!("watch-{}", sequence(doc)?);
        watch.created_at = now;
        watch.added_at = now;
        watch.updated_at = now;
        watch.state = idle_state(&watch);
        ids.push(watch.id.clone());
        let watch_id=watch.id.clone();
        doc.watches.push(watch);
        sources::import_choices(doc,&watch_id,value,&mut imported_creators)?;
        added += 1;
    }
    Ok(
        json!({"ok":true,"preview":false,"added":added,"restored":restored,"skipped":skipped,"watchIds":ids}),
    )
}
