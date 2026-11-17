# Siege enemy artwork

Requested: use a mod's own artwork or its creator's profile art inside enemy silhouettes, with user choice and original fallback.

Implemented in the existing Canvas renderer (no new renderer, dependency, copied art or engine rewrite). The MO2 bridge preserves supplied artwork references from item/update/source metadata. It never guesses an author identity or scrapes a profile. Modes: original/offline (default), mod art, creator art, mixed art. Choice is saved in existing per-profile Siege state. Existing saved enemies without references remain playable.

Images are cover-cropped and clipped to the existing enemy polygon. Outlines, eyes, attack warnings, crowns and health remain visible. Selection is stable for an enemy, not randomized every frame. Up to 128 images are cached as aspect-preserving 128-pixel bitmaps (about 8 MiB maximum decoded thumbnails); additional images use original silhouettes until closing/reopening resets the session budget. Loads are only for visible enemies; closing Siege releases the cache. Invalid/missing/loading/failed/CORS-denied images keep the original enemy. This is playful decoration, not a factual depiction or judgment of an author.

Privacy: only HTTPS images from the same known provider CDN set as the library (Nexus, Modrinth, Thunderstore); no arbitrary URLs, credentials or custom ports. Explicit opt-in discloses remote loading; anonymous CORS, omitted credentials and no-referrer are applied on fetch; redirects are rejected before following. Only PNG, JPEG and static WebP encodings with inspected headers are accepted; APNG/animated WebP and unsupported formats fall back. Downloads stop above 8 MiB; headers must declare at most 8192 pixels per side and 4 megapixels before image decode. Exact existing CDN origins are added to bridge/native connect-src, without widening image sources; other CSP directives remain unchanged. No assets are bundled or redistributed. Creator discovery, arbitrary user uploads and a per-mod image picker remain future work, not silently claimed complete.

## Verification

Unit coverage: URL and metadata validation, deduplication, missing author, modes, stable choices, portrait/landscape cover geometry, clip ordering, one-load cache and cleanup. Existing companion/source tests load the actual artwork module. Generated HTML rebuilt from sources.

Native Windows/WebView2 visual quality and real provider availability still require live acceptance. No claim that cloud unit tests establish native rendering or FPS. No new network work when mode is off; Validated choices are cached by immutable artwork array and mode, avoiding per-frame URL parsing. Cache prevents repeat image construction on frame path; no unmeasured speedup claimed.

Independent review caught and repaired redirect following, both CSP paths, and malformed saved options. Browser smoke attempted with installed Chromium but failed at crashpad startup; native visual acceptance remains open. Aggregate: 128 JS pass; 496 Python tests, 29 skips (467 pass). Only known provider CDN metadata is accepted; encoded byte and declared-dimension budgets fail closed.
