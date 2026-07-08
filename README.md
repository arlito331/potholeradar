# PotholeRadar

**PotholeRadar** proactively scans a geographic area — country, city, and radius — for real potholes using Google Street View imagery and Claude Vision. Unlike [PotholeWatch](https://github.com/arlito331/potholewatch), which reacts to news/social reports of specific incidents, PotholeRadar sweeps an area blind, with no prior report required.

Part of the PowerFix tool family: [PotholeWatch](https://github.com/arlito331/potholewatch) (reactive incident monitor) · [Case Study Generator](https://github.com/arlito331/powerfix-case-study) (ROI PDFs) · **PotholeRadar** (proactive area scanner).

## How it works

1. Resolve a center point either by searching a place name (landmark, neighborhood, address — anything Nominatim can find) on the map in the app, clicking the map directly, or dragging the pin — or, when triggered without lat/lng, by geocoding a `city, country` pair (Google Geocoding, Nominatim fallback).
2. Generate a grid of scan points covering the chosen radius, spaced ~15m apart (close to the ~10-15m native interval of real Street View panoramas, to leave as few gaps as possible between sampled locations), capped at `max_points` and thinned evenly by distance from center if the raw grid exceeds it.
3. For each point: check Street View **metadata** first (free) to skip points with no panorama coverage, then fetch 9 close-in Street View images — 8 compass headings at pitch -25°/FOV 55°, plus one steeper pitch -40°/FOV 45° "Down" shot (see the tuning notes below for why these specific angles), plus one top-down satellite/aerial image from the Static Maps API as supplementary context (`fetch_top_down_image` — resolution is often too coarse to resolve a pothole on its own, so the prompt tells the model not to rely on it alone).
4. Send all 10 images to Claude Vision in a **two-step judgment**: step 1 classifies what's actually present (`feature_type` — `asphalt_pothole`, `drainage_infrastructure`, `patch_repair`, `crack_only`, `normal_wear`, `water_no_visible_hole`, `unclear_artifact`, or `other_no_damage`), and only `feature_type == "asphalt_pothole"` can go on to be confirmed in step 2. This is enforced at the code level too — `pothole_confirmed` is ANDed with `feature_type == "asphalt_pothole"` in `identify_pothole_in_images`, not just trusted from the model's own flag — so a categorical misclassification can't slip through even if the model's boolean disagrees with its own typing. Confirmed findings also need to clear a confidence floor (`MIN_CONFIDENCE`). Capped at 20 confirmed findings per scan for now (test-round limit — the scan stops early once it hits this, see `MAX_FINDINGS` in `potholeradar.py`).
4b. **Two output lists, not one.** The strict pass above (`findings`) is prone to both false positives and false negatives on its own — it's a single one-shot verdict trying to be both high-precision and high-recall at once. So every point that isn't plain clean pavement (`feature_type` not `normal_wear`/`other_no_damage` — this includes drains, patches, cracks, unclear artifacts, and even asphalt-pothole-typed points that didn't clear the confidence floor) also gets recorded in a separate `candidates` list, with no extra API calls since it reuses the same classification already computed. `findings` stays the trustworthy "confirmed" list; `candidates` is a deliberately wide net meant for a human to skim and triage — the app shows both, candidates in a visually distinct muted-orange section/marker.
5. Write `scans/latest_scan.json` + an archived copy and a manifest entry in `scans/history/`, and try to email an HTML digest of findings if any were confirmed (a failure here — e.g. a bad Gmail OAuth secret — is logged but never blocks the scan results from being saved/committed).
6. `index.html` (GitHub Pages) is the app: a **New Scan** tab (map + place search + adjustable radius slider, or a **Street** mode — see below) that triggers a scan directly from the browser, and a **Coverage Map** tab showing every scanned area across Latin America, with drill-down into each scan's individual findings. Both the "scan complete" state and the detail drill-down offer a **Download PDF Report** button — a client-side jsPDF report (no backend involved) listing every confirmed finding with its Street View image, severity, size, and coordinates.
7. **Street mode**: instead of a radius, pan/zoom the map and click an actual road — it's fetched from OpenStreetMap's Overpass API client-side, rendered as a clickable line, and the scan then samples points along that exact road's geometry (`sample_along_path` in `potholeradar.py`) instead of tiling a circle. Coverage Map renders these as a line instead of a circle.

## Running a scan

The live app at `arlito331.github.io/potholeradar/` is the normal way to trigger a scan:

1. On the **New Scan** tab, click "Set token" once and paste a GitHub personal access token with **`repo`** + **`workflow`** scope (stored only in your browser's localStorage — never sent anywhere except `api.github.com`).
2. Pick a country (used to bias the place search and for record-keeping), then either search a place name, paste exact coordinates (decimal `8.401417, -80.270611` or DMS `8°24'05.1"N 80°16'14.2"W` as copied straight from Google Maps), click the map, or drag the pin to set the center point. Adjust the radius slider. Place-name search can resolve to a large district's general center rather than a specific spot — if you have exact coordinates for a known location, use those instead of a place name for real precision.
3. Click **"Run Scan →"**. The page calls GitHub's `workflow_dispatch` API directly, then polls the run's status live until it completes, with a link to the full GitHub Actions log the whole time.
4. Once complete, switch to **Coverage Map** to see it plotted, or click "View on Coverage Map" from the success message.

This calls the same GitHub Actions workflow you can also trigger manually from **Actions → PotholeRadar Scan → Run workflow** on GitHub itself, with the same inputs (`country`, `city`, `radius_km`, `max_points`, optionally `lat`/`lng` if you already know the exact center point, and optionally `street_path` — a JSON array of `[lat,lng]` pairs — for a street-mode scan) — the app is just a nicer front door to it, not a separate system.

To run locally instead:

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export GOOGLE_MAPS_API_KEY=...
export GMAIL_CLIENT_ID=...
export GMAIL_CLIENT_SECRET=...
export GMAIL_REFRESH_TOKEN=...
python potholeradar.py --country Panama --city "Panama City" --radius-km 2 --max-points 100
# or, with an already-resolved center point:
python potholeradar.py --country Panama --city "El Cangrejo" --lat 8.9917 --lng -79.5297 --radius-km 1 --max-points 50
```

## Required secrets (GitHub repo settings → Secrets and variables → Actions)

- `ANTHROPIC_API_KEY`
- `GOOGLE_MAPS_API_KEY` — needs Geocoding API, Street View Static API, and Street View metadata enabled
- `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` — OAuth for sending the email digest (see PotholeWatch's `get_token.py` / `secrets/get_gmail_token.py` for how to generate these; PotholeRadar needs its own copies, independent of PotholeWatch's)

These are separate from PotholeWatch's secrets — set them up fresh on this repo even if reusing the same underlying Google/Anthropic accounts.

## Cost notes

Each scanned point costs up to 9 Street View Static image fetches + 1 Static Maps satellite fetch + 1 Claude Vision call (10 images). The free metadata pre-check filters out points with no coverage before any billed image fetch happens. The app's `max_points` dropdown offers 1/5/10/15/20/30/50/75/100 (default 50) — the workflow itself still accepts any value up to the hard ceiling of 500 if triggered directly from GitHub Actions. Confirmed findings are separately capped at 20 per scan (`MAX_FINDINGS`) regardless of how many points are scanned.

## Detection tuning notes

This has gone through a few rounds of real-scan-driven correction, in both directions:

1. **Under-finding.** A blind lat/lng grid wastes a lot of its point budget on locations with no Street View coverage at all (water, buildings, private property) — e.g. only 9/20 points had coverage in one early test. Given the `max_points` dropdown is capped at 20 for this test round, a wide radius spreads those 20 points too thin to reliably land on a bad spot — going narrow (small radius directly on a known/suspected street) concentrates them far more effectively than going wide. Road-network snapping (below) is the real long-term fix.
2. **Over-strict, then over-loose.** The Vision prompt was inherited from PotholeWatch's "confirm one specific reported incident" framing (appropriately strict there), which initially caused real partial potholes to go unconfirmed here. Loosening it overcorrected — it briefly told the model that standing water alone was "strong evidence," which produced confirmed "potholes" that were just puddles inferred from indirect language ("indicates," "suggesting"). That line was removed; water without a directly visible hole is now explicitly listed as NOT sufficient. Two more specific false-positive traps (depressions right next to drainage openings, which are often intentional; and "separate" holes inferred next to a patch repair, which are often just the patch's own uneven edges) are now explicitly called out.
3. **Confidence floor as a backstop.** Since prompt wording alone has proven fragile in both directions, `identify_pothole_in_images` now also gets a code-level floor: `MIN_CONFIDENCE` in `potholeradar.py` — a `pothole_confirmed: true` result is only counted as a real finding if the model's own `confidence_visual` clears that bar. Originally set to 70 after false positives clustered at 62-65%, then lowered to 55 once it became clear real confirmed potholes were also being rejected by that bar — 70 had only ever been checked against the false-positive side. Tune this constant directly if real examples show it's miscalibrated either way.
4. **Overcorrecting the camera angle broke it the other way.** After the wide-shot fix (above), a follow-up version pushed to pitch -50/-85 with a narrow 40 FOV — and a real street-mode scan came back with 0 findings on a street with known real defects. Decoding the scan's stored debug images showed why: the near-nadir angle (very close to/beneath the capture rig) is a known Street View stitching artifact zone with no real camera data, rendered as a flat, textureless gray-brown blur. The model consistently misread that blur as "murky water" or a "camera malfunction" across multiple independent points on the same street — the images were unusable noise, not evidence the pavement was clear. Angles are now pitch -25/fov 55 (8 headings) + one pitch -40/fov 45 "Down" shot — close enough to judge pavement, shallow enough to stay out of the corrupted zone — and the prompt explicitly tells the model to disregard any image that looks like this artifact rather than guess at what it might show.
5. **The water-pooling rule was rejecting real potholes, not just puddles.** A user cross-checking the app against manually browsing Street View in Google Earth found a clearly damaged patch of road (large pool covering broken pavement, visible edges around the waterline) that a scan hadn't confirmed. The original wording only mentioned that pure puddles-with-no-visible-hole should be rejected, but in practice the model was treating *any* water as reason to hold back even when edges/depth were visible around it. The prompt now explicitly says partial visibility due to water/glare/shadow is not disqualifying as long as the visible portion clearly shows a genuine hole — still rejecting pure puddles, but no longer penalizing real damage just because it's sitting in a puddle (common right after rain).
6. **A single ground-level snapshot per point misses things a human scrolling through Street View catches by adjusting angle/zoom.** There's no fix for that gap without an interactive/multi-frame capture approach (not built), but as a cheap supplementary signal each point now also gets one top-down satellite image (`fetch_top_down_image`, Static Maps API, `scale=2` for higher pixel density) alongside the 9 Street View angles — corroborating context only, since satellite resolution is usually too coarse to resolve a pothole on its own, and the prompt says so explicitly.
7. **A checklist of exceptions doesn't converge — it needs one more exception every time a new failure mode shows up.** The drainage-exclusion rule above (item 2) only covered damage *next to* a drain, not the drain opening itself; a real scan on Calle 57 Este confirmed a curb drain with a missing cover as a "pothole" because a missing cover technically satisfies "hole with real depth and exposed material" — the general pothole criteria — without anything forcing the model to first ask *what kind of thing is this*. Rather than add yet another bullet to the exclusion list, `identify_pothole_in_images` now runs a genuine two-step judgment (see item 4 above): classify the feature type first, then only assess severity/confirm for things typed as `asphalt_pothole`. This is a structural change, not a wording patch — it should generalize to future categories (manhole covers, speed bumps, tar seams) without needing a new bullet point every time one shows up.
8. **A single strict pass can't be both high-precision and high-recall.** Even after the fix above, re-testing two more user-identified real potholes on the same street (Calle 57 Este) came back misclassified as `drainage_infrastructure` both times — the street genuinely has a prominent drainage channel running along it, and the model appears to anchor on that dominant feature rather than separately scanning the rest of the frame for a distinct pothole nearby. Rather than keep patching the strict prompt to try to nail both precision and recall in one verdict, the pipeline now also keeps a `candidates` list (item 4b above) of everything that isn't perfectly clean pavement, confirmed or not — a deliberately loose net a human can skim, on the theory that "flag everything imperfect, filter later" is more robust than "make one model call get it exactly right every time."

If you have confirmed real-world examples (or clear negatives) from manually browsing Street View, sharing the exact images/descriptions is far more useful for calibrating this than further guessing at prompt wording. The `debug_points` array (every scanned point, not just confirmed findings) is the fastest way to check what the model actually saw when a scan comes back empty — decode `debug_image_b64` for a point and look at it directly before assuming the pavement was clear.

## Regression testing

`regression_test.py` re-runs `identify_pothole_in_images` against a small, hand-verified list of real coordinates (`CASES` in that file) with known-correct `feature_type`/`pothole_confirmed` outcomes — a confirmed false positive, a known real pothole the automated pipeline hasn't caught yet, etc. Run it via **Actions → PotholeRadar Detection Regression Test → Run workflow**, or locally with the same env vars as a normal scan (`python regression_test.py`). It exits non-zero if anything regresses.

This only has two cases right now — it's meant to grow every time a real false positive or false negative gets confirmed, so future prompt/logic changes get checked against actual past failures automatically instead of being validated against whatever single example just broke and then never checked again.

## Roadmap (not built yet)

- Polygon drawing on the map instead of a circle (a District/Corregimiento/Barrio administrative-boundary approach was prototyped and shelved in favor of the simpler, universal map+search+radius picker — the real-boundary accuracy is still worth revisiting later; street-mode above covers the line-shaped case)
- Waze/Google Places hazard corroboration as a cross-check on already-confirmed findings
- Historical scan comparison/diffing (new pothole vs. previously seen at the same location)
- Scheduled/cron scans (currently manual-trigger only)
- Moving base64 images out of `latest_scan.json` into external storage if scan volume grows
- Raising/removing the 20-finding-per-scan test cap once the pipeline is validated
- Connecting confirmed findings to the Case Study Generator to show PowerFix cost-savings potential per area (Phase 2)
