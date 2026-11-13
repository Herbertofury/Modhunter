# Watchtower: pages and site sign-ins

[Rust Desktop](Rust-Desktop.md) / [Update Workshop](Update-Workshop.md) / [Reference audit and acceptance](../native/evidence/whimwatch-parity-0.10.0.md)

Watchtower is the Rust desktop's page and account-session manager. Track a mod release page, creator feed, changelog or other public HTTPS page, using an isolated browser session when the page needs your sign-in. The first successful observation establishes what the page currently says. Later checks identify changed readable text for you to review.

A page observation does not establish a new compatible mod version. Continue to use the Update Workshop's exact source/release/file review and MO2's native download and installation workflow when updating an installed mod.

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

Nexus authentication, authenticated Nexus downloads and native install choices remain inside MO2. Signing into the same website in your system browser does not transfer that browser's session into Watchtower. Some identity providers refuse embedded login; use the site's supported sign-in method where available. Watchtower does not bypass login, payment or human-verification barriers.

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
| An update is available on the page | Review its exact provider release/file and compatibility in the Update Workshop; page tracking does not install files. |

## Evidence and feature scope

The implementation was informed by a static audit of WhimWatch **0.4.2**, pinned to its exact source and uploaded asset. Its useful account/page-management behaviors were independently implemented in Rust and the existing Modhunter presentation. Sims DBPF discovery, direct `.package`/`.ts4script` installation and file-level rollback were not transferred into MO2's installation ownership. Generic page monitoring, multiple named sessions and watchlist interchange are Modhunter additions beyond that reference.

Required Chromium checks load the shipped manager with a native-command fixture to exercise forms, row actions, history, import validation, errors and responsive layout. Required Windows checks use actual WebView2 and internally owned loopback pages to verify account isolation, reuse of a remembered profile after closing/reopening its views within one process, a fresh profile selected by a new storage generation, authenticated capture and changed content, incomplete/stale/canceled rejection, same-session popups and remote IPC denial. The fixture's reopen check does not restart Modhunter. An additional anonymous `https://example.com/` check exercises the production capture path through a durable saved baseline. The release validator requires these receipts alongside the existing dashboard, installer and native MO2 gates.

These fixtures do not use a real website account. Provider-specific OAuth/2FA/challenges, long-running account sessions, populated MO2 installations, real Nexus downloads and FOMOD decisions need that real environment. See the [exact audit and release boundaries](../native/evidence/whimwatch-parity-0.10.0.md) and the passing workflow attached to each release.
