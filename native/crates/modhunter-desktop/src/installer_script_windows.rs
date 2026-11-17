//! Pass a verified resource to Windows PowerShell without changing its identity.
//!
//! Tauri returns canonical `\\?\` paths. PowerShell 5.1 exposes that spelling as
//! PSScriptRoot, which the installer deliberately rejects. Only the child -File
//! argument uses the conventional spelling; verification and read locks keep
//! their original native paths.

use crate::installer_bundle::{VerifiedBundle, reject_link_ancestors};
use std::{
    ffi::OsString,
    fs,
    os::windows::ffi::{OsStrExt, OsStringExt},
    path::{Path, PathBuf},
};

type Result<T> = std::result::Result<T, String>;
const SCRIPT: &str = "Install_MO2_Plugin.ps1";
const LEGACY_MAX_PATH: usize = 260;

fn unsupported(reason: &str) -> String {
    format!(
        "The bundled installer location is not supported by Windows PowerShell 5.1 ({reason}). Move the complete Modhunter folder to a shorter conventional Windows path and try again."
    )
}

fn starts_ascii_case_insensitive(value: &[u16], prefix: &[u8]) -> bool {
    value.len() >= prefix.len()
        && value
            .iter()
            .zip(prefix)
            .all(|(&unit, &ascii)| unit <= 0x7f && (unit as u8).eq_ignore_ascii_case(&ascii))
}

fn separator(unit: u16) -> bool {
    unit == u16::from(b'\\') || unit == u16::from(b'/')
}

fn invalid_character(unit: u16) -> bool {
    unit < 32 || b"<>:\"|?*".iter().any(|&ascii| unit == u16::from(ascii))
}

fn reserved_filename(component: &[u16]) -> bool {
    // Win32 recognizes device names even before an extension. Avoid lossy
    // conversion and also cover its superscript COM/LPT digits and console
    // aliases; none may be reinterpreted as a device at the legacy boundary.
    let stem = component
        .split(|&unit| unit == u16::from(b'.'))
        .next()
        .unwrap_or_default();
    let mut end = stem.len();
    while end > 0 && stem[end - 1] == u16::from(b' ') {
        end -= 1;
    }
    let stem = &stem[..end];
    [
        b"CON".as_slice(),
        b"PRN",
        b"AUX",
        b"NUL",
        b"CONIN$",
        b"CONOUT$",
    ]
    .iter()
    .any(|name| stem.len() == name.len() && starts_ascii_case_insensitive(stem, name))
        || (stem.len() == 4
            && (starts_ascii_case_insensitive(stem, b"COM")
                || starts_ascii_case_insensitive(stem, b"LPT"))
            && matches!(stem[3], 0x31..=0x39 | 0x00b9 | 0x00b2 | 0x00b3))
}

fn ordinary_spelling(path: &Path) -> Result<PathBuf> {
    let original: Vec<u16> = path.as_os_str().encode_wide().collect();
    let ordinary = if starts_ascii_case_insensitive(&original, br"\\?\UNC\") {
        [vec![u16::from(b'\\'); 2], original[8..].to_vec()].concat()
    } else if original.starts_with(&[92, 92, 63, 92]) {
        if original.len() < 7
            || !matches!(original[4], 0x41..=0x5a | 0x61..=0x7a)
            || original[5] != u16::from(b':')
            || original[6] != u16::from(b'\\')
        {
            return Err(unsupported("device or volume namespace"));
        }
        original[4..].to_vec()
    } else {
        original
    };
    // The unchanged Windows PowerShell 5.1/.NET resource reader uses ordinary
    // Win32 paths. Include the terminating NUL in its documented MAX_PATH
    // bound, and refuse rather than truncate or reinterpret a longer path.
    if ordinary.len() >= LEGACY_MAX_PATH {
        return Err(unsupported(
            "resource path exceeds the legacy Windows limit",
        ));
    }
    let filesystem_parts = if ordinary.len() >= 3
        && matches!(ordinary[0], 0x41..=0x5a | 0x61..=0x7a)
        && ordinary[1] == u16::from(b':')
        && separator(ordinary[2])
    {
        ordinary[3..]
            .split(|&unit| separator(unit))
            .collect::<Vec<_>>()
    } else if ordinary.starts_with(&[92, 92]) {
        let parts: Vec<_> = ordinary[2..].split(|&unit| separator(unit)).collect();
        if parts.len() < 2
            || parts[..2].iter().any(|part| {
                part.is_empty()
                    || part.iter().copied().any(invalid_character)
                    || *part == [u16::from(b'.')]
                    || *part == [u16::from(b'.'); 2]
            })
        {
            return Err(unsupported("incomplete or unsupported UNC namespace"));
        }
        parts[2..].to_vec()
    } else {
        return Err(unsupported("an absolute disk or UNC path is required"));
    };
    if filesystem_parts.iter().any(|part| {
        part.is_empty()
            || part.len() > 255
            || part.iter().copied().any(invalid_character)
            || matches!(part.last(), Some(0x20 | 0x2e))
            || reserved_filename(part)
    }) {
        return Err(unsupported("ambiguous or reserved filename"));
    }
    Ok(PathBuf::from(OsString::from_wide(&ordinary)))
}

fn require_same_resource(original: &Path, ordinary: &Path) -> Result<()> {
    reject_link_ancestors(original)?;
    reject_link_ancestors(ordinary)?;
    let actual = fs::canonicalize(original).map_err(|error| unsupported(&error.to_string()))?;
    let compatible = fs::canonicalize(ordinary).map_err(|error| unsupported(&error.to_string()))?;
    // Do not case-fold or convert to UTF-8 here. Windows directories can be
    // case-sensitive, and lossy text comparison is not filesystem identity.
    if actual != compatible {
        return Err(unsupported(
            "the conventional path identifies a different resource",
        ));
    }
    Ok(())
}

/// Call only after all pinned resource handles are locked and verified. The
/// returned path is exclusively the child process's -File argument.
pub(crate) fn argument(bundle: &VerifiedBundle) -> Result<PathBuf> {
    let ordinary_root = ordinary_spelling(&bundle.root)?;
    let script = ordinary_root.join(SCRIPT);
    // Every payload read must be representable by the same legacy root. Do
    // this once per locked bundle, not once per selected MO2 installation.
    for relative in std::iter::once("SHA256SUMS.txt").chain(bundle.files.keys().map(String::as_str))
    {
        ordinary_spelling(&ordinary_root.join(relative))?;
    }
    require_same_resource(&bundle.root, &ordinary_root)?;
    require_same_resource(&bundle.root.join(SCRIPT), &script)?;
    Ok(script)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::installer_bundle::BuildIdentity;
    use std::collections::BTreeMap;

    fn fixture_bundle(root: PathBuf) -> VerifiedBundle {
        VerifiedBundle {
            root,
            identity: BuildIdentity {
                version: "0.13.0".into(),
                commit: "a".repeat(40),
                workflow: "Owned native path fixture".into(),
            },
            manifest_sha256: "0".repeat(64),
            files: BTreeMap::from([(SCRIPT.into(), "0".repeat(64))]),
        }
    }

    #[test]
    fn installer_script_ordinary_drive_and_unc_preserve_unicode_and_case() {
        for (native, ordinary) in [
            (
                r"\\?\c:\ModHunter 日本語 🦀 [literal]\Install_MO2_Plugin.ps1",
                r"c:\ModHunter 日本語 🦀 [literal]\Install_MO2_Plugin.ps1",
            ),
            (
                r"\\?\UNC\Server\Share\日本語\Install_MO2_Plugin.ps1",
                r"\\Server\Share\日本語\Install_MO2_Plugin.ps1",
            ),
            (
                r"\\?\uNc\Server\Share\日本語\Install_MO2_Plugin.ps1",
                r"\\Server\Share\日本語\Install_MO2_Plugin.ps1",
            ),
        ] {
            assert_eq!(
                ordinary_spelling(Path::new(native)).unwrap(),
                Path::new(ordinary)
            );
            assert_eq!(
                ordinary_spelling(Path::new(ordinary)).unwrap(),
                Path::new(ordinary)
            );
        }
    }

    #[test]
    fn installer_script_refuses_device_relative_and_incomplete_namespaces() {
        for path in [
            r"\\.\C:\Modhunter\Install_MO2_Plugin.ps1",
            r"\\?\GLOBALROOT\Device\HarddiskVolume1\Install_MO2_Plugin.ps1",
            r"\\?\Volume{example}\Install_MO2_Plugin.ps1",
            r"\??\C:\Modhunter\Install_MO2_Plugin.ps1",
            r"C:Modhunter\Install_MO2_Plugin.ps1",
            r"Modhunter\Install_MO2_Plugin.ps1",
            r"\\Server\",
        ] {
            assert!(ordinary_spelling(Path::new(path)).is_err(), "{path}");
        }
    }

    #[test]
    fn installer_script_refuses_dos_aliases_and_literal_verbatim_components() {
        for component in [
            "NUL",
            "con.txt.more",
            "PRN .log",
            "COM1",
            "lpt9.log",
            "COM¹",
            "LPT²",
            "CONIN$",
            "CONOUT$",
            "folder.",
            "folder ",
            ".",
            "..",
            "stream:other",
            "bad?name",
            "bad\u{1f}name",
        ] {
            for prefix in [r"C:\", r"\\?\C:\", r"\\?\UNC\Server\Share\"] {
                let path = format!("{prefix}{component}\\{SCRIPT}");
                assert!(ordinary_spelling(Path::new(&path)).is_err(), "{path}");
            }
        }
    }

    #[test]
    fn installer_script_legacy_limit_counts_utf16_without_truncation() {
        let short = format!("C:\\{}\\{SCRIPT}", "日".repeat(230));
        assert!(short.len() > LEGACY_MAX_PATH);
        assert_eq!(
            ordinary_spelling(Path::new(&short)).unwrap(),
            Path::new(&short)
        );
        let long = format!("\\\\?\\C:\\{}\\{SCRIPT}", "日".repeat(240));
        assert!(ordinary_spelling(Path::new(&long)).is_err());
    }

    #[test]
    fn installer_script_argument_retains_the_exact_owned_resource() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("Resource 日本語 🦀 [literal]");
        fs::create_dir(&root).unwrap();
        fs::write(
            root.join(SCRIPT),
            b"# Inert native path fixture; never execute",
        )
        .unwrap();
        fs::write(root.join("SHA256SUMS.txt"), b"Inert fixture").unwrap();
        let canonical_root = fs::canonicalize(&root).unwrap();
        let bundle = fixture_bundle(canonical_root.clone());
        let script = argument(&bundle).unwrap();
        assert!(
            !script
                .as_os_str()
                .encode_wide()
                .collect::<Vec<_>>()
                .starts_with(&[92, 92, 63, 92])
        );
        assert_eq!(
            fs::canonicalize(script).unwrap(),
            canonical_root.join(SCRIPT)
        );
        assert_eq!(bundle.root, canonical_root);
    }

    #[test]
    fn installer_script_identity_does_not_accept_equal_bytes_at_another_path() {
        let temporary = tempfile::tempdir().unwrap();
        let first = temporary.path().join("first.ps1");
        let other = temporary.path().join("other.ps1");
        fs::write(&first, b"# Same bytes are not filesystem identity").unwrap();
        fs::write(&other, fs::read(&first).unwrap()).unwrap();
        assert!(require_same_resource(&first, &other).is_err());
        assert!(require_same_resource(&first, &first).is_ok());
    }
}
