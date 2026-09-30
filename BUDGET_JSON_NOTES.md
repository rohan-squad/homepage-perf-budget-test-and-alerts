# budget.json — what it gates, and what it can't

**Companion to:** the four budget files in `.github/`
**Source of truth:** the budget doc, Part 2.2 (lab milestones) and Part 3 (quantity)
**Decision:** CI **fails on red**, **warns on amber**. Field/CrUX is never in these files — it's the dashboard's job.

---

## The four files

| File | Threshold line | Effect in CI |
|---|---|---|
| `budget.mobile.json` | **Red** (Part 2.2 mobile + Part 3) | **Fails the build** on breach |
| `budget.desktop.json` | **Red** (Part 2.2 desktop + Part 3) | **Fails the build** on breach |
| `budget.mobile.warn.json` | **Amber** (green-zone ceilings) | **Warns only** — runs in a `continue-on-error` step, never fails the build |
| `budget.desktop.warn.json` | **Amber** | **Warns only** |

Two passes per device in one CI run: the warn file first (soft signal), the red file second (hard gate). Only red can break the build. This is the two-tier behaviour — an early heads-up on drift into amber, a hard stop only on a genuine red breach.

## Why these numbers

All values are the **red thresholds** from the doc, converted to Sitespeed's units (ms for timings, bytes for sizes). The warn files use the **green-zone ceiling** (the top of green / start of amber), so they fire the moment a metric leaves green.

**Timings differ by device** (mobile is a climb, desktop is a hold):

| Metric | Mobile red (fail) | Desktop red (fail) |
|---|---|---|
| LCP | 3000 ms | 2000 ms |
| TBT | 1000 ms | 400 ms |
| CLS | 0.1 | 0.25 |
| TTFB (lab) | 1000 ms | 500 ms |
| FCP | 3000 ms | 3000 ms |

**Quantity is shared** across both devices (same bytes shipped; higher ceiling used), so the `requests`, `transferSize`, and `thirdParty` blocks are identical in both red files.

## The three tiers of enforcement

Sitespeed's `budget.json` is only one layer. Here's the full picture so nobody assumes a green build means every rule passed.

### Tier 1 — `budget.json` (these files) — automatic, native

Sitespeed reads the file and fails its own run on breach. Covers:
- LCP, TBT, CLS, TTFB, FCP (lab timings)
- Total requests + requests by type (JS, CSS, image, font)
- `httpErrors: 0` — catches any 4xx/5xx (this covers **R16**, the dead `gumlytics.com` 410s)
- Total transfer + transfer by type
- Third-party transfer size (**R13** — player JS is inside this)
- SVG per-file size is **not** a native budget field — see Tier 2

### Tier 2 — HAR-inspection script — automatic, custom (built next, after the CI workflow)

A small script parses the HAR that Sitespeed already saves, and exits non-zero on a violation. This is where the rules that need per-request inspection live:
- **R1 (partial)** — hero image `fetchPriority` = high, and the LCP resource is requested early (not injected late). The HAR exposes `_priority` / fetch-priority per request. Can't detect a CSS `background-image` directly, but can flag an LCP resource that lacks high priority.
- **R4** — image formats: flag any `image/png` or `image/jpeg` response that should be WebP/AVIF.
- **R5** — SVG per-file size: flag any `.svg` response over 10 KB (the red line).
- **R7 (partial)** — fonts served first-party (not `fonts.googleapis.com`).
- **R10 / R11** — duplicate media URLs, or simultaneous HLS-manifest + MP4 for one video (the exact video-fetch bug from 15 Sep).

Tier 2 is more code than Tier 1 and each check is validated against a real HAR before it's trusted, so it's built incrementally after the spine (Tier 1 + workflow) is proven.

### Tier 3 — human checklist only — not automatable

These are code patterns or process, invisible to both the budget and the HAR. They stay on the Part 6 / Part 8 checklists:
- **R2** — motion deferred via IntersectionObserver (code pattern)
- **R6** — reserved space for late content (DOM/CSS)
- **R8 / R9** — no media before interaction / facade (behavioural; the no-interaction run is a signal but not proof)
- **R14** — third-party gated on consent/idle (behavioural timing)
- **R15 / R17** — swap rule / quarterly audit (process)

## What a green build guarantees

**A green CI build means the Tier 1 measurable thresholds passed — nothing more.** It does not certify the Tier 3 behavioural rules, and (until Tier 2 ships) does not certify image formats, SVG sizes, or duplicate-media checks. The pre-publish checklist (Part 8) remains the backstop for everything outside Tier 1.

## Per-URL note

These files target the homepage only. When more URLs join the budget, use Sitespeed's per-URL override syntax rather than separate files:

```json
{ "budget": {
    "https://www.squadstack.ai/pricing": { "timings": { "largestContentfulPaint": 2500 } },
    "timings": { "largestContentfulPaint": 3000 }
} }
```

The bare `timings` block is the default; the per-URL block overrides it for that URL.

## Units reference (Sitespeed budget schema)

- `timings.*` — milliseconds (CLS is unitless)
- `transferSize.*` — bytes
- `requests.*` — count
- `thirdParty.transferSize` — bytes
- `requests.httpErrors` — count of 4xx/5xx responses
