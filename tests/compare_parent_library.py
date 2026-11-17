"""Compare exact parent/release sources with the unchanged full-library benchmark.

This is diagnostic evidence only. It never edits product code, publishes a
release, changes release ceilings, forces GC, or substitutes traced warmups for
ordinary performance samples. Run from a clean committed proof-only branch.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys

PARENT = '2f1dfddbd92732f83fc145defd194d4b286bf8ec'
CANDIDATE = 'f96b86969ec9f9c7ba6928284f16223b673d33c0'
SETTLE = '() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))'


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


class TraceLocator:
    def __init__(self, locator, page):
        self.actual, self.page = locator, page

    def __getattr__(self, name):
        return getattr(self.actual, name)

    def click(self, *args, **kwargs):
        self.page.start_trace()
        return self.actual.click(*args, **kwargs)


class TracePage:
    def __init__(self, page, context, name, output, receipts, tracing):
        self.actual, self.context, self.name = page, context, name
        self.output, self.receipts, self.tracing = output, receipts, tracing
        self.capture, self.session = None, None
        self.traced = False

    def __getattr__(self, name):
        return getattr(self.actual, name)

    def locator(self, selector, *args, **kwargs):
        value = self.actual.locator(selector, *args, **kwargs)
        return TraceLocator(value, self) if selector == '#libraryList' else value

    def evaluate(self, expression, *args, **kwargs):
        result = self.actual.evaluate(expression, *args, **kwargs)
        if self.capture is not None and expression == SETTLE:
            self.finish_trace()
        return result

    def start_trace(self):
        if self.traced:
            raise AssertionError('Expected exactly one List trace per excluded warmup')
        self.traced = True
        self.session = self.context.new_cdp_session(self.actual)
        available = set(self.session.send('Tracing.getCategories')['categories'])
        requested = ('devtools.timeline', 'blink.user_timing', 'v8',
                     'disabled-by-default-v8.gc', 'blink_gc')
        categories = tuple(category for category in requested if category in available)
        if not {'devtools.timeline', 'blink.user_timing'}.issubset(categories):
            raise AssertionError('Chromium does not expose required timeline categories')
        self.tracing.TRACE_MODES['parent-list'] = categories
        self.categories = categories
        self.capture = self.tracing.begin_trace(self.session, 'parent-list')
        self.actual.evaluate('name=>performance.mark(name)',
                             'modhunter-style-profile-list-start')

    def finish_trace(self):
        self.actual.evaluate('name=>performance.mark(name)',
                             'modhunter-style-profile-list-end')
        destination = self.output / (self.name + '-warmup-list.trace.json.gz')
        trace, receipt = self.tracing.finish_trace(
            self.session, self.actual, destination, self.capture, completion_seconds=10)
        self.capture = None
        if receipt.get('dataLossOccurred'):
            raise AssertionError('List diagnostic trace lost events')
        summary = self.tracing.summarize_trace(trace)
        for name in ('modhunter-style-profile-list-start', 'modhunter-style-profile-list-end'):
            if not summary['eventCounts'].get(name):
                raise AssertionError('List diagnostic trace lacks its workload marker: ' + name)
        summary_path = self.output / (self.name + '-warmup-list.trace-summary.json')
        write_json(summary_path, summary)
        receipt.update(variant=self.name, warmup=True, includedInMeasuredMedians=False,
                       categories=self.categories, summaryFile=summary_path.name,
                       gcEventTotals=[event for event in summary['timedEventTotals']
                                      if re.search(r'gc|garbage|scavenge|mark.compact', event['name'], re.I)],
                       styleLayoutEvents=summary['styleLayoutEvents'])
        self.receipts.append(receipt)


class TraceContext:
    def __init__(self, actual, name, output, receipts, tracing):
        self.actual, self.name = actual, name
        self.output, self.receipts, self.tracing = output, receipts, tracing

    def __getattr__(self, name):
        return getattr(self.actual, name)

    def new_page(self):
        return TracePage(self.actual.new_page(), self.actual, self.name,
                         self.output, self.receipts, self.tracing)

    def new_cdp_session(self, page):
        return self.actual.new_cdp_session(page.actual if isinstance(page, TracePage) else page)


class WarmupTraceBrowser:
    """Wrap only the first two contexts; measured rounds use raw Playwright."""
    def __init__(self, actual, output, receipts, tracing):
        self.actual, self.output, self.receipts, self.tracing = actual, output, receipts, tracing
        self.contexts = 0

    def new_context(self, *args, **kwargs):
        actual = self.actual.new_context(*args, **kwargs)
        index = self.contexts
        self.contexts += 1
        if index < 2:
            return TraceContext(actual, ('before', 'after')[index], self.output,
                                self.receipts, self.tracing)
        return actual


def comparison(result):
    before, after = result['medians']['before'], result['medians']['after']
    values = {}
    for metric in before:
        pairs = [{'round':i+1, 'before':left[metric], 'after':right[metric],
                  'ratio':right[metric]/left[metric]}
                 for i, (left, right) in enumerate(zip(result['variants']['before'],
                                                       result['variants']['after']))]
        values[metric] = {'beforeMedian':before[metric], 'afterMedian':after[metric],
                          'medianRatio':after[metric]/before[metric], 'pairedRounds':pairs}
    sums = {variant:[sum(row[key] for key in ('galleryMs','galleryDensityMs','listMs'))
                     for row in samples] for variant,samples in result['variants'].items()}
    return {'metrics':values, 'threeActionMeasuredSumsMs':sums,
            'threeActionMedianRatio':statistics.median(sums['after'])/statistics.median(sums['before']),
            'scope':'Three-action sums exclude harness work between intervals; not end-to-end clocks. No automatic no-loss assertion or modified release budget.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--rounds', type=int, choices=(3,4,5), default=3)
    parser.add_argument('--trace-warmup', action='store_true')
    parser.add_argument('--describe', action='store_true', help='Compile/check exact source identities without launching a browser')
    args = parser.parse_args()
    root = args.repo.resolve()
    output = (args.output or root/'native/target/parent-library-comparison').resolve()
    sys.path.insert(0, str(root/'tests'))
    import visual_release_qa as acceptance
    import visual_style_profile as tracing
    if acceptance.ROOT != root:
        raise AssertionError('Imported benchmark belongs to a different repository')
    for relative in ('tests/visual_release_qa.py', 'tests/visual_style_profile.py',
                     'tests/test_frontend_responsiveness.py', 'mo2_update_arsenal/web/catalog.json'):
        local = (root/relative).read_bytes()
        expected = subprocess.check_output(['git','show',CANDIDATE+':'+relative], cwd=root)
        if local != expected:
            raise AssertionError('Proof-only branch changed the existing measurement helper: '+relative)
    compiled = {'before':acceptance.compile_dashboard(PARENT),
                'after':acceptance.compile_dashboard(CANDIDATE)}
    if compiled['before'][1]['mo2_update_arsenal/web/catalog.json'] != compiled['after'][1]['mo2_update_arsenal/web/catalog.json']:
        raise AssertionError('Compared sources use different fixture seeds')
    identity = {'parentCommit':PARENT, 'candidateCommit':CANDIDATE, 'mods':11207,
                'rounds':args.rounds, 'warmupPairs':1,
                'benchmarkFunctionSha256':hashlib.sha256(inspect.getsource(acceptance.benchmark).encode()).hexdigest(),
                'sourceHashes':{key:value[1] for key,value in compiled.items()},
                'measurement':'Unchanged acceptance.benchmark(): full import, exact last-item search, restoration, sort, navigation, List density, Gallery, Gallery density, List, final density reset; fresh contexts and alternating order.',
                'scope':'Controlled diagnostic only. ok means completed correct workload, not absence of performance regressions. No publication, ceiling changes, forced GC, or removal of records/features.'}
    if args.describe:
        print(json.dumps(identity, indent=2))
        return
    source_status = subprocess.check_output(['git','status','--porcelain','--untracked-files=all'], cwd=root, text=True)
    if source_status:
        raise AssertionError('Controlled comparison requires a clean committed source tree')
    proof_commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root, text=True).strip()
    output.mkdir(parents=True, exist_ok=True)
    proof = dict(identity, ok=False, diagnosticCommit=proof_commit, platform=platform.platform(),
                 pythonVersion=platform.python_version(), playwrightVersion=importlib.metadata.version('playwright'),
                 logicalCpus=os.cpu_count(), traceWarmupOnly=args.trace_warmup, traces=[])
    receipt_path = output/'parent-library-comparison.json'
    def checkpoint(value):
        proof['completeLibrary'] = value
        write_json(receipt_path, proof)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as runtime:
            launch = {'headless':True, 'args':['--no-sandbox','--disable-dev-shm-usage']}
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
            if executable:
                launch['executable_path'] = executable
            browser = runtime.chromium.launch(**launch)
            proof['browserVersion'] = browser.version
            proof['executablePath'] = executable or 'Playwright managed Chromium'
            driver = WarmupTraceBrowser(browser, output, proof['traces'], tracing) if args.trace_warmup else browser
            try:
                result = acceptance.benchmark(driver, {name:value[0] for name,value in compiled.items()},
                                              11207, args.rounds, checkpoint)
                proof['completeLibrary'] = result
                proof['comparison'] = comparison(result)
                if args.trace_warmup and len(proof['traces']) != 2:
                    raise AssertionError('Missing parent or candidate excluded-warmup List trace')
                proof['ok'] = True
            finally:
                browser.close()
        end_commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root, text=True).strip()
        end_status = subprocess.check_output(['git','status','--porcelain','--untracked-files=all'], cwd=root, text=True)
        if end_commit != proof_commit or end_status:
            proof['ok'] = False
            raise AssertionError('Source changed during the controlled comparison')
    except BaseException as error:
        proof['ok'] = False
        proof['error'] = repr(error)
        raise
    finally:
        write_json(receipt_path, proof)
    print(json.dumps({'ok':proof['ok'], 'parent':PARENT, 'candidate':CANDIDATE,
                      'browserVersion':proof['browserVersion'], 'medians':result['medians'],
                      'comparison':proof['comparison'], 'traces':[{'variant':x['variant'],
                          'file':x['file'], 'gcEventTotals':x['gcEventTotals']} for x in proof['traces']]}, indent=2))


if __name__ == '__main__':
    main()
