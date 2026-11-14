# Watchtower: pages and site sign-ins

[Rust Desktop](Rust-Desktop.md) / [Update Workshop](Update-Workshop.md) / [Reference audit and acceptance](../native/evidence/whimwatch-parity-0.10.0.md)

Watchtower is the Rust desktop's page and account-session manager. Track a mod release page, creator feed, changelog or other public HTTPS page, using an isolated browser session when the page needs your sign-in. The first successful observation establishes what the page currently says. Later checks identify changed readable text for you to review.

A page observation does not establish a new compatible mod version. Watchtower keeps page observations, exact file choices, downloaded archives and installation reviews as separate steps. Updating an installed mod requires an explicit source relationship, a compatible exact target and a reviewed archive; a changed page never starts an installation.

## Add and check a page

1. Open **Watchtower** from the native toolbar and choose **Add page**.
2. Paste the page address. Add an optional name, folder, tags and notes so the page is easy to find later.
3. Under **Read using**, choose the relevant site sign-in if the page needs your account. A public page can use the public browser.
4. If only part of the page matters, expand **What to compare & check timing**. **Page region** accepts a CSS selector such as `main` or `#releases`; **Ignore regions** accepts one selector per line. These help exclude changing navigation, ads or counters. A selector that no longer matches needs review instead of silently replacing the baseline with missing content.
5. Leave **Check this page after saving** selected for an immediate targeted check, then save.

The initial successful read is a baseline. Subsequent changes appear in **Changes to review**. Open page details to inspect the captured content and retained change history; use **Mark seen** when you have reviewed that observation. **Seen history** records acknowledgments and offers undo when a newer acknowledgment has not superseded the earlier one. A later page change remains unread.

The native store validates and deduplicates page identities. HTTPS origins and meaningful URL path/query case are retained. Loopback/private IP addresses, reserved/local hostnames, embedded username/password URLs and unsupported address schemes are refused by normal tracking; the Windows test fixture has its own internal exception and does not expose a production switch for bypassing this policy.

## Keep site sign-ins separate

Open **Site sign-ins**, choose **Add site sign-in**, enter the website and give the session a recognizable name. The website opens in its own native browser window. Enter your credentials on that website; Modhunter has no password form and does not copy credentials into watchlist JSON, MO2 messages or exports.

Each named session has a separate WebView2 profile. This supports separate sessions for the same site without sharing cookies with another session or the privileged local manager. A page can use a session only for the intended site origin. Native popups preserve their parent session for supported login flows; the website still cannot invoke Modhunter's local filesystem or MO2 commands.

| Control or state | Meaning |
| --- | --- |
| Remember this sign-in | The selected native browser profile persists across app launches. |
| Remember disabled | A temporary **on-disk** browser profile is used for the current app run; the next launch uses a fresh profile. This is not an in-memory session or a forensic-erasure guarantee. |
| Open the website | Opens that session's native site window so you can sign in or complete a human verification step. |
| Confirm the session | Your indication that the session is ready for another check. Page access must still succeed; confirmation does not independently prove membership or subscription entitlement. |
| Reset the session | Closes its windows, advances its storage generation and invalidates pending observations before a fresh session is used. |

The account list separates your last confirmation, observed website sign-in indicators, the current page's accessibility and a verification hold. A visible sign-out control can indicate a signed-in account while the release remains locked. Sign-in or human-verification failures hold checks sharing that account; opening its browser or observing a successful login does not silently release that hold. Complete the website flow, then explicitly confirm the session to resume eligible checks. Observations contain categorical state, never account names, passwords, cookies or page text.

Nexus authentication, authenticated Nexus downloads and native install choices remain inside MO2. Signing into the same website in your system browser does not transfer that browser's session into Watchtower. Some identity providers refuse embedded login; use the site's supported sign-in method where available. Watchtower does not bypass login, payment or human-verification barriers.

## Choose exact sources and files

Open page details and choose **Source & creator choices** to connect that page to an exact installed item. The association uses the profile and item identities, not a similar mod name. Record the game, runtime and variant, confirm their compatibility, then choose whether this source supplies an installed update or separate content. Changing the association or compatibility choices invalidates earlier download and review work. A runtime marked not applicable cannot override a known, different installed runtime.

Alternative sources are grouped only when their exact installed item and confirmed game/runtime/variant match. A source you explicitly prefer remains preferred when another page is newer or temporarily more accessible. Accessibility is displayed separately from version information. Page dates never override compatibility or select an update automatically.

A creator group can opt into discovery of linked new releases and mute that creator on selected site origins. New releases remain separate choices until you classify them. Creator opt-in, an account login or a similar filename does not connect a new mod to an installed one. Saved creator preferences and exact file choices survive restart and watchlist JSON interchange; account observations are local and are not imported.

Choose **Find files** from the tracked page to inspect its accessible archives. Each returned file has its own durable identity and one explicit relationship:

- **Installed update** names the exact installed target and still requires confirmed source compatibility.
- **Separate content** has no installed update association. Its later installation review must choose a new target.
- **Ignore file** records that exact file as not wanted. You can restore or reset the choice without deleting the tracked page or its history.

| Source | Supported discovery boundary |
| --- | --- |
| Patreon | Exact post attachment relationships from the selected browser session; same-creator linked releases are labeled separately. Membership or post access must actually be available. |
| LoversLab | The exact release page and its complete accessible chooser, resolving attachment IDs or a direct archive response. An unresolved chooser is never treated as one update file. |
| Google Drive and MEGA | Explicit direct file links. File identity excludes access/decryption keys. Shared folders remain visible as unsupported containers; select a direct file link. |
| Other HTTPS pages | Explicit archive links. For an unfamiliar signed URL, a changed request address requires a new file choice rather than assuming the token names the same file. |

Discovery accepts a complete bounded result. A missing attachment record, unresolved chooser, timeout or oversized result preserves the previous complete result instead of replacing it with a shortened list. Signed download addresses remain transient inside the native browser workflow; saved file choices contain identity hashes and your decisions.

Opening a classified file creates a short-lived native lease for that exact request. The host verifies the browser's navigation and actual download address, including observed redirects. Navigating to another file or page revokes that lease, and a popup does not inherit it. A supported MEGA blob transfer requires the exact file page to have loaded and the blob to come from that page's origin. Ordinary downloads started from an unclassified browsing page can be saved, but carry no installed update association; use **Find files** for a classified managed update.

A resulting archive waits in **Page downloads** for explicit download approval. After the complete bytes are verified, **Review contents** begins a separate archive and target review. Account changes, source edits, expiry or changed file decisions can invalidate pending work. Downloads do not grant installation authority by themselves.

## Organize and control checking

Use **All pages**, **Changes to review**, **Awaiting first check**, **Needs attention** and **Paused & muted** to find the relevant work. Search covers page names, sites, folders and tags. Site and folder filters, sorting and row selection support larger lists. **Removed pages** keeps removed entries available for restoration.

**Check all** and **Check due** have different purposes: the first requests eligible enabled pages, while the latter respects each page's saved interval and retry timing. A paused page, muted site or muted folder stays out of checks until you enable it again. Selection controls can check, acknowledge, pause/resume, remove or restore a group. **Cancel checks** stops current work; late observations from canceled or changed jobs cannot overwrite newer state.

Manual checks are the default. In **Preferences**, you can enable checking due pages at launch or in the background while Modhunter is open. Set a default interval and retained history size; each page can have its own interval. These options do not create an operating-system service and do not check pages while Modhunter is closed.

Native work uses a bounded queue and per-site pacing. The list retains last-good observations while later checks run. Failed, incomplete, canceled or outdated results do not become successful baselines. A site may still be slow, offline, rate-limited or require human interaction; **Needs attention** keeps that condition visible.

## Import, export and privacy

**Import** accepts a Modhunter watchlist JSON document or one page address per line. Choose a JSON file or paste its contents, select **Preview import**, inspect the validation/deduplication result, then apply it. A preview alone does not change the list. Browser sessions and passwords are not imported; assign the appropriate local session when needed.

**Export** opens a format choice and saves through the native Downloads workflow. **Watchlist JSON** is the interchange format that Modhunter can import; **Spreadsheet CSV** is a readable page-configuration report. JSON excludes observation history by default. **Include captured page history in JSON** adds the retained text and change report when you explicitly select it; importing that report still establishes fresh page baselines and does not restore trusted acknowledgment state.

Exports exclude browser credentials and profile storage. Page URLs, names, notes and optional captured content can still reveal private interests or account-only information, so inspect a file before sharing it. Export is for watchlist interchange; it is not a backup of website sign-ins or installed mod files.

Preferences can hide page titles, obscure the manager when it loses focus and enable generic change notifications that omit titles and addresses. Muted site origins and folders are editable there. These are visibility and checking controls; they do not make the website connection anonymous.

State is versioned and atomically saved. If the stored document cannot be read safely, the manager shows a recovery error rather than silently overwriting it. **Preserve file & start fresh** is an explicit recovery action; inspect/export any available data first.

## Native privacy and browser cleanup

**Global quick hide** is opt-in. When enabled, **Ctrl + Alt + H** hides currently visible Modhunter windows and remembers which windows to restore. A window requested while the app is hidden stays hidden until you restore the app; background checking windows stay hidden. Registration conflicts or unsupported native operations are reported rather than presented as a successful setting.

**Request capture protection** applies Windows capture exclusion to the app's native windows, including newly created website windows before they become visible. This is a supported Windows capture-policy request, not a guarantee against every capture tool, remote desktop path or camera. Hiding page titles and obscuring an unfocused manager remain separate controls.

The **Clear cache** action on one named account clears that account profile's disk cache and browsing/download history. It deliberately retains cookies, credentials and site storage, and does not clear another account. **Clear site browsing data** and the exit option use the saved sign-in retention preference; disabling retention also resets named accounts through their normal session-generation workflow. Browser-profile cleanup is scoped to Modhunter's own profiles and does not touch a separate system-browser profile.

Clearing all profiles shares one 20-second time budget. If a profile fails or the budget runs out, the result identifies the profiles confirmed cleared and those not started or still unconfirmed. It does not report complete cleanup from a partial result. Site storage is retained by the cache-only action because websites may depend on it for sign-in; this is not a secure-erasure feature.

**Reduce hidden-check media** is optional and off by default. It blocks only image, media and font requests in hidden checking windows; scripts, documents, fetch and XHR remain enabled, and interactive login/review windows are unaffected. A reported required-resource failure prevents a successful capture. Changing this policy establishes a fresh observation context while retaining the last complete baseline until a new complete check succeeds. Leave it disabled for pages whose meaningful content depends on media or for provider behavior that has not been verified with the option enabled.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| Login or verification required | Open the page's selected session, complete the site's supported flow, then recheck. Confirming a session alone does not grant access. |
| Page remains locked after sign-in | Check the exact account and page entitlement on the website. A valid login can still lack access to that content. |
| A large page cannot be captured completely | Select a smaller stable content region. Incomplete text is rejected to avoid a misleading baseline. |
| No page region matched | Update or clear the saved selector after inspecting the current page. |
| Frequent unhelpful changes | Compare the release/changelog region and ignore changing counters, navigation or advertisements. |
| A page does not run in a background check | Check its enabled state, site/folder mute, interval, retry state and selected session. |
| Removed page seems missing | Open **Removed pages** and restore it. |
| Session changes seem ignored | Reopen the selected native session. A system-browser login is separate, and reset cancels older in-flight work. |
| An update is available on the page | Confirm its exact source compatibility, use Find files, classify the intended archive and review the installation target. Page tracking does not install files. |
| A download no longer matches the selected file | Find files again and reopen the exact attachment. A different page, folder, popup or expired lease cannot reuse the old update relationship. |
| Shared folder has no managed download action | Select a direct file link from that folder; Modhunter does not guess one file or silently choose a folder archive. |

## Evidence and feature scope

The implementation was informed by a static audit of WhimWatch **0.4.2**, pinned to its exact source and uploaded asset. Its useful account/page-management behaviors were independently implemented in Rust and the existing Modhunter presentation. Sims DBPF discovery, direct `.package`/`.ts4script` installation and file-level rollback were not transferred into MO2's installation ownership. Generic page monitoring, multiple named sessions and watchlist interchange are Modhunter additions beyond that reference.

Required Chromium checks load the shipped manager with a native-command fixture to exercise forms, row actions, history, import validation, exact source/creator choices, stale-response rejection, account-state distinctions, errors and responsive layout. Required Windows checks use actual WebView2 and internally owned loopback pages to verify account isolation, reuse of a remembered profile after closing/reopening its views within one process, a fresh profile selected by a new storage generation, authenticated capture and changed content, incomplete/stale/canceled rejection, same-session popups and remote IPC denial. The fixture's reopen check does not restart Modhunter. An additional anonymous `https://example.com/` check exercises the production capture path through a durable saved baseline. Additional native gates exercise real DownloadStarting deferrals and explicit consent, exact selected-file redirect authority, rejection of an unrelated same-host download, cache retention and account isolation, optional hidden-resource filtering, native capture-affinity requests and quick-hide intent restoration. The quick-hide fixture sends the native hotkey message through the registered driver; it does not claim a physical keyboard or universal capture-exclusion test. The release validator requires these receipts alongside the existing dashboard, installer and native MO2 gates.

A green compile or a source fixture test alone does not satisfy the native runtime gates. These fixtures do not use a real website account. Provider-specific OAuth/2FA/challenges, long-running account sessions, populated MO2 installations, real Nexus downloads and FOMOD decisions need that real environment. See the [exact audit and release boundaries](../native/evidence/whimwatch-parity-0.10.0.md) and the passing workflow attached to each release.
