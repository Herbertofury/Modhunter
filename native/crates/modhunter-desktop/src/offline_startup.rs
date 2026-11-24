use modhunter_core::ImportRequest;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default, rename_all = "camelCase")]
pub(crate) struct Preferences {
    pub profile: Option<ImportRequest>,
    pub ini_path: Option<PathBuf>,
    pub mods_path: Option<PathBuf>,
    pub endpoint: Option<String>,
    pub eslifier_data_path: Option<PathBuf>,
    #[serde(skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub historical_evidence: std::collections::BTreeMap<String, PathBuf>,
    #[serde(flatten)]
    other: std::collections::BTreeMap<String, serde_json::Value>,
}

impl Preferences {
    pub fn remember_import(&mut self, request: ImportRequest) {
        self.ini_path.clone_from(&request.ini_path);
        self.mods_path.clone_from(&request.mods_path);
        self.profile = Some(request);
    }
}

/// Other arguments belong to the desktop's existing commands. A malformed
/// offline request must surface its error before any remembered endpoint is used.
pub(crate) fn requested_profile(
    args: impl Iterator<Item = String>,
) -> Result<Option<ImportRequest>, String> {
    let (mut profile, mut ini_path, mut mods_path) = (None, None, None);
    for arg in args {
        let (flag, value) = arg.split_once('=').unwrap_or((&arg, ""));
        let slot = match flag {
            "--offline-profile" => &mut profile,
            "--mo2-ini" => &mut ini_path,
            "--mods-dir" => &mut mods_path,
            _ => continue,
        };
        if slot.is_some() {
            return Err(format!("{flag} was supplied more than once."));
        }
        let path = PathBuf::from(value);
        if !path.is_absolute() {
            return Err(format!(
                "{flag} requires an absolute path using {flag}=<path>."
            ));
        }
        if flag == "--mods-dir" {
            if !path.is_dir() {
                return Err(format!("{flag} must identify an existing directory."));
            }
        } else if !path.is_file() {
            return Err(format!("{flag} must identify an existing file."));
        }
        if flag == "--offline-profile"
            && !path
                .file_name()
                .is_some_and(|n| n.eq_ignore_ascii_case("modlist.txt"))
        {
            return Err("--offline-profile must identify a profile's modlist.txt.".into());
        }
        *slot = Some(path);
    }
    match profile {
        Some(modlist_path) => Ok(Some(ImportRequest {
            modlist_path,
            ini_path,
            mods_path,
            workers: None,
        })),
        None if ini_path.is_some() || mods_path.is_some() => {
            Err("--mo2-ini and --mods-dir require --offline-profile.".into())
        }
        None => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use modhunter_core::ProfileImporter;
    use serde_json::json;
    use std::fs;

    #[test]
    fn explicit_split_profile_uses_the_importer_and_preserves_unrelated_preferences() {
        let tmp = tempfile::tempdir().unwrap();
        let profile = tmp.path().join("Profiles/Chosen Profile");
        let mods = tmp.path().join("installed mods");
        fs::create_dir_all(&profile).unwrap();
        fs::create_dir_all(mods.join("Sample")).unwrap();
        let modlist = profile.join("modlist.txt");
        let ini = tmp.path().join("ModOrganizer.ini");
        fs::write(&modlist, "+Sample\n").unwrap();
        fs::write(&ini, "[General]\ngameName=Skyrim Special Edition\n").unwrap();
        let mut prefs: Preferences = serde_json::from_value(json!({
            "endpoint":"http://127.0.0.1:54321/", "eslifierDataPath":"prior",
            "historicalEvidence":{"unrelated-profile":"previous.json"},
            "unrelatedSetting":{"value":true}, "iniPath":"old.ini", "modsPath":"old-directory"
        }))
        .unwrap();
        let args = [
            format!("--mods-dir={}", mods.display()),
            format!("--offline-profile={}", modlist.display()),
            format!("--mo2-ini={}", ini.display()),
        ];
        let request = requested_profile(args.into_iter()).unwrap().unwrap();
        let result = ProfileImporter::new().import(&request).unwrap();
        assert_eq!(result.catalog["profileName"], "Chosen Profile");
        assert_eq!(result.catalog["items"][0]["name"], "Sample");
        let id = result.catalog["profileId"].as_str().unwrap();
        let report = tmp.path().join("evidence.json");
        prefs
            .historical_evidence
            .insert(id.to_owned(), report.clone());
        prefs.remember_import(request);
        let saved = serde_json::to_value(&prefs).unwrap();
        assert_eq!(saved["endpoint"], "http://127.0.0.1:54321/");
        assert_eq!(saved["eslifierDataPath"], "prior");
        assert_eq!(saved["unrelatedSetting"], json!({"value":true}));
        assert_eq!(saved["iniPath"], saved["profile"]["iniPath"]);
        assert_eq!(saved["modsPath"], saved["profile"]["modsPath"]);
        let reloaded: Preferences = serde_json::from_value(saved).unwrap();
        assert_eq!(reloaded.historical_evidence.get(id), Some(&report));
        assert_eq!(
            reloaded.historical_evidence.get("unrelated-profile"),
            Some(&PathBuf::from("previous.json"))
        );
        let restored = reloaded.profile.unwrap();
        assert_eq!(
            (restored.modlist_path, restored.ini_path, restored.mods_path),
            (modlist, Some(ini), Some(mods))
        );
    }

    #[test]
    fn invalid_offline_arguments_are_errors_and_omitted_overrides_use_auto_detection() {
        let tmp = tempfile::tempdir().unwrap();
        let list = tmp.path().join("modlist.txt");
        let ini = tmp.path().join("ModOrganizer.ini");
        fs::write(&list, "").unwrap();
        fs::write(&ini, "").unwrap();
        let target = format!("--offline-profile={}", list.display());
        let exact = requested_profile([target.clone()].into_iter())
            .unwrap()
            .unwrap();
        assert!(exact.mods_path.is_none() && exact.ini_path.is_none());
        assert!(requested_profile(std::iter::empty()).unwrap().is_none());
        for args in [
            vec!["--offline-profile".to_owned()],
            vec!["--offline-profile=modlist.txt".to_owned()],
            vec![format!("--offline-profile={}", ini.display())],
            vec![format!("--mo2-ini={}", ini.display())],
            vec![target.clone(), format!("--mods-dir={}", list.display())],
            vec![
                target.clone(),
                format!("--mo2-ini={}", tmp.path().display()),
            ],
            vec![target.clone(), target],
        ] {
            assert!(requested_profile(args.into_iter()).is_err());
        }
    }
}
