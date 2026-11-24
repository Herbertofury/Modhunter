//! Conservative physical declaration preview for one explicitly supplied BOS INI.
//! Does not resolve forms, inspect a profile or infer the runtime winner.
use serde::Serialize;
use std::collections::BTreeMap;
use std::io::Read;

const MAX_BYTES: usize = 512 * 1024;
const MAX_LINES: usize = 4096;
const MAX_LINE_BYTES: usize = 2048;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
enum Section {
    Forms,
    References,
    Conditional,
    Properties,
    Unreviewed,
}

#[derive(Clone, Copy, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum Selector<'a> {
    EditorId {
        value: &'a str,
    },
    FormId {
        value: &'a str,
        plugin: Option<&'a str>,
        #[serde(skip)]
        id: u32,
    },
}

impl Selector<'_> {
    fn identity(self) -> String {
        match self {
            Self::EditorId { value } => format!("editor:{value}"),
            Self::FormId { plugin, id, .. } => {
                format!(
                    "form:{}:{id:x}",
                    plugin.unwrap_or_default().to_ascii_lowercase()
                )
            }
        }
    }
}

#[derive(Serialize)]
#[serde(
    tag = "state",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
enum Finding<'a> {
    DeclaredPair {
        base: Selector<'a>,
        replacement: Selector<'a>,
        has_optional_fields: bool,
    },
    Unreviewed {
        reason: &'static str,
    },
}

#[derive(Serialize)]
struct Row<'a> {
    line: usize,
    section: Section,
    #[serde(flatten)]
    finding: Finding<'a>,
}

#[derive(Serialize)]
struct Repeat<'a> {
    section: Section,
    selector: Selector<'a>,
    lines: Vec<usize>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Report<'a> {
    source_name: &'a str,
    runtime_status: &'static str,
    declared_pairs: usize,
    unreviewed_rows: usize,
    repeated_selectors: Vec<Repeat<'a>>,
    rows: Vec<Row<'a>>,
}

fn valid_filename(name: &str) -> bool {
    name.len() <= 160
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b' ' | b'.' | b'_' | b'-'))
        && name
            .to_ascii_lowercase()
            .strip_suffix("_swap.ini")
            .is_some_and(|stem| stem.bytes().any(|b| b.is_ascii_alphanumeric()))
}

fn plugin_name(name: &str) -> bool {
    name.len() <= 128
        && !name.contains("..")
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b' ' | b'.' | b'_' | b'-'))
        && name.rsplit_once('.').is_some_and(|(stem, suffix)| {
            stem.bytes().any(|b| b.is_ascii_alphanumeric())
                && ["esp", "esm", "esl"]
                    .iter()
                    .any(|ext| suffix.eq_ignore_ascii_case(ext))
        })
}

fn hexadecimal(value: &str) -> Option<u32> {
    let digits = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))?;
    if digits.len() > 8 || !digits.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    u32::from_str_radix(digits, 16).ok().filter(|&n| n != 0)
}

fn selector(text: &str) -> Option<Selector<'_>> {
    let text = text.trim();
    if let Some((value, name)) = text.split_once('~') {
        let id = hexadecimal(value)?;
        return plugin_name(name).then_some(Selector::FormId {
            value,
            plugin: Some(name),
            id,
        });
    }
    if let Some(id) = hexadecimal(text) {
        return Some(Selector::FormId {
            value: text,
            plugin: None,
            id,
        });
    }
    // Bare all-hex values can be interpreted as numbers; do not guess.
    (text.len() <= 96
        && !text.bytes().all(|b| b.is_ascii_hexdigit())
        && text
            .bytes()
            .next()
            .is_some_and(|b| b.is_ascii_alphabetic() || b == b'_')
        && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'))
    .then_some(Selector::EditorId { value: text })
}

fn section(title: &str) -> Section {
    match title.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
        Some("Forms") => Section::Forms,
        Some("References") => Section::References,
        Some("Transforms" | "Properties") => Section::Properties,
        Some(s) if s.contains('|') => Section::Conditional,
        _ => Section::Unreviewed,
    }
}

fn review(section: Section, text: &str) -> Finding<'_> {
    let unreviewed = |reason| Finding::Unreviewed { reason };
    if !matches!(section, Section::Forms | Section::References) {
        return unreviewed("section_not_reviewed");
    }
    if text.contains(['=', '#', ';']) || text.bytes().any(|b| b.is_ascii_control() && b != b'\t') {
        return unreviewed("ambiguous_entry");
    }
    let mut fields = text.split('|');
    let first = fields.next().unwrap_or_default();
    let Some(second) = fields.next() else {
        return unreviewed("no_paired_selectors");
    };
    if first.contains(',') || second.contains(',') {
        return unreviewed("selector_set_not_reviewed");
    }
    let optional = fields.count();
    if optional > 2 {
        return unreviewed("extra_fields_not_reviewed");
    }
    let (Some(base), Some(replacement)) = (selector(first), selector(second)) else {
        return unreviewed("unsupported_selector");
    };
    Finding::DeclaredPair {
        base,
        replacement,
        has_optional_fields: optional > 0,
    }
}

fn preview<'a>(name: &'a str, text: &'a str) -> Report<'a> {
    let mut rows = Vec::new();
    let mut groups: BTreeMap<(Section, String), (Selector<'a>, Vec<usize>)> = BTreeMap::new();
    let mut current = Section::Unreviewed;
    let mut declared_pairs = 0;
    for (i, raw) in text.lines().enumerate() {
        let line = i + 1;
        let content = raw.trim();
        if content.is_empty() || content.starts_with([';', '#']) {
            continue;
        }
        if content.starts_with('[') {
            // A malformed section header cannot inherit the previous section.
            current = section(content);
            continue;
        }
        let finding = review(current, content);
        if let Finding::DeclaredPair { base, .. } = finding {
            declared_pairs += 1;
            groups
                .entry((current, base.identity()))
                .or_insert_with(|| (base, Vec::new()))
                .1
                .push(line);
        }
        rows.push(Row {
            line,
            section: current,
            finding,
        });
    }
    let mut repeated_selectors: Vec<_> = groups
        .into_iter()
        .filter(|(_, (_, lines))| lines.len() > 1)
        .map(|((section, _), (selector, lines))| Repeat {
            section,
            selector,
            lines,
        })
        .collect();
    repeated_selectors.sort_by_key(|entry| entry.lines[0]);
    Report {
        source_name: name,
        runtime_status: "not_evaluated",
        declared_pairs,
        unreviewed_rows: rows.len() - declared_pairs,
        repeated_selectors,
        rows,
    }
}

pub(super) fn run(name: &str, input: impl Read) -> Result<String, Box<dyn std::error::Error>> {
    if !valid_filename(name) {
        return Err("Choose a label ending in _SWAP.ini, not a file path".into());
    }
    let mut bytes = Vec::new();
    input.take((MAX_BYTES + 1) as u64).read_to_end(&mut bytes)?;
    if bytes.len() > MAX_BYTES {
        return Err("BOS preview exceeds 512 KiB".into());
    }
    let text = std::str::from_utf8(&bytes).map_err(|_| "BOS preview needs UTF-8 text")?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    for (i, line) in text.lines().enumerate() {
        if i >= MAX_LINES || line.len() > MAX_LINE_BYTES {
            return Err("BOS preview exceeds 4096 lines or 2048 bytes on one line".into());
        }
    }
    Ok(serde_json::to_string(&preview(name, text))?)
}
