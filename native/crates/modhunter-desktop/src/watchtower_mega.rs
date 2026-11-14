//! Complete browser-owned public-folder discovery. The provider keeps keys,
//! credentials and decrypted data. Native code receives only file metadata and
//! transient page/blob addresses, and independently binds the exact child.
use super::*;
use policy::download_scope::MegaFolderFile;

const SCRIPT: &str = include_str!("../../../ui/mega-folder.js");

fn inspect(
    app: &tauri::AppHandle,
    window: &WebviewWindow,
    load: &Load,
    context: &SiteContext,
    source: &Url,
    expected: Option<&MegaFolderFile>,
    key: &str,
) -> Result<Value> {
    let cancel = context
        .cancellation
        .as_deref()
        .ok_or("The folder preparation has no cancellation owner.")?;
    let deadline = Instant::now() + CHECK_DEADLINE;
    wait_loaded(window, load, cancel, deadline)?;
    let cfg = json!({"source":source.as_str(),"expected":expected,"key":key,
        "mode":if expected.is_some(){"arm"}else{"inspect"},"fixtureOrigin":context.fixture_origin});
    loop {
        binding(app, context)?;
        let watch = context
            .download_watch
            .as_ref()
            .ok_or("The folder has no tracked source.")?;
        if !app.state::<WatchRuntime>().store.watch_eligible(&watch.0) {
            return Err(
                "Folder discovery stopped because the source or account is no longer eligible."
                    .into(),
            );
        }
        let result = eval_json(
            window,
            format!("(()=>{{const cfg={cfg};return ({SCRIPT});}})()"),
            cancel,
            deadline,
        )?;
        match result["state"].as_str() {
            Some("ready") => {
                binding(app, context)?;
                return Ok(result);
            }
            Some("pending") if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(100))
            }
            _ => {
                return Err(result["message"]
                    .as_str()
                    .unwrap_or(
                        "The MEGA folder did not provide a complete accessible file inventory.",
                    )
                    .to_owned());
            }
        }
    }
}

pub(super) fn resolve(
    app: &tauri::AppHandle,
    context: &SiteContext,
    url: &Url,
    preparation: &super::offers::Preparation,
) -> Result<(MegaFolderFile, Url)> {
    if !matches!(url.host_str(), Some("mega.nz" | "mega.io"))
        || !policy::download_scope::is_folder(url)
    {
        return Err("Only a MEGA shared folder can use this exact-file resolver.".into());
    }
    let load = Arc::new(Load::default());
    let window = WindowGuard(site_window(
        app,
        context.clone(),
        url.clone(),
        false,
        None,
        load.clone(),
    )?);
    preparation.window(&window.0)?;
    let result = inspect(app, &window.0, &load, context, url, None, "")?;
    let file: MegaFolderFile = serde_json::from_value(result["file"].clone())
        .map_err(|_| "The MEGA folder returned invalid file metadata.")?;
    let selected = Url::parse(
        result["url"]
            .as_str()
            .ok_or("The folder did not identify an exact child address.")?,
    )
    .map_err(err)?;
    policy::download_scope::SelectedDownloadScope::new_mega_folder_file(
        selected.clone(),
        file.clone(),
        "metadata_validation".into(),
        None,
    )?;
    binding(app, context)?;
    Ok((file, selected))
}

pub(super) fn arm(
    app: &tauri::AppHandle,
    window: &WebviewWindow,
    load: &Load,
    context: &SiteContext,
) -> Result<()> {
    let scope = context
        .download_scope
        .as_ref()
        .ok_or("No exact file authority was created.")?;
    let (file, key, url) = scope
        .lock()
        .map_err(err)?
        .mega_folder()
        .ok_or("This is not a verified folder file.")?;
    inspect(app, window, load, context, &url, Some(&file), &key)?;
    Ok(())
}
