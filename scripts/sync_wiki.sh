#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  ""|--preview) mode=--preview ;;
  --publish) mode=--publish ;;
  *) echo 'Usage: bash scripts/sync_wiki.sh [--preview|--publish]' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || exit 2

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote="https://github.com/Herbertofury/Modhunter.wiki.git"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT

if [[ -n "${MODHUNTER_WIKI_TOKEN:-}" ]]; then
  cat > "$work/askpass" <<'ASK'
#!/usr/bin/env bash
case "$1" in
  *Username*) printf '%s\n' x-access-token ;;
  *Password*) printf '%s\n' "$MODHUNTER_WIKI_TOKEN" ;;
  *) exit 1 ;;
esac
ASK
  chmod 700 "$work/askpass"
  export GIT_ASKPASS="$work/askpass"
fi
export GIT_TERMINAL_PROMPT=0

if ! git clone -q "$remote" "$work/wiki"; then
  echo 'Wiki clone failed. Check the initialized Wiki and Git credentials.' >&2
  exit 2
fi
if ! git -C "$work/wiki" rev-parse --verify HEAD >/dev/null; then
  echo 'Wiki has no commit. Initialize the first Home page before sync.' >&2
  exit 2
fi
git -C "$here" diff --quiet HEAD -- wiki/ &&
  [[ -z "$(git -C "$here" status --porcelain --untracked-files=all -- wiki/)" ]] ||
  { echo 'Commit wiki source changes before syncing.' >&2; exit 2; }
source_commit="$(git -C "$here" rev-parse HEAD)"
mkdir "$work/source"
git -C "$here" archive "$source_commit" wiki/ | tar -xf - -C "$work/source"
echo "Source commit: $source_commit"
echo "Current wiki commit: $(git -C "$work/wiki" rev-parse HEAD)"
if [[ "$mode" == --preview ]]; then
  python3 "$here/scripts/sync_wiki.py" "$work/source/wiki" "$work/wiki"
  exit
fi
python3 "$here/scripts/sync_wiki.py" "$work/source/wiki" "$work/wiki" --apply
git -C "$work/wiki" add -A
if git -C "$work/wiki" diff --cached --quiet; then
  echo 'Wiki already current.'
  exit
fi
git -C "$work/wiki" -c user.name='Modhunter Wiki Sync' -c user.email='actions@users.noreply.github.com' commit -qm "docs: sync Modhunter Wiki from $source_commit"
git -C "$work/wiki" push -q origin HEAD
printf 'Wiki push accepted for %s\n' "$(git -C "$work/wiki" rev-parse HEAD)"
