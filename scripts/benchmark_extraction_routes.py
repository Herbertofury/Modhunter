"""Measure complete local HTTP observations with optional, separately installed crawler engines."""
import argparse
import asyncio
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
from time import perf_counter, sleep

ROWS = 120
ROUTES = ("scrapy", "playwright", "crawl4ai")


def expected(sequence):
    return [(f"Release {i} revision {sequence}", f"/file/{i}?v={sequence}") for i in range(ROWS)]


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        route, _, query = self.path.partition("?v=")
        if route not in ("/static", "/dynamic") or not query.isdigit():
            self.send_error(404)
            return
        links = "".join(f'<article><a href="{href}">{title}</a></article>' for title, href in expected(int(query)))
        body = links if route == "/static" else ""
        markup = f"<!DOCTYPE html><title>Published files</title><main>{body}</main>"
        if route == "/dynamic":
            markup += f"<script>setTimeout(()=>document.querySelector('main').innerHTML={json.dumps(links)},30)</script>"
        data = markup.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def verify(rows, sequence, start, results):
    rows = [tuple(row) for row in rows]
    elapsed = (perf_counter() - start) * 1000
    if rows != expected(sequence):
        raise ValueError(f"Expected every title and href in order, received {len(rows)} of {ROWS} for revision {sequence}")
    results.append({"ms": elapsed, "sha256": sha256(json.dumps(rows).encode()).hexdigest()})


def scrapy_run(args, start, results):
    import scrapy
    from scrapy.crawler import CrawlerProcess

    class Releases(scrapy.Spider):
        name = "fixture_releases"

        async def start(self):
            yield scrapy.Request(args.url + "?v=0", cb_kwargs={"sequence": 0, "started": start})

        def parse(self, response, sequence, started):
            nodes = response.css("main article a")
            rows = [(n.xpath("string(.)").get(), n.attrib.get("href")) for n in nodes]
            try:
                verify(rows, sequence, started, results)
            except ValueError as exc:
                errors.append(str(exc))
                return
            if sequence < args.rounds:
                yield scrapy.Request(args.url + f"?v={sequence + 1}",
                                     cb_kwargs={"sequence": sequence + 1, "started": perf_counter()})

    errors = []
    process = CrawlerProcess({"LOG_ENABLED": False, "ROBOTSTXT_OBEY": False,
                              "TELNETCONSOLE_ENABLED": False})
    process.crawl(Releases)
    process.start()
    if errors:
        raise ValueError(errors[0])


async def chromium_run(args, start, results):
    from playwright.async_api import async_playwright

    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(channel=args.channel)
        page = await browser.new_page()
        for sequence in range(args.rounds + 1):
            started = start if not sequence else perf_counter()
            response = await page.goto(args.url + f"?v={sequence}")
            if not response or response.status != 200:
                raise ValueError("Fixture HTTP response failed")
            await page.wait_for_selector("main article a")
            rows = await page.locator("main article a").evaluate_all(
                "links => links.map(a => [a.textContent, a.getAttribute('href')])")
            verify(rows, sequence, started, results)
        await browser.close()


async def crawl4ai_run(args, start, results):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from parsel import Selector

    options = {"headless": True, "verbose": False}
    if args.channel:
        options["channel"] = args.channel
        options["chrome_channel"] = args.channel
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, verbose=False, wait_for="css:main article a")
    async with AsyncWebCrawler(config=BrowserConfig(**options)) as crawler:
        for sequence in range(args.rounds + 1):
            started = start if not sequence else perf_counter()
            result = await crawler.arun(url=args.url + f"?v={sequence}", config=run_config)
            if not result.success or result.status_code != 200:
                raise ValueError(result.error_message or f"HTTP {result.status_code}")
            nodes = Selector(result.html).css("main article a")
            verify([(n.xpath("string(.)").get(), n.attrib.get("href")) for n in nodes], sequence, started, results)


def worker(args):
    start = perf_counter()
    samples = []
    report = {"engine": args.engine, "version": version(args.engine), "samples": samples}
    try:
        if args.engine == "scrapy":
            scrapy_run(args, start, samples)
        else:
            asyncio.run((chromium_run if args.engine == "playwright" else crawl4ai_run)(args, start, samples))
        if len(samples) != args.rounds + 1:
            raise ValueError("Incomplete observation series")
        warm = sorted(s["ms"] for s in samples[1:])
        report.update(complete=True, coldMs=samples[0]["ms"], medianMs=statistics.median(warm),
                      p95Ms=warm[math.ceil(.95 * len(warm)) - 1])
    except Exception as exc:
        report.update(complete=False, reason=str(exc))
    args.output.write_text(json.dumps(report), encoding="utf8")


def measure(args, base, route, engine, tmp):
    import psutil

    result = tmp / f"{route}-{engine}.json"
    cmd = [sys.executable, __file__, "--engine", engine, "--url", f"{base}/{route}",
           "--rounds", str(args.rounds), "--output", str(result)]
    if args.channel:
        cmd += ["--channel", args.channel]
    started = perf_counter()
    peak_rss = 0
    cpu_by_pid = {}
    with subprocess.Popen(cmd) as proc:
        root = psutil.Process(proc.pid)
        while proc.poll() is None:
            memory = 0
            try:
                family = [root, *root.children(recursive=True)]
            except psutil.NoSuchProcess:
                family = []
            for item in family:
                try:
                    memory += item.memory_info().rss
                    cpu = item.cpu_times()
                    cpu_by_pid[item.pid, item.create_time()] = cpu.user + cpu.system
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
            peak_rss = max(peak_rss, memory)
            sleep(.05)
    if proc.returncode:
        raise RuntimeError(f"{engine} process exited with status {proc.returncode}")
    report = json.loads(result.read_text(encoding="utf8"))
    report.update(workload=route, processTreePeakRssBytesSampled=peak_rss,
                  processTreeCpuSecondsSampled=sum(cpu_by_pid.values()), processWallSeconds=perf_counter() - started)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--channel", choices=("chrome", "msedge", "chromium"), default="chromium",
                        help="Use the same installed Chromium channel in both rendering engines")
    parser.add_argument("--engines", nargs="+", choices=ROUTES, default=ROUTES)
    parser.add_argument("--engine", choices=ROUTES, help=argparse.SUPPRESS)
    parser.add_argument("--url", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if args.engine:
        worker(args)
        return
    with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server, tempfile.TemporaryDirectory() as tmp:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            results = [measure(args, f"http://127.0.0.1:{server.server_port}", route, engine, Path(tmp))
                       for route in ("static", "dynamic") for engine in args.engines]
        finally:
            server.shutdown()
    args.output.write_text(json.dumps({"rowsPerObservation": ROWS, "warmRounds": args.rounds, "browserChannel": args.channel,
        "scope": "Serial fresh HTTP loads on loopback. Cold includes engine import and startup. Every observation verifies all titles, relative links and order. CPU and aggregate RSS sampled at about 50 ms; shared pages can be counted multiple times.",
        "results": results}, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
