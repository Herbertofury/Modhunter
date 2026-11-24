"""Verify a self-hosted Firecrawl against every book on one public scraping-practice page."""
import argparse
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class Books(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.base = url
        self.in_product = False
        self.in_heading = False
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article":
            self.in_product = "product_pod" in attrs.get("class", "").split()
        if tag == "h3":
            self.in_heading = True
        if tag == "a" and self.in_product and self.in_heading:
            self.items.append((attrs["title"], urljoin(self.base, attrs["href"])))

    def handle_endtag(self, tag):
        if tag == "article":
            self.in_product = False
        if tag == "h3":
            self.in_heading = False


def items(html, url):
    parser = Books(url)
    parser.feed(html)
    parser.close()
    if len(parser.items) != 20 or len(set(parser.items)) != 20:
        raise ValueError("The practice page no longer contains the expected 20 distinct books")
    return parser.items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:3002/v2/scrape")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", default=6, type=int)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    url = "https://books.toscrape.com/"
    direct, service = [], []
    expected = None
    payload = json.dumps({"url": url, "formats": ["rawHtml"], "onlyMainContent": False, "maxAge": 0}).encode()
    for _ in range(args.rounds + 1):
        start = perf_counter()
        with urlopen(url, timeout=60) as response:
            baseline = items(response.read().decode("utf-8"), url)
        direct.append((perf_counter() - start) * 1000)
        if expected is not None and expected != baseline:
            raise ValueError("The public site's catalog changed during the comparison")
        expected = baseline
        start = perf_counter()
        request = Request(args.endpoint, payload, {"Content-Type": "application/json"})
        with urlopen(request, timeout=120) as response:
            observed = json.load(response)
        if not observed.get("success") or items(observed["data"]["rawHtml"], url) != expected:
            raise ValueError("Firecrawl returned an incomplete or changed catalog")
        service.append((perf_counter() - start) * 1000)

    def metrics(values):
        ordered = sorted(values[1:])
        return {"firstMs": values[0], "warmSamplesMs": values[1:], "medianMs": median(ordered),
                "p95Ms": ordered[math.ceil(len(ordered) * .95) - 1]}
    args.output.write_text(json.dumps({"url": url, "rows": len(expected), "warmRounds": args.rounds,
        "scope": "End-to-end public HTTP and completed local scrape API responses. Service is already running and uses maxAge=0. OS, network and upstream CDN caches are uncontrolled.",
        "outputSha256": sha256(json.dumps(expected).encode()).hexdigest(),
        "directHttp": metrics(direct), "selfHostedFirecrawl": metrics(service)}, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
