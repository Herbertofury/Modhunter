"""Read-only pinned release vs candidate comparison on one owned build lane."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from qt_heartbeat import EXPECTED_TEST1_COMMIT
from fixture import SIZES

REPO = "Herbertofury/Modhunter"
OPT_IN_REF = "refs/heads/upgrade/modhunter-mo2-refresh-fast"
BASELINE_MANIFEST = "15efc9150f72d8680c562aa29c5263f342586e7fa28accdce45299dc23996be7"
BASELINE_TREE = "e81e2357ec7da6f9fa8e4af072ad81622f403fca"


def opted_in(env):
    return (env.get("GITHUB_ACTIONS") == "true"
            and env.get("GITHUB_REPOSITORY", "").casefold() == REPO.casefold()
            and env.get("GITHUB_EVENT_NAME") == "push"
            and env.get("GITHUB_REF") == OPT_IN_REF)


def require(condition, note):
    if not condition:
        raise RuntimeError(note)


def run(repo, candidate):
    if not opted_in(os.environ):
        return
    require(sys.platform == "win32" and candidate == os.environ.get("GITHUB_SHA")
            and len(candidate) == 40 and all(c in "0123456789abcdef" for c in candidate)
            and candidate != EXPECTED_TEST1_COMMIT, "Same-repository hosted candidate not bound")
    scratch = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    worker = Path(__file__).with_name("qt_heartbeat.py")
    # Fresh repo and only the pinned original Git object; no tags, alternate
    # remotes, persisted credentials/config, or writes to the candidate repo.
    with tempfile.TemporaryDirectory(prefix="modhunter-qt-release-baseline-", dir=scratch) as key:
        baseline = Path(key)
        def git(*args):
            return subprocess.check_output(["git", "-C", str(baseline), *args],
                                           timeout=95, text=True).strip()
        git("init", "--quiet")
        git("fetch", "--quiet", "--depth=1", "--no-tags", f"https://github.com/{REPO}.git", EXPECTED_TEST1_COMMIT)
        require(git("rev-parse", "FETCH_HEAD") == EXPECTED_TEST1_COMMIT and
                git("rev-parse", "FETCH_HEAD^{tree}") == BASELINE_TREE, "Wrong immutable release preimage")
        git("-c", "core.autocrlf=false", "checkout", "--detach", "--quiet", "FETCH_HEAD")
        # Same path anchors full portable-state Git/path-derived output hashes;
        # restore fresh files/appdata for each trial, and balance ordering by
        # reversing the starts per size. No absolute paths in output.
        with tempfile.TemporaryDirectory(prefix="modhunter-qt-heartbeat-", dir=scratch) as case:
            private = Path(case)
            for i, count in enumerate(SIZES):
                arms = [("test.1", baseline, EXPECTED_TEST1_COMMIT), ("candidate", repo, candidate)]
                if i % 2:
                    arms.reverse()
                runs = {}
                for label, source, commit in arms:
                    require(not any(private.iterdir()), "Previous fixture data was not isolated")
                    cmd = [sys.executable, str(worker), "--repository", str(source), "--count", str(count),
                           "--expect-commit", commit, "--output", str(private / "unused"),
                           "--private-child", str(private)]
                    output = subprocess.check_output(cmd, timeout=180, text=True)
                    r = json.loads(output)
                    require(r.get("ok") is True and r.get("hostedWindowsQt") is True and r.get("nativeMO2") is False
                            and r.get("candidateCommit") == commit and r.get("inputCount") == count,
                            "Unexpected fixture run receipt")
                    if label == "test.1":
                        require(r["source"] == {"files": 28, "pythonAndHtmlManifestSha256": BASELINE_MANIFEST},
                                "Baseline did not match all packaged Python/HTML bytes")
                    runs[label] = r
                    # Production service and helper only wrote into this private
                    # test directory. Delete only the whole owned trial tree.
                    shutil.rmtree(private)
                    private.mkdir()
                baseline_run, new = runs["test.1"], runs["candidate"]
                require(baseline_run["fixtureFilesSha256"] == new["fixtureFilesSha256"] and
                        [(a["name"], a["input"]) for a in baseline_run["phases"]] ==
                        [(a["name"], a["input"]) for a in new["phases"]],
                        "Exact normalized snapshot or fixture differed")
                print("MODHUNTER_QT_HEARTBEAT_DIAGNOSTIC " +
                      json.dumps({"scope": "synthetic organizer, actual hosted Windows Qt, no MO2",
                                  "order": [a[0] for a in arms], "inputCount": count, **runs},
                                 allow_nan=False, separators=(",", ":")), flush=True)
