//! Persisted metadata and observations. Browser credentials never enter this schema.
use serde::{Deserialize, Serialize};

pub const SCHEMA_VERSION: u32 = 1;
pub const MAX_CAPTURE_BYTES: usize = 256 * 1024;
pub const MAX_PREVIEW_BYTES: usize = 16 * 1024;
pub const MAX_STORE_BYTES: usize = 64 * 1024 * 1024;
pub const MAX_IMPORT_BYTES: usize = 8 * 1024 * 1024;

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", default)]
pub struct WatchSettings {
    pub check_on_launch: bool,
    pub background_checking: bool,
    pub notifications: bool,
    pub default_interval_minutes: u32,
    pub history_limit: usize,
    pub muted_origins: Vec<String>,
    pub muted_folders: Vec<String>,
    pub hide_titles: bool,
    pub privacy_screen: bool,
}
impl Default for WatchSettings {
    fn default() -> Self {
        Self {
            check_on_launch: false,
            background_checking: false,
            notifications: false,
            default_interval_minutes: 60,
            history_limit: 20,
            muted_origins: Vec::new(),
            muted_folders: Vec::new(),
            hide_titles: false,
            privacy_screen: false,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ModAssociation {
    pub profile_id: String,
    pub item_id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub source_url: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Session {
    pub id: String,
    pub label: String,
    pub origin: String,
    pub remember: bool,
    /// "notOpened", "opened", "confirmed", or "reset"; confirmation is user supplied.
    pub state: String,
    pub storage_generation: u64,
    pub created_at: u64,
    pub updated_at: u64,
    pub last_opened_at: Option<u64>,
    pub last_confirmed_at: Option<u64>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Observation {
    pub id: String,
    pub hash: String,
    pub title: String,
    pub final_url: String,
    pub started_at: u64,
    pub checked_at: u64,
    pub text_bytes: usize,
    /// A bounded preview; the digest always covers the complete accepted capture.
    pub text_preview: String,
    pub preview_truncated: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChangeEntry {
    pub observation_id: String,
    pub hash: String,
    #[serde(default)]
    pub context_generation: u64,
    pub checked_at: u64,
    pub title: String,
    pub final_url: String,
    pub kind: String,
    pub summary: String,
    pub added: Vec<String>,
    pub removed: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Watch {
    pub id: String,
    pub url: String,
    pub title: String,
    pub folder: String,
    pub tags: Vec<String>,
    pub notes: String,
    pub enabled: bool,
    pub session_id: Option<String>,
    #[serde(default)]
    pub requires_session: bool,
    pub selector: String,
    pub ignore_selectors: Vec<String>,
    pub interval_minutes: u32,
    pub association: Option<ModAssociation>,
    pub created_at: u64,
    pub updated_at: u64,
    pub added_at: u64,
    pub removed_at: Option<u64>,
    pub generation: u64,
    /// Advances only when the selected content or account binding changes.
    #[serde(default)]
    pub context_generation: u64,
    /// Wall-clock equality must never make a restored page appear already read.
    #[serde(default)]
    pub fresh_check_required: bool,
    pub operation_id: u64,
    pub state: String,
    pub last_attempt_at: Option<u64>,
    pub last_success_at: Option<u64>,
    pub next_check_at: u64,
    pub retry_at: u64,
    pub consecutive_errors: u32,
    pub last_error: String,
    pub latest: Option<Observation>,
    pub seen_hash: Option<String>,
    pub seen_observation_id: Option<String>,
    pub seen_event_id: Option<String>,
    pub history: Vec<ChangeEntry>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SeenEvent {
    pub id: String,
    pub watch_id: String,
    pub title: String,
    pub at: u64,
    pub observation_id: String,
    pub hash: String,
    pub previous_hash: Option<String>,
    pub previous_observation_id: Option<String>,
    pub previous_event_id: Option<String>,
    pub undone: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CheckJob {
    pub watch_id: String,
    pub operation_id: u64,
    pub generation: u64,
    pub started_at: u64,
    pub url: String,
    pub title: String,
    pub selector: String,
    pub ignore_selectors: Vec<String>,
    pub session_id: Option<String>,
    pub session_storage_generation: u64,
    pub session: Option<Session>,
}

/// A rendered observation supplied by the desktop, never by the MO2 dashboard.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", default)]
pub struct PageObservation {
    pub final_url: String,
    pub title: String,
    pub text: String,
    pub status: String,
    pub message: String,
    pub http_status: Option<u16>,
    /// Incomplete browser captures must not advance the successful baseline.
    pub truncated: bool,
}
impl Default for PageObservation {
    fn default() -> Self {
        Self {
            final_url: String::new(),
            title: String::new(),
            text: String::new(),
            status: "ok".into(),
            message: String::new(),
            http_status: None,
            truncated: false,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Document {
    pub schema_version: u32,
    pub revision: u64,
    pub next_id: u64,
    pub settings: WatchSettings,
    pub watches: Vec<Watch>,
    pub sessions: Vec<Session>,
    pub seen_history: Vec<SeenEvent>,
}
impl Default for Document {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            revision: 0,
            next_id: 1,
            settings: WatchSettings::default(),
            watches: Vec::new(),
            sessions: Vec::new(),
            seen_history: Vec::new(),
        }
    }
}
