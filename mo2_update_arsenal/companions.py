"""Local metadata candidates, not claims of extracted playable mod assets."""
import re

_MATCH = re.compile(r'\b(followers?|companions?)\b', re.I)
_SUPPORT = re.compile(r'\b(patch(?:es)?|framework|overhaul|manager|outfits?|replacer|fix(?:es)?|tweaks?)\b', re.I)


def companion_candidates(items):
    results, seen = [], set()
    for item in items:
        mid = str(item.get('id') or '')
        name = str(item.get('name') or '')
        if not mid or mid in seen or item.get('separator') or item.get('installed') is False:
            continue
        categories = item.get('categories') or []
        if not isinstance(categories, list):
            categories = [categories]
        labels = [str(c.get('name', '')) if isinstance(c, dict) else str(c) for c in categories]
        labels += [str(item.get('category') or ''), str(item.get('group') or '')]
        evidence = []
        if _MATCH.search(name):
            evidence.append('name')
        if any(_MATCH.search(label) for label in labels):
            evidence.append('category/group')
        if not evidence:
            continue
        seen.add(mid)
        results.append({'modId': mid, 'name': name, 'evidence': evidence,
                        'needsReview': bool(_SUPPORT.search(name)),
                        'representation': 'metadata-inspired', 'enabled': item.get('enabled')})
    return results
