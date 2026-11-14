//! Local page watches with explicit observation ownership and no credentials.
//!
//! These are change observations, never mod-version or installation authority.
//! Browser/network work is supplied by the desktop after a check lease begins.
mod source_choice;
mod store;
mod types;
mod url_policy;

pub use source_choice::{SourceOfferChoice, SourceOfferEvidence};
pub use store::WatchStore;
pub use types::{
    CheckJob, CreatorPreferences, MAX_CAPTURE_BYTES, ModAssociation, PageObservation, Session,
    SessionAccess, SourceBinding, SourceFileDecision, SourcePreferences, Watch, WatchSettings,
};
pub use url_policy::{canonical_page_url, is_public_ip, page_origin};
