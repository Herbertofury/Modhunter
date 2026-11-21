//! Explicit, one-shot review. The desktop owns file choices; the renderer never
//! grants path authority or receives a filename, settings value, or profile ID.
pub use modhunter_core::ini_snapshot::SnapshotSide as Side;
use modhunter_core::ini_snapshot::{ChangeKind, SelectedSnapshot, SnapshotComparison};
use std::{
    path::PathBuf,
    sync::atomic::{AtomicBool, AtomicU64, Ordering},
};

#[derive(Default)]
pub struct ReviewGate {
    serial: AtomicU64,
    view_revision: AtomicU64,
    running: AtomicBool,
    closed: AtomicBool,
}

impl ReviewGate {
    pub fn invalidate(&self) {
        self.view_revision.fetch_add(1, Ordering::AcqRel);
        self.serial.fetch_add(1, Ordering::AcqRel);
    }

    pub fn close(&self) {
        self.closed.store(true, Ordering::Release);
        self.invalidate();
    }

    pub fn view_revision(&self) -> u64 {
        self.view_revision.load(Ordering::Acquire)
    }

    pub fn current(&self, token: u64) -> bool {
        !self.closed.load(Ordering::Acquire) && self.serial.load(Ordering::Acquire) == token
    }

    // This is called from the existing shell blocking worker, not the UI thread.
    pub fn review(
        &self,
        mut select: impl FnMut(Side) -> Result<Option<PathBuf>, String>,
        context_current: impl Fn() -> bool + Sync,
    ) -> Result<Option<(u64, SnapshotComparison)>, String> {
        if !context_current() || self.closed.load(Ordering::Acquire) {
            return Ok(None);
        }
        self.running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "A settings comparison is already running.")?;
        struct Running<'a>(&'a AtomicBool);
        impl Drop for Running<'_> {
            fn drop(&mut self) {
                self.0.store(false, Ordering::Release);
            }
        }
        let _running = Running(&self.running);
        let token = self.serial.fetch_add(1, Ordering::AcqRel) + 1;
        let valid = || self.current(token) && context_current();
        let mut capture = |side| -> Result<Option<SelectedSnapshot>, String> {
            if !valid() {
                return Ok(None);
            }
            let path = select(side);
            if !valid() {
                return Ok(None);
            }
            let Some(path) = path? else {
                return Ok(None);
            };
            // Capture this choice before another native dialog can delay the
            // read. Do not reopen the first path later to reconstruct "Before".
            let snapshot = SelectedSnapshot::read(&path, side);
            if !valid() {
                return Ok(None);
            }
            snapshot.map(Some).map_err(|error| error.to_string())
        };
        let Some(before) = capture(Side::Before)? else {
            return Ok(None);
        };
        let Some(after) = capture(Side::After)? else {
            return Ok(None);
        };
        let compared = before.compare(&after, &|| !valid());
        if !valid() {
            return Ok(None);
        }
        compared
            .map(|report| Some((token, report)))
            .map_err(|error| error.to_string())
    }
}

/// Only static, localized labels and typed numeric counts enter the dialog.
pub fn format_report(report: &SnapshotComparison, translate: impl Fn(&str) -> String) -> String {
    let same = if report.byte_identical {
        "The two copies have identical bytes."
    } else if report.effective_settings_equal {
        "The effective settings match, but the input bytes differ."
    } else {
        "The effective settings differ."
    };
    let mut lines = vec![
        translate(same),
        String::new(),
        format!("{}: {}", translate("Added"), report.changes.added),
        format!("{}: {}", translate("Removed"), report.changes.removed),
        format!("{}: {}", translate("Changed"), report.changes.changed),
    ];
    if !report.labeled_changes.is_empty() {
        lines.push(String::new());
        for change in &report.labeled_changes {
            let label = match change.setting {
                "instanceBaseDirectory" => "Instance base directory",
                "modsDirectory" => "Mods directory",
                "profilesDirectory" => "Profiles directory",
                "gameNexusName" => "Game Nexus name",
                "gameName" => "Game name",
                "game" => "Game identifier",
                "gameRuntimeVersion" => "Game runtime version",
                _ => "Recognized setting",
            };
            let kind = match change.kind {
                ChangeKind::Added => "Added",
                ChangeKind::Removed => "Removed",
                ChangeKind::Changed => "Changed",
            };
            lines.push(format!("{}: {}", translate(label), translate(kind)));
        }
    }
    lines.extend([
        String::new(),
        translate("All changes are counted. Values and other setting names stay private."),
        translate("Profile and editor are not verified. No files were changed."),
    ]);
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn selected_bytes_cross_actual_core_and_redacted_result_is_exact() {
        let d = tempfile::tempdir().unwrap();
        let left = d.path().join("Private before copy.ini");
        let right = d.path().join("Private after copy.ini");
        let before = b"[General]\nGameVersion=DO_NOT_DISPLAY_BEFORE\n";
        let after = b"[General]\nGameVersion=DO_NOT_DISPLAY_AFTER\n";
        fs::write(&left, before).unwrap();
        fs::write(&right, after).unwrap();
        let gate = ReviewGate::default();
        let mut selection = Vec::new();
        let (token, result) = gate
            .review(
                |side| {
                    selection.push(side);
                    Ok(Some(match side {
                        Side::Before => left.clone(),
                        Side::After => right.clone(),
                    }))
                },
                || true,
            )
            .unwrap()
            .unwrap();
        assert_eq!(selection, [Side::Before, Side::After]);
        assert!(gate.current(token));
        assert_eq!(result.changes.changed, 1);
        assert_eq!(result.labeled_changes[0].setting, "gameRuntimeVersion");
        let receipt = serde_json::to_string(&result).unwrap();
        assert!(!receipt.contains("DO_NOT_DISPLAY"));
        assert!(!receipt.contains("Private"));
        let text = format_report(&result, str::to_owned);
        assert!(text.contains("Game runtime version: Changed"));
        assert!(!text.contains("DO_NOT_DISPLAY"));
        assert!(!text.contains("Private before"));
        assert_eq!(fs::read(left).unwrap(), before);
        assert_eq!(fs::read(right).unwrap(), after);
        assert_eq!(gate.view_revision(), 0);
        gate.invalidate();
        assert_eq!(gate.view_revision(), 1);
        assert!(!gate.current(token));
    }

    #[test]
    fn first_selection_is_captured_before_the_second_picker_can_observe_a_replacement() {
        let d = tempfile::tempdir().unwrap();
        let left = d.path().join("original-synthetic.ini");
        let right = d.path().join("new-synthetic.ini");
        let old = b"[General]\nGameVersion=DO_NOT_DISPLAY_BEFORE\n";
        let new = b"[General]\nGameVersion=DO_NOT_DISPLAY_AFTER\n";
        fs::write(&left, old).unwrap();
        fs::write(&right, new).unwrap();
        let gate = ReviewGate::default();
        let (_, report) = gate
            .review(
                |side| {
                    Ok(Some(match side {
                        Side::Before => left.clone(),
                        Side::After => {
                            // An external writer changes the original path
                            // while the second native picker is waiting.
                            fs::write(&left, new).unwrap();
                            right.clone()
                        }
                    }))
                },
                || true,
            )
            .unwrap()
            .unwrap();
        assert!(!report.byte_identical);
        assert_eq!(report.changes.changed, 1);
        let text = format_report(&report, str::to_owned);
        assert!(text.contains("Game runtime version: Changed"));
        assert!(!text.contains("DO_NOT_DISPLAY"));
        assert!(!text.contains("synthetic.ini"));
        assert_eq!(fs::read(&left).unwrap(), new);
        assert_eq!(fs::read(&right).unwrap(), new);
        assert_eq!(fs::read_dir(d.path()).unwrap().count(), 2);
    }

    #[test]
    fn inaccessible_first_copy_is_reported_before_requesting_the_second_copy() {
        let d = tempfile::tempdir().unwrap();
        let missing = d.path().join("PRIVATE_MISSING_COPY.ini");
        let gate = ReviewGate::default();
        let mut selections = Vec::new();
        let error = gate
            .review(
                |side| {
                    selections.push(side);
                    Ok(Some(missing.clone()))
                },
                || true,
            )
            .unwrap_err();
        assert_eq!(selections, [Side::Before]);
        assert!(error.contains("Before snapshot"));
        assert!(!error.contains("PRIVATE_MISSING_COPY"));
        assert!(!error.contains(d.path().to_str().unwrap()));
    }

    #[test]
    fn cancel_or_context_change_never_advances_to_second_picker() {
        let gates = ReviewGate::default();
        let mut calls = 0;
        assert!(
            gates
                .review(
                    |_| {
                        calls += 1;
                        Ok(None)
                    },
                    || true
                )
                .unwrap()
                .is_none()
        );
        assert_eq!(calls, 1);
        assert!(
            gates
                .review(
                    |_| {
                        calls += 1;
                        gates.invalidate();
                        Ok(Some("unused.ini".into()))
                    },
                    || true,
                )
                .unwrap()
                .is_none()
        );
        assert_eq!(calls, 2);
    }

    #[test]
    fn closing_with_second_choice_open_suppresses_no_file_and_future_picks() {
        let gate = ReviewGate::default();
        let mut calls = 0;
        let d = tempfile::tempdir().unwrap();
        let left = d.path().join("left.ini");
        fs::write(&left, b"[General]\nGameVersion=prior").unwrap();
        let result = gate.review(
            |side| {
                calls += 1;
                if side == Side::After {
                    gate.close();
                    return Ok(Some(d.path().join("deliberately_missing.ini")));
                }
                Ok(Some(left.clone()))
            },
            || true,
        );
        assert!(result.unwrap().is_none()); // stale rather than opening the missing input
        assert_eq!(calls, 2);
        assert!(
            gate.review(|_| panic!("a closed review cannot pick files"), || true)
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn nested_request_is_refused_and_a_retry_is_possible_after_cancel() {
        let gate = ReviewGate::default();
        assert!(
            gate.review(
                |_| {
                    assert!(
                        gate.review(|_| panic!("a second picker must not open"), || true)
                            .unwrap_err()
                            .contains("already running")
                    );
                    Ok(None)
                },
                || true
            )
            .unwrap()
            .is_none()
        );
        assert!(gate.review(|_| Ok(None), || true).unwrap().is_none());
    }
}
