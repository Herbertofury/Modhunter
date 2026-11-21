//! Read-only semantic comparison of two user-selected QSettings INI snapshots.
//! This is an observation, not a profile identity, author, or restore receipt.
use crate::{
    CancellationCheck, CoreError, check_cancelled,
    importer::read_ini_comparison_input,
    ini::{Ini, decode_text},
};
use serde::Serialize;
use std::path::Path;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ChangeKind {
    Added,
    Removed,
    Changed,
}

#[derive(Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChangeCounts {
    pub added: usize,
    pub removed: usize,
    pub changed: usize,
}

impl ChangeCounts {
    fn record(&mut self, kind: ChangeKind) {
        match kind {
            ChangeKind::Added => self.added += 1,
            ChangeKind::Removed => self.removed += 1,
            ChangeKind::Changed => self.changed += 1,
        }
    }
    fn is_empty(&self) -> bool {
        self.added == 0 && self.removed == 0 && self.changed == 0
    }
}

#[derive(Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LabeledChange {
    pub setting: &'static str,
    pub kind: ChangeKind,
}

#[derive(Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SnapshotComparison {
    pub schema_version: u32,
    pub comparison: &'static str,
    pub order_evidence: &'static str,
    pub profile_identity: &'static str,
    pub editor_attribution: &'static str,
    pub values: &'static str,
    pub byte_identical: bool,
    pub effective_settings_equal: bool,
    pub before_settings: usize,
    pub after_settings: usize,
    pub changes: ChangeCounts,
    pub labeled_changes: Vec<LabeledChange>,
    pub unlabeled_changes: ChangeCounts,
}

// Labels deliberately come from the repository's existing offline importer,
// never from user-supplied text or value-shaped heuristics. Unknown setting
// names may themselves contain paths, accounts or arbitrary text.
fn known_label(section: &str, key: &str) -> Option<&'static str> {
    match (section, key) {
        ("settings", "base_directory") => Some("instanceBaseDirectory"),
        ("settings", "mod_directory") => Some("modsDirectory"),
        ("settings", "profiles_directory") => Some("profilesDirectory"),
        ("general", "gamenexusname") => Some("gameNexusName"),
        ("general", "gamename") => Some("gameName"),
        ("general", "game") => Some("game"),
        ("general", "gameversion") => Some("gameRuntimeVersion"),
        _ => None,
    }
}

fn parse_snapshot(bytes: &[u8], side: &str) -> Result<Ini, CoreError> {
    Ini::parse_unique(&decode_text(bytes).map_err(|error| {
        CoreError::Invalid(format!(
            "{side} snapshot is not a supported INI encoding: {error}"
        ))
    })?)
    .map_err(|error| CoreError::Invalid(format!("{side} snapshot cannot be compared: {error}")))
}

fn compare_bytes(
    before: &[u8],
    after: &[u8],
    cancel: CancellationCheck<'_>,
) -> Result<SnapshotComparison, CoreError> {
    check_cancelled(cancel)?;
    let byte_identical = before == after;
    let before = parse_snapshot(before, "Before")?;
    check_cancelled(cancel)?;
    let after = parse_snapshot(after, "After")?;
    let mut result = SnapshotComparison {
        schema_version: 1,
        comparison: "qsettingsIni",
        order_evidence: "callerSupplied",
        profile_identity: "notVerified",
        editor_attribution: "unknown",
        values: "notIncluded",
        byte_identical,
        effective_settings_equal: false,
        before_settings: 0,
        after_settings: 0,
        changes: ChangeCounts::default(),
        labeled_changes: Vec::new(),
        unlabeled_changes: ChangeCounts::default(),
    };
    let mut record = |section: &str, key: &str, kind| {
        result.changes.record(kind);
        if let Some(setting) = known_label(section, key) {
            result.labeled_changes.push(LabeledChange { setting, kind });
        } else {
            result.unlabeled_changes.record(kind);
        }
    };
    for (section, key, old) in before.canonical_entries() {
        result.before_settings += 1;
        match after.get_canonical(section, key) {
            None => record(section, key, ChangeKind::Removed),
            Some(new) if old != new => record(section, key, ChangeKind::Changed),
            _ => {}
        }
    }
    for (section, key, _) in after.canonical_entries() {
        result.after_settings += 1;
        if before.get_canonical(section, key).is_none() {
            record(section, key, ChangeKind::Added);
        }
    }
    check_cancelled(cancel)?;
    result
        .labeled_changes
        .sort_unstable_by_key(|row| row.setting);
    result.effective_settings_equal = result.changes.is_empty();
    Ok(result)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SnapshotSide {
    Before,
    After,
}

/// An opaque, short-lived copy of one explicitly selected input. No filename
/// is retained and no raw data can be serialized or presented to the renderer.
pub struct SelectedSnapshot(Vec<u8>);

impl SelectedSnapshot {
    pub fn read(path: &Path, side: SnapshotSide) -> Result<Self, CoreError> {
        let side = match side {
            SnapshotSide::Before => "Before",
            SnapshotSide::After => "After",
        };
        read_ini_comparison_input(path)
            .map(Self)
            .map_err(|error| CoreError::Invalid(format!("{side} snapshot: {error}")))
    }

    pub fn compare(
        &self,
        after: &Self,
        cancel: CancellationCheck<'_>,
    ) -> Result<SnapshotComparison, CoreError> {
        compare_bytes(&self.0, &after.0, cancel)
    }
}

/// Compare only the two explicitly selected regular files. No profile
/// discovery, dependencies, paths from INI values, or writes are performed.
pub fn compare_files(
    before: &Path,
    after: &Path,
    cancel: CancellationCheck<'_>,
) -> Result<SnapshotComparison, CoreError> {
    check_cancelled(cancel)?;
    let before = SelectedSnapshot::read(before, SnapshotSide::Before)?;
    check_cancelled(cancel)?;
    let after = SelectedSnapshot::read(after, SnapshotSide::After)?;
    before.compare(&after, cancel)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalized_comments_encoding_and_escapes_not_false_changes() {
        let mut after = vec![0xff, 0xfe];
        for unit in "[GENERAL]\r\nGameName=日本\r\n;comment".encode_utf16() {
            after.extend(unit.to_le_bytes());
        }
        let before = br"[General]
gamename=\x65e5\x672c";
        let report = compare_bytes(before, &after, &|| false).unwrap();
        assert!(report.effective_settings_equal);
        assert!(!report.byte_identical);
        assert_eq!(report.before_settings, 1);
        assert_eq!(report.after_settings, 1);
    }

    #[test]
    fn complete_diff_is_redacted_deterministic_and_no_guessed_attribution() {
        let before = br"[General]
gameName=Test game
gameVersion=prior private runtime
[Settings]
base_directory=Z:\hidden-old
secret=token-before
drops=hidden name";
        let after = br"[General]
GameName=Test game
game=private value
[Settings]
base_directory=Z:\hidden-new
secret=token-after
added=hidden secret";
        let report = compare_bytes(before, after, &|| false).unwrap();
        assert_eq!(report.before_settings, 5);
        assert_eq!(report.after_settings, 5);
        assert_eq!(
            report.changes,
            ChangeCounts {
                added: 2,
                removed: 2,
                changed: 2
            }
        );
        assert_eq!(
            report.unlabeled_changes,
            ChangeCounts {
                added: 1,
                removed: 1,
                changed: 1
            }
        );
        assert_eq!(
            report
                .labeled_changes
                .iter()
                .map(|r| r.setting)
                .collect::<Vec<_>>(),
            ["game", "gameRuntimeVersion", "instanceBaseDirectory"]
        );
        assert_eq!(report.editor_attribution, "unknown");
        assert_eq!(report.profile_identity, "notVerified");
        assert!(!report.effective_settings_equal);
        let json = serde_json::to_string(&report).unwrap();
        for secret in [
            "prior private",
            "hidden-old",
            "hidden-new",
            "token-before",
            "token-after",
            "private value",
            "drops",
            "hidden secret",
        ] {
            assert!(!json.contains(secret));
        }
        assert_eq!(report, compare_bytes(before, after, &|| false).unwrap());
    }

    #[test]
    fn ambiguity_and_decode_errors_refuse_entire_observation() {
        let plain = b"[settings]\nbase_directory=safe";
        let duplicate = b"[settings]\nBase_Directory=DoNotLeakOne\nbase_directory=DoNotLeakTwo";
        let invalid = compare_bytes(plain, duplicate, &|| false)
            .unwrap_err()
            .to_string();
        assert!(invalid.contains("After snapshot"));
        assert!(invalid.contains("Duplicate INI key at line 3"));
        assert!(!invalid.contains("DoNotLeak"));
        assert!(compare_bytes(&[0xff, 0xfe, 0], plain, &|| false).is_err());
        assert!(compare_bytes(plain, plain, &|| true).is_err());
    }
}
