const here=new URL(location.href);here.hash='';
const result={finalUrl:here.href,title:String(document.title||'').slice(0,240),text:'',status:'ok',message:'',httpStatus:null,truncated:false};
const fail=(status,message)=>Object.assign(result,{status,message});
if(here.origin!==new URL(cfg.url).origin)return fail('redirected','This page redirected to another site. Open the saved page and review its address.');
if(document.readyState==='loading')return fail('loading','The page is still loading.');
const visible=e=>{if(!e)return false;const s=getComputedStyle(e);return s.display!=='none'&&s.visibility!=='hidden'&&s.contentVisibility!=='hidden'&&e.getClientRects().length>0};
if([...document.querySelectorAll('input[type="password"]')].slice(0,100).some(visible))return fail('signInRequired','Sign in in this site session, then check the page again.');
const title=result.title.toLowerCase();
if(/(^|\.)patreon\.com$/.test(here.hostname)&&/\/posts\//.test(here.pathname)&&/unlock this post|join to unlock|become a member to unlock/i.test(String(document.body?.innerText||'').slice(0,32768)))return fail('accessRestricted','This release is not readable by the selected account. Sign-in and release access are separate; the previous complete observation is retained.');
if(/just a moment|checking your browser|verify you are human|security verification|attention required/.test(title)||[...document.querySelectorAll('#challenge-running,#challenge-stage,[data-cf-beacon]~#challenge-form')].some(visible))return fail('challenge','This site needs an interactive check. Open its session to complete it.');
if(/access denied|403 forbidden|429 too many requests|this site can.t be reached|can.t reach this page/.test(title)||document.documentURI.startsWith('chrome-error:'))return fail('unavailable','The site declined or could not complete this request. Review it in the site session and retry later.');
if (cfg.selector === 'jsonld:') {
    if (cfg.ignore.length) return fail('selectorMissing', 'Clear ignored regions to read the complete JSON-LD.');
    const blocks = [];
    let total = 0;
    for (const script of document.querySelectorAll('script[type]')) {
        if (script.type.split(';')[0].trim().toLowerCase() !== 'application/ld+json') continue;
        const raw = script.textContent;
        if (raw.length > 2 * 1024 * 1024) return fail('tooLarge', 'JSON-LD block exceeds the reader limit.');
        try {
            const data = JSON.parse(raw);
            if (data === null || typeof data !== 'object') throw new Error();
        } catch {
            return fail('selectorMissing', 'A declared JSON-LD block is malformed. The prior observation was retained.');
        }
        // Keep numeric lexemes, escaped characters and spaces inside strings intact.
        const compact = raw.replace(/"(?:\\.|[^"\\])*"|[ \t\r\n]+/g, token => token[0] === '"' ? token : '');
        total += new TextEncoder().encode(compact).length + (blocks.length ? 1 : 0);
        if (total > 262144) return fail('tooLarge', 'Complete JSON-LD exceeds the observation limit.');
        blocks.push(compact);
    }
    if (!blocks.length) return fail('selectorMissing', 'The page declares no JSON-LD blocks.');
    result.text = blocks.join('\n');
    return result;
}
// CSS regions preserve the original first-match behavior. XPath can select
// several release sections. Handle their union once, in document order.
function regions(expression, firstCssMatch) {
    if (!expression.startsWith('xpath:')) {
        if (!firstCssMatch) return document.querySelectorAll(expression);
        const element = document.querySelector(expression);
        return element ? [element] : [];
    }
    const found = document.evaluate(expression.slice(6), document, null,
        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    const elements = [];
    for (let i = 0; i < found.snapshotLength; i++) {
        const element = found.snapshotItem(i);
        if (element.nodeType !== Node.ELEMENT_NODE) throw new Error('Select elements');
        elements.push(element);
    }
    return elements;
}
let roots;
try {
    roots = cfg.selector ? regions(cfg.selector, true) :
        [document.querySelector('main,article,[role="main"]') || document.body].filter(Boolean);
} catch {
    return fail('selectorMissing', 'Invalid page region. Use CSS or xpath: followed by an element expression.');
}
if (!roots.length) return fail('selectorMissing','The selected page region was not found. The last successful result is retained.');
const ignoredRoots = new Set();
try {
    for (const selector of cfg.ignore) {
        for (const element of regions(selector, false)) ignoredRoots.add(element);
    }
} catch {
    return fail('selectorMissing', 'Invalid ignored region. Use CSS or xpath: followed by an element expression.');
}
const excluded = 'script,style,noscript,template,iframe,form,input,textarea,select,[aria-hidden="true"]';
const chunks = [];
let count = 0, chars = 0, precedingRoot = null;
const started = performance.now();
for (const root of roots) {
    if (precedingRoot && precedingRoot.contains(root)) continue;
    precedingRoot = root;
    if (root.closest('[aria-busy="true"]') || root.querySelector('[aria-busy="true"]')) {
        return fail('loading','The selected page region is still loading.');
    }
    if (root.closest(excluded)) continue;
    if (ignoredRoots.size) {
        let ancestor = root;
        while (ancestor && !ignoredRoots.has(ancestor)) ancestor = ancestor.parentElement;
        if (ancestor) continue;
    }
    // Prune excluded subtrees before visiting text. Visibility remains a
    // per-parent check since a descendant can override visibility:hidden.
    const walker = ignoredRoots.size ? document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
            if (node.nodeType === Node.TEXT_NODE) return NodeFilter.FILTER_ACCEPT;
            return ignoredRoots.has(node) || node.matches(excluded) ?
                NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP;
        }
    }) : document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node, lastParent, parentVisible;
    while ((node = walker.nextNode())) {
        if (++count > 100000 || (count % 100 === 0 && performance.now() - started > 1000)) {
            result.truncated = true;
            break;
        }
        const parent = node.parentElement;
        if (parent !== lastParent) {
            lastParent = parent;
            parentVisible = (ignoredRoots.size || !parent?.closest(excluded)) && visible(parent);
        }
        if (!parentVisible) continue;
        const text = String(node.nodeValue || '').replace(/\s+/g, ' ').trim();
        if (!text) continue;
        chars += text.length + 1;
        if (chars > 262144) {
            result.truncated = true;
            break;
        }
        chunks.push(text);
    }
    if (result.truncated) break;
}
result.text=chunks.join('\n');
if(new TextEncoder().encode(result.text).length>262144){result.text='';result.truncated=true;}
if(result.truncated){result.status='tooLarge';result.message='The selected content exceeded the complete-capture limit. Select a smaller region.';}
if(!result.text&&!result.truncated)return fail('selectorMissing','No readable content was found. Select a page region or finish sign-in.');
return result;
