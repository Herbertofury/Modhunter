//! Browser authority and bounded rendered-page extraction, independent of Windows.
#![cfg_attr(not(windows), allow(dead_code))]
use serde_json::{Value, json};
use url::Url;

pub const MAX_RESULT_BYTES: usize = 1_200_000;

/// Readiness belongs to one top-level WebView2 navigation. In particular, the
/// cancelled initial about:blank must never complete a newer website request.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct NavigationProgress {
    pub revision: u64,
    pub navigation_id: Option<u64>,
    pub website: bool,
    pub completed: bool,
    pub success: bool,
    pub http_status: u16,
    pub web_error: i32,
}
impl NavigationProgress {
    pub fn invalidate(&mut self) {
        *self = Self {
            revision: self.revision.wrapping_add(1),
            ..Self::default()
        };
    }
    pub fn started(&mut self, navigation_id: u64, website: bool) {
        self.invalidate();
        self.navigation_id = Some(navigation_id);
        self.website = website;
    }
    pub fn complete(
        &mut self,
        navigation_id: u64,
        success: bool,
        http_status: u16,
        web_error: i32,
    ) -> bool {
        if self.navigation_id != Some(navigation_id) {
            return false;
        }
        self.completed = true;
        self.success = success;
        self.http_status = http_status;
        self.web_error = web_error;
        true
    }
    pub fn ready(&self) -> bool {
        self.website && self.completed && self.success
    }
}

pub fn trusted_manager(label: &str, url: &Url) -> bool {
    label == "watchtower" && crate::policy::local_asset(url, "/watchtower.html")
}

/// Website views never navigate to application, file, credential, or local origins.
/// This also applies to sign-in redirects; no host suffix grants special authority.
pub fn website_navigation(url: &Url) -> bool {
    // Apply the one shared host/transport policy without imposing a saved watch's
    // 4 KiB path/query limit on a provider's longer OAuth state redirect.
    let mut origin = url.clone();
    origin.set_path("/");
    origin.set_query(None);
    origin.set_fragment(None);
    modhunter_core::watchtower::canonical_page_url(origin.as_str()).is_ok()
}

pub fn handoff(url: &Url) -> Result<Value, String> {
    if url.scheme() != "modhunter-watch"
        || url.host_str() != Some("track")
        || !matches!(url.path(), "" | "/")
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err("Invalid Watchtower page handoff.".into());
    }
    let mut fields = std::collections::BTreeMap::new();
    for (key, value) in url.query_pairs() {
        if !matches!(key.as_ref(), "url" | "title" | "profileId" | "itemId")
            || fields
                .insert(key.into_owned(), value.into_owned())
                .is_some()
        {
            return Err("Invalid or repeated Watchtower handoff field.".into());
        }
    }
    let target = modhunter_core::watchtower::canonical_page_url(
        fields.get("url").map(String::as_str).unwrap_or(""),
    )?;
    let field = |name: &str, max: usize| -> Result<String, String> {
        let value = fields.get(name).cloned().unwrap_or_default();
        if value.chars().count() > max || value.chars().any(char::is_control) {
            return Err(format!("Invalid {name} in Watchtower handoff."));
        }
        Ok(value)
    };
    Ok(json!({"url":target,"title":field("title",240)?,
        "profileId":field("profileId",256)?,"itemId":field("itemId",256)?}))
}

/// JSON data is embedded only through serde; user selectors never become code.
pub fn capture_script(url: &str, selector: &str, ignore: &[String]) -> String {
    let args = json!({"url":url,"selector":selector,"ignore":ignore});
    format!("(()=>{{const cfg={args};{CAPTURE_BODY}}})()")
}

const CAPTURE_BODY: &str = r#"
const here=new URL(location.href);here.hash='';
const result={finalUrl:here.href,title:String(document.title||'').slice(0,240),text:'',status:'ok',message:'',httpStatus:null,truncated:false};
const fail=(status,message)=>Object.assign(result,{status,message});
if(here.origin!==new URL(cfg.url).origin)return fail('redirected','This page redirected to another site. Open the saved page and review its address.');
if(document.readyState==='loading')return fail('loading','The page is still loading.');
const visible=e=>{if(!e)return false;const s=getComputedStyle(e);return s.display!=='none'&&s.visibility!=='hidden'&&s.contentVisibility!=='hidden'&&e.getClientRects().length>0};
if([...document.querySelectorAll('input[type="password"]')].slice(0,100).some(visible))return fail('signInRequired','Sign in in this site session, then check the page again.');
const title=result.title.toLowerCase();
if(/just a moment|checking your browser|verify you are human|security verification|attention required/.test(title)||[...document.querySelectorAll('#challenge-running,#challenge-stage,[data-cf-beacon]~#challenge-form')].some(visible))return fail('challenge','This site needs an interactive check. Open its session to complete it.');
if(/access denied|403 forbidden|429 too many requests|this site can.t be reached|can.t reach this page/.test(title)||document.documentURI.startsWith('chrome-error:'))return fail('unavailable','The site declined or could not complete this request. Review it in the site session and retry later.');
let root;try{root=cfg.selector?document.querySelector(cfg.selector):(document.querySelector('main,article,[role="main"]')||document.body)}catch{return fail('selectorMissing','The CSS selector is invalid. Edit this watched page.');}
if(!root)return fail('selectorMissing','The selected page region was not found. The last successful result is retained.');
if(root.closest('[aria-busy="true"]')||root.querySelector('[aria-busy="true"]'))return fail('loading','The selected page region is still loading.');
let ignores=[];try{for(const selector of cfg.ignore){ignores.push(...document.querySelectorAll(selector));if(ignores.length>2000)return fail('error','Too many ignored regions; narrow the ignored selectors.');}}catch{return fail('selectorMissing','An ignored CSS selector is invalid.');}
const ignored=e=>ignores.some(n=>n===e||n.contains(e));
const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node,count=0,chars=0;const chunks=[],started=performance.now();
while((node=walker.nextNode())){if(++count>100000||(count%100===0&&performance.now()-started>1000)){result.truncated=true;break;}const parent=node.parentElement;if(!parent||parent.closest('script,style,noscript,template,iframe,form,input,textarea,select,[aria-hidden="true"]')||ignored(parent)||!visible(parent))continue;const text=String(node.nodeValue||'').replace(/\s+/g,' ').trim();if(!text)continue;chars+=text.length+1;if(chars>262144){result.truncated=true;break;}chunks.push(text);}
result.text=chunks.join('\n');
if(new TextEncoder().encode(result.text).length>262144){result.text='';result.truncated=true;}
if(result.truncated){result.status='tooLarge';result.message='The selected content exceeded the complete-capture limit. Select a smaller region.';}
if(!result.text&&!result.truncated)return fail('selectorMissing','No readable content was found. Select a page region or finish sign-in.');
return result;
"#;

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn obsolete_blank_completion_cannot_complete_a_website_navigation() {
        let mut load = NavigationProgress::default();
        assert!(!load.complete(1, false, 0, 14));
        load.started(1, false);
        load.started(2, true);
        assert!(!load.complete(1, false, 0, 14));
        assert!(!load.completed);
        assert!(load.complete(2, true, 200, 0));
        assert!(load.ready());
        let complete = load.clone();
        assert!(!load.complete(1, false, 0, 14));
        assert_eq!(load, complete);
        load.started(3, true);
        assert!(!load.ready());
        assert!(!load.complete(2, true, 200, 0));
    }
    #[test]
    fn blank_success_and_same_url_reload_require_their_own_readiness() {
        let mut load = NavigationProgress::default();
        load.started(4, false);
        load.complete(4, true, 0, 0);
        assert!(!load.ready());
        load.started(5, true);
        load.complete(5, true, 200, 0);
        let revision = load.revision;
        load.invalidate();
        assert_ne!(revision, load.revision);
        assert!(!load.ready());
        assert!(!load.complete(5, true, 200, 0));
        load.started(6, true);
        load.complete(6, false, 403, 9);
        assert!(!load.ready());
        assert_eq!(load.http_status, 403);
        assert_eq!(load.web_error, 9);
    }
    #[test]
    fn management_authority_requires_exact_label_origin_and_document() {
        assert!(trusted_manager(
            "watchtower",
            &Url::parse("http://tauri.localhost/watchtower.html").unwrap()
        ));
        for (label, url) in [
            ("site-1", "http://tauri.localhost/watchtower.html"),
            ("watchtower", "https://example.com/watchtower.html"),
            ("watchtower", "http://127.0.0.1:5555/watchtower.html"),
            ("watchtower", "http://tauri.localhost/Modhunter.html"),
        ] {
            assert!(!trusted_manager(label, &Url::parse(url).unwrap()));
        }
    }
    #[test]
    fn external_sites_cannot_reach_native_or_local_origins() {
        for url in [
            "http://example.com/",
            "https://user:password@example.com/",
            "https://127.0.0.1/",
            "https://192.168.0.1/",
            "https://[::1]/",
            "https://example.local/",
            "https://localhost./",
            "http://tauri.localhost/watchtower.html",
            "file:///C:/secret",
            "javascript:alert(1)",
        ] {
            assert!(!website_navigation(&Url::parse(url).unwrap()), "{url}");
        }
        assert!(website_navigation(
            &Url::parse("https://accounts.example.com/oauth?state=one&code=two").unwrap()
        ));
        assert!(website_navigation(
            &Url::parse(&format!(
                "https://accounts.example.com/oauth?state={}",
                "s".repeat(8000)
            ))
            .unwrap()
        ));
    }
    #[test]
    fn dashboard_handoff_is_bounded_and_never_accepts_duplicate_or_native_targets() {
        let data=handoff(&Url::parse("modhunter-watch://track?url=https%3A%2F%2Fexample.com%2Fmod&title=Example&profileId=p&itemId=m").unwrap()).unwrap();
        assert_eq!(data["url"], "https://example.com/mod");
        for raw in [
            "modhunter-watch://track?url=https%3A%2F%2Fexample.com&url=https%3A%2F%2Fevil.com",
            "modhunter-watch://track?url=http%3A%2F%2F127.0.0.1%3A4000%2F",
            "modhunter-watch://other?url=https%3A%2F%2Fexample.com",
            "modhunter-watch://track?url=https%3A%2F%2Fexample.com&command=install",
        ] {
            assert!(handoff(&Url::parse(raw).unwrap()).is_err(), "{raw}");
        }
    }
    #[test]
    fn selectors_are_data_and_captures_have_fixed_bounds() {
        let script = capture_script(
            "https://example.com/a",
            "x');globalThis.compromised=true;//",
            &[".ads".into()],
        );
        assert!(script.contains("\"selector\":\"x');globalThis.compromised=true;//\""));
        assert!(script.contains("new TextEncoder().encode(result.text).length>262144"));
        assert!(!script.contains("document.cookie"));
    }
}
