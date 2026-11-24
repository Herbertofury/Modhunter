"""Run changedetection.io batch mode on a public scraping-practice page and verify its full snapshot."""
from argparse import ArgumentParser
from hashlib import sha256
from html.parser import HTMLParser
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter, sleep
from urllib.request import urlopen


class TitleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.article = self.heading = self.link = False
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            self.article = "product_pod" in dict(attrs).get("class", "").split()
        elif tag == "h3":
            self.heading = self.article
        elif tag == "a" and self.heading:
            self.link = True
            self.rows.append("")

    def handle_endtag(self, tag):
        if tag == "article":
            self.article = False
        elif tag == "h3":
            self.heading = False
        elif tag == "a":
            self.link = False

    def handle_data(self, data):
        if self.link:
            self.rows[-1] += data


def check(store, log, url, rounds):
    expected = TitleText()
    with urlopen(url, timeout=40) as response:
        expected.feed(response.read().decode("utf-8"))
    expected.close()
    watches = list(store.glob("*/watch.json"))
    if len(watches) != 1:
        raise ValueError("Expected exactly one owned watch")
    watch = json.loads(watches[0].read_text(encoding="utf8"))
    history = (watches[0].parent / "history.txt").read_text(encoding="utf8").splitlines()
    if watch.get("last_error") or len(history) != 1:
        raise ValueError("The repeated checks failed or produced different snapshots")
    _, basename = history[0].split(",", 1)
    if Path(basename).name != basename:
        raise ValueError("Unexpected snapshot name")
    rows = (watches[0].parent / basename).read_text(encoding="utf8").splitlines()
    if len(expected.rows) != 20 or rows != expected.rows:
        raise ValueError("Saved snapshot differs from the complete visible book titles")
    times = [float(s) * 1000 for s in re.findall(r"completed watch [\w-]+ in ([.\d]+)s", log)]
    if len(times) != rounds:
        raise ValueError("Missing completed worker timings")
    warm = sorted(times[1:])
    return {"release": version("changedetection.io"), "url": url, "rows": len(rows),
            "snapshotSha256": sha256("\n".join(rows).encode()).hexdigest(), "distinctSnapshots": len(history),
            "workerMs": times, "warmMedianMs": statistics.median(warm),
            "warmP95Ms": warm[math.ceil(.95 * len(warm)) - 1]}


def main():
    import psutil
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=6)
    args = parser.parse_args()
    if args.rounds < 2:
        parser.error("At least two checks are required")
    exe = shutil.which("changedetection.io", path=str(Path(sys.executable).parent))
    if not exe:
        parser.error("Install changedetection.io in the current Python environment")
    url = "https://books.toscrape.com/"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = os.environ.copy()
        if os.name == "nt":
            env.update(APPDATA=str(root / "roaming"), LOCALAPPDATA=str(root / "local"))
        store, log = root / "store", root / "worker.log"
        options = {"include_filters": ["article.product_pod h3 a"], "fetch_backend": "html_requests"}
        cmd = [exe, "-h", "127.0.0.1", "-C", "-d", str(store), "-u", url, "-u0", json.dumps(options),
               "-r", "all", str(args.rounds), "-b"]
        start = perf_counter()
        peak = 0
        cpu = {}
        with log.open("wb") as output, subprocess.Popen(cmd, env=env, stdout=output, stderr=subprocess.STDOUT) as proc:
            process = psutil.Process(proc.pid)
            while proc.poll() is None:
                memory = 0
                try:
                    children = [process, *process.children(recursive=True)]
                except psutil.NoSuchProcess:
                    children = []
                for child in children:
                    try:
                        memory += child.memory_info().rss
                        sample = child.cpu_times()
                        cpu[child.pid, child.create_time()] = sample.user + sample.system
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        continue
                peak = max(peak, memory)
                sleep(.05)
        wall = perf_counter() - start
        trace = log.read_text(encoding="utf8", errors="replace")
        # The upstream batch runner signals itself after the final worker finishes.
        # Windows reports this native SIGTERM as exit status 15.
        finished = f"Batch mode: All {args.rounds} iterations completed, initiating shutdown"
        if finished not in trace or proc.returncode not in ((0, 15) if os.name == "nt" else (0, -15)):
            raise RuntimeError(f"Batch completion was not verified (exit {proc.returncode})")
        result = check(store, trace, url, args.rounds)
        result["processExitCode"] = proc.returncode
        result.update(processWallSeconds=wall, processTreeCpuSecondsSampled=sum(cpu.values()),
                      processTreePeakRssBytesSampled=peak,
                      scope="Serial worker observations on one public practice page. Timing includes HTTP and the selected text processor. Batch wall time includes startup, scheduling and shutdown. Resource samples use a 50 ms interval.")
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
