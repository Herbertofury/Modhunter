# Design Quality Standard

This standard applies to user-facing UI changes. It operationalizes product invariants 1, 6, 10, 14, 17 and 18; it does not mandate a framework, aesthetic, or chat skill.

## Establish the design target

Use the user's references, audience, tasks, content density, platform and existing design system to choose a coherent direction. Record the relevant comparison and acceptance criteria before substantial redesign. Preserve brand and customization. A generic dashboard, gradients, animations or a popular component kit are not evidence of good design.

Use deliberate hierarchy, type scale, spacing, alignment, color and interaction states. Reuse accessible primitives and the project's tokens when they fit. For greenfield work, compare suitable current approaches against the actual requirements rather than selecting a fashionable stack by default.

## Inspect and exercise the result

- Inspect the real affected screens at representative narrow and wide viewports, including zoom/reflow, long content, and relevant themes.
- Check loading, empty, error, success, disabled and focus states when applicable. Every affected control must reach its promised result; preserve truthful feedback and state.
- Check keyboard navigation, visible focus, labels/semantics, contrast and reduced-motion behavior. WCAG 2.2 AA is the web acceptance reference unless the project specifies a stronger target; do not claim complete conformance from screenshots alone. See [W3C's criterion reference](https://www.w3.org/WAI/WCAG22/quickref/).
- Review real screenshots against the design brief, content and credible reference. Functional assertions and visual judgment complement each other. A screenshot diff cannot establish that the original design is good.
- When visual regressions are plausible, use controlled screenshot baselines: consistent browser/OS/fonts/viewport/data, narrowly justified masks, and reviewed baseline changes. [Playwright documents environment sensitivity and snapshot review](https://playwright.dev/docs/test-snapshots).
- Protect responsiveness, stability and equivalent-work performance. [Web Vitals distinguishes field measurements from lab regression evidence](https://web.dev/articles/vitals); a lab score alone does not establish field performance or interaction quality.

## Evidence-bound acceptance

Record the inspected screens/states, exercised paths, environment, comparison and remaining defects. Scope verification to the changed or causally affected surface. If runtime inspection is blocked, preserve that evidence gap and its recovery action; do not claim visual approval or flawless design.

The Project Visual QA Showcase chat skill can help a selected chat workflow. Coding agents can perform these checks with their available native tools without activating it.
