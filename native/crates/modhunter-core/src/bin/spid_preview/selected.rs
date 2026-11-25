//! Read a single explicitly selected file, sharing the existing SPID parser.
use super::{MAX_BYTES, Report, run, valid_name};
use std::fs::{self, Metadata, OpenOptions};
use std::io::Read;
use std::path::Path;

// Same local-file shape and read-only handle conditions as ModHunter's NIF inspector.
fn regular(m: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if m.file_attributes() & 0x400 != 0 {
            return false;
        }
    }
    m.is_file()
}

fn same_stamp(a: &Metadata, b: &Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if (a.dev(), a.ino(), a.ctime(), a.ctime_nsec())
            != (b.dev(), b.ino(), b.ctime(), b.ctime_nsec())
        {
            return false;
        }
    }
    regular(a)
        && regular(b)
        && a.len() == b.len()
        && a.modified().is_ok_and(|m| b.modified().ok() == Some(m))
}

#[cfg(windows)]
fn windows_local_syntax(path: &Path) -> bool {
    use std::path::{Component, Prefix};
    match path.components().next() {
        Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_)) && path.is_absolute() => {
        }
        Some(Component::Prefix(_) | Component::RootDir) | None => return false,
        Some(_) => {}
    }
    path.components().all(|part| match part {
        Component::Normal(s) => s.to_str().is_some_and(|s| {
            let base = s
                .split('.')
                .next()
                .unwrap_or_default()
                .trim_end_matches(' ')
                .to_ascii_uppercase();
            let device = matches!(
                base.as_str(),
                "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
            ) || base
                .strip_prefix("COM")
                .or_else(|| base.strip_prefix("LPT"))
                .is_some_and(|n| {
                    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "¹", "²", "³"].contains(&n)
                });
            !device
                && !s.ends_with([' ', '.'])
                && !s
                    .chars()
                    .any(|c| c.is_ascii_control() || r#"<>:"|?*"#.contains(c))
        }),
        _ => true,
    })
}

pub(super) fn preview(path: &Path) -> Result<Report, String> {
    const INPUT: &str = "Select one ordinary file with a supported _DISTR.ini basename";
    const DRIFT: &str = "Selected SPID file changed while reading; no report emitted";
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| valid_name(name))
        .ok_or(INPUT)?;
    #[cfg(windows)]
    if !windows_local_syntax(path) {
        return Err(INPUT.into());
    }
    let before = fs::symlink_metadata(path).map_err(|_| INPUT)?;
    if !regular(&before) {
        return Err(INPUT.into());
    }
    if before.len() > MAX_BYTES as u64 {
        return Err("SPID preview input exceeds the 512 KiB limit; no report emitted".into());
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x0020_0000).share_mode(0x0000_0001);
    }
    let mut file = options.open(path).map_err(|_| INPUT)?;
    let opened = file.metadata().map_err(|_| DRIFT)?;
    if !same_stamp(&before, &opened) {
        return Err(DRIFT.into());
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| INPUT)?;
    let after = file.metadata().map_err(|_| DRIFT)?;
    let selected = fs::symlink_metadata(path).map_err(|_| DRIFT)?;
    if !same_stamp(&opened, &after)
        || !same_stamp(&after, &selected)
        || bytes.len() as u64 != opened.len()
    {
        return Err(DRIFT.into());
    }
    run(name, bytes.as_slice())
}
