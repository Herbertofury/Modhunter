//! Selected-file declaration evidence for a narrow KID v4.1.0 syntax subset.
//! Grammar facts: powerof3/Keyword-Item-Distributor v4.1.0, LookupConfigs,
//! EnumDefs and FormData. Independent preview, no runtime resolver or upstream code.
use serde::Serialize;
use std::io::Read;

const BYTE_LIMIT: usize = 512 * 1024;
const LINE_LIMIT: usize = 4096;
const LINE_BYTES: usize = 8192;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Report {
    source_name: String,
    scope: &'static str,
    runtime_status: &'static str,
    rows: Vec<Row>,
}

#[derive(Serialize)]
#[serde(
    tag = "status",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
enum Row {
    Declaration {
        line: usize,
        item_type: &'static str,
        keyword: Keyword,
        unevaluated_slots: usize,
    },
    Unreviewed {
        line: usize,
        reason: &'static str,
    },
}

#[derive(Serialize)]
#[serde(
    tag = "kind",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
enum Keyword {
    EditorId {
        editor_id: String,
    },
    PluginForm {
        plugin: String,
        local_form_hex: String,
    },
    UnqualifiedForm {
        form_hex: String,
    },
}

fn basename(text: &str) -> bool {
    text.len() <= 160
        && text
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b". -_".contains(&b))
        && text.bytes().any(|b| b.is_ascii_alphanumeric())
}

fn hex_id(text: &str, max: usize) -> Option<&str> {
    let raw = text
        .strip_prefix("0x")
        .or_else(|| text.strip_prefix("0X"))?;
    (!raw.is_empty()
        && raw.len() <= max
        && raw.bytes().all(|b| b.is_ascii_hexdigit())
        && raw.bytes().any(|b| b != b'0'))
    .then_some(raw)
}

fn editor_id(text: &str) -> bool {
    // Do not guess whether unresolved all-hex tokens are FormIDs or editor IDs.
    text.len() <= 160
        && text
            .as_bytes()
            .first()
            .is_some_and(|b| b.is_ascii_alphabetic() || *b == b'_')
        && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
        && !text.bytes().all(|b| b.is_ascii_hexdigit())
        && !text.eq_ignore_ascii_case("NONE")
}

fn keyword(text: &str) -> Option<Keyword> {
    if let Some((id, plugin)) = text.split_once('~') {
        let (stem, ext) = plugin.rsplit_once('.')?;
        if !basename(stem)
            || !["esp", "esm", "esl"]
                .iter()
                .any(|s| ext.eq_ignore_ascii_case(s))
            || plugin.len() > 160
            || plugin != plugin.trim()
        {
            return None;
        }
        return Some(Keyword::PluginForm {
            plugin: plugin.into(),
            local_form_hex: hex_id(id, 6)?.to_ascii_uppercase(),
        });
    }
    if text.starts_with("0x") || text.starts_with("0X") {
        return Some(Keyword::UnqualifiedForm {
            form_hex: hex_id(text, 8)?.to_ascii_uppercase(),
        });
    }
    editor_id(text).then(|| Keyword::EditorId {
        editor_id: text.into(),
    })
}

fn item_type(text: &str) -> Option<&'static str> {
    // Application-facing names, not underlying engine form enum values.
    const TYPES: &[&str] = &[
        "Activator",
        "Ammo",
        "Armor",
        "Book",
        "Enchantment",
        "Flora",
        "Furniture",
        "Ingredient",
        "Key",
        "Location",
        "Magic Effect",
        "Misc Item",
        "Potion",
        "Race",
        "Scroll",
        "Soul Gem",
        "Spell",
        "Talking Activator",
        "Weapon",
    ];
    TYPES.iter().copied().find(|&item| item == text)
}

fn decimal_percent(text: &str) -> bool {
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let syntax = text
        .split_once('.')
        .map_or_else(|| digits(text), |(a, b)| digits(a) && digits(b));
    syntax
        && text.len() <= 12
        && text
            .parse::<f64>()
            .is_ok_and(|v| (0.0..=100.0).contains(&v))
}

fn declaration(line: usize, text: &str) -> Row {
    let unreviewed = |reason| Row::Unreviewed { line, reason };
    let Some((key, value)) = text.split_once('=') else {
        return unreviewed("not_basic_assignment");
    };
    if !key.trim().eq_ignore_ascii_case("keyword") {
        return unreviewed("unsupported_directive");
    }

    let parts: Vec<_> = value.trim().split('|').take(6).collect();
    if !(2..=5).contains(&parts.len()) || parts.iter().any(|p| p.is_empty()) {
        return unreviewed("unsupported_slot_shape");
    }
    let Some(item_type) = item_type(parts[1]) else {
        return unreviewed("unsupported_item_type");
    };
    let Some(keyword) = keyword(parts[0]) else {
        return unreviewed("unsupported_keyword_identifier");
    };
    let is_none = |s: &str| s.eq_ignore_ascii_case("NONE");
    // Only plain comma-separated filter IDs; no compound operators, runtime or trait grammar.
    if parts
        .get(2)
        .is_some_and(|p| !is_none(p) && !p.split(',').all(|t| self::keyword(t.trim()).is_some()))
        || parts.get(3).is_some_and(|p| !is_none(p))
        || parts
            .get(4)
            .is_some_and(|p| !is_none(p) && !decimal_percent(p))
    {
        return unreviewed("unsupported_optional_syntax");
    }
    Row::Declaration {
        line,
        item_type,
        keyword,
        unevaluated_slots: parts.len() - 2,
    }
}

pub fn run(name: &str, input: impl Read) -> Result<Report, String> {
    if !basename(name)
        || !name
            .to_ascii_lowercase()
            .strip_suffix("_kid.ini")
            .is_some_and(basename)
    {
        return Err("Choose one filename ending in _KID.ini, not a path".into());
    }

    let mut bytes = Vec::new();
    input
        .take(BYTE_LIMIT as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "KID preview could not read stdin")?;
    if bytes.len() > BYTE_LIMIT {
        return Err("KID preview input exceeds 512 KiB; no report emitted".into());
    }
    let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes);
    let input = std::str::from_utf8(bytes).map_err(|_| "KID preview requires UTF-8 input")?;
    if input
        .chars()
        .any(|c| c.is_control() && !matches!(c, '\r' | '\n' | '\t'))
    {
        return Err("KID preview input contains unsupported control characters".into());
    }
    let mut rows = Vec::new();
    let mut section = false;
    for (i, raw) in input.lines().enumerate() {
        if i >= LINE_LIMIT || raw.len() > LINE_BYTES {
            return Err("KID preview exceeds its physical line limit; no report emitted".into());
        }
        if raw.contains('\r') {
            return Err("KID preview requires LF or CRLF line endings".into());
        }
        let text = raw.trim();
        if text.is_empty() || text.starts_with(';') {
            continue;
        }
        section |= text.starts_with('[');
        let line = i + 1;
        rows.push(if section {
            Row::Unreviewed {
                line,
                reason: "unsupported_section",
            }
        } else {
            declaration(line, text)
        });
    }
    Ok(Report {
        source_name: name.into(),
        scope: "single supplied KID INI; selected declaration syntax, not rule or runtime validation",
        runtime_status: "not_evaluated",
        rows,
    })
}
