"""Strict standalone Chromium A/B for the detached artwork-only clip proposal.

Only synthetic libraries and intercepted artwork. No releases, native evidence,
user profiles, or browser fallback. Run explicitly on a supported browser host:
python tests/gallery_artwork_clip_ab.py --executable PATH --output DIRECTORY
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter

from PIL import Image, ImageChops
import visual_release_qa as qa
from visual_style_profile import (
    compare_geometry, complete_geometry, deep_row_geometry, record_markup_fingerprint, metric_snapshot,
)

OLD_CSS = "82ea1fe2712b3b834bd50381e301f86448efb22adf8a7747edfef42550625009"
SELECTOR = '#modList > .mod-row > button.mod-art[data-act="preview"]'
APP = 'http://gallery-art-clip.fixture.test/'
FIXTURE_URL = 'https://cdn.modrinth.com/data/fixture/'
SIZE = 11207
FIXTURE_SHA = "afd855e4ac76f284630ffb4ed6d9dc37280113c9559aef4402516795b7fef0a0"


def exact_arms():
    candidate, assets = qa.compile_dashboard()
    css = (qa.ROOT / qa.WEB / 'atelier.css').read_text('utf-8')
    fragment = re.findall(r'/\* Gallery preview art is a crop, not a separate interactive scroller\..*?\n}\n', css, re.S)
    assert len(fragment) == 1 and candidate.count(fragment[0]) == 1
    assert SELECTOR + '{overflow:clip}' in fragment[0]
    restored = css.replace(fragment[0], '')
    assert hashlib.sha256(restored.encode()).hexdigest() == OLD_CSS, 'A/B control is not exact shipped CSS'
    return candidate.replace(fragment[0], ''), candidate, assets


def asset():
    data = json.loads((qa.ROOT / 'tests/fixtures/visual_public_catalog.json').read_text('utf-8'))
    iris = next(item for item in data['sources'] if item['slug'] == 'iris')
    raw = (qa.ROOT / 'tests/fixtures' / iris['thumbnailFile']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == iris['sha256']
    return raw, iris['mimeType']


def synthetic_catalog():
    catalog = qa.dashboard.catalog(count=9)
    for index, item in enumerate(catalog['items']):
        if index % 3 in (1, 2):
            item['update'].update(thumbnailUrl=FIXTURE_URL + ('good.png' if index % 3 == 1 else 'missing.png'),
                                  thumbnailKind='icon' if index > 3 else 'image')
        item['name'] += ' ArtClipFindTarget' if index == 0 else ' — long title · längerer Titel · 超級模組相容性'
        if index % 3 == 2:
            item['source'] = {'type':'modrinth','verified':True,'url':f'https://modrinth.com/mod/fixture-{index}'}
    return catalog


def complete_fixture():
    fixture = qa.dashboard.catalog(count=SIZE)
    fixture['offline'] = True
    data = json.dumps(fixture, sort_keys=True).encode()
    assert hashlib.sha256(data).hexdigest() == FIXTURE_SHA, 'Ordinary trial fixture is not the exact native generator bytes'
    return fixture


def open_arm(browser, html, viewport, fixture, *, forced_colors='none', height=900, motion='reduce'):
    context = browser.new_context(viewport={'width': viewport, 'height': height},
                                  forced_colors=forced_colors, reduced_motion=motion)
    uri = qa.fixture_page(html, fixture) if fixture else html
    raw, mime = asset()
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_default_timeout(30000)

    def route(request):
        url = request.request.url
        if url == APP:
            request.fulfill(content_type='text/html', body=uri)
        elif url == FIXTURE_URL + 'good.png':
            request.fulfill(content_type=mime, body=raw)
        elif url == FIXTURE_URL + 'missing.png':
            request.fulfill(status=404, content_type='text/plain', body='Expected test fallback')
        else:
            request.abort()  # no real provider, download or font hosts
    context.route('**/*', route)
    page.goto(APP, wait_until='load')
    page.wait_for_function('!!window.__ARSENAL_QA__')
    return context, page, errors


def assert_modes(page, view, expect):
    assert page.locator('body').get_attribute('data-library-view') == view
    assert page.locator(SELECTOR).evaluate_all(
        '(nodes,expected)=>nodes.length>0&&nodes.every(n=>getComputedStyle(n).overflow===expected)', expect
    ), f'Preview overflow should be {expect}'
    row_overflow = page.locator('#modList > .mod-row').first.evaluate('el=>getComputedStyle(el).overflow')
    assert row_overflow != 'clip', 'Card container was changed by artwork arm'


def screenshot(page, path):
    page.screenshot(path=str(path), caret='hide', animations='disabled')
    return path


def pixels_match(control, variant):
    with Image.open(control) as a, Image.open(variant) as b:
        a, b = a.convert('RGB'), b.convert('RGB')
        assert a.size == b.size and ImageChops.difference(a, b).getbbox() is None, f'Pixel mismatch: {control.name}'


def ax(session, selector):
    root = session.send('DOM.getDocument', {'depth': 0})['root']['nodeId']
    node = session.send('DOM.querySelector', {'nodeId': root, 'selector': selector})['nodeId']
    assert node
    observed = session.send('Accessibility.getPartialAXTree', {'nodeId': node, 'fetchRelatives': False})['nodes'][0]
    def normalized(value):
        if isinstance(value, dict):
            return {key:normalized(child) for key,child in value.items() if key!='backendDOMNodeId'}
        if isinstance(value, list):
            return [normalized(child) for child in value]
        return value
    return normalized({key:observed.get(key) for key in ('ignored','role','name','description','properties')})


def focused_art_test(browser, html, arm, output, width, colors, density, lang, zoom):
    context, page, errors = open_arm(browser, html, width, synthetic_catalog(), forced_colors=colors)
    try:
        cdp = context.new_cdp_session(page)
        cdp.send('Accessibility.enable')
        page.wait_for_function('document.querySelectorAll("#modList > .mod-row").length===9')
        for name in ('locale-catalogs.js','preferences.js'):
            page.add_script_tag(content=(qa.ROOT/'native/ui'/name).read_text('utf-8'))
        page.evaluate("locale=>ModhunterI18n.apply({locale,theme:'light'})", lang)
        if zoom != 1:
            page.evaluate('factor=>{document.documentElement.style.zoom=factor}', zoom)
        expected = 'clip' if arm == 'candidate' and width >= 761 and colors == 'none' else 'hidden'
        assert_modes(page, 'list', 'hidden')
        page.locator('#libraryGallery').click()
        if density == 'comfy':
            page.locator('#densityToggle').click()
        qa.settle(page)
        assert_modes(page, 'gallery', expected)
        page.locator('#row-item-1 .mod-art').scroll_into_view_if_needed()
        page.locator('#row-item-2 .mod-art').scroll_into_view_if_needed()
        page.wait_for_function("""() => {
          const ok=document.querySelector('#row-item-1 .mod-preview-image'),fail=document.querySelector('#row-item-2 .mod-preview-image');
          return ok?.complete && ok.naturalWidth===96 && !ok.hidden && fail?.hidden;
        }""")
        if width >= 761 and colors == 'none':
            assert page.locator(SELECTOR).evaluate_all("""nodes => nodes.every(n=>
              n.scrollWidth <= n.clientWidth && n.scrollHeight <= n.clientHeight && n.scrollTop===0 && n.scrollLeft===0 &&
              !n.querySelector('a[href],area[href],button,input,select,textarea,[tabindex],[contenteditable]'))"""), (
                'Artwork may require programmatic find/scroll/focus; do not adopt this CSS arm'
            )
        # Exercise renderer find, not browser-chrome Find or Windows magnifier.
        tested_find = {}
        for name in ('ArtClipFindTarget', page.evaluate("ModhunterI18n.translate('No preview supplied')")):
            result = page.evaluate("""text => {
              getSelection().removeAllRanges();
              let found=window.find(text,false,false,true);
              const hit=getSelection()?.anchorNode?.parentElement;
              const result=[found,hit?.closest('.mod-row')?.dataset.id,
                            hit?.closest('.mod-art')?.getAttribute('data-act')];
              getSelection().removeAllRanges();return result;
            }""", name)
            assert result[0] and result[1], f'Renderer search cannot find fixture text: {name}'
            tested_find[name] = result
        tag = f'{width}-{colors}-{density}-{lang}-{zoom}'
        focus = page.locator('#row-item-0 .mod-art')
        focus.scroll_into_view_if_needed()
        focus.focus()
        page.keyboard.press('Shift+Tab')
        page.keyboard.press('Tab')
        assert focus.evaluate('n=>document.activeElement===n && n.matches(":focus-visible")')
        qa.settle(page)
        shots = [screenshot(page, output/f'{arm}-{tag}-keyboard-focus.png')]
        labels = [ax(cdp, f'#row-item-{i} .mod-art') for i in range(3)]
        assert all(not node['ignored'] and node['role']['value']=='button' and node['name']['value'] for node in labels)
        deep = deep_row_geometry(page, cdp)
        for row in deep:
            # Structural/pixel equivalence still checked; only this intentional
            # computed-style difference is represented separately above.
            assert row['computed']['art'].pop('overflow') == expected
        geometry = complete_geometry(page, 9)
        page.keyboard.press('Enter')
        assert page.locator('#detailDrawer').evaluate('el=>el.classList.contains("open")')
        drawer = page.locator('#drawerContent .mod-art')
        assert drawer.evaluate('(n)=>getComputedStyle(n).overflow') == 'hidden'
        qa.settle(page)
        shots.append(screenshot(page, output/f'{arm}-{tag}-inspector.png'))
        page.keyboard.press('Escape')
        assert not page.locator('#detailDrawer').evaluate('el=>el.classList.contains("open")')
        after_escape = page.evaluate("document.activeElement?.getAttribute('data-act')")
        page.emulate_media(media='print')
        assert_modes(page, 'gallery', 'hidden')
        page.emulate_media(media='screen')
        page.locator('#libraryList').click()
        qa.settle(page)
        assert_modes(page, 'list', 'hidden')
        assert not errors, errors
        return {'shots':shots, 'ax':labels, 'deep':deep, 'bounds':geometry,
                'rendererFind':tested_find, 'focusAfterEscape':after_escape}
    finally:
        context.close()


def full_native_shape_trial(browser, html, arm):
    # Chromium fixture uses the same synthetic generator and viewport; this
    # retains all cards and is not the released Windows WebView2 native run.
    context, page, errors = open_arm(browser, html, 1028, None, height=633, motion='no-preference')
    try:
        session = context.new_cdp_session(page)
        session.send('Performance.enable')
        page.evaluate('library=>modhunterImportCatalog(library)', complete_fixture())
        qa.settle(page)
        list_geometry = complete_geometry(page, SIZE)
        metrics = metric_snapshot(session)
        start = perf_counter()
        page.locator('#libraryGallery').click()
        qa.settle(page)
        elapsed = (perf_counter() - start)*1000
        after = metric_snapshot(session)
        assert_modes(page, 'gallery', 'clip' if arm == 'candidate' else 'hidden')
        result = {'wallMs':elapsed, 'metrics':{k:after[k]-v for k,v in metrics.items() if k in after}}
        gallery_geometry = complete_geometry(page, SIZE)
        markup = record_markup_fingerprint(page, SIZE)
        tail = page.locator('#row-item-11000')
        tail.scroll_into_view_if_needed()
        tail.locator('.mod-art').focus()
        page.keyboard.press('Shift+Tab')
        page.keyboard.press('Tab')
        qa.settle(page)
        assert tail.locator('.mod-art').evaluate('n=>document.activeElement===n && n.matches(":focus-visible")')
        scroll_and_focus = tail.evaluate("""row=>{
          const art=row.querySelector('.mod-art'),r=art.getBoundingClientRect(),h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          // Hidden card boxes and outer workbench may programmatically scroll
          // without changing the top-level page offset. Check the whole path.
          const chain=[];
          for(let n=art;n;n=n.parentElement)chain.push([n.id||n.tagName,n.scrollLeft,n.scrollTop,n.clientWidth,n.clientHeight]);
          return {scroll:[scrollX,scrollY],chain,viewport:[innerWidth,innerHeight,devicePixelRatio],
                  rect:r.toJSON(),same:art===h||art.contains(h),focus:document.activeElement?.dataset.id};
        }""")
        assert scroll_and_focus['same'] and scroll_and_focus['focus']=='item-11000'
        assert scroll_and_focus['viewport']==[1028,633,1], 'Browser trial viewport does not match pinned shape'
        assert not errors, errors
        return list_geometry, gallery_geometry, markup, result, scroll_and_focus
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    baseline, candidate, assets = exact_arms()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        # One selected supported Chromium, no fallback or retry.
        browser = runtime.chromium.launch(executable_path=str(args.executable), headless=True, chromium_sandbox=True)
        receipt = {'acceptance':False, 'fixtureSha256':FIXTURE_SHA, 'browserVersion':browser.version,
                   'scope':'Chromium diagnostics only, not exact WebView2/MO2, Windows zoom or browser-chrome find',
                   'assets':assets, 'cases':[]}
        cases = ((1440,'none','compact','en',1), (1028,'none','compact','es',1), (1028,'none','comfy','zh-TW',1),
                 (1440,'none','comfy','it',1.5), (761,'none','compact','en',2),
                 (760,'none','compact','es',1), (390,'none','comfy','zh-TW',1), (1440,'active','compact','en',1))
        try:
            base_layout = full_native_shape_trial(browser, baseline, 'baseline')
            new_layout = full_native_shape_trial(browser, candidate, 'candidate')
            assert not compare_geometry(base_layout[0], new_layout[0])['differenceCount']
            assert not compare_geometry(base_layout[1], new_layout[1])['differenceCount']
            assert base_layout[2] == new_layout[2]
            assert base_layout[4] == new_layout[4], 'Deep record scroll/focus/hit target changed'
            receipt['deepRecordScrollAndFocus'] = base_layout[4]
            receipt['isolatedOrdinaryTrials'] = {'baseline':base_layout[3], 'candidate':new_layout[3]}
            for case in cases:
                base = focused_art_test(browser, baseline, 'baseline', args.output, *case)
                new = focused_art_test(browser, candidate, 'candidate', args.output, *case)
                for a,b in zip(base.pop('shots'), new.pop('shots'), strict=True):
                    pixels_match(a,b)
                assert base == new, f'Geometry/layout, AX, renderer find or keyboard mismatch: {case}'
                receipt['cases'].append({'test':case, 'pixelExact':True, 'geometryAndAXExact':True})
        finally:
            browser.close()
            (args.output/'clip-ab.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
