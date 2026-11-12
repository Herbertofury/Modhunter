//! Native Modhunter work engine. No function in this crate modifies MO2 files.
pub mod bridge;
pub mod importer;
pub mod ini;

pub use bridge::{BridgeClient, BridgeHealth, validate_bridge_endpoint};
pub use importer::{ImportRequest, ImportResult, ImportStats, ProfileImporter};

#[derive(Debug, thiserror::Error)]
pub enum CoreError {
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

pub(crate) fn io_error(context: impl Into<String>, source: std::io::Error) -> CoreError {
    CoreError::Io {
        context: context.into(),
        source,
    }
}
