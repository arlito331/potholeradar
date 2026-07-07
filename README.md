# PotholeRadar

**PotholeRadar** proactively scans a geographic area — country, city, and radius — for real potholes using Google Street View imagery and Claude Vision. Unlike [PotholeWatch](https://github.com/arlito331/potholewatch), which reacts to news/social reports of specific incidents, PotholeRadar sweeps an area blind, with no prior report required.

Part of the PowerFix tool family: [PotholeWatch](https://github.com/arlito331/potholewatch) (reactive incident monitor) · [Case Study Generator](https://github.com/arlito331/powerfix-case-study) (ROI PDFs) · **PotholeRadar** (proactive area scanner).

## How it works

1. Resolve a center point either by searching a place name (landmark, neighborhood, address — anything Nominatim can find) on the map in the app, clicking the map directly, or dragging the pin — or, when triggered without lat/lng, by geocoding a `city, country` pair (Google Geocoding, Nominatim fallback).
2. Generate a grid of scan points covering the chosen radius, spaced ~25m apart (real Street View panoramas run every ~10-15m along a road — wider spacing risked missing the exact spot on a small targeted scan entirely), capped at `max_points` and thinned evenly by distance from center if the raw grid exceeds it.
3. For each point: check Street View **metadata** first (free) to skip points with no panorama coverage, then fetch 9 close-in Street View images — 8 compass headings at a narrow 40° FOV and steep -50° pitch, plus one very steep (-85°) straight-down shot. Deliberately no wide/shallow shots: a real false positive traced back to the model "confirming" a pothole from a barely-resolvable dark smudge far in the distance in a wide 90° FOV shot — every shot is now framed like a human tilting down and zooming in on the pavement itself, not a wide establishing view.
4. Send all 9 images to Claude Vision with a strict "true pothole vs. damaged road" prompt — only confirmed potholes (a real hole with exposed base material, not cracks/patches/wear/drainage features/patch edges) at or above a confidence floor (`MIN_CONFIDENCE`) are recorded. Capped at 20 confirmed findings per scan for now (test-round limit — the scan stops early once it hits this, see `MAX_FINDINGS` in `potholeradar.py`).
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

Each scanned point costs up to 9 Street View Static image fetches + 1 Claude Vision call (9 images). The free metadata pre-check filters out points with no coverage before any billed image fetch happens. The app's `max_points` dropdown offers 1/5/10/15/20/30/50/75/100 — the workflow itself still accepts any value up to the hard ceiling of 500 if triggered directly from GitHub Actions. Confirmed findings are separately capped at 20 per scan (`MAX_FINDINGS`) regardless of how many points are scanned.

## Detection tuning notes

This has gone through a few rounds of real-scan-driven correction, in both directions:

1. **Under-finding.** A blind lat/lng grid wastes a lot of its point budget on locations with no Street View coverage at all (water, buildings, private property) — e.g. only 9/20 points had coverage in one early test. Given the `max_points` dropdown is capped at 20 for this test round, a wide radius spreads those 20 points too thin to reliably land on a bad spot — going narrow (small radius directly on a known/suspected street) concentrates them far more effectively than going wide. Road-network snapping (below) is the real long-term fix.
2. **Over-strict, then over-loose.** The Vision prompt was inherited from PotholeWatch's "confirm one specific reported incident" framing (appropriately strict there), which initially caused real partial potholes to go unconfirmed here. Loosening it overcorrected — it briefly told the model that standing water alone was "strong evidence," which produced confirmed "potholes" that were just puddles inferred from indirect language ("indicates," "suggesting"). That line was removed; water without a directly visible hole is now explicitly listed as NOT sufficient. Two more specific false-positive traps (depressions right next to drainage openings, which are often intentional; and "separate" holes inferred next to a patch repair, which are often just the patch's own uneven edges) are now explicitly called out.
3. **Confidence floor as a backstop.** Since prompt wording alone has proven fragile in both directions, `identify_pothole_in_images` now also gets a code-level floor: `MIN_CONFIDENCE = 70` in `potholeradar.py` — a `pothole_confirmed: true` result is only counted as a real finding if the model's own `confidence_visual` clears that bar, since observed false positives clustered at 62-65%. Tune this constant directly if real examples show it's miscalibrated either way.
4. **Overcorrecting the camera angle broke it the other way.** After the wide-shot fix (above), a follow-up version pushed to pitch -50/-85 with a narrow 40 FOV — and a real street-mode scan came back with 0 findings on a street with known real defects. Decoding the scan's stored debug images showed why: the near-nadir angle (very close to/beneath the capture rig) is a known Street View stitching artifact zone with no real camera data, rendered as a flat, textureless gray-brown blur. The model consistently misread that blur as "murky water" or a "camera malfunction" across multiple independent points on the same street — the images were unusable noise, not evidence the pavement was clear. Angles are now pitch -25/fov 55 (8 headings) + one pitch -40/fov 45 "Down" shot — close enough to judge pavement, shallow enough to stay out of the corrupted zone — and the prompt explicitly tells the model to disregard any image that looks like this artifact rather than guess at what it might show.

If you have confirmed real-world examples (or clear negatives) from manually browsing Street View, sharing the exact images/descriptions is far more useful for calibrating this than further guessing at prompt wording. The `debug_points` array (every scanned point, not just confirmed findings) is the fastest way to check what the model actually saw when a scan comes back empty — decode `debug_image_b64` for a point and look at it directly before assuming the pavement was clear.

## Roadmap (not built yet)

- Polygon drawing on the map instead of a circle (a District/Corregimiento/Barrio administrative-boundary approach was prototyped and shelved in favor of the simpler, universal map+search+radius picker — the real-boundary accuracy is still worth revisiting later; street-mode above covers the line-shaped case)
- Waze/Google Places hazard corroboration as a cross-check on already-confirmed findings
- Historical scan comparison/diffing (new pothole vs. previously seen at the same location)
- Scheduled/cron scans (currently manual-trigger only)
- Moving base64 images out of `latest_scan.json` into external storage if scan volume grows
- Raising/removing the 20-finding-per-scan test cap once the pipeline is validated
- Connecting confirmed findings to the Case Study Generator to show PowerFix cost-savings potential per area (Phase 2)
