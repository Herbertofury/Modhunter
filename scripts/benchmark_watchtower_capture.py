"""Compare completed rendered captures in the same Chromium process and verify every byte."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "0547bcd2e4ca5cc1bb693d74dc636935e94bbac7"


def prior_script(ref):
    path = "native/crates/modhunter-desktop/src/watchtower_policy.rs"
    source = subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT).decode()
    if 'const CAPTURE_BODY: &str = r#"' in source:
        return source.split('const CAPTURE_BODY: &str = r#"', 1)[1].split('"#;', 1)[0]
    return subprocess.check_output(["git", "show", f"{ref}:native/ui/capture-page.js"], cwd=ROOT).decode()


def summary(samples):
    ordered = sorted(samples)
    return {"samplesMs": samples, "medianMs": statistics.median(samples),
            "p95Ms": ordered[math.ceil(len(samples) * .95) - 1]}


def run(ref, rounds):
    scripts = {"baseline": prior_script(ref), "candidate": (ROOT / "native/ui/capture-page.js").read_text("utf8")}
    results = []
    with sync_playwright() as runtime, runtime.chromium.launch() as browser:
        for ignored in (False, True):
            page = browser.new_page()
            parts = [f"<article>Release {i}<aside>Comment {i}</aside></article>" for i in range(1200)]
            page.set_content("<main>" + "".join(parts) + "</main>")
            cfg = {"url": page.url, "selector": "main", "ignore": ["aside"] if ignored else []}
            expected = "\n".join(text for i in range(1200) for text in
                                 ([f"Release {i}"] if ignored else [f"Release {i}", f"Comment {i}"]))
            samples = {name: [] for name in scripts}
            first = {}
            for trial in range(rounds + 1):
                order = list(scripts) if trial % 2 == 0 else list(reversed(scripts))
                for name in order:
                    js = "cfg => {const start = performance.now(); const observation = (() => {" + scripts[name]
                    js += "})(); return {ms: performance.now()-start, observation};}"
                    result = page.evaluate(js, cfg)
                    observation = result["observation"]
                    if observation["status"] != "ok" or observation["truncated"] or observation["text"] != expected:
                        raise ValueError(f"{name} did not return the complete expected capture")
                    if trial:
                        samples[name].append(result["ms"])
                    else:
                        first[name] = result["ms"]
            results.append({"entries": len(parts), "ignoreSelector": cfg["ignore"],
                            "outputSha256": hashlib.sha256(expected.encode()).hexdigest(),
                            "firstCaptureMs": first, "warm": {key: summary(times) for key, times in samples.items()}})
            page.close()
        version = browser.version
    return {"baseline": ref, "chromium": version, "rounds": rounds,
            "scope": "In-page completed extraction on a generated DOM, excluding browser startup, network and IPC.",
            "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=BASELINE)
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    report = run(args.baseline_ref, args.rounds)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps([s["warm"]["candidate"]["medianMs"] for s in report["results"]]))
