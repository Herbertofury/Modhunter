#!/usr/bin/env bash
# Exercise the real launcher against a disposable Git remote, with no network.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(mktemp -d)"
trap 'rm -rf -- "$root"' EXIT
export GIT_CONFIG_GLOBAL="$root/gitconfig" GIT_CONFIG_NOSYSTEM=1
unset MODHUNTER_WIKI_TOKEN
git config --global user.name 'Wiki Fixture'
git config --global user.email 'fixture@example.invalid'
git config --global url."file://$root/wiki-remote".insteadOf 'https://github.com/Herbertofury/Modhunter.wiki.git'
git init -q --bare --initial-branch=main "$root/wiki-remote"
git init -q --initial-branch=main "$root/seed"
printf 'original\n' > "$root/seed/Home.md"
printf 'manual notes\n' > "$root/seed/Personal.md"
git -C "$root/seed" add .
git -C "$root/seed" commit -qm 'seed wiki'
git -C "$root/seed" push -q "$root/wiki-remote" main
initial="$(git -C "$root/wiki-remote" rev-parse HEAD)"

mkdir -p "$root/source/scripts" "$root/source/wiki"
cp "$here"/sync_wiki.{sh,py} "$root/source/scripts/"
cp "$root/seed/Home.md" "$root/source/wiki/Home.md"
git init -q --initial-branch=main "$root/source"
git -C "$root/source" add .
git -C "$root/source" commit -qm 'publisher source'
preview="$(bash "$root/source/scripts/sync_wiki.sh")"
grep -F "Current wiki commit: $initial" <<< "$preview"
test "$(git -C "$root/wiki-remote" rev-parse HEAD)" = "$initial"
bash "$root/source/scripts/sync_wiki.sh" --publish
adopted="$(git -C "$root/wiki-remote" rev-parse HEAD)"
test "$adopted" != "$initial"
git -C "$root/wiki-remote" show HEAD:.modhunter-managed.json | grep -F '"Home.md"'
git -C "$root/wiki-remote" show HEAD:Personal.md | grep -Fx 'manual notes'
bash "$root/source/scripts/sync_wiki.sh" --publish
test "$(git -C "$root/wiki-remote" rev-parse HEAD)" = "$adopted"

git -C "$root/seed" pull --ff-only -q "$root/wiki-remote" main
printf 'human edit\n' > "$root/seed/Home.md"
git -C "$root/seed" commit -qam 'manual wiki edit'
git -C "$root/seed" push -q "$root/wiki-remote" main
advanced="$(git -C "$root/wiki-remote" rev-parse HEAD)"
printf 'new source\n' > "$root/source/wiki/Home.md"
git -C "$root/source" commit -qam 'change source Home'
if bash "$root/source/scripts/sync_wiki.sh" --publish; then
  echo 'Conflict unexpectedly published' >&2
  exit 1
fi
test "$(git -C "$root/wiki-remote" rev-parse HEAD)" = "$advanced"
git -C "$root/wiki-remote" show HEAD:Home.md | grep -Fx 'human edit'
