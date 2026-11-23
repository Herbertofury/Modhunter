//! Evidence-led author/source continuity. No fetching, install, or entitlement side effects.
use crate::{CoreError, source_adapters::canonical_source_url};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum AccountStatus {
    Active,
    Unavailable,
    Suspended,
    Banned,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StatusEvidence {
    pub author_id: String,
    pub site: String,
    pub status: AccountStatus,
    /// Only a reviewed first-party provider status statement may establish a ban.
    pub provider_statement_verified: bool,
    pub evidence_url: String,
    pub observed_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Relocation {
    pub author_id: String,
    pub old_project_url: String,
    pub new_project_url: String,
    pub evidence_url: String,
    pub observed_at: u64,
    /// Reviewed author-controlled cross-link, not a display-name resemblance.
    pub author_link_verified: bool,
    /// Exact same mod/game/edition, not merely the new author's profile.
    pub project_mapping_verified: bool,
    pub destination_adapter_supported: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ContinuityDecision {
    pub status: AccountStatus,
    pub warning: String,
    pub old_project_url: String,
    pub candidate_project_url: Option<String>,
    pub can_track_destination: bool,
    pub tracking_requires_user_choice: bool,
    pub evidence_urls: Vec<String>,
}

fn public_url(raw: &str) -> Result<String, CoreError> {
    let value = canonical_source_url(raw)
        .ok_or_else(|| CoreError::Invalid("Expected a public HTTPS source URL".into()))?;
    // Do not silently retain sensitive URL parameters or accept altered evidence identifiers.
    if value != raw {
        return Err(CoreError::Invalid(
            "Use canonical public URLs without secrets, fragments or tracking parameters".into(),
        ));
    }
    Ok(value)
}

/// Evaluate reviewed evidence for a single tracked project. Missing pages/403/404 and
/// unreviewed reports never become confirmed bans. Stale/future evidence cannot enable a move.
pub fn evaluate(
    author_id: &str,
    current_project_url: &str,
    status: Option<&StatusEvidence>,
    relocation: Option<&Relocation>,
    now: u64,
    max_age_seconds: u64,
) -> Result<ContinuityDecision, CoreError> {
    if author_id.trim().is_empty() || max_age_seconds == 0 {
        return Err(CoreError::Invalid(
            "Author identity and nonzero freshness policy required".into(),
        ));
    }
    let old = public_url(current_project_url)?;
    let fresh = |observed: u64| observed <= now && now - observed <= max_age_seconds;
    let mut result = ContinuityDecision {
        status: AccountStatus::Unknown,
        warning: "Author status is unverified".into(),
        old_project_url: old.clone(),
        candidate_project_url: None,
        can_track_destination: false,
        tracking_requires_user_choice: true,
        evidence_urls: vec![],
    };
    if let Some(e) = status {
        let url = public_url(&e.evidence_url)?;
        let site = public_url(&e.site)?;
        let host = |s: &str| {
            url::Url::parse(s)
                .ok()
                .and_then(|u| u.host_str().map(str::to_owned))
        };
        if e.author_id == author_id && host(&site) == host(&old) && fresh(e.observed_at) {
            result.evidence_urls.push(url.clone());
            result.status = match e.status {
                AccountStatus::Banned | AccountStatus::Suspended
                    if !e.provider_statement_verified || host(&url) != host(&site) =>
                {
                    AccountStatus::Unknown
                }
                _ => e.status.clone(),
            };
            result.warning = match result.status {
                AccountStatus::Banned => "Provider reports this author banned; reason not inferred",
                AccountStatus::Suspended => {
                    "Provider reports this author suspended; reason not inferred"
                }
                AccountStatus::Unavailable => "Source unavailable; this does not establish a ban",
                AccountStatus::Active => "Author reported active at the recorded check",
                AccountStatus::Unknown => "Author status is unverified",
            }
            .into();
        }
    }
    if let Some(m) = relocation {
        let from = public_url(&m.old_project_url)?;
        let to = public_url(&m.new_project_url)?;
        let evidence = public_url(&m.evidence_url)?;
        if m.author_id == author_id && from == old && to != old && fresh(m.observed_at) {
            result.candidate_project_url = Some(to);
            if !result.evidence_urls.contains(&evidence) {
                result.evidence_urls.push(evidence);
            }
            result.can_track_destination = m.author_link_verified
                && m.project_mapping_verified
                && m.destination_adapter_supported;
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    const OLD: &str = "https://www.nexusmods.com/skyrimspecialedition/mods/100";
    fn movement() -> Relocation {
        Relocation {
            author_id: "creator-1".into(),
            old_project_url: OLD.into(),
            new_project_url: "https://modholdmods.com/mods/example".into(),
            evidence_url: "https://example.org/creator/move".into(),
            observed_at: 90,
            author_link_verified: true,
            project_mapping_verified: true,
            destination_adapter_supported: true,
        }
    }
    fn status() -> StatusEvidence {
        StatusEvidence {
            author_id: "creator-1".into(),
            site: "https://www.nexusmods.com/".into(),
            status: AccountStatus::Banned,
            provider_statement_verified: true,
            evidence_url: "https://www.nexusmods.com/users/123".into(),
            observed_at: 90,
        }
    }
    #[test]
    fn confirmed_ban_is_separate_from_move() {
        let d = evaluate("creator-1", OLD, Some(&status()), None, 100, 20).unwrap();
        assert_eq!(d.status, AccountStatus::Banned);
        assert!(!d.can_track_destination);
    }
    #[test]
    fn missing_page_is_not_a_ban() {
        let mut e = status();
        e.status = AccountStatus::Unavailable;
        let d = evaluate("creator-1", OLD, Some(&e), None, 100, 20).unwrap();
        assert_eq!(d.status, AccountStatus::Unavailable);
    }
    #[test]
    fn rumor_or_foreign_statement_is_not_a_ban() {
        let mut e = status();
        e.provider_statement_verified = false;
        assert_eq!(
            evaluate("creator-1", OLD, Some(&e), None, 100, 20)
                .unwrap()
                .status,
            AccountStatus::Unknown
        );
        e.provider_statement_verified = true;
        e.evidence_url = "https://example.org/rumor".into();
        assert_eq!(
            evaluate("creator-1", OLD, Some(&e), None, 100, 20)
                .unwrap()
                .status,
            AccountStatus::Unknown
        );
    }
    #[test]
    fn verified_move_does_not_require_ban() {
        let d = evaluate("creator-1", OLD, None, Some(&movement()), 100, 20).unwrap();
        assert!(d.can_track_destination);
        assert!(d.tracking_requires_user_choice);
        assert_eq!(d.old_project_url, OLD);
        assert_eq!(d.status, AccountStatus::Unknown);
    }
    #[test]
    fn each_mapping_gate_is_required() {
        for gate in 0..3 {
            let mut m = movement();
            match gate {
                0 => m.author_link_verified = false,
                1 => m.project_mapping_verified = false,
                _ => m.destination_adapter_supported = false,
            };
            assert!(
                !evaluate("creator-1", OLD, None, Some(&m), 100, 20)
                    .unwrap()
                    .can_track_destination
            );
        }
    }
    #[test]
    fn stale_future_or_foreign_mapping_cannot_migrate() {
        for time in [0, 101] {
            let mut m = movement();
            m.observed_at = time;
            assert!(
                !evaluate("creator-1", OLD, None, Some(&m), 100, 20)
                    .unwrap()
                    .can_track_destination
            );
        }
        let mut m = movement();
        m.author_id = "lookalike".into();
        assert!(
            !evaluate("creator-1", OLD, None, Some(&m), 100, 20)
                .unwrap()
                .can_track_destination
        );
        m = movement();
        m.old_project_url = "https://www.nexusmods.com/skyrimspecialedition/mods/101".into();
        assert!(
            !evaluate("creator-1", OLD, None, Some(&m), 100, 20)
                .unwrap()
                .can_track_destination
        );
    }
    #[test]
    fn unsafe_or_secret_urls_rejected() {
        for u in [
            "http://example.org/",
            "https://localhost/",
            "https://127.0.0.1/",
            "https://example.org/?token=secret",
            "https://user:password@example.org/",
        ] {
            let mut m = movement();
            m.new_project_url = u.into();
            assert!(evaluate("creator-1", OLD, None, Some(&m), 100, 20).is_err());
        }
    }
    #[test]
    fn status_wrong_author_or_site_stays_unknown() {
        let mut e = status();
        e.author_id = "other".into();
        assert_eq!(
            evaluate("creator-1", OLD, Some(&e), None, 100, 20)
                .unwrap()
                .status,
            AccountStatus::Unknown
        );
        e = status();
        e.site = "https://example.org/".into();
        assert_eq!(
            evaluate("creator-1", OLD, Some(&e), None, 100, 20)
                .unwrap()
                .status,
            AccountStatus::Unknown
        );
    }
    #[test]
    fn strict_serialization_roundtrip() {
        let m = movement();
        let json = serde_json::to_string(&m).unwrap();
        assert_eq!(serde_json::from_str::<Relocation>(&json).unwrap(), m);
        let mut value = serde_json::to_value(&m).unwrap();
        value["secret"] = "not-accepted".into();
        assert!(serde_json::from_value::<Relocation>(value).is_err());
    }
}
