//! Inventory one explicitly supplied SPID document. Not a distributor validator.
use serde::Serialize;
use std::io::Read;

const MAX_BYTES: usize = 512 * 1024;
const MAX_LINES: usize = 4096;
const MAX_LINE_BYTES: usize = 8192;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Report {
    source_name: String,
    scope: &'static str,
    runtime_status: &'static str,
    rows: Vec<Row>,
}

#[derive(Debug, Serialize)]
#[serde(
    tag = "status",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
enum Row {
    Declaration {
        line: usize,
        form_type: &'static str,
        target: Target,
        unevaluated_slots: usize,
    },
    Unreviewed {
        line: usize,
        reason: &'static str,
    },
}

#[derive(Debug, Serialize)]
#[serde(
    tag = "kind",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
enum Target {
    EditorId {
        editor_id: String,
    },
    PluginForm {
        plugin: String,
        local_form_hex: String,
    },
}

fn plain_plugin(name: &str) -> bool {
    name.rsplit_once('.').is_some_and(|(stem, ext)| {
        !stem.is_empty()
            && name.len() <= 160
            && stem
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b' ' | b'.' | b'_' | b'-'))
            && ["esp", "esm", "esl"]
                .iter()
                .any(|e| ext.eq_ignore_ascii_case(e))
    })
}

fn target(text: &str) -> Option<Target> {
    if let Some((id, plugin)) = text.split_once('~') {
        let hex = id
            .trim()
            .strip_prefix("0x")
            .or_else(|| id.trim().strip_prefix("0X"))?;
        let plugin = plugin.trim();
        // Skyrim SE local FormID identity only; never infer a runtime load index.
        if hex.is_empty()
            || hex.len() > 6
            || !hex.bytes().all(|b| b.is_ascii_hexdigit())
            || hex.bytes().all(|b| b == b'0')
            || !plain_plugin(plugin)
        {
            return None;
        }
        return Some(Target::PluginForm {
            plugin: plugin.into(),
            local_form_hex: hex.to_ascii_uppercase(),
        });
    }
    if text.starts_with("0x") || text.starts_with("0X") {
        return None;
    }
    if !text.is_empty()
        && text.len() <= 160
        && text.bytes().any(|b| b.is_ascii_alphabetic())
        && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
    {
        return Some(Target::EditorId {
            editor_id: text.into(),
        });
    }
    None
}

fn form_type(input: &str) -> Option<&'static str> {
    const TYPES: &[&str] = &[
        "Spell",
        "Perk",
        "Item",
        "Shout",
        "Package",
        "Keyword",
        "Outfit",
        "SleepOutfit",
        "Faction",
        "Skin",
        "Form",
    ];
    TYPES.iter().copied().find(|t| *t == input)
}

fn row(line: usize, text: &str) -> Row {
    let unreviewed = |reason| Row::Unreviewed { line, reason };
    let Some((key, body)) = text.split_once('=') else {
        return unreviewed("not_basic_assignment");
    };
    let Some(form_type) = form_type(key.trim()) else {
        return unreviewed("unsupported_directive");
    };
    let mut parts = body.split('|');
    let primary = parts.next().unwrap_or_default().trim();
    let unevaluated_slots = parts.count();
    if unevaluated_slots > 6 {
        return unreviewed("unsupported_slot_shape");
    }
    let Some(target) = target(primary) else {
        return unreviewed("unsupported_primary_identifier");
    };
    Row::Declaration {
        line,
        form_type,
        target,
        unevaluated_slots,
    }
}

pub fn run(name: &str, input: impl Read) -> Result<Report, String> {
    let valid_name = name.len() <= 160
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'_' | b' '))
        && name
            .to_ascii_lowercase()
            .strip_suffix("_distr.ini")
            .is_some_and(|stem| stem.bytes().any(|b| b.is_ascii_alphanumeric()));
    if !valid_name {
        return Err("Choose one SPID filename ending in _DISTR.ini, not a path".into());
    }
    let mut bytes = Vec::new();
    input
        .take(MAX_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > MAX_BYTES {
        return Err("SPID preview input exceeds the 512 KiB limit; no report emitted".into());
    }
    let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes);
    let input = std::str::from_utf8(bytes).map_err(|_| "SPID preview requires UTF-8 input")?;
    if input
        .chars()
        .any(|c| c.is_control() && !matches!(c, '\r' | '\n' | '\t'))
    {
        return Err("SPID preview input contains unsupported control characters".into());
    }
    let mut rows = Vec::new();
    let mut in_section = false;
    for (i, text) in input.lines().enumerate() {
        if i >= MAX_LINES || text.len() > MAX_LINE_BYTES {
            return Err(
                "SPID preview exceeds its line count or line length limit; no report emitted"
                    .into(),
            );
        }
        let text = text.trim();
        if text.is_empty() || text.starts_with(';') {
            continue;
        }
        if text.starts_with('[') {
            in_section = true;
        }
        rows.push(if in_section {
            Row::Unreviewed {
                line: i + 1,
                reason: "unsupported_section",
            }
        } else {
            row(i + 1, text)
        });
    }
    Ok(Report {
        source_name: name.into(),
        scope: "one supplied file; basic SPID declaration targets only; downstream slots not evaluated",
        runtime_status: "not_evaluated",
        rows,
    })
}
