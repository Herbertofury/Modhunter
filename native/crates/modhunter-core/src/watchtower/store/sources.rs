//! Exact source relationships and user choices, separate from page-change state.
use super::*;

fn field(value: &Value, name: &str, limit: usize) -> Result<String, String> {
    let text = value
        .get(name)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{name} must be text."))?
        .trim();
    if text.len() > limit || text.chars().any(char::is_control) {
        return Err(format!(
            "{name} is too long or contains control characters."
        ));
    }
    Ok(text.to_owned())
}
fn flag(value: &Value, name: &str, fallback: bool) -> Result<bool, String> {
    value.get(name).map_or(Ok(fallback), |v| {
        v.as_bool()
            .ok_or_else(|| format!("{name} must be true or false."))
    })
}
fn valid_association(value: &Option<ModAssociation>) -> bool {
    value.as_ref().is_none_or(|a| {
        !a.profile_id.is_empty()
            && !a.item_id.is_empty()
            && a.profile_id.len() <= 1000
            && a.item_id.len() <= 1000
            && a.name.len() <= 512
            && a.source_url.len() <= 4096
            && !a.profile_id.chars().any(char::is_control)
            && !a.item_id.chars().any(char::is_control)
            && (a.source_url.is_empty() || canonical_page_url(&a.source_url).is_ok())
    })
}
fn same_association(a: &Option<ModAssociation>, b: &Option<ModAssociation>) -> bool {
    matches!((a,b),(Some(a),Some(b)) if a.profile_id==b.profile_id && a.item_id==b.item_id)
}
pub(super) fn binding<'a>(doc: &'a Document, watch: &Watch) -> Option<&'a SourceBinding> {
    doc.source_preferences.bindings.iter().find(|row| {
        row.watch_id == watch.id
            && row.source_url == watch.url
            && match (&row.association, &watch.association) {
                (None, None) => true,
                _ => same_association(&row.association, &watch.association),
            }
    })
}
pub(super) fn access<'a>(doc: &'a Document, session: &Session) -> Option<&'a SessionAccess> {
    doc.source_preferences.accounts.iter().find(|row| {
        row.session_id == session.id && row.storage_generation == session.storage_generation
    })
}
pub(super) fn creator_muted(doc: &Document, watch: &Watch) -> bool {
    binding(doc, watch)
        .and_then(|b| b.creator_id.as_ref())
        .and_then(|id| doc.source_preferences.creators.iter().find(|c| &c.id == id))
        .is_some_and(|c| {
            page_origin(&watch.url).is_ok_and(|origin| c.muted_origins.contains(&origin))
        })
}
pub(super) fn held(doc: &Document, watch: &Watch) -> bool {
    watch
        .session_id
        .as_ref()
        .and_then(|id| doc.sessions.iter().find(|session| &session.id == id))
        .and_then(|session| access(doc, session))
        .is_some_and(|row| row.verification_hold)
}
pub(super) fn eligible(doc: &Document, watch: &Watch) -> bool {
    !creator_muted(doc, watch) && !held(doc, watch)
}
fn invalidate(watch: &mut Watch, now: u64) -> Result<(), String> {
    watch.generation = watch
        .generation
        .checked_add(1)
        .ok_or("Page generation limit reached.")?;
    watch.updated_at = now;
    if watch.state == "checking" {
        watch.state = "cancelled".into();
        watch.last_error = "The source choice changed; the previous check was cancelled.".into();
    }
    Ok(())
}
fn checked_watch<'a>(doc: &'a Document, payload: &Value) -> Result<&'a Watch, String> {
    let id = required(payload, "watchId")?;
    let watch = doc
        .watches
        .iter()
        .find(|w| w.id == id && w.removed_at.is_none())
        .ok_or("Choose an active tracked page.")?;
    if payload.get("generation").and_then(Value::as_u64) != Some(watch.generation) {
        return Err("This source changed. Reload its choices before saving.".into());
    }
    Ok(watch)
}
pub(super) fn enrich(doc: &Document, watch: &Watch, value: &mut Value) {
    value["sourceBinding"] = json!(binding(doc, watch));
    value["creatorMuted"] = json!(creator_muted(doc, watch));
    value["verificationHold"] = json!(held(doc, watch));
    if creator_muted(doc, watch) {
        value["muted"] = json!(true);
    }
}
/// Alternatives are grouped only by explicit profile/mod identity. A newer page
/// never outranks a different game/runtime/variant or overwrites a chosen source.
pub(super) fn detail(doc: &Document, watch: &Watch, value: &mut Value) {
    let selected = binding(doc, watch);
    let mut candidates:Vec<_>=doc.watches.iter().filter(|other|other.removed_at.is_none() && same_association(&other.association,&watch.association)).map(|other| {
        let choice=binding(doc,other);
        let compatible=selected.zip(choice).is_some_and(|(a,b)|a.compatibility_confirmed && b.compatibility_confirmed && a.game==b.game && a.runtime==b.runtime && a.variant==b.variant && a.content_intent=="update" && b.content_intent=="update");
        let eligible=super::eligible(other,&doc.settings) && self::eligible(doc,other);
        let readable=eligible && matches!(other.state.as_str(),"baseline"|"changed"|"unchanged");
        let preferred=choice.is_some_and(|c|c.preferred);
        let reason=if !compatible {"Confirm the same game, runtime and variant before comparing these sources."} else if held(doc,other) {"Account verification is on hold."} else if !eligible {"Source is paused or muted."} else if readable {"Page was readable at its last successful check. File access still needs review."} else {"Page access is not currently confirmed. Check this source."};
        json!({"watchId":other.id,"url":other.url,"title":other.title,"preferred":preferred,"compatible":compatible,"pageReadable":readable,"eligible":eligible,"reason":reason,"binding":choice})
    }).collect();
    candidates.sort_by_key(|row| {
        (
            !row["preferred"].as_bool().unwrap_or(false),
            !row["compatible"].as_bool().unwrap_or(false),
            !row["pageReadable"].as_bool().unwrap_or(false),
            row["watchId"].as_str().unwrap_or("").to_owned(),
        )
    });
    value["sourceAlternatives"] = json!(candidates);
    value["sourceFiles"] = json!(
        doc.source_preferences
            .files
            .iter()
            .filter(|row| row.watch_id == watch.id)
            .collect::<Vec<_>>()
    );
}
pub(super) fn apply(
    doc: &mut Document,
    action: &str,
    payload: &Value,
    now: u64,
) -> Result<Value, String> {
    match action {
        "saveCreator" => {
            let id = payload.get("id").and_then(Value::as_str);
            let previous = id
                .and_then(|id| doc.source_preferences.creators.iter().find(|c| c.id == id))
                .cloned();
            if id.is_some() && previous.is_none() {
                return Err("This creator group no longer exists.".into());
            }
            let profile_id = field(payload, "profileId", 1000)?;
            if profile_id.is_empty() {
                return Err("A creator group needs the exact MO2 profile identity.".into());
            }
            let name = field(payload, "name", 200)?;
            if name.is_empty() {
                return Err("Name this creator group.".into());
            }
            if let Some(old) = &previous
                && (old.profile_id != profile_id
                    || payload.get("revision").and_then(Value::as_u64) != Some(old.revision))
            {
                return Err(
                    "The creator profile or revision changed. Reload it before saving.".into(),
                );
            }
            let entries = payload
                .get("mutedOrigins")
                .and_then(Value::as_array)
                .ok_or("Muted origins must be a list.")?;
            if entries.len() > 100 {
                return Err("Choose at most 100 exact site origins per creator.".into());
            }
            let mut muted = BTreeSet::new();
            for entry in entries {
                muted.insert(page_origin(
                    entry.as_str().ok_or("A site origin must be text.")?,
                )?);
            }
            let id = previous
                .as_ref()
                .map(|c| c.id.clone())
                .unwrap_or(format!("creator-{}", sequence(doc)?));
            let record = CreatorPreferences {
                id: id.clone(),
                profile_id,
                name,
                offer_new_content: flag(payload, "offerNewContent", false)?,
                muted_origins: muted.into_iter().collect(),
                revision: previous.as_ref().map_or(Ok(1), |old| {
                    old.revision
                        .checked_add(1)
                        .ok_or("Creator revision limit reached.")
                })?,
            };
            let affected: HashSet<_> = doc
                .source_preferences
                .bindings
                .iter()
                .filter(|b| b.creator_id.as_ref() == Some(&id))
                .map(|b| b.watch_id.clone())
                .collect();
            doc.source_preferences.creators.retain(|c| c.id != id);
            doc.source_preferences.creators.push(record);
            for watch in &mut doc.watches {
                if affected.contains(&watch.id) {
                    invalidate(watch, now)?;
                }
            }
            Ok(
                json!({"creatorId":id,"watchIds":affected,"message":"Creator source choices saved."}),
            )
        }
        "saveSourceBinding" => {
            let watch = checked_watch(doc, payload)?.clone();
            let creator_id = match payload.get("creatorId") {
                Some(Value::String(id)) if !id.is_empty() => {
                    let creator = doc
                        .source_preferences
                        .creators
                        .iter()
                        .find(|c| &c.id == id)
                        .ok_or("Choose an existing creator group.")?;
                    if watch
                        .association
                        .as_ref()
                        .is_none_or(|a| a.profile_id != creator.profile_id)
                    {
                        return Err(
                            "The creator group must belong to this linked mod's exact profile."
                                .into(),
                        );
                    }
                    Some(id.clone())
                }
                None | Some(Value::Null) | Some(Value::String(_)) => None,
                _ => return Err("Invalid creator identity.".into()),
            };
            let game = field(payload, "game", 100)?;
            let runtime = field(payload, "runtime", 160)?;
            let variant = field(payload, "variant", 160)?;
            let compatibility_confirmed = flag(payload, "compatibilityConfirmed", false)?;
            if compatibility_confirmed && game.is_empty() {
                return Err("Name the exact game before confirming source compatibility.".into());
            }
            let content_intent = field(payload, "contentIntent", 40)?;
            if !matches!(
                content_intent.as_str(),
                "update" | "newContent" | "unclassified"
            ) {
                return Err("Choose update, separate new content, or unclassified.".into());
            }
            if content_intent == "update" && watch.association.is_none() {
                return Err("An update source needs an exact installed-mod association.".into());
            }
            let preferred = flag(payload, "preferred", false)?;
            if preferred && watch.association.is_none() {
                return Err("Link the exact installed mod before preferring a source.".into());
            }
            let mut affected = HashSet::from([watch.id.clone()]);
            if preferred {
                for row in &mut doc.source_preferences.bindings {
                    if same_association(&row.association, &watch.association)
                        && row.game == game
                        && row.runtime == runtime
                        && row.variant == variant
                        && row.content_intent == content_intent
                    {
                        if row.preferred {
                            affected.insert(row.watch_id.clone());
                        }
                        row.preferred = false;
                    }
                }
            }
            doc.source_preferences
                .bindings
                .retain(|row| row.watch_id != watch.id);
            doc.source_preferences.bindings.push(SourceBinding {
                watch_id: watch.id.clone(),
                source_url: watch.url.clone(),
                association: watch.association.clone(),
                creator_id,
                game,
                runtime,
                variant,
                compatibility_confirmed,
                content_intent,
                preferred,
            });
            for row in &mut doc.watches {
                if affected.contains(&row.id) {
                    invalidate(row, now)?;
                }
            }
            Ok(
                json!({"watchId":watch.id,"watchIds":affected,"message":"Source identity and variant choices saved. Earlier file approvals are no longer current."}),
            )
        }
        "saveFileDecision" | "clearFileDecision" => {
            let watch = checked_watch(doc, payload)?.clone();
            let key = field(payload, "sourceFileKey", 64)?;
            if !valid_hash(&key) {
                return Err("Choose an exact resolved source file.".into());
            }
            if action == "clearFileDecision" {
                doc.source_preferences
                    .files
                    .retain(|f| !(f.watch_id == watch.id && f.source_file_key == key));
            } else {
                let name = field(payload, "name", 960)?;
                let kind = field(payload, "kind", 40)?;
                if !matches!(kind.as_str(), "update" | "newContent" | "unclassified") {
                    return Err("Choose an update, separate content, or unclassified file.".into());
                }
                let association = if kind == "update" {
                    Some(
                        watch
                            .association
                            .clone()
                            .ok_or("This file has no exact installed-mod association.")?,
                    )
                } else {
                    None
                };
                let ignored = flag(payload, "ignored", false)?;
                doc.source_preferences
                    .files
                    .retain(|f| !(f.watch_id == watch.id && f.source_file_key == key));
                doc.source_preferences.files.push(SourceFileDecision {
                    watch_id: watch.id.clone(),
                    source_url: watch.url.clone(),
                    source_file_key: key,
                    name,
                    ignored,
                    kind,
                    association,
                    updated_at: now,
                });
            }
            invalidate(
                doc.watches
                    .iter_mut()
                    .find(|w| w.id == watch.id)
                    .ok_or("Source disappeared.")?,
                now,
            )?;
            Ok(
                json!({"watchId":watch.id,"message":"Exact file choice saved. Earlier download approvals were invalidated."}),
            )
        }
        _ => Err("Unknown source-choice command.".into()),
    }
}
pub(super) fn validate(doc: &Document) -> Result<(), String> {
    let prefs = &doc.source_preferences;
    if prefs.creators.len() > 10_000
        || prefs.bindings.len() > 10_000
        || prefs.files.len() > 100_000
        || prefs.accounts.len() > 10_000
    {
        return Err("Saved source choices exceed their complete-store limits.".into());
    }
    let mut ids = HashSet::new();
    for creator in &prefs.creators {
        if !ids.insert(&creator.id)
            || record_id(&creator.id, "creator-")? >= doc.next_id
            || creator.profile_id.is_empty()
            || creator.profile_id.len() > 1000
            || creator.name.is_empty()
            || creator.name.len() > 200
            || creator.revision == 0
            || creator.name.chars().any(char::is_control)
            || creator.profile_id.chars().any(char::is_control)
            || creator.muted_origins.len() > 100
        {
            return Err("Invalid saved creator relationship.".into());
        }
        for origin in &creator.muted_origins {
            if &page_origin(origin)? != origin {
                return Err("Invalid creator site origin.".into());
            }
        }
    }
    let mut bindings = HashSet::new();
    let mut preferred = HashSet::new();
    for row in &prefs.bindings {
        if !bindings.insert(&row.watch_id)
            || !doc.watches.iter().any(|w| w.id == row.watch_id)
            || canonical_page_url(&row.source_url)? != row.source_url
            || row.game.len() > 100
            || row.runtime.len() > 160
            || row.game.chars().any(char::is_control)
            || row.runtime.chars().any(char::is_control)
            || (row.compatibility_confirmed && row.game.is_empty())
            || row.variant.len() > 160
            || row.variant.chars().any(char::is_control)
            || !valid_association(&row.association)
            || (row.content_intent == "update" && row.association.is_none())
            || !matches!(
                row.content_intent.as_str(),
                "update" | "newContent" | "unclassified"
            )
            || row.creator_id.as_ref().is_some_and(|id| {
                !prefs.creators.iter().any(|c| {
                    &c.id == id
                        && row
                            .association
                            .as_ref()
                            .is_some_and(|a| a.profile_id == c.profile_id)
                })
            })
        {
            return Err("Invalid saved source relationship.".into());
        }
        if row.preferred {
            let Some(a) = &row.association else {
                return Err("A preferred source needs an exact mod identity.".into());
            };
            if !preferred.insert((
                &a.profile_id,
                &a.item_id,
                &row.game,
                &row.runtime,
                &row.variant,
                &row.content_intent,
            )) {
                return Err(
                    "More than one source is preferred for the same exact mod variant.".into(),
                );
            }
        }
    }
    let mut files = HashSet::new();
    for row in &prefs.files {
        if !files.insert((&row.watch_id, &row.source_file_key))
            || !doc.watches.iter().any(|w| w.id == row.watch_id)
            || !valid_hash(&row.source_file_key)
            || canonical_page_url(&row.source_url)? != row.source_url
            || row.name.len() > 960
            || row.name.chars().any(char::is_control)
            || !valid_association(&row.association)
            || !matches!(row.kind.as_str(), "update" | "newContent" | "unclassified")
            || (row.kind == "update") != row.association.is_some()
        {
            return Err("Invalid saved source-file decision.".into());
        }
    }
    let mut accounts = HashSet::new();
    for row in &prefs.accounts {
        if !accounts.insert(&row.session_id)
            || !doc.sessions.iter().any(|s| s.id == row.session_id)
            || row.storage_generation == 0
            || !matches!(
                row.login_state.as_str(),
                "unknown" | "signedIn" | "signedOut"
            )
            || !matches!(
                row.status.as_str(),
                "unknown"
                    | "pageAccessible"
                    | "restricted"
                    | "signInRequired"
                    | "challenge"
                    | "embeddedLoginBlocked"
            )
            || !matches!(
                row.guidance.as_str(),
                "" | "completeVerification" | "siteLogin" | "embeddedIdentityProvider"
            )
        {
            return Err("Invalid saved account observation.".into());
        }
    }
    Ok(())
}
pub(super) fn forget_account(doc: &mut Document, id: &str) {
    doc.source_preferences
        .accounts
        .retain(|row| row.session_id != id);
}
pub(super) fn release_hold(doc: &mut Document, id: &str) {
    if let Some(row) = doc
        .source_preferences
        .accounts
        .iter_mut()
        .find(|row| row.session_id == id)
    {
        row.verification_hold = false;
        // A user confirmation is not an independently observed login.
        row.login_state = "unknown".into();
        row.status = "unknown".into();
        row.guidance.clear();
    }
}
pub(super) fn hold(
    doc: &mut Document,
    id: &str,
    generation: u64,
    status: &str,
    now: u64,
) -> Result<(), String> {
    let login = if status == "signInRequired" {
        "signedOut"
    } else {
        "unknown"
    };
    doc.source_preferences
        .accounts
        .retain(|row| row.session_id != id);
    doc.source_preferences.accounts.push(SessionAccess {
        session_id: id.into(),
        storage_generation: generation,
        login_state: login.into(),
        status: status.into(),
        verification_hold: true,
        guidance: if status == "challenge" {
            "completeVerification"
        } else {
            "siteLogin"
        }
        .into(),
        observed_at: now,
    });
    for watch in &mut doc.watches {
        if watch.session_id.as_deref() == Some(id) && watch.removed_at.is_none() && watch.enabled {
            invalidate(watch, now)?;
            watch.state = status.into();
            watch.last_error = "This account's checks are held. Open its sign-in window, complete verification, then confirm and recheck.".into();
        }
    }
    Ok(())
}
impl WatchStore {
    /// Enforced again against the native MO2 context at review and submission.
    /// This checks an explicit compatibility declaration, never a release date.
    pub fn validate_source_compatibility(
        &self,
        watch_id: &str,
        game: &str,
        runtime: &str,
    ) -> Result<(), String> {
        fn game_key(raw: &str) -> String {
            let key: String = raw
                .chars()
                .filter(|c| c.is_ascii_alphanumeric())
                .flat_map(char::to_lowercase)
                .collect();
            match key.as_str() {
                "skyrimse" | "skyrimae" => "skyrimspecialedition".into(),
                "thesims4" => "sims4".into(),
                _ => key,
            }
        }
        let source = self.source_binding(watch_id).ok_or(
            "Set the source's exact game, runtime and variant before reviewing its files.",
        )?;
        if !source.compatibility_confirmed {
            return Err(
                "Confirm this source's game, runtime and variant before reviewing its files."
                    .into(),
            );
        }
        let expected = game_key(&source.game);
        let actual = game_key(game);
        if expected.is_empty()
            || actual.is_empty()
            || expected == "unknown"
            || actual == "unknown"
            || expected != actual
        {
            return Err("The source's declared game does not match the connected MO2 game.".into());
        }
        let expected = source.runtime.trim().to_ascii_lowercase();
        let actual = runtime.trim().to_ascii_lowercase();
        let unversioned = matches!(expected.as_str(), "not applicable" | "unversioned");
        if (unversioned
            && !matches!(
                actual.as_str(),
                "" | "unknown" | "not applicable" | "unversioned"
            ))
            || (!unversioned
                && (expected.is_empty() || expected == "unknown" || expected != actual))
        {
            return Err("The source's declared runtime does not match the connected MO2 runtime. Review compatibility instead of choosing by release date.".into());
        }
        Ok(())
    }
    /// Rank only native-resolved exact files. Page readability, user session
    /// confirmation and the date of a check never assert file availability.
    pub fn source_offer_choices(
        &self,
        evidence: &[crate::watchtower::SourceOfferEvidence],
        now: u64,
    ) -> Vec<crate::watchtower::SourceOfferChoice> {
        use crate::watchtower::source_choice::{SourceOfferChoice, publication_time, rank};
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let doc = &inner.document;
        let rows = evidence
            .iter()
            .map(|offer| {
                let watch = doc.watches.iter().find(|w| w.id == offer.watch_id);
                let source = watch.and_then(|w| binding(doc, w));
                let file = watch.and_then(|w| {
                    doc.source_preferences.files.iter().find(|f| {
                        f.watch_id == w.id
                            && f.source_url == w.url
                            && f.source_file_key == offer.source_file_key
                    })
                });
                let current = !inner.read_only
                    && offer.current
                    && watch.is_some_and(|w| {
                        w.removed_at.is_none()
                            && w.generation == offer.watch_generation
                            && super::eligible(w, &doc.settings)
                            && eligible(doc, w)
                            && (!w.requires_session || w.session_id.is_some())
                    });
                let compatible = source.is_some_and(|s| {
                    s.compatibility_confirmed
                        && !s.game.is_empty()
                        && !s.runtime.is_empty()
                        && s.runtime != "unknown"
                        && s.content_intent == "update"
                        && s.association.is_some()
                });
                let classified = watch.zip(file).is_some_and(|(w, f)| {
                    f.kind == "update"
                        && !f.ignored
                        && same_association(&f.association, &w.association)
                });
                let accessible = matches!(
                    offer.access_evidence.as_str(),
                    "patreonAttachment" | "loversLabChooser" | "loversLabDirect" | "megaFolderFile"
                );
                let group_id = source.filter(|_| compatible).map(|s| {
                    format!(
                        "{:x}",
                        Sha256::digest(
                            serde_json::to_vec(&(
                                &s.association.as_ref().unwrap().profile_id,
                                &s.association.as_ref().unwrap().item_id,
                                &s.game,
                                &s.runtime,
                                &s.variant
                            ))
                            .unwrap()
                        )
                    )
                });
                let host = watch
                    .and_then(|w| url::Url::parse(&w.url).ok())
                    .and_then(|url| url.host_str().map(str::to_owned))
                    .unwrap_or_default();
                let provider_date = match offer.publication_evidence.as_str() {
                    "patreonPost" => host == "patreon.com" || host.ends_with(".patreon.com"),
                    "loversLabAttachment" | "loversLabRelease" => {
                        host == "loverslab.com" || host.ends_with(".loverslab.com")
                    }
                    _ => false,
                };
                let published_at = provider_date
                    .then(|| {
                        publication_time(&offer.published_at, &offer.publication_evidence, now)
                    })
                    .flatten();
                let reason = if !current {
                    "File access evidence expired or the source/account changed. Find files again."
                } else if !compatible {
                    "Confirm this exact source's game, runtime and variant before comparing it."
                } else if file.is_some_and(|f| f.ignored) {
                    "This exact file is ignored."
                } else if !classified {
                    "Classify this exact file as an installed update before comparing releases."
                } else if !accessible {
                    "A page link is visible, but this exact file's access is not confirmed."
                } else {
                    "Current accessible compatible file."
                };
                SourceOfferChoice {
                    id: offer.id.clone(),
                    watch_id: offer.watch_id.clone(),
                    group_id,
                    eligible: current && compatible && classified && accessible,
                    preferred: source.is_some_and(|s| s.preferred),
                    recommended: false,
                    rank: None,
                    published_at,
                    publication_evidence: published_at.map(|_| offer.publication_evidence.clone()),
                    same_day_files: 0,
                    reason: reason.into(),
                }
            })
            .collect();
        rank(rows)
    }

    pub fn source_binding(&self, watch_id: &str) -> Option<SourceBinding> {
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let watch = inner.document.watches.iter().find(|w| w.id == watch_id)?;
        binding(&inner.document, watch).cloned()
    }
    pub fn source_file_decision(&self, watch_id: &str, key: &str) -> Option<SourceFileDecision> {
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let watch = inner.document.watches.iter().find(|w| w.id == watch_id)?;
        inner
            .document
            .source_preferences
            .files
            .iter()
            .find(|row| {
                row.watch_id == watch_id
                    && row.source_url == watch.url
                    && row.source_file_key == key
                    && (row.kind != "update"
                        || same_association(&row.association, &watch.association))
            })
            .cloned()
    }
    pub fn creator_offers_new_content(&self, watch_id: &str) -> bool {
        let inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        inner
            .document
            .watches
            .iter()
            .find(|w| w.id == watch_id)
            .and_then(|watch| binding(&inner.document, watch))
            .and_then(|row| row.creator_id.as_ref())
            .and_then(|id| {
                inner
                    .document
                    .source_preferences
                    .creators
                    .iter()
                    .find(|c| &c.id == id)
            })
            .is_some_and(|c| c.offer_new_content)
    }
    /// Called by native page observation only. No renderer command can assert
    /// observed login or page accessibility. File entitlement is never inferred.
    pub fn observe_session_access(
        &self,
        session_id: &str,
        generation: u64,
        login_state: &str,
        status: &str,
        guidance: &str,
        now: u64,
    ) -> Result<bool, String> {
        if !matches!(login_state, "unknown" | "signedIn" | "signedOut")
            || !matches!(
                status,
                "unknown"
                    | "pageAccessible"
                    | "restricted"
                    | "signInRequired"
                    | "challenge"
                    | "embeddedLoginBlocked"
            )
            || !matches!(
                guidance,
                "" | "completeVerification" | "siteLogin" | "embeddedIdentityProvider"
            )
        {
            return Err("Unsupported native account observation.".into());
        }
        self.mutate_if(
            |doc| {
                if !doc
                    .sessions
                    .iter()
                    .any(|s| s.id == session_id && s.storage_generation == generation)
                {
                    return Ok(false);
                }
                if let Some(old) = doc
                    .source_preferences
                    .accounts
                    .iter()
                    .find(|a| a.session_id == session_id && a.storage_generation == generation)
                    && (now < old.observed_at
                        || (old.login_state == login_state
                            && old.status == status
                            && old.guidance == guidance))
                {
                    return Ok(false);
                }
                if matches!(status, "challenge" | "signInRequired") {
                    let already = doc.source_preferences.accounts.iter().any(|a| {
                        a.session_id == session_id
                            && a.storage_generation == generation
                            && a.status == status
                            && a.verification_hold
                    });
                    if already {
                        return Ok(false);
                    }
                    hold(doc, session_id, generation, status, now)?;
                } else {
                    let held = doc
                        .source_preferences
                        .accounts
                        .iter()
                        .any(|a| a.session_id == session_id && a.verification_hold);
                    let row = SessionAccess {
                        session_id: session_id.into(),
                        storage_generation: generation,
                        login_state: login_state.into(),
                        status: status.into(),
                        verification_hold: held,
                        guidance: guidance.into(),
                        observed_at: now,
                    };
                    doc.source_preferences
                        .accounts
                        .retain(|a| a.session_id != session_id);
                    doc.source_preferences.accounts.push(row);
                }
                Ok(true)
            },
            |changed| *changed,
        )
    }
}

pub(super) fn export_choices(doc: &Document, watch: &Watch) -> Value {
    let binding = doc
        .source_preferences
        .bindings
        .iter()
        .find(|b| b.watch_id == watch.id);
    let creator = binding
        .and_then(|b| b.creator_id.as_ref())
        .and_then(|id| doc.source_preferences.creators.iter().find(|c| &c.id == id));
    json!({"binding":binding,"creator":creator,"files":doc.source_preferences.files.iter().filter(|f|f.watch_id==watch.id).collect::<Vec<_>>()})
}
pub(super) type ImportedCreators = std::collections::BTreeMap<String, (String, CreatorPreferences)>;
pub(super) fn import_choices(
    doc: &mut Document,
    watch_id: &str,
    value: &Value,
    creators: &mut ImportedCreators,
) -> Result<(), String> {
    let Some(choices) = value.get("sourceChoices") else {
        return Ok(());
    };
    if !choices.is_object() {
        return Err("Source choices must be an object.".into());
    }
    let creator = choices
        .get("creator")
        .filter(|v| !v.is_null())
        .map(|v| {
            serde_json::from_value::<CreatorPreferences>(v.clone())
                .map_err(|_| "Invalid imported creator choices.")
        })
        .transpose()?;
    let creator_id = if let Some(original) = creator {
        if original.id.len() > 100 || original.id.is_empty() {
            return Err("Invalid imported creator identity.".into());
        }
        if let Some((id, prior)) = creators.get(&original.id) {
            if prior != &original {
                return Err("The export contains conflicting choices for the same creator.".into());
            }
            Some(id.clone())
        } else {
            let id = format!("creator-{}", sequence(doc)?);
            let mut imported = original.clone();
            imported.id = id.clone();
            imported.revision = 1;
            doc.source_preferences.creators.push(imported);
            creators.insert(original.id.clone(), (id.clone(), original));
            Some(id)
        }
    } else {
        None
    };
    if let Some(raw) = choices.get("binding").filter(|v| !v.is_null()) {
        let mut binding: SourceBinding = serde_json::from_value(raw.clone())
            .map_err(|_| "Invalid imported source relationship.")?;
        if binding.creator_id.is_some() != creator_id.is_some() {
            return Err("The source's creator choices are incomplete.".into());
        }
        binding.watch_id = watch_id.into();
        binding.creator_id = creator_id;
        // An import never replaces an existing explicit source preference.
        if binding.preferred
            && doc.source_preferences.bindings.iter().any(|old| {
                old.preferred
                    && same_association(&old.association, &binding.association)
                    && old.game == binding.game
                    && old.runtime == binding.runtime
                    && old.variant == binding.variant
                    && old.content_intent == binding.content_intent
            })
        {
            binding.preferred = false;
        }
        doc.source_preferences.bindings.push(binding);
    } else if creator_id.is_some() {
        return Err("An imported creator needs its exact source relationship.".into());
    }
    if let Some(files) = choices.get("files") {
        let files = files
            .as_array()
            .ok_or("Imported file choices must be a list.")?;
        if files.len() > 100_000 {
            return Err("The complete imported file-choice list exceeds its limit.".into());
        }
        for raw in files {
            let mut file: SourceFileDecision =
                serde_json::from_value(raw.clone()).map_err(|_| "Invalid imported file choice.")?;
            file.watch_id = watch_id.into();
            doc.source_preferences.files.push(file);
        }
    }
    validate(doc)
}
pub(super) fn validate_import_choices(watch: &Watch, value: &Value) -> Result<(), String> {
    if value.get("sourceChoices").is_none() {
        return Ok(());
    }
    let mut doc = Document {
        next_id: 2,
        ..Default::default()
    };
    let mut watch = watch.clone();
    watch.id = "watch-1".into();
    doc.watches.push(watch);
    import_choices(&mut doc, "watch-1", value, &mut ImportedCreators::new())
}
