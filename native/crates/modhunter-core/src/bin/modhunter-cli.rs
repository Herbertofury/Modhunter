mod bos_preview;
mod bsa_directory;
mod kid_preview;
mod mcp_stdio;
mod nif_metadata;
mod plugin_order_preview;
#[path = "../plugin_template/skyrim_se.rs"]
mod skyrim_se_plugin_template;
mod spid_preview;

use modhunter_core::{
    BridgeClient, ImportRequest, PluginAuditRequest, ProfileImporter, SourceScanRequest,
    audit_plugins, scan_sources,
};
use serde_json::json;
use std::fs;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn value(args: &[String], key: &str) -> Result<Option<String>, String> {
    match args.iter().position(|v| v == key) {
        Some(i) => args
            .get(i + 1)
            .filter(|s| !s.starts_with("--"))
            .cloned()
            .map(Some)
            .ok_or_else(|| format!("{key} requires a value")),
        None => Ok(None),
    }
}
fn number(args: &[String], key: &str, default: usize) -> Result<usize, String> {
    value(args, key)?.map_or(Ok(default), |s| {
        s.parse()
            .map_err(|_| format!("{key} must be a positive integer"))
    })
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|arg| {
            arg.into_string()
                .map_err(|_| "Command arguments must be valid Unicode")
        })
        .collect::<Result<_, _>>()?;
    match args.first().map(String::as_str) {
        Some("import") => {
            let request = profile_request(&args)?;
            let result = ProfileImporter::new().import(&request)?;
            println!("{}", serde_json::to_string(&result)?);
        }
        Some("watch-feed") => {
            let url = value(&args, "--url")?.ok_or("watch-feed requires --url HTTPS_FEED")?;
            let reader = modhunter_core::watchtower::PublicFeedReader::new()?;
            let result = reader.observe(&url::Url::parse(&url)?, false)?;
            println!("{}", serde_json::to_string(&result)?);
        }
        Some("probe") => {
            let endpoint = value(&args, "--endpoint")?
                .ok_or("probe requires --endpoint http://127.0.0.1:PORT/")?;
            println!(
                "{}",
                serde_json::to_string(&BridgeClient::new()?.probe(&endpoint)?)?
            );
        }
        Some("audit-plugins") => {
            let path = value(&args, "--request")?
                .ok_or("audit-plugins requires --request PATH or --request - for stdin")?;
            const REQUEST_LIMIT: u64 = 16 * 1024 * 1024;
            let mut bytes = Vec::new();
            if path == "-" {
                std::io::stdin()
                    .take(REQUEST_LIMIT + 1)
                    .read_to_end(&mut bytes)?;
            } else {
                fs::File::open(path)?
                    .take(REQUEST_LIMIT + 1)
                    .read_to_end(&mut bytes)?;
            }
            if bytes.len() as u64 > REQUEST_LIMIT {
                return Err("Plugin audit request exceeds the 16 MiB limit".into());
            }
            let bytes = bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(&bytes);
            let request: PluginAuditRequest = serde_json::from_slice(bytes)?;
            println!("{}", serde_json::to_string(&audit_plugins(&request)?)?);
        }
        Some("plugin-file-info") => {
            let (path, header_only) = match args.as_slice() {
                [_, flag, path] if flag == "--file" => (path, false),
                [_, flag, path, mode] if flag == "--file" && mode == "--header-only" => {
                    (path, true)
                }
                _ => return Err("plugin-file-info requires --file PATH [--header-only]".into()),
            };
            let result = modhunter_core::plugin_analysis::inspect_selected_skyrim_plugin(
                std::path::Path::new(path),
                header_only,
            )?;
            println!("{}", serde_json::to_string(&result)?);
        }
        Some("plugin-template") => {
            const REQUEST: &str =
                "plugin-template takes only [--author TEXT] [--description TEXT]; bytes on stdout";
            let (pairs, remainder) = args[1..].as_chunks::<2>();
            if !remainder.is_empty() {
                return Err(REQUEST.into());
            }
            let (mut author, mut description) = (None, None);
            for pair in pairs {
                let text = pair[1].as_str();
                let slot = match pair[0].as_str() {
                    "--author" => &mut author,
                    "--description" => &mut description,
                    _ => return Err(REQUEST.into()),
                };
                if text.starts_with("--") || slot.replace(text).is_some() {
                    return Err(REQUEST.into());
                }
            }
            // Finish request and byte validation before beginning binary stdout.
            let bytes = skyrim_se_plugin_template::empty_plugin(author.unwrap_or(""), description)?;
            let mut stdout = std::io::stdout().lock();
            stdout.write_all(&bytes)?;
            stdout.flush()?;
        }
        Some("scan-sources") => {
            let path = value(&args, "--request")?
                .ok_or("scan-sources requires --request PATH or --request - for stdin")?;
            const REQUEST_LIMIT: u64 = 32 * 1024 * 1024;
            let mut bytes = Vec::new();
            if path == "-" {
                std::io::stdin()
                    .take(REQUEST_LIMIT + 1)
                    .read_to_end(&mut bytes)?;
            } else {
                fs::File::open(path)?
                    .take(REQUEST_LIMIT + 1)
                    .read_to_end(&mut bytes)?;
            }
            if bytes.len() as u64 > REQUEST_LIMIT {
                return Err("Source scan request exceeds 32 MiB".into());
            }
            let bytes = bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(&bytes);
            let request: SourceScanRequest = serde_json::from_slice(bytes)?;
            let output = serde_json::to_string(&scan_sources(&request)?)?;
            if output.len() > 30 * 1024 * 1024 {
                return Err("Source scan result exceeds 30 MiB; select a smaller batch".into());
            }
            println!("{output}");
        }
        Some("archive-review") => {
            let path =
                value(&args, "--request")?.ok_or("archive-review requires --request PATH|-")?;
            let mut bytes = Vec::new();
            if path == "-" {
                std::io::stdin()
                    .take(32 * 1024 * 1024 + 1)
                    .read_to_end(&mut bytes)?;
            } else {
                fs::File::open(path)?
                    .take(32 * 1024 * 1024 + 1)
                    .read_to_end(&mut bytes)?;
            }
            if bytes.len() > 32 * 1024 * 1024 {
                return Err("Archive request exceeds 32 MiB".into());
            }
            let request: modhunter_core::archive_review::Request = serde_json::from_slice(&bytes)?;
            let result = modhunter_core::archive_review::inspect(&request, &|| false)
                .map_err(std::io::Error::other)?;
            println!("{}", serde_json::to_string(&result)?);
        }
        Some("bsa-directory") => bsa_directory::run(&args[1..])?,
        Some("bsa-compare") => bsa_directory::run_compare(&args[1..])?,
        Some("duplicate-report") => {
            let path =
                value(&args, "--request")?.ok_or("duplicate-report requires --request PATH|-")?;
            let mut bytes = Vec::new();
            if path == "-" {
                std::io::stdin()
                    .take(32 * 1024 * 1024 + 1)
                    .read_to_end(&mut bytes)?;
            } else {
                fs::File::open(path)?
                    .take(32 * 1024 * 1024 + 1)
                    .read_to_end(&mut bytes)?;
            }
            if bytes.len() > 32 * 1024 * 1024 {
                return Err("Duplicate request exceeds 32 MiB".into());
            }
            let request: modhunter_core::duplicate_report::Request =
                serde_json::from_slice(&bytes)?;
            let result = modhunter_core::duplicate_report::execute(request, || false)
                .map_err(std::io::Error::other)?;
            println!("{}", serde_json::to_string(&result)?);
        }
        Some("content-vault") => {
            let path =
                value(&args, "--request")?.ok_or("content-vault requires --request PATH|-")?;
            let mut bytes = Vec::new();
            if path == "-" {
                std::io::stdin()
                    .take(1024 * 1024 + 1)
                    .read_to_end(&mut bytes)?;
            } else {
                fs::File::open(path)?
                    .take(1024 * 1024 + 1)
                    .read_to_end(&mut bytes)?;
            }
            if bytes.len() > 1024 * 1024 {
                return Err("Content request exceeds 1 MiB".into());
            }
            let request: modhunter_core::content_vault::Request = serde_json::from_slice(&bytes)?;
            let reply = modhunter_core::content_vault::execute(request, || false)
                .map_err(std::io::Error::other)?;
            println!("{}", serde_json::to_string(&reply)?);
        }
        Some("mcp-stdio") => mcp_stdio::run(&args[1..])?,
        Some("bos-preview") => {
            let [_, flag, name] = args.as_slice() else {
                return Err(
                    "bos-preview requires --name FILENAME_SWAP.ini and one file on stdin".into(),
                );
            };
            if flag != "--name" {
                return Err(
                    "bos-preview requires --name FILENAME_SWAP.ini and one file on stdin".into(),
                );
            }
            println!("{}", bos_preview::run(name, std::io::stdin().lock())?);
        }
        Some("spid-preview") => {
            let report = match args.as_slice() {
                [_, flag, name] if flag == "--name" => spid_preview::run(name, std::io::stdin().lock())?,
                [_, flag, path] if flag == "--file" => spid_preview::selected_file(std::path::Path::new(path))?,
                _ => return Err("spid-preview requires exactly one of --name FILENAME_DISTR.ini (stdin) or --file PATH".into()),
            };
            println!("{}", serde_json::to_string(&report)?);
        }
        Some("plugin-order-preview") => plugin_order_preview::run(&args[1..])?,
        Some("kid-preview") => {
            let [_, flag, name] = args.as_slice() else {
                return Err(
                    "kid-preview requires --name FILENAME_KID.ini and one file on stdin".into(),
                );
            };
            if flag != "--name" {
                return Err(
                    "kid-preview requires --name FILENAME_KID.ini and one file on stdin".into(),
                );
            }
            let report = kid_preview::run(name, std::io::stdin().lock())?;
            println!("{}", serde_json::to_string(&report)?);
        }
        Some("nif-info") => nif_metadata::run(&args[1..])?,
        Some("ini-compare") => {
            if !matches!(args.as_slice(), [_, left, _, right, _]
                if (left == "--before" && right == "--after")
                    || (left == "--after" && right == "--before"))
            {
                return Err("ini-compare requires --before PATH --after PATH".into());
            }
            let before = PathBuf::from(value(&args, "--before")?.ok_or("Select a before INI")?);
            let after = PathBuf::from(value(&args, "--after")?.ok_or("Select an after INI")?);
            let report = modhunter_core::ini_snapshot::compare_files(&before, &after, &|| false)?;
            println!("{}", serde_json::to_string(&report)?);
        }
        Some("benchmark") => benchmark(&args)?,
        Some("benchmark-profile") => benchmark_profile(&args)?,
        Some("--version") => println!("Modhunter Rust engine {}", env!("CARGO_PKG_VERSION")),
        None | Some("--help") | Some("help") => println!(
            "Modhunter Rust engine\n\nimport --modlist PATH [--ini PATH] [--mods PATH] [--workers 1..32]\nwatch-feed --url HTTPS_FEED (read-only RSS or Atom observation)\nprobe --endpoint http://127.0.0.1:PORT/\naudit-plugins --request PATH|-\nplugin-file-info --file PATH [--header-only] (selected Skyrim SE-format file; no load-order claims)\nplugin-template [--author TEXT] [--description TEXT] (empty Skyrim SE full ESP candidate bytes on stdout; no file writes)\nscan-sources --request PATH|-\narchive-review --request PATH|- (bounded original-byte review)\nbsa-directory --archive PATH (named PC BSA 103–105, metadata only)\nbsa-compare --left PATH --right PATH (directory names only; no winner or payload check)\nduplicate-report --request PATH|- (complete ownership and reversible quarantine)\ncontent-vault --request PATH|- (explicit scoped backup/restore)\nspid-preview --name FILENAME_DISTR.ini | --file PATH (one bounded UTF-8 file; targets only)\nbos-preview --name FILENAME_SWAP.ini (one bounded UTF-8 file on stdin; authored swap pairs only)\nkid-preview --name FILENAME_KID.ini (one bounded UTF-8 file on stdin; declaration preview only)\nmcp-stdio [--legacy-2025-11-25] [--bsa ALIAS PATH] [--plugin ALIAS PATH] (read-only stdio; optional launch-time file snapshots)\nplugin-order-preview capture|preview (explicit snapshot JSON on stdin; never applies a change)\nnif-info --file PATH (selected Skyrim SE NIF block inventory and limited DDS slots)\nini-compare --before PATH --after PATH (read-only selected QSettings INIs; values redacted)\nbenchmark [--mods 2500] [--rounds 5] [--workers 8] (generated fixture)\nbenchmark-profile --modlist PATH [--ini PATH] [--mods PATH] [--workers 8] [--rounds 5] (read-only selected profile)\n\nImport and plugin audits are read-only. ESL eligibility is conditional; save, dependent-mod, asset and external-reference safety is not certified. The live MO2 plugin remains responsible for downloads and FOMOD installation. Benchmark uses a generated fixture, not a user's MO2 installation."
        ),
        Some(command) => {
            return Err(format!("Unknown command: {command}. Run modhunter-cli --help").into());
        }
    }
    Ok(())
}

fn profile_request(args: &[String]) -> Result<ImportRequest, Box<dyn std::error::Error>> {
    Ok(ImportRequest {
        modlist_path: PathBuf::from(value(args, "--modlist")?.ok_or("Select --modlist PATH")?),
        ini_path: value(args, "--ini")?.map(PathBuf::from),
        mods_path: value(args, "--mods")?.map(PathBuf::from),
        workers: value(args, "--workers")?.map(|s| s.parse()).transpose()?,
    })
}

fn benchmark_profile(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let request = profile_request(args)?;
    let rounds = number(args, "--rounds", 5)?;
    if rounds == 0 {
        return Err("Choose at least one warm round".into());
    }
    let importer = ProfileImporter::new();
    let measure = || -> Result<_, Box<dyn std::error::Error>> {
        let mut result = importer.import(&request)?;
        // Timestamps and instrumentation vary by design. Every mod, group,
        // plugin, conflict, warning, load-order entry and source must agree.
        for field in [
            "/importedAt",
            "/generatedAt",
            "/pluginAnalysis/checkedAt",
            "/pluginAnalysis/stats",
            "/sources/0/timestamp",
        ] {
            if let Some(value) = result.catalog.pointer_mut(field) {
                *value = serde_json::Value::Null;
            }
        }
        Ok(result)
    };
    let cold = measure()?;
    let mut warm = Vec::new();
    for _ in 0..rounds {
        let result = measure()?;
        if result.catalog != cold.catalog || result.warnings != cold.warnings {
            return Err(
                "The selected profile produced different catalogs during measurement".into(),
            );
        }
        warm.push(result.stats);
    }
    let mut times: Vec<_> = warm.iter().map(|s| s.elapsed_ms).collect();
    times.sort_by(f64::total_cmp);
    let median = (times[(rounds - 1) / 2] + times[rounds / 2]) / 2.0;
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "scope":"complete specified profile; read-only native engine imports",
            "osCache":"not evicted", "coldCache":"new importer","fullCatalogEquivalent":true,
            "throughputModsPerSecondAtMedian":cold.stats.mods as f64*1000.0/median,
            "cold":cold.stats,"warm":warm,"warmMedianMs":median,"warmP95Ms":times[rounds-(rounds/20)-1],
            "platform":std::env::consts::OS,"architecture":std::env::consts::ARCH
        }))?
    );
    Ok(())
}

fn benchmark(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let count = number(args, "--mods", 2500)?;
    let rounds = number(args, "--rounds", 5)?;
    let workers = number(args, "--workers", 8)?;
    if !(1..=20_000).contains(&count) || !(1..=30).contains(&rounds) || !(1..=32).contains(&workers)
    {
        return Err("Benchmark requires 1..20000 mods, 1..30 rounds, and 1..32 workers".into());
    }
    let nonce = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let root = std::env::temp_dir().join(format!(
        "modhunter-benchmark-{}-{nonce}",
        std::process::id()
    ));
    fs::create_dir(&root)?;
    struct RemoveOnDrop(PathBuf);
    impl Drop for RemoveOnDrop {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    let _cleanup = RemoveOnDrop(root.clone());
    let profile = root.join("profiles/Benchmark");
    fs::create_dir_all(&profile)?;
    let mods = root.join("mods");
    fs::create_dir(&mods)?;
    fs::write(
        root.join("ModOrganizer.ini"),
        "[General]\ngameName=Skyrim Special Edition\ngameVersion=1.6.1170\n",
    )?;
    let mut modlist = String::from("# Automatically generated benchmark fixture\n");
    for n in (0..count).rev() {
        let name = format!("Fixture Mod {n:05}");
        modlist.push(if n % 3 == 0 { '-' } else { '+' });
        modlist.push_str(&name);
        modlist.push('\n');
        let directory = mods.join(&name);
        fs::create_dir(&directory)?;
        fs::write(
            directory.join("meta.ini"),
            format!(
                "[General]\ngameName=Skyrim Special Edition\nmodid={}\nversion=1.0\nnewestVersion=2.0\nauthor=Fixture Author\nnotes=Deterministic Rust import benchmark fixture\n",
                n + 1
            ),
        )?;
        if n % 50 == 0 {
            fs::create_dir_all(directory.join("SKSE/Plugins"))?;
            fs::write(
                directory.join("SKSE/Plugins/fixture.dll"),
                b"fixture-not-executable",
            )?;
        }
    }
    fs::write(profile.join("modlist.txt"), modlist)?;
    let request = ImportRequest {
        modlist_path: profile.join("modlist.txt"),
        ini_path: None,
        mods_path: None,
        workers: Some(workers),
    };
    let importer = ProfileImporter::new();
    let cold = importer.import(&request)?;
    let first_items = cold.catalog["items"].clone();
    if cold.stats.mods != count {
        return Err("Benchmark import lost rows".into());
    }
    let mut warm = Vec::new();
    for _ in 0..rounds {
        let result = importer.import(&request)?;
        if result.catalog["items"] != first_items {
            return Err("Warm importer changed profile semantics".into());
        }
        if result.stats.metadata_cache_hits != count {
            return Err("Warm import missed expected metadata cache entries".into());
        }
        warm.push(result.stats);
    }
    let mut times: Vec<_> = warm.iter().map(|s| s.elapsed_ms).collect();
    times.sort_by(f64::total_cmp);
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "fixture":"generated MO2 metadata; no live MO2/game/Nexus operations", "mods":count,"workers":workers,
            "platform":std::env::consts::OS,"architecture":std::env::consts::ARCH,
            "cold":cold.stats,"warm":warm,"warmMedianMs":times[times.len()/2],
            "rowIdentityAndMetadataEquivalent":true,"metadataCacheVerified":true,
            "note":"Cold means an empty Modhunter metadata cache; OS filesystem caches are not forcibly evicted. Timings are measurements, not release guarantees."
        }))?
    );
    Ok(())
}
