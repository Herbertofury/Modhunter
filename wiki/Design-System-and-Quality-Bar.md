# Modhunter / Design & Quality Standard

[Home](Home) · [Unified Command Center](Unified-Modhunter) · [Update Workshop](Update-Workshop)

> **North star:** A premium, handcrafted mod-management tool with a coherent game world around it. **Utility first. Beauty second. Delight everywhere. Slop nowhere.**

This is an **active product contract**, not permission to strip features or a claim of native-Windows certification. Every visual change must meet it before promotion.

## 01 / Product hierarchy

| Priority | Surface | What the user should learn or do |
| --- | --- | --- |
| 1 | Current MO2 profile, runtime and connection | Which installation the user is acting on, and whether the data is live |
| 2 | Update Workshop | What actually needs review, what is current, what is unknown, and why |
| 3 | Source / exact file selection and approved download queue | Choose a compatible file and dispatch only with explicit confirmation |
| 4 | Install, backup, dependency and conflict review | Use MO2's native workflows, keep control of FOMOD and load-order changes |
| 5 | Questbound, AetherFX, Shiverfell, Mythforge, Siege and Atlas | Celebrate real upkeep without obstructing it |

**Do not market “update every mod” as a guarantee.** Some providers have no public verification interface; their rows must remain visible as *manual review* or *unverified*. A release candidate must never be called installation-approved just because a version is newer.

## 02 / Art direction

**One visual language, three entry points.** The portable HTML and MO2-hosted page share the same compiled CSS/HTML/JS; the Electron desktop chrome uses the matching palette, type scale and interaction semantics.

- **Base:** warm obsidian, mineral slate, oxidized green. Warm brass is reserved for primary action or earned reward, not every decorative stroke.
- **Status colors:** verdigris for connected/current, amber for review, coral for errors. Color is always paired with a text label.
- **Typography:** system UI font for data and controls, a restrained high-contrast serif for product/editorial headings. No remotely hosted fonts required.
- **Surfaces:** hairline dividers, restrained shadows, deliberate elevation, consistent 6–12 px radii. Avoid stacking glowing gradients or oversized glass effects.
- **Rhythm:** an 8 px base spacing system; 12–16 px row rhythm, 20–28 px section rhythm; readable column widths and clear alignment.
- **Motion:** ≤180 ms for routine hover/focus; celebratory effects only after a meaningful action. Respect reduced-motion settings and retain the adjustable FX Studio.
- **Copy:** short, verifiable, action-led. “3 updates to review” beats fabricated “all mods secured” marketing.
- **Iconography:** each symbol has a consistent job; no random emoji decoration in dense operations. Fantasy accents belong to the game experience, not every file picker.

The visual system lives in [\`mo2_update_arsenal/web/atelier.css\`](https://github.com/Herbertofury/Modhunter/blob/main/mo2_update_arsenal/web/atelier.css). Desktop chrome is [\`desktop/chrome.css\`](https://github.com/Herbertofury/Modhunter/blob/main/desktop/chrome.css). **Both light and dark themes** require equivalent contrast and affordances.

## 03 / Component acceptance

**Dashboard / navigation:** obvious profile badge, no competing first-class CTAs, stable sidebar, keyboard skip link, source state visible. Avoid duplicate action names and nested interactive controls.

**Update Workshop:** visible *full list*, no viewport virtualization or arbitrary record limits, legible status in one column, exact versions and risk reasons, meaningful empty states, consistent actions. A reviewed item must survive scrolling; profile changes invalidate old choices.

**Mod rows:** name and installed version take priority; source links are honest; focus, keyboard navigation and notes remain available; no “update” label for unresolved sources.

**File picker:** show exact archive name/version/provider and cautionary description. Explicit review before queueing, native MO2 install after download, backup before install. Never silently select an archive.

**Games / rewards:** each mode retains its identity, persistent progression and original controls. Earned awards use real MO2 evidence; offline play is unambiguously marked practice. Spectacle never blocks the maintainer workflow.

**Desktop tabs:** readable full titles where available, horizontally scrollable tabs, separate keyboard-accessible Open and Close controls, clear connected/offline status, preserved independent local import.

## 04 / Verification gates

- [ ] All Python tests pass, including update-plan validation, profile isolation, provider fallbacks and UI contract checks.
- [ ] JavaScript and Electron syntax pass, and the single-file HTML builds with no unresolved placeholders.
- [ ] One compiled dashboard stays byte-identical inside portable HTML, desktop and MO2 package.
- [ ] Desktop (≥1280 px), mid-size (~900 px), and phone (~390 px) pass a visual check: no overlaps, clipped labels, hidden actions or viewport item caps.
- [ ] Light and dark themes, keyboard focus, hover/disabled states, normal and reduced motion behave meaningfully.
- [ ] No browser errors in dashboard, Workshop, Shiverfell or Siege navigation.
- [ ] Source and risk status are conveyed in text, not color alone.
- [ ] A real MO2 2.5.x Windows instance verifies scan, file choice, queue, standard installation, restore/backup and profile transitions before claiming native compatibility.
- [ ] Wiki and README document observed behavior, caveats and versioned screenshots rather than aspirational placeholders.
- [ ] The canonical GitHub build and live GitHub Wiki both publish from the same source checkpoint.

## 05 / Never regress

No cropped/mod-count-limited lists, no virtual scrolling to manufacture speed, no silent install, no fabricated proof/rewards, no removed game modes, no dependency between the three independently runnable entry points. Avoid automatic background watchers unless explicitly selected. Never replace working UI with a static mockup.

## 06 / Current implementation checkpoint

The **Atelier** layer unifies panel spacing, restrained surface treatment, updater-first hierarchy, Workshop rows, light-theme parity, responsive behavior and accessible focus. Electron's tab controls are separate clickable buttons rather than a click target nested inside another control. The existing update pipeline, game engine and profile authorization remain authoritative.

**Remaining runtime acceptance:** native Windows MO2 and Electron QA with actual installed mod profiles, accessibility audit from real screenshots, and multi-provider authenticated testing. Source/CI success alone is not those proofs.

[Implementation in GitHub](https://github.com/Herbertofury/Modhunter) · [Release and tests](https://github.com/Herbertofury/Modhunter/actions/workflows/build-workshop.yml) · [Troubleshooting](Troubleshooting)
