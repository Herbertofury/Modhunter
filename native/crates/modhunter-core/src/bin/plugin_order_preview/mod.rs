//! Offline CLI: bind or preview an explicitly supplied inert snapshot.
//! No MO2 bridge, game lookup, filesystem discovery, mutator or command execution.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    io::{self, Read},
};

const MAX_BYTES: usize = 2 * 1024 * 1024;
const MAX_ROWS: usize = 4096;
const MAX_MASTER_LINKS: usize = 16384;
type Result<T> = std::result::Result<T, &'static str>;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Snapshot {
    profile_id: String,
    execution_epoch: u64,
    rows: Vec<Row>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Row {
    id: String,
    file_name: String,
    enabled: bool,
    order_class: OrderClass,
    // null means not inspected, [] means explicitly observed empty in this input.
    declared_masters: Option<Vec<String>>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum OrderClass {
    LockedMaster,
    Master,
    Regular,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Identity {
    profile_id: String,
    execution_epoch: u64,
    snapshot_sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Request {
    expected: Identity,
    snapshot: Snapshot,
    action: Action,
}

#[derive(Debug, Deserialize)]
#[serde(
    tag = "type",
    rename_all = "snake_case",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
enum Action {
    SetEnabled { id: String, enabled: bool },
    MoveBefore { id: String, anchor_id: String },
    MoveAfter { id: String, anchor_id: String },
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct Placement {
    id: String,
    index: usize,
    enabled: bool,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct Finding {
    plugin_id: String,
    declared_master: Option<String>,
    status: &'static str,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct Preview {
    scope: &'static str,
    status: &'static str,
    before: Identity,
    after: Identity,
    before_order: Vec<Placement>,
    after_order: Vec<Placement>,
    before_direct_master_findings: Vec<Finding>,
    after_direct_master_findings: Vec<Finding>,
    record_conflicts: &'static str,
    asset_conflicts: &'static str,
    actual_profile_accepted: &'static str,
}

fn plugin_name(name: &str) -> bool {
    (1..=160).contains(&name.len())
        && name == name.trim()
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b" -_.".contains(&b))
        && name.rsplit_once('.').is_some_and(|(stem, ext)| {
            stem.bytes().any(|b| b.is_ascii_alphanumeric())
                && ["esp", "esm", "esl"]
                    .iter()
                    .any(|e| ext.eq_ignore_ascii_case(e))
        })
}

fn valid_id(id: &str) -> bool {
    (1..=64).contains(&id.len())
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b))
}

fn validate(snapshot: &Snapshot) -> Result<()> {
    if snapshot.profile_id.len() != 16
        || !snapshot
            .profile_id
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || snapshot.rows.is_empty()
        || snapshot.rows.len() > MAX_ROWS
    {
        return Err("Invalid or oversized supplied profile snapshot");
    }
    let mut ids = HashSet::new();
    let mut names = HashSet::new();
    let mut seen_regular = false;
    let mut count = 0;
    for row in &snapshot.rows {
        if !valid_id(&row.id)
            || !ids.insert(&row.id)
            || !plugin_name(&row.file_name)
            || !names.insert(row.file_name.to_ascii_lowercase())
        {
            return Err("Invalid or duplicate plugin name or stable ID");
        }
        if row.order_class == OrderClass::LockedMaster && !row.enabled {
            return Err("A designated locked master must already be enabled");
        }
        if row.order_class == OrderClass::Regular {
            seen_regular = true;
        } else if seen_regular {
            return Err("Caller-provided master and regular groups are not ordered");
        }
        if let Some(masters) = &row.declared_masters {
            count += masters.len();
            if count > MAX_MASTER_LINKS {
                return Err("Too many supplied declared-master edges");
            }
            let mut unique = HashSet::new();
            if masters
                .iter()
                .any(|name| !plugin_name(name) || !unique.insert(name.to_ascii_lowercase()))
            {
                return Err("Invalid or duplicate declared-master name");
            }
        }
    }
    Ok(())
}

fn identity(snapshot: &Snapshot) -> Result<Identity> {
    validate(snapshot)?;
    let bytes = serde_json::to_vec(snapshot).map_err(|_| "Cannot serialize supplied snapshot")?;
    Ok(Identity {
        profile_id: snapshot.profile_id.clone(),
        execution_epoch: snapshot.execution_epoch,
        snapshot_sha256: format!(
            "{:x}",
            Sha256::digest([b"plugin-order-preview-v1\0".as_slice(), &bytes].concat())
        ),
    })
}

fn placements(snapshot: &Snapshot) -> Vec<Placement> {
    snapshot
        .rows
        .iter()
        .enumerate()
        .map(|(index, row)| Placement {
            id: row.id.clone(),
            index,
            enabled: row.enabled,
        })
        .collect()
}

fn findings(snapshot: &Snapshot) -> Vec<Finding> {
    let by_name: HashMap<_, _> = snapshot
        .rows
        .iter()
        .enumerate()
        .map(|(i, row)| (row.file_name.to_ascii_lowercase(), (i, row.enabled)))
        .collect();
    let mut findings = Vec::new();
    for (i, row) in snapshot
        .rows
        .iter()
        .enumerate()
        .filter(|(_, row)| row.enabled)
    {
        let mut add = |status, master: Option<&str>| {
            findings.push(Finding {
                plugin_id: row.id.clone(),
                declared_master: master.map(str::to_owned),
                status,
            })
        };
        match &row.declared_masters {
            None => add("declarations_unknown", None),
            Some(masters) => {
                for master in masters {
                    let status = match by_name.get(&master.to_ascii_lowercase()) {
                        None => "not_in_supplied_snapshot",
                        Some((_, false)) => "declared_master_disabled_in_snapshot",
                        Some((j, _)) if *j == i => "self_reference_in_snapshot",
                        Some((j, _)) if *j > i => "declared_master_after_plugin_in_snapshot",
                        _ => continue,
                    };
                    add(status, Some(master));
                }
            }
        }
    }
    findings
}

fn preview(request: Request) -> Result<Preview> {
    let original = request.snapshot;
    let before = identity(&original)?;
    if before.profile_id != request.expected.profile_id {
        return Err("Profile ID changed since capture; capture again before previewing");
    }
    if before.execution_epoch != request.expected.execution_epoch {
        return Err(
            "Snapshot execution epoch changed since capture; capture again before previewing",
        );
    }
    if before.snapshot_sha256 != request.expected.snapshot_sha256 {
        return Err(
            "Supplied snapshot digest changed since capture; capture again before previewing",
        );
    }
    let mut after = original.clone();
    let index = |id: &str| {
        original
            .rows
            .iter()
            .position(|row| row.id == id)
            .ok_or("Selected or anchor stable ID is not in the exact snapshot")
    };
    match request.action {
        Action::SetEnabled { ref id, enabled } => {
            let row = &mut after.rows[index(id)?];
            if row.order_class == OrderClass::LockedMaster {
                return Err("Locked master cannot be toggled in this preview");
            }
            row.enabled = enabled;
        }
        Action::MoveBefore {
            ref id,
            ref anchor_id,
        }
        | Action::MoveAfter {
            ref id,
            ref anchor_id,
        } => {
            let from = index(id)?;
            let anchor = index(anchor_id)?;
            if from == anchor {
                return Err("Source and anchor must be different stable IDs");
            }
            if after.rows[from].order_class == OrderClass::LockedMaster {
                return Err("Locked master cannot be moved in this preview");
            }
            let place_after = matches!(request.action, Action::MoveAfter { .. });
            let insertion = anchor - usize::from(from < anchor) + usize::from(place_after);
            let row = after.rows.remove(from);
            after.rows.insert(insertion, row);
            if original
                .rows
                .iter()
                .zip(&after.rows)
                .any(|(old, new)| old.order_class == OrderClass::LockedMaster && old.id != new.id)
            {
                return Err("Proposed order changes a locked master position");
            }
        }
    }
    let after_identity = identity(&after)?;
    Ok(Preview {
        scope: "caller_supplied_snapshot_only",
        status: if before == after_identity {
            "no_change_in_preview"
        } else {
            "proposed_not_applied"
        },
        before,
        after: after_identity,
        before_order: placements(&original),
        after_order: placements(&after),
        before_direct_master_findings: findings(&original),
        after_direct_master_findings: findings(&after),
        record_conflicts: "not_evaluated",
        asset_conflicts: "not_evaluated",
        actual_profile_accepted: "not_evaluated",
    })
}

fn parse_stdin(command: &str, input: impl Read) -> Result<String> {
    let mut bytes = Vec::new();
    input
        .take(MAX_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Cannot read snapshot from stdin")?;
    if bytes.len() > MAX_BYTES {
        return Err("Snapshot input exceeds 2 MiB; no report emitted");
    }
    match command {
        "capture" => {
            let snapshot = serde_json::from_slice(&bytes).map_err(|_| "Invalid snapshot JSON")?;
            serde_json::to_string(&identity(&snapshot)?)
                .map_err(|_| "Cannot serialize snapshot receipt")
        }
        "preview" => {
            let req = serde_json::from_slice(&bytes).map_err(|_| "Invalid preview request JSON")?;
            serde_json::to_string(&preview(req)?).map_err(|_| "Cannot serialize snapshot preview")
        }
        _ => Err(USAGE),
    }
}

const USAGE: &str = "plugin-order-preview requires capture or preview and one JSON object on stdin";

pub(super) fn run(args: &[String]) -> Result<()> {
    let [command] = args else {
        return Err(USAGE);
    };
    if !matches!(command.as_str(), "capture" | "preview") {
        return Err(USAGE);
    }
    let report = parse_stdin(command, io::stdin().lock())?;
    println!("{report}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Snapshot {
        serde_json::from_value(serde_json::json!({
            "profileId":"0123456789abcdef", "executionEpoch":7,
            "rows":[
                {"id":"game", "fileName":"Base.esm", "enabled":true, "orderClass":"locked_master", "declaredMasters":[]},
                {"id":"utility", "fileName":"Utility.esm", "enabled":true, "orderClass":"master", "declaredMasters":["base.esm"]},
                {"id":"base-module", "fileName":"Feature.esp", "enabled":true, "orderClass":"regular", "declaredMasters":[]},
                {"id":"add-on", "fileName":"Addon.esp", "enabled":true, "orderClass":"regular", "declaredMasters":["Feature.ESP","Utility.esm","External.esl"]},
                {"id":"dynamic", "fileName":"Dynamic.esp", "enabled":false, "orderClass":"regular", "declaredMasters":null}
            ]
        })).unwrap()
    }

    fn request(snapshot: Snapshot, action: Action) -> Request {
        Request {
            expected: identity(&snapshot).unwrap(),
            snapshot,
            action,
        }
    }

    fn proposed(snapshot: Snapshot, kind: &str, id: &str, anchor: &str) -> Result<Preview> {
        let action = serde_json::from_value(serde_json::json!({
            "type":kind,"id":id,"anchorId":anchor
        }))
        .unwrap();
        preview(request(snapshot, action))
    }

    fn contains(items: &[Finding], plugin: &str, master: Option<&str>, status: &str) -> bool {
        items.iter().any(|f| {
            f.plugin_id == plugin && f.declared_master.as_deref() == master && f.status == status
        })
    }

    #[test]
    fn disabling_preserves_every_row_and_evidences_the_direct_dependent() {
        let s = fixture();
        let p = preview(request(
            s.clone(),
            Action::SetEnabled {
                id: "base-module".into(),
                enabled: false,
            },
        ))
        .unwrap();
        assert_eq!(p.status, "proposed_not_applied");
        assert_eq!(p.before.profile_id, p.after.profile_id);
        assert_eq!(p.before.execution_epoch, p.after.execution_epoch);
        assert_ne!(p.before.snapshot_sha256, p.after.snapshot_sha256);
        for (i, (older, newer)) in p.before_order.iter().zip(&p.after_order).enumerate() {
            assert_eq!((&older.id, older.index), (&newer.id, newer.index));
            assert_eq!(newer.enabled, if i == 2 { false } else { older.enabled });
        }
        assert!(contains(
            &p.after_direct_master_findings,
            "add-on",
            Some("Feature.ESP"),
            "declared_master_disabled_in_snapshot"
        ));
        assert!(contains(
            &p.before_direct_master_findings,
            "add-on",
            Some("External.esl"),
            "not_in_supplied_snapshot"
        ));
        assert!(!contains(
            &p.before_direct_master_findings,
            "add-on",
            Some("Feature.ESP"),
            "declared_master_disabled_in_snapshot"
        ));
        assert_eq!(p.record_conflicts, "not_evaluated");
        assert_eq!(p.asset_conflicts, "not_evaluated");
        assert_eq!(p.actual_profile_accepted, "not_evaluated");
        assert_eq!(p.before, identity(&s).unwrap());
    }

    #[test]
    fn moving_in_both_directions_keeps_other_relative_order_and_warns_on_dependency() {
        for (kind, id, anchor) in [
            ("move_after", "base-module", "add-on"),
            ("move_before", "add-on", "base-module"),
        ] {
            let p = proposed(fixture(), kind, id, anchor).unwrap();
            let ids: Vec<_> = p.after_order.iter().map(|e| e.id.as_str()).collect();
            assert_eq!(ids, ["game", "utility", "add-on", "base-module", "dynamic"]);
            assert!(contains(
                &p.after_direct_master_findings,
                "add-on",
                Some("Feature.ESP"),
                "declared_master_after_plugin_in_snapshot"
            ));
        }
        assert_eq!(
            proposed(fixture(), "move_before", "base-module", "add-on")
                .unwrap()
                .status,
            "no_change_in_preview"
        );
    }

    #[test]
    fn cannot_guess_or_reorder_locked_masters_or_cross_declared_group_boundary() {
        for (id, anchor) in [
            ("game", "utility"),
            ("utility", "game"),
            ("base-module", "utility"),
            ("ADD-ON", "base-module"),
            ("base-module", "base-module"),
        ] {
            assert!(proposed(fixture(), "move_before", id, anchor).is_err());
        }
        assert!(
            preview(request(
                fixture(),
                Action::SetEnabled {
                    id: "game".into(),
                    enabled: false
                }
            ))
            .is_err()
        );
    }

    #[test]
    fn empty_known_master_list_is_not_unknown_and_no_op_has_matching_receipts() {
        let p = preview(request(
            fixture(),
            Action::SetEnabled {
                id: "dynamic".into(),
                enabled: true,
            },
        ))
        .unwrap();
        assert!(contains(
            &p.after_direct_master_findings,
            "dynamic",
            None,
            "declarations_unknown"
        ));
        assert!(!contains(
            &p.after_direct_master_findings,
            "base-module",
            None,
            "declarations_unknown"
        ));
        assert!(!contains(
            &p.before_direct_master_findings,
            "dynamic",
            None,
            "declarations_unknown"
        ));
        let p = preview(request(
            fixture(),
            Action::SetEnabled {
                id: "dynamic".into(),
                enabled: false,
            },
        ))
        .unwrap();
        assert_eq!(p.before, p.after);
        assert_eq!(p.status, "no_change_in_preview");
    }

    #[test]
    fn stale_profile_epoch_same_epoch_reorder_and_observation_drift_are_refused() {
        let s = fixture();
        let action = || Action::SetEnabled {
            id: "dynamic".into(),
            enabled: true,
        };
        let mut a = request(s.clone(), action());
        a.snapshot.execution_epoch += 1;
        assert!(preview(a).is_err());
        let mut a = request(s.clone(), action());
        a.snapshot.profile_id = "1123456789abcdef".into();
        assert!(preview(a).is_err());
        let mut a = request(s.clone(), action());
        a.snapshot.rows.swap(2, 3);
        assert!(preview(a).is_err());
        let mut a = request(s, action());
        a.snapshot.rows[4].declared_masters = Some(vec![]);
        assert!(preview(a).is_err());
    }

    #[test]
    fn request_limits_strict_shape_and_duplicate_identity_fail_closed() {
        let mut s = fixture();
        s.rows[1].id = s.rows[0].id.clone();
        assert!(identity(&s).is_err());
        let mut s = fixture();
        s.rows[1].file_name = "bAse.ESM".into();
        assert!(identity(&s).is_err());
        let mut rhs = serde_json::to_string(&fixture()).unwrap();
        rhs.insert_str(1, "\"profileId\":\"1123456789abcdef\",");
        assert!(parse_stdin("capture", rhs.as_bytes()).is_err());
        assert!(parse_stdin("preview", b"{}".as_slice()).is_err());
        assert!(parse_stdin("capture", vec![b' '; MAX_BYTES + 1].as_slice()).is_err());
        let mut s = fixture();
        s.rows
            .extend(std::iter::repeat_n(s.rows[0].clone(), MAX_ROWS));
        assert!(identity(&s).is_err());
    }

    #[test]
    fn reports_self_reference_without_treating_other_editions_or_an_actual_profile_as_seen() {
        let mut s = fixture();
        s.rows[3].declared_masters = Some(vec!["Addon.esp".into()]);
        let p = preview(request(
            s,
            Action::SetEnabled {
                id: "dynamic".into(),
                enabled: true,
            },
        ))
        .unwrap();
        assert!(contains(
            &p.after_direct_master_findings,
            "add-on",
            Some("Addon.esp"),
            "self_reference_in_snapshot"
        ));
        assert_eq!(p.scope, "caller_supplied_snapshot_only");
    }
}
