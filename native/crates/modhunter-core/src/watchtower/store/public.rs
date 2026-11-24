//! Row-scoped access to anonymous structured observations. The launcher holds
//! the desktop's ownership lock throughout each mutating operation.
use super::*;
use crate::watchtower::PublicFormat;
use url::Url;

fn source_url(raw: &str) -> Result<Url, String> {
    let url = Url::parse(&canonical_page_url(raw)?).map_err(|_| "Invalid public source URL")?;
    // Existing browser watches may embed signed or account-scoped URLs.
    // Search never returns query values, including for ordinary public filters.
    if url.query_pairs().any(|(key, _)| {
        let key = key.to_ascii_lowercase();
        matches!(
            key.as_str(),
            "key"
                | "sig"
                | "code"
                | "email"
                | "auth"
                | "authorization"
                | "oauth"
                | "api_key"
                | "apikey"
        ) || [
            "token",
            "secret",
            "password",
            "credential",
            "signature",
            "session",
        ]
        .iter()
        .any(|part| key.contains(part))
            || key.starts_with("x-amz-")
            || key.starts_with("x-goog-")
    }) {
        return Err("Use an anonymous public URL without credential parameters.".into());
    }
    Ok(url)
}

fn public_row(doc: &Document, w: &Watch) -> Option<Value> {
    if w.removed_at.is_some() || w.session_id.is_some() || w.requires_session {
        return None;
    }
    let format = match PublicFormat::from_selector(&w.selector)? {
        PublicFormat::Json => "json",
        PublicFormat::Feed => "feed",
    };
    let mut url = source_url(&w.url).ok()?;
    let has_query = url.query().is_some();
    url.set_query(None);
    let title = if doc.settings.hide_titles || doc.settings.privacy_screen {
        url.host_str().unwrap_or("Public source")
    } else {
        &w.title
    }
    .to_owned();
    if doc.settings.privacy_screen {
        url.set_path("/");
    }
    let hash = w.latest.as_ref().map(|o| &o.hash);
    let revision = Sha256::digest(
        serde_json::to_vec(&(
            &w.id,
            &w.url,
            &w.selector,
            w.generation,
            w.operation_id,
            &w.state,
            hash,
        ))
        .ok()?,
    );
    let eligible = eligible(w, &doc.settings) && sources::eligible(doc, w);
    let history = w
        .history
        .iter()
        .filter(|h| h.context_generation == w.context_generation);
    let previous = history
        .clone()
        .rev()
        .map(|h| &h.hash)
        .find(|h| Some(*h) != hash);
    Some(
        json!({"watchId":w.id, "watchRevision":format!("{revision:x}"), "title":title,
        "url":url.as_str(), "hasQuery":has_query, "format":format, "enabled":w.enabled, "eligible":eligible,
        "state":w.state, "hasChanges":has_changes(w), "lastSuccessAt":w.last_success_at,
        "lastAttemptAt":w.last_attempt_at,"retryAt":w.retry_at,"observationHash":hash,"previousDistinctHash":previous,"retainedHistoryCount":history.count(),
        "observedBytes":w.latest.as_ref().map(|o|o.text_bytes)}),
    )
}

impl WatchStore {
    /// Read current state without resetting ephemeral sessions or recovering other watches.
    /// A caller requesting write access must already hold the exclusive desktop ownership lock.
    pub fn open_for_public_watches(path: PathBuf, writable: bool) -> Result<Self, String> {
        let document = read_document(&path)
            .map_err(|_| "Saved Watchtower state could not be read. No change was made.")?;
        Ok(Self {
            path,
            inner: Mutex::new(Inner {
                document,
                warning: String::new(),
                read_only: !writable,
            }),
            write_guard: Mutex::new(()),
            writable,
        })
    }

    pub fn public_watch_rows(&self) -> Vec<Value> {
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        inner
            .document
            .watches
            .iter()
            .filter_map(|w| public_row(&inner.document, w))
            .collect()
    }

    pub fn public_watch_row(&self, id: &str) -> Result<Value, String> {
        let inner = self.inner.lock().map_err(lock_error)?;
        inner
            .document
            .watches
            .iter()
            .find(|w| w.id == id)
            .and_then(|w| public_row(&inner.document, w))
            .ok_or_else(|| "Public structured watch is unavailable.".into())
    }

    pub fn add_public_watch(
        &self,
        raw_url: &str,
        format: PublicFormat,
        title: &str,
        now: u64,
    ) -> Result<Value, String> {
        let url = source_url(raw_url)?.to_string();
        if title.len() > 512 || title.chars().any(char::is_control) {
            return Err("Invalid watch title.".into());
        }
        let selector = match format {
            PublicFormat::Json => "json:",
            PublicFormat::Feed => "feed:",
        };
        self.mutate_if(
            |doc| {
                if let Some(w) = doc
                    .watches
                    .iter()
                    .find(|w| w.url == url && w.session_id.is_none() && !w.requires_session)
                {
                    // Desktop saveWatch can restore a removed duplicate. An agent add never restores or edits one.
                    let row = public_row(doc, w)
                        .filter(|_| w.selector == selector)
                        .ok_or("That URL is unavailable for a new anonymous structured watch.")?;
                    return Ok(json!({"created":false,"watch":row}));
                }
                let added = commands::apply(
                    doc,
                    "saveWatch",
                    &json!({"url":url,"title":title,"selector":selector}),
                    now,
                )?;
                let added_row = doc
                    .watches
                    .last()
                    .filter(|w| added["watchId"] == w.id)
                    .and_then(|w| public_row(doc, w))
                    .ok_or("Watch could not be saved.")?;
                Ok(json!({"created":true,"watch":added_row}))
            },
            |result| result["created"] == true,
        )
    }

    /// Recover only this selected lease after a previous writer stopped. Ownership
    /// excludes any active UI or MCP writer before this method can be called.
    pub fn begin_public_check(
        &self,
        id: &str,
        revision: &str,
        now: u64,
    ) -> Result<CheckJob, String> {
        let row = self.public_watch_row(id)?;
        if row["watchRevision"] != revision {
            return Err("Watch changed. Read its current revision before refreshing.".into());
        }
        if row["eligible"] != true {
            return Err("Watch is paused or muted.".into());
        }
        if row["retryAt"].as_u64().is_some_and(|at| at > now) {
            return Err("This source is in its retry cooldown.".into());
        }
        if row["state"] == "checking" {
            self.mutate(|doc| {
                let watch = doc
                    .watches
                    .iter_mut()
                    .find(|w| w.id == id)
                    .ok_or("Watch is unavailable.")?;
                watch.state = "interrupted".into();
                watch.last_error =
                    "The previous reader stopped before completing its observation.".into();
                Ok(())
            })?;
        }
        self.begin_check(id, now)
    }
}
