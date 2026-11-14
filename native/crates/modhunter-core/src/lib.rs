//! Native Modhunter work engine. Imports and audits are read-only. Explicit content-vault restores are separately confirmed mutations.
pub mod acquisition;
pub mod app_maintenance;
pub mod app_reset;
pub mod archive_review;
pub mod bridge;
pub mod content_vault;
pub mod duplicate_report;
pub mod importer;
pub mod ini;
pub mod plugin_analysis;
pub mod source_adapters;
pub mod watchtower;

pub use bridge::{BridgeClient, BridgeHealth, validate_bridge_endpoint};
pub use importer::{ImportRequest, ImportResult, ImportStats, ProfileImporter};
pub use plugin_analysis::{
    PluginAnalyzer, PluginAuditRequest, PluginAuditResult, PluginMod, PluginState, audit_plugins,
};

pub use source_adapters::{SourceScanRequest, SourceScanResult, scan_sources};

#[derive(Debug, thiserror::Error)]
pub enum CoreError {
    #[error("MO2 rejected the operation: {0}")]
    NativeRejected(String),
    #[error("Operation canceled; the last complete profile remains available.")]
    Cancelled,
    #[error("{0}")]
    Invalid(String),
    #[error("{context}: {source}")]
    Io {
        context: String,
        #[source]
        source: std::io::Error,
    },
    #[error("MO2 connection failed: {0}")]
    Network(#[from] reqwest::Error),
    #[error("Invalid MO2 response: {0}")]
    Json(#[from] serde_json::Error),
}

/// A cheap, thread-safe cancellation check. Once true, it must stay true for
/// this operation. Engines check it between files and bounded payload reads;
/// cancellation returns an error instead of a partial success catalog.
pub type CancellationCheck<'a> = &'a (dyn Fn() -> bool + Sync);

pub(crate) fn check_cancelled(check: CancellationCheck<'_>) -> Result<(), CoreError> {
    if check() {
        Err(CoreError::Cancelled)
    } else {
        Ok(())
    }
}

pub(crate) fn io_error(context: impl Into<String>, source: std::io::Error) -> CoreError {
    CoreError::Io {
        context: context.into(),
        source,
    }
}
