//! Real WebView2 blob/deferral boundary with an owned provider-shaped fixture.
//! This is not a live MEGA account, API or decryption test.
use super::*;
use policy::download_scope::{MegaFolderFile, SelectedDownloadScope};
const KEY: &str = "__modhunterMegaFixture";

fn folder_view(
    app: &tauri::AppHandle,
    server: &FixtureServer,
    suffix: &str,
) -> Result<(WindowGuard, Arc<Mutex<SelectedDownloadScope>>)> {
    let preparation = acquisition::begin_preparation(app)?;
    let mut context = exact_context(app, server, suffix, "/tracked")?;
    context.cancellation = Some(preparation.cancel());
    let mut url = server.url("/folder/pub12345");
    url.set_fragment(Some("abcdefghijklmnopqrstuv/file/file1234"));
    let file = MegaFolderFile {
        public_id: "pub12345".into(),
        root_id: "root1234".into(),
        file_id: "file1234".into(),
        parent_id: "root1234".into(),
        name: "fixture.zip".into(),
        bytes: archive_bytes().len() as u64,
        modified_at: 100,
    };
    let scope = Arc::new(Mutex::new(SelectedDownloadScope::new_mega_folder_file(
        url.clone(),
        file.clone(),
        KEY.into(),
        context.fixture_origin.as_deref(),
    )?));
    context.download_scope = Some(scope.clone());
    preparation.scope(scope.clone())?;
    let (window, _) = fixture_view(app, context.clone(), url.clone(), true)?;
    preparation.window(&window.0)?;
    let file_json = serde_json::to_value(&file).map_err(err)?;
    let setup = format!(
        "(()=>{{const file={file_json};window.M={{RootID:file.rootId,d:{{[file.rootId]:{{h:file.rootId,t:1,name:'Owned folder',tf:1,td:0,tb:file.bytes}},[file.fileId]:{{h:file.fileId,p:file.parentId,t:0,name:file.name,s:file.bytes,ts:file.modifiedAt}}}},c:{{[file.rootId]:{{[file.fileId]:1}}}}}};window.pfid=file.publicId;window.fminitialized=true;window.loadfm={{loaded:1,loading:false}};window.$={{}};window.dl_queue=[];const a=document.createElement('a');a.id='dllink';a.download=file.name;document.body.append(a);}})()"
    );
    window.0.eval(setup).map_err(err)?;
    let cfg = json!({"source":url.as_str(),"expected":file,"key":KEY,"mode":"arm","fixtureOrigin":context.fixture_origin});
    let script = format!(
        "(()=>{{const cfg={cfg};return ({});}})()",
        include_str!("../../../ui/mega-folder.js")
    );
    let result = eval_json(
        &window.0,
        script,
        &AtomicBool::new(false),
        Instant::now() + Duration::from_secs(8),
    )?;
    if result["state"] != "ready" {
        return Err(format!(
            "The owned folder observer did not arm: {}",
            result["message"]
        ));
    }
    preparation.retain(&window.0)?;
    Ok((window, scope))
}
fn blob_script(case: &str) -> String {
    let mutation = match case {
        "wrongChild" => {
            "window.$.memIOSaveAttempt='wrong123';window.dl_queue=[{id:'wrong123',n:'fixture.zip',size:blob.size}];"
        }
        "ambiguous" => {
            "M.d.other123={h:'other123',p:'root1234',t:0,name:'Other.zip',s:1,ts:100};M.c.root1234.other123=1;M.d.root1234.tf=2;M.d.root1234.tb++;"
        }
        "wrongBlob" => {
            "document.addEventListener('click',()=>{if(window.__modhunterMegaFixture.proof)window.__modhunterMegaFixture.proof.blobUrl='blob:'+location.origin+'/different';},{once:true});"
        }
        _ => "",
    };
    format!(
        "(async()=>{{const response=await fetch('/download.zip',{{credentials:'include'}}),blob=await response.blob();window.$.memIOSaveAttempt='file1234';window.dl_queue=[{{id:'file1234',n:'fixture.zip',size:blob.size}}];{mutation}const a=document.getElementById('dllink');a.href=URL.createObjectURL(blob);a.click();window.__folderClicked=true;}})().catch(e=>window.__folderError=String(e));"
    )
}
pub(super) fn check(
    app: &tauri::AppHandle,
    manager: &WebviewWindow,
    server: &FixtureServer,
) -> Result<Value> {
    let (window, _) = folder_view(app, server, "mega-folder-valid")?;
    let id = new_pending(app, &window.0, &blob_script("valid"))?;
    if command(
        manager,
        "approveDownload",
        json!({"id":id,"confirm":"DOWNLOAD"}),
    )?["ok"]
        != true
    {
        return Err("The exact folder blob could not be explicitly approved.".into());
    }
    let ready = wait_job(app, &id, |r| {
        matches!(
            r["state"].as_str(),
            Some("ready" | "failed" | "interrupted")
        )
    })?;
    if ready["state"] != "ready"
        || ready["bytes"] != archive_bytes().len() as u64
        || ready["binding"]["contentIntent"] != "update"
    {
        return Err("The exact provider-shaped folder blob did not preserve original bytes and update ownership.".into());
    }
    drop(window);
    let mut denied = Vec::new();
    for case in ["wrongChild", "ambiguous", "wrongBlob"] {
        let (window, scope) = folder_view(app, server, &format!("mega-folder-{case}"))?;
        let count = jobs(app).len();
        window.0.eval(blob_script(case)).map_err(err)?;
        let deadline = Instant::now() + Duration::from_secs(10);
        while scope.lock().map_err(err)?.live() {
            if Instant::now() >= deadline {
                return Err(format!(
                    "The real folder {case} download did not revoke its native authority."
                ));
            }
            std::thread::sleep(Duration::from_millis(40));
        }
        if jobs(app).len() != count {
            return Err(format!(
                "The rejected folder {case} download created a receipt."
            ));
        }
        denied.push(case);
        drop(window);
    }
    let (preparing, scope) = folder_view(app, server, "mega-folder-cancel-before-blob")?;
    let (sign_in, _) = fixture_view(
        app,
        server.context("fixture-a1", 1),
        server.url("/tracked"),
        true,
    )?;
    let sign_in_label = sign_in.0.label().to_string();
    let preparing_label = preparing.0.label().to_string();
    let before = jobs(app).len();
    preparing.0.eval("window.__folderPreparing=true;window.__folderWait=new Promise(resolve=>window.__finishFolder=resolve);".to_string()).map_err(err)?;
    let observed = eval_json(
        &preparing.0,
        "window.__folderPreparing===true".into(),
        &AtomicBool::new(false),
        Instant::now() + Duration::from_secs(5),
    )?;
    if observed != true {
        return Err("The owned folder did not enter its pre-blob preparation phase.".into());
    }
    acquisition::cancel_preparation(app)?;
    let deadline = Instant::now() + Duration::from_secs(8);
    while app.get_webview_window(&preparing_label).is_some() {
        if Instant::now() >= deadline {
            return Err(
                "Cancel preparation did not close the owned pre-blob transfer view.".into(),
            );
        }
        std::thread::sleep(Duration::from_millis(30));
    }
    if scope.lock().map_err(err)?.live()
        || jobs(app).len() != before
        || app.get_webview_window(&sign_in_label).is_none()
        || jobs(app)
            .iter()
            .find(|row| row["id"] == id)
            .is_none_or(|row| row["state"] != "ready")
    {
        return Err("Pre-blob cancellation retained authority, created late bytes, closed an ordinary sign-in, or lost a completed archive.".into());
    }
    Ok(
        json!({"actualWebView2Blob":true,"explicitApproval":true,"completeOriginalArchive":true,"denied":denied,"preBlobCancellation":true,"ordinarySignInRetained":true,"completedArchiveRetained":true,
        "scope":"Owned loopback provider-shaped state and real WebView2 DownloadStarting/async deferral; no live MEGA account or provider network/decryption claim."}),
    )
}
