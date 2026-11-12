#!/usr/bin/env bash
# Syncs wiki Markdown into the separate GitHub Wiki Git repository.
set -euo pipefail
repo="${GITHUB_REPOSITORY:-Herbertofury/Modhunter}"
remote="https://github.com/${repo}.wiki.git"
if [[ -n "${MODHUNTER_WIKI_TOKEN:-}" ]];then
  remote="https://x-access-token:${MODHUNTER_WIKI_TOKEN}@github.com/${repo}.wiki.git"
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
if ! git clone -q "$remote" "$work/wiki"; then
  echo 'Wiki clone unavailable. Initialize the first Home page on GitHub Wiki first, or check Wiki push credentials.' >&2
  exit 2
fi
cd "$work/wiki"
# GitHub Wiki supports Markdown filenames including _Sidebar.md / _Footer.md.
find . -mindepth 1 -maxdepth 1 ! -name .git -type f -delete
cp -a "$here/wiki/." ./
# Exclude the repository-only instructions from the live Wiki.
rm -f README.md
git add -A
if git diff --cached --quiet;then echo 'Wiki already in sync';exit 0;fi
git -c user.name='Modhunter Wiki Sync' -c user.email='actions@users.noreply.github.com' commit -qm 'docs: synchronize Modhunter Wiki'
git push -q origin HEAD
printf 'Synced Modhunter Wiki pages successfully.\n'
