use modhunter_core::{
    BridgeClient, ImportRequest, PluginAuditRequest, ProfileImporter, SourceScanRequest,
    audit_plugins, scan_sources,
};
use serde_json::json;
use std::fs;
use std::io::Read;
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
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("import") => {
            let request = ImportRequest {
                modlist_path: PathBuf::from(
                    value(&args, "--modlist")?.ok_or("import requires --modlist PATH")?,
                ),
                ini_path: value(&args, "--ini")?.map(PathBuf::from),
                mods_path: value(&args, "--mods")?.map(PathBuf::from),
                workers: value(&args, "--workers")?.map(|s| s.parse()).transpose()?,
            };
            let result = ProfileImporter::new().import(&request)?;
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
        Some("benchmark") => benchmark(&args)?,
        Some("--version") => println!("Modhunter Rust engine {}", env!("CARGO_PKG_VERSION")),
        None | Some("--help") | Some("help") => println!(
            "Modhunter Rust engine\n\nimport --modlist PATH [--ini PATH] [--mods PATH] [--workers 1..32]\nprobe --endpoint http://127.0.0.1:PORT/\naudit-plugins --request PATH|-\nscan-sources --request PATH|-\narchive-review --request PATH|- (bounded original-byte review)\nduplicate-report --request PATH|- (complete ownership and reversible quarantine)\ncontent-vault --request PATH|- (explicit scoped backup/restore)\nbenchmark [--mods 2500] [--rounds 5] [--workers 8]\n\nImport and plugin audits are read-only. ESL eligibility is conditional; save, dependent-mod, asset and external-reference safety is not certified. The live MO2 plugin remains responsible for downloads and FOMOD installation. Benchmark uses a generated fixture, not a user's MO2 installation."
        ),
        Some(command) => {
            return Err(format!("Unknown command: {command}. Run modhunter-cli --help").into());
        }
    }
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
