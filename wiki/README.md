# ModHunter Wiki source

This is the canonical source for [ModHunter's GitHub Wiki](https://github.com/Herbertofury/Modhunter/wiki), which is a separate Git repository. This README is not published as a page.

## Preview, reconcile, publish

1. Commit the source pages and ensure the live Wiki already has an initial **Home** commit.
2. Preview via **Actions → Verify and sync ModHunter Wiki → Run workflow** on `main`, leaving **publish** unchecked. Locally, use `bash scripts/sync_wiki.sh` with Git and Python 3.
3. If a page conflicts, compare the exact live and source revisions and reconcile deliberately in the source. An existing page is adopted only when its bytes match. Later edits to a managed page, including Home or Sidebar, or an independent deletion halt the entire sync. No unowned pages or removed source pages are deleted.
4. Once reviewed, select **publish**, or run `bash scripts/sync_wiki.sh --publish`. Pull requests only test fixtures. Publishing uses a non-force Git push; if the wiki advanced, preview against the new tip again. Record the source and wiki commits printed by the script separately; a preview is not a publication.

Actions prefers the optional `MODHUNTER_WIKI_TOKEN` secret and otherwise uses the built-in token. If that credential lacks Wiki write permission, preview may succeed while publishing is refused; do not broaden access as a workaround without approval. Local execution can use an existing Git credential helper or the same environment variable. Do not paste credentials into remote URLs or logs.

## Athena closeout pilot

For material changes, record a short, source-anchored map in the change or relevant documentation: **entry point → changed component → dependency/output → affected consumer**. Walk one real path, pin the reviewed source revision and evidence, and distinguish code observation from inference and runtime proof. Mark the map stale if its evidence or source changes. No graph tool, account, or model is required. See the [reviewed Athena rule](https://github.com/Herbertofury/Athena/blob/1d02ea9a4f5a679e1578712c542efe41741e61af/guidance/WIKI_CLOSEOUT.md).

For this publisher: [workflow](../.github/workflows/sync-wiki.yml) → [launcher](../scripts/sync_wiki.sh) → [planner](../scripts/sync_wiki.py) → separate Wiki checkout. The [fixture tests](../scripts/test_sync_wiki.py) verify preservation, conflict preflight and repeat runs, not live credential or publication acceptance.
