// Runs inside the selected site's browser. The host treats every result as
// untrusted metadata. No credential, cookie, or download authorization leaves it.
window[cfg.key] = {state:'pending'};
(async () => {
 const here = new URL(location.href), source = new URL(cfg.source);
 const fail = message => { throw Error(message); };
 if (here.origin !== source.origin) fail('The source redirected to another site. Review its address.');
 const title = document.title.toLowerCase();
 if (/just a moment|verify you are human|checking your browser|attention required/.test(title)) fail('Site verification required. Open this page and finish its challenge.');
 const visible = element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
 if ([...document.querySelectorAll('input[type=password]')].some(visible)) fail('Sign in to this site, then find files again.');
 const files = [], identities = new Map();
 const archive = /\.(zip|7z|rar)$/i;
 function boundedNodes(root, selector, limit) {
  const nodes = [...root.querySelectorAll(selector)];
  if (nodes.length > limit) fail('This page has too many links to inspect completely. Track a specific release page.');
  return nodes;
 }
 function externalIdentity(url) {
  if (url.hostname === 'drive.google.com') {
   const id = /^\/(?:file\/d|drive\/folders)\/([\w-]+)/.exec(url.pathname)?.[1] || url.searchParams.get('id');
   if (id && /^[\w-]{5,200}$/.test(id)) return 'drive:' + (/folders/.test(url.pathname) ? 'folder:' : 'file:') + id;
  }
  if (['mega.nz','mega.io'].includes(url.hostname)) {
   const match = /^\/(file|folder)\/([\w-]+)/.exec(url.pathname);
   const legacy = /^#!?([\w-]+)!/.exec(url.hash);
   if (match) return 'mega:' + match[1] + ':' + match[2];
   if (legacy) return 'mega:file:' + legacy[1];
  }
  return '';
 }
 function publicIdentity(url) {
  const safe = new URL(url.href);
  safe.hash = '';
  for (const key of [...safe.searchParams.keys()]) {
   if (/^(?:x-amz-|x-goog-|signature$|sig$|token$|access_token$|auth$|authorization$|expires$|policy$|key-pair-id$|csrfkey$|downloadkey$|se$|sp$|sv$|sr$)/i.test(key)) safe.searchParams.delete(key);
  }
  safe.searchParams.sort();
  return 'url:' + safe.href;
 }
 function add(raw, name, metadata = {}) {
  let url;
  if (typeof raw !== 'string' || !raw.trim()) fail('The source omitted a file address. No partial file list was accepted.');
  try { url = new URL(raw, here); } catch { fail('The source supplied an invalid file link. No partial file list was accepted.'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.href.length > 8192) fail('The source supplied an unsupported file address. No partial file list was accepted.');
  const identity = metadata.identity || externalIdentity(url) || publicIdentity(url);
  if (!identity || identity.length > 8192) fail('The source supplied an invalid file identity.');
  const existing = identities.get(identity);
  if (existing) {
   if (existing.url !== url.href && metadata.identity) fail('The source returned conflicting links for one file. Refresh the release page.');
   return;
  }
  if (files.length >= 200) fail('This release has more than 200 file choices. Track a narrower release page; the complete previous result is retained.');
  const label = String(name || 'Review download').replace(/[\u0000-\u001f\u007f]/g,' ').trim();
  if (label.length > 240) fail('A source filename is too long to identify safely. Review the release page.');
  const row = {url:url.href, name:label || 'Review download', identity, related:!!metadata.related,
   sourcePostId:metadata.sourcePostId || '', publishedAt:metadata.publishedAt || '',
   kind:metadata.kind || (externalIdentity(url) ? 'external' : 'attachment')};
  identities.set(identity,row); files.push(row);
 }
 async function json(url) {
  const response = await fetch(url,{credentials:'include',redirect:'error',signal:AbortSignal.timeout(12000)});
  if (!response.ok) fail('Source access failed (' + response.status + '). Sign-in and file access are separate.');
  if (!response.body) fail('The source returned no readable response.');
  const reader = response.body.getReader(), chunks = []; let size = 0;
  try {
   for (;;) {
    const part = await reader.read(); if (part.done) break;
    size += part.value.byteLength;
    if (size > 2000000) { await reader.cancel(); fail('Source metadata is too large to inspect completely. Choose a specific release.'); }
    chunks.push(part.value);
   }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let at = 0;
  for (const part of chunks) { bytes.set(part,at); at += part.byteLength; }
  return JSON.parse(new TextDecoder().decode(bytes));
 }
 if (/(^|\.)patreon\.com$/.test(here.hostname)) {
  const postId = raw => /^\/(?:[^/]+\/)?posts\/(?:[^/]*-)?(\d+)\/?$/.exec(new URL(raw,here).pathname)?.[1];
  const post = postId(here.href);
  if (!post || postId(source.href) !== post) fail('Track the exact Patreon release post to resolve its attachments.');
  const detail = id => json(here.origin + '/api/posts/' + encodeURIComponent(id) + '?include=attachments_media,campaign&fields%5Bpost%5D=post_file,content,content_json_string,current_user_can_view,published_at&fields%5Bmedia%5D=file_name,download_url,created_at');
  const release = await detail(post), data = release.data, attributes = data?.attributes;
  if (data?.id !== post || attributes?.current_user_can_view !== true) fail('This post is not confirmed accessible to the selected account. Open it to review your access.');
  const campaign = data.relationships?.campaign?.data?.id;
  function attachments(doc, expectedId, related) {
   if (doc.data?.id !== expectedId || doc.data?.attributes?.current_user_can_view !== true) fail('A linked release is unavailable to this account. Review it before using a partial result.');
   const a = doc.data.attributes;
   const references = doc.data.relationships?.attachments_media?.data || [];
   if (!Array.isArray(references) || references.length > 200) fail('The release attachment relationship is incomplete or too large.');
   const included = doc.included || [];
   if (!Array.isArray(included) || included.length > 1000) fail('The release metadata cannot be inspected completely.');
   for (const reference of references) {
    if (reference.type !== 'media' || typeof reference.id !== 'string') fail('The release attachment relationship is invalid.');
    const matches = included.filter(item => item.type === 'media' && item.id === reference.id);
    if (matches.length !== 1) fail('The source omitted or duplicated an attachment record. Refresh the release page.');
    const file = matches[0].attributes || {};
    if (archive.test(file.file_name || '')) add(file.download_url,file.file_name,{identity:'patreon:post:' + expectedId + ':media:' + reference.id,related,sourcePostId:expectedId,publishedAt:a.published_at || ''});
   }
   if (archive.test(a.post_file?.name || '')) add(a.post_file.url,a.post_file.name,{identity:'patreon:post:' + expectedId + ':post-file:' + a.post_file.name,related,sourcePostId:expectedId,publishedAt:a.published_at || ''});
  }
  attachments(release,post,false);
  const links = [];
  if (typeof attributes.content === 'string' && attributes.content.length > 1000000) fail('The release description is too large to inspect completely.');
  const parsed = new DOMParser().parseFromString(String(attributes.content || ''),'text/html');
  for (const node of boundedNodes(parsed,'a[href]',1000)) links.push(node.getAttribute('href'));
  if (attributes.content_json_string) {
   if (typeof attributes.content_json_string !== 'string' || attributes.content_json_string.length > 1000000) fail('The structured release description is too large to inspect completely.');
   let content; try { content = JSON.parse(attributes.content_json_string); } catch { fail('The release description is incomplete. Refresh the source page.'); }
   const queue = [content]; let visited = 0;
   while (queue.length) {
    if (++visited > 5000) fail('The structured release description is too large to inspect completely.');
    const value = queue.pop();
    if (typeof value === 'string' && /^https:\/\//.test(value)) links.push(value);
    else if (Array.isArray(value)) queue.push(...value);
    else if (value && typeof value === 'object') queue.push(...Object.values(value));
    if (queue.length > 5000) fail('The structured release description is too large to inspect completely.');
   }
  }
  const linkedPosts = new Set();
  for (const raw of links) {
   let url; try { url = new URL(raw,here); } catch { continue; }
   if (externalIdentity(url)) add(url.href,'Review external download',{related:false});
   if (url.origin === here.origin) { const id = postId(url.href); if (id && id !== post) linkedPosts.add(id); }
  }
  // Linked posts are explicitly labelled; they never inherit the installed-mod
  // association merely because their names or publication dates look similar.
  if ((!files.length || cfg.includeNewContent === true) && campaign) {
   if (linkedPosts.size > 8) fail('This post links to more than eight releases. Track a specific linked release to inspect its complete files.');
   for (const id of linkedPosts) {
    const linked = await detail(id);
    if (linked.data?.relationships?.campaign?.data?.id !== campaign) continue;
    attachments(linked,id,true);
   }
  }
 } else if (/(^|\.)loverslab\.com$/.test(here.hostname)) {
  const id = /\/files\/file\/(\d+)/.exec(source.pathname)?.[1];
  if (!id || /\/files\/file\/(\d+)/.exec(here.pathname)?.[1] !== id) fail('Use the exact LoversLab file page, not an unrelated topic or file.');
  for (const link of boundedNodes(document,'a[href]',10000)) {
   let url; try { url = new URL(link.getAttribute('href'),here); } catch { continue; }
   if (url.origin !== here.origin || /\/files\/file\/(\d+)/.exec(url.pathname)?.[1] !== id || url.searchParams.get('do') !== 'download') continue;
   const row = link.closest('li,tr,.ipsDataItem');
   const name = row?.querySelector('.ipsDataItem_title')?.textContent?.trim() || link.getAttribute('download') || link.textContent.trim() || 'Open file chooser';
   const file = url.searchParams.get('r');
   if (file && !/^\d+$/.test(file)) fail('The file chooser returned an invalid attachment identity.');
   add(url.href,name,{identity:'loverslab:file:' + id + (file ? ':attachment:' + file : ':chooser'),kind:file ? 'attachment' : 'chooser'});
  }
 } else {
  for (const link of boundedNodes(document,'a[href]',10000)) {
   let url; try { url = new URL(link.getAttribute('href'),here); } catch { continue; }
   let name = link.getAttribute('download');
   if (!name) { try { name = decodeURIComponent(url.pathname.split('/').pop() || ''); } catch { name = url.pathname.split('/').pop() || ''; } }
   if (archive.test(name)) add(url.href,name);
   else if (externalIdentity(url)) add(url.href,link.textContent.trim() || 'Review external host');
  }
 }
 window[cfg.key] = {state:'ready',files};
})().catch(error => { window[cfg.key] = {state:'error',message:String(error.message || error).slice(0,500)}; });
