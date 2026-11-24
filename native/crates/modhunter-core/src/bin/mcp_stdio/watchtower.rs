//! Launcher-selected access to the shared Watchtower store.
use modhunter_core::watchtower::{PublicDocumentReader, PublicFormat, WatchStore};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    path::{Path, PathBuf},
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};
use url::Url;

pub const SEARCH: &str = "watchtower_search";
pub const ADD: &str = "watchtower_add";
pub const REFRESH: &str = "watchtower_refresh";

pub struct Scope {
    directory: PathBuf,
    pub manage: bool,
    reader: OnceLock<Result<PublicDocumentReader, String>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Search {
    #[serde(default)]
    query: String,
    #[serde(default)]
    offset: usize,
    revision: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum Format {
    Json,
    Feed,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Add {
    url: String,
    format: Format,
    #[serde(default)]
    title: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct Refresh {
    watch_id: String,
    watch_revision: String,
}
pub enum Query {
    Search(Search),
    Add(Add),
    Refresh(Refresh),
}
impl Query {
    pub fn writes(&self) -> bool {
        !matches!(self, Self::Search(_))
    }
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

impl Scope {
    pub fn configure(mode: &str, path: &Path) -> Result<Self, &'static str> {
        let manage = match mode {
            "read" => false,
            "manage" => true,
            _ => return Err("Use read or manage for --watchtower"),
        };
        let directory = path
            .canonicalize()
            .map_err(|_| "Watchtower requires an existing configuration directory")?;
        if !directory.is_dir() {
            return Err("Watchtower requires an existing configuration directory");
        }
        Ok(Self {
            directory,
            manage,
            reader: OnceLock::new(),
        })
    }

    fn writer(&self) -> Result<File, String> {
        if !self.manage {
            return Err("The launcher selected read-only Watchtower access.".into());
        }
        WatchStore::acquire_writer(&self.directory)
            .map_err(|_| "Another process owns Watchtower or the ownership file is unavailable. No change was made.".into())
    }

    pub fn execute(&self, query: Query) -> Result<Value, String> {
        let _owner = if query.writes() {
            Some(self.writer()?)
        } else {
            None
        };
        let store = WatchStore::open_for_public_watches(
            self.directory.join("watchtower.json"),
            _owner.is_some(),
        )?;
        match query {
            Query::Search(page) => search(&store, page),
            Query::Add(request) => store.add_public_watch(
                &request.url,
                match request.format {
                    Format::Json => PublicFormat::Json,
                    Format::Feed => PublicFormat::Feed,
                },
                &request.title,
                now(),
            ),
            Query::Refresh(request) => {
                if request.watch_id.len() > 128
                    || request.watch_id.is_empty()
                    || request.watch_revision.len() != 64
                {
                    return Err("Invalid watch identity or revision.".into());
                }
                let reader = self
                    .reader
                    .get_or_init(PublicDocumentReader::new)
                    .as_ref()
                    .map_err(Clone::clone)?;
                let job =
                    store.begin_public_check(&request.watch_id, &request.watch_revision, now())?;
                let observed = Url::parse(&job.url)
                    .ok()
                    .zip(PublicFormat::from_selector(&job.selector))
                    .ok_or_else(|| "Invalid structured URL or format.".to_owned())
                    .and_then(|(url, format)| reader.observe(&url, format, job.filtered_resources));
                match observed {
                    Ok(data) => {
                        if !store.complete_check(&job, data, now())? {
                            return Err("Watch changed during observation.".into());
                        }
                    }
                    Err(reason) => {
                        store.fail_check(&job, "unavailable", &reason, now())?;
                        return Err(format!(
                            "Public observation failed. The previous baseline was retained. {reason}"
                        ));
                    }
                }
                let row = store.public_watch_row(&request.watch_id)?;
                if !matches!(
                    row["state"].as_str(),
                    Some("baseline" | "changed" | "unchanged")
                ) {
                    return Err(format!(
                        "The source returned an incomplete observation ({}). The previous baseline was retained.",
                        row["state"]
                    ));
                }
                Ok(json!({"watch":row}))
            }
        }
    }

    pub fn tools(&self) -> Vec<Value> {
        let mut tools = vec![
            json!({"name":SEARCH,"title":"Search public structured watches",
            "description":"Search title or URL path in the selected Watchtower store. Returns anonymous JSON and RSS/Atom watch state and observation hashes. Use nextOffset and revision for subsequent pages.",
            "inputSchema":{"type":"object","additionalProperties":false,"properties":{"query":{"type":"string","maxLength":512},"offset":{"type":"integer","minimum":0},"revision":{"type":"string","maxLength":64}}},
            "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}}),
        ];
        if self.manage {
            tools.push(json!({"name":ADD,"title":"Add a public structured watch",
                "description":"Register an explicitly selected anonymous HTTPS JSON or RSS/Atom source in Watchtower. An existing matching watch is returned. Use watchtower_refresh for the first observation.",
                "inputSchema":{"type":"object","additionalProperties":false,"required":["url","format"],"properties":{"url":{"type":"string","maxLength":4096},"format":{"type":"string","enum":["json","feed"]},"title":{"type":"string","maxLength":512}}},
                "annotations":{"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}}));
            tools.push(json!({"name":REFRESH,"title":"Refresh a public structured watch",
                "description":"Fetch the selected public document anonymously and persist its observation. Use the watchId and current watchRevision from search or add.",
                "inputSchema":{"type":"object","additionalProperties":false,"required":["watchId","watchRevision"],"properties":{"watchId":{"type":"string","minLength":1,"maxLength":128},"watchRevision":{"type":"string","minLength":64,"maxLength":64}}},
                "annotations":{"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}}));
        }
        tools
    }
}

fn search(store: &WatchStore, page: Search) -> Result<Value, String> {
    if page.query.len() > 512 || page.query.chars().any(char::is_control) {
        return Err("Invalid search text.".into());
    }
    let query = page.query.trim().to_lowercase();
    let rows: Vec<_> = store
        .public_watch_rows()
        .into_iter()
        .filter(|row| {
            ["title", "url"].iter().any(|key| {
                row[key]
                    .as_str()
                    .is_some_and(|s| s.to_lowercase().contains(&query))
            })
        })
        .collect();
    let digest = format!(
        "{:x}",
        Sha256::digest(
            serde_json::to_vec(&(&query, &rows))
                .map_err(|_| "Unable to calculate watch revision")?
        )
    );
    if (page.offset > 0 || page.revision.is_some())
        && page.revision.as_deref() != Some(digest.as_str())
    {
        return Err("Watches or query changed. Restart the search.".into());
    }
    if page.offset > rows.len() {
        return Err("Offset is beyond available watches.".into());
    }
    let end = rows.len().min(page.offset.saturating_add(64));
    Ok(
        json!({"revision":digest,"total":rows.len(),"offset":page.offset,"nextOffset":(end<rows.len()).then_some(end),"watches":rows[page.offset..end]}),
    )
}
