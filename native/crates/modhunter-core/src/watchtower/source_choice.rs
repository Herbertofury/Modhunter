//! Ranking is a review aid. It never grants download, installation or access
//! authority, and only compares exact user-confirmed ownership/compatibility.
use serde::Serialize;

/// Ephemeral facts supplied by the native same-session resolver. These are not
/// accepted by a renderer command and do not contain addresses or credentials.
#[derive(Clone, Debug)]
pub struct SourceOfferEvidence {
    pub id: String,
    pub watch_id: String,
    pub watch_generation: u64,
    pub source_file_key: String,
    pub current: bool,
    pub access_evidence: String,
    pub published_at: String,
    pub publication_evidence: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceOfferChoice {
    pub id: String,
    pub watch_id: String,
    pub group_id: Option<String>,
    pub eligible: bool,
    pub preferred: bool,
    pub recommended: bool,
    pub rank: Option<usize>,
    pub published_at: Option<u64>,
    pub publication_evidence: Option<String>,
    pub same_day_files: usize,
    pub reason: String,
}

pub(crate) fn publication_time(raw: &str, evidence: &str, now: u64) -> Option<u64> {
    if !matches!(
        evidence,
        "patreonPost" | "loversLabAttachment" | "loversLabRelease"
    ) {
        return None;
    }
    let value = chrono::DateTime::parse_from_rfc3339(raw).ok()?;
    let millis = u64::try_from(value.timestamp_millis()).ok()?;
    // Scheduled/future posts, a local check timestamp and malformed dates cannot
    // become evidence of a published release. No fallback to checked_at exists.
    (millis > 0 && millis <= now).then_some(millis)
}

pub(crate) fn rank(mut rows: Vec<SourceOfferChoice>) -> Vec<SourceOfferChoice> {
    let mut groups = std::collections::BTreeMap::<String, Vec<usize>>::new();
    for (index, row) in rows.iter().enumerate() {
        if row.eligible
            && let Some(group) = &row.group_id
        {
            groups.entry(group.clone()).or_default().push(index);
        }
    }
    for indices in groups.values_mut() {
        for index in indices.iter().copied() {
            // Only confirmed update files at the exact source on the same UTC
            // publication day contribute to this tie. New content, ignored
            // files and unknown dates do not inflate a release's file count.
            rows[index].same_day_files = if rows[index].published_at.is_some() {
                indices
                    .iter()
                    .filter(|other| {
                        rows[**other].watch_id == rows[index].watch_id
                            && rows[**other].published_at.map(|time| time / 86_400_000)
                                == rows[index].published_at.map(|time| time / 86_400_000)
                    })
                    .count()
            } else {
                0
            };
        }
        indices.sort_by_key(|index| {
            let row = &rows[*index];
            (
                !row.preferred,
                row.published_at.is_none(),
                std::cmp::Reverse(row.published_at.unwrap_or(0) / 86_400_000),
                std::cmp::Reverse(row.same_day_files),
                std::cmp::Reverse(row.published_at.unwrap_or(0)),
                row.watch_id.clone(),
                row.id.clone(),
            )
        });
        let undated = indices
            .iter()
            .any(|index| rows[*index].published_at.is_none());
        for (position, index) in indices.iter().copied().enumerate() {
            let row = &mut rows[index];
            row.rank = Some(position + 1);
            row.recommended = position == 0;
            row.reason = if row.preferred {
                "Your preferred source has a current accessible, compatible exact update file."
                    .into()
            } else if position == 0 && row.published_at.is_some() {
                if undated {
                    "Newest confirmed release date among accessible compatible files. Undated alternatives still need review."
                } else {
                    "Newest confirmed release date among accessible compatible files."
                }.into()
            } else if row.published_at.is_none() {
                "Accessible compatible exact file; its publication date is unknown.".into()
            } else {
                "Accessible compatible alternative; current preference and release evidence determine its order.".into()
            };
        }
    }
    rows
}
