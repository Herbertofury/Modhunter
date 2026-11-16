# Modhold public update metadata

Implementation checkpoint, 2026-11-16. Native Windows acceptance is pending.

The existing Rust source scan recognizes exact `https://modholdmods.com/g/{game}/mods/{id}/{slug}` links and reads the official anonymous `/api/v1/mods/{id}` endpoint. A matching mod ID, canonical page/game identity and main file version are required before the existing Python installed-version comparator can report a candidate. Unknown, stale, conflicting and wrong-game evidence remains unknown/review. This does not establish game/runtime compatibility or permission to overwrite a mod.

Project grouping, the existing fifteen-minute cache, conditional revalidation, response bounds and provider cooldowns apply. No credential, new connection, cookie export or archive download is used. Download capability remains false: Modhold requires a separate player grant for downloads. The site advertises 600 requests/minute; clients must respect HTTP cooldowns, not hammer failed requests.

The canonical cross-app identity and metadata policy lives in [Modding-Knowledge](https://github.com/Herbertofury/Modding-Knowledge/blob/integration/modhold-public-contract/config/modhold-source-contract.json). This app does not create a competing shared database. Its native adapter is an application-specific consumer of that source contract. Database ingestion/refresh and other application consumers are not yet connected.

## Evidence and limits

Anonymous live API read of the user's example mod203 returned version1.0.1 and main file759,19,940,724 bytes and a provider-declared SHA256. The [dated factual projection](https://github.com/Herbertofury/Modding-Knowledge/blob/integration/modhold-public-contract/data/modhold-public-2026-11-16.json) retains provenance. No mod archive was downloaded or hash-verified. Timestamp strings lack a timezone, so no UTC release time is invented. Provider SHA256 and scan claims do not imply independently verified file safety.

The API's description was truncated and did not enumerate the page's complete gallery/video set. Full media support is unresolved, not claimed. MO2R's current repository has documentation only; this change does not claim a native MO2R application exists.

Primary sources: [Official API](https://modholdmods.com/developers), [robots policy](https://modholdmods.com/robots.txt), [legal index](https://modholdmods.com/legal), [requested mod](https://modholdmods.com/g/skyrim-se/mods/203/followers-vs-followers). Public API access does not grant a license to redistribute mod code or artwork.
