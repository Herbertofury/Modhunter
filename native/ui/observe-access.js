// Informational, same-session browser observations. Never returns account names,
// cookie values, credentials, page text, private addresses, or entitlement claims.
(() => {
 const origin = location.origin;
 if (origin !== cfg.origin) return null;
 const visible = element => !!element?.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
 const title = document.title.toLowerCase();
 const body = String(document.body?.innerText || '').slice(0,32768).toLowerCase();
 const google = location.hostname === 'accounts.google.com';
 if (google && (/disallowed_useragent|embedded_user_agent/.test(location.href) || /this browser or app may not be secure|doesn.t comply with google.s secure browsers policy|can.t sign you in.*browser/.test(body)))
  return {origin,loginState:'unknown',status:'embeddedLoginBlocked',guidance:'embeddedIdentityProvider'};
 // Only the owning site can contribute login/page observations. A successful
 // OAuth provider page cannot assert login to the original website.
 if (origin !== cfg.sessionOrigin) return null;
 if (/just a moment|verify you are human|checking your browser|attention required/.test(title))
  return {origin,loginState:'unknown',status:'challenge',guidance:'completeVerification'};
 const passwords = [...document.querySelectorAll('input[type=password]')];
 const loginPage = /\/(?:login|signin|sign-in|auth)(?:\/|$)/i.test(location.pathname) || /sign in|log in|login/i.test(document.title);
 if (loginPage && passwords.some(visible)) return {origin,loginState:'signedOut',status:'signInRequired',guidance:'siteLogin'};
 const nodes = [...document.querySelectorAll('a[href],button,[role=menuitem]')];
 if (nodes.length > 10000) return null;
 const logout = nodes.some(node => {
  if (!visible(node)) return false;
  const label = String(node.textContent || node.getAttribute('aria-label') || '').trim().toLowerCase();
  if (/^(?:log\s*out|sign\s*out)$/.test(label)) return true;
  try { const url = new URL(node.getAttribute('href'),location.href); return url.origin === origin && /\/(?:logout|signout)\/?$/i.test(url.pathname); } catch { return false; }
 });
 const loginState = logout ? 'signedIn' : 'unknown';
 if (/unlock this post|join to unlock|become a member to unlock|you (?:do not|don.t) have permission to (?:view|access)/.test(body))
  return {origin,loginState,status:'restricted',guidance:''};
 const content = [...document.querySelectorAll('main,article,[role=main]')].some(node => visible(node) && String(node.innerText || '').trim().length > 80);
 return {origin,loginState,status:content ? 'pageAccessible' : 'unknown',guidance:''};
})()
