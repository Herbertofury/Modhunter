"""Validate the four owned UI catalogs and create one deterministic JS bundle."""
from __future__ import annotations
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ("en", "es", "it", "zh-TW")
PREFIX = "window.ModhunterLocaleCatalogs = "

def build() -> str:
    result = {locale: {} for locale in LOCALES}
    for path in sorted((ROOT / "native/ui/locales").glob("*.json")):
        catalog = json.loads(path.read_text(encoding="utf-8"))
        if set(catalog) != set(LOCALES):
            raise ValueError(f"{path.name}: all four locales are required")
        keys = set(catalog["en"])
        for locale in LOCALES:
            if set(catalog[locale]) != keys:
                raise ValueError(f"{path.name}: incomplete {locale} catalog")
            for key, value in catalog[locale].items():
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{path.name}: empty {locale} text for {key!r}")
                if sorted(re.findall(r"\{[A-Za-z][A-Za-z0-9_]*\}",key)) != sorted(re.findall(r"\{[A-Za-z][A-Za-z0-9_]*\}",value)):
                    raise ValueError(f"{path.name}: changed {locale} placeholders for {key!r}")
                if key in result[locale] and result[locale][key] != value:
                    raise ValueError(f"{path.name}: conflicting translation for {key!r}")
                result[locale][key] = value
    return PREFIX + json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + ";\n"

if __name__ == "__main__":
    output = ROOT / "native/ui/locale-catalogs.js"
    # Keep the tracked generated bundle byte-identical on Windows and Unix.
    output.write_bytes(build().encode("utf-8"))
    print(json.dumps({"localeCatalogs":len(LOCALES),"keys":len(json.loads(output.read_text("utf-8")[len(PREFIX):-2])["en"])}))
