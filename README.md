# PotholeRadar

**PotholeRadar** proactively scans a geographic area — country, city, and radius — for real potholes using Google Street View imagery and Claude Vision. Unlike [PotholeWatch](https://github.com/arlito331/potholewatch), which reacts to news/social reports of specific incidents, PotholeRadar sweeps an area blind, with no prior report required.

Part of the PowerFix tool family: [PotholeWatch](https://github.com/arlito331/potholewatch) (reactive incident monitor) · [Case Study Generator](https://github.com/arlito331/powerfix-case-study) (ROI PDFs) · **PotholeRadar** (proactive area scanner).

## How it works

1. Resolve a center point either by searching a place name (landmark, neighborhood, address — anything Nominatim can find) on the map in the app, clicking the map directly, or dragging the pin — or, when triggered without lat/lng, by geocoding a `city, country` pair (Google Geocoding, Nominatim fallback).
2. Generate a grid of scan points covering the chosen radius, spaced ~130m apart, capped at `max_points` (default 150, hard ceiling 500).
3. For each point: check Street View **metadata** first (free) to skip points with no panorama coverage, then fetch 5 Street View images (N/E/S/W headings + downward tilt).
4. Send all 5 images to Claude Vision with a strict "true pothole vs. damaged road" prompt — only confirmed potholes (a real hole with exposed base material, not cracks/patches/wear) are recorded. Capped at 20 confirmed findings per scan for now (test-round limit — the scan stops early once it hits this, see `MAX_FINDINGS` in `potholeradar.py`).
5. Write `scans/latest_scan.json` + an archived copy and a manifest entry in `scans/history/`, and try to email an HTML digest of findings if any were confirmed (a failure here — e.g. a bad Gmail OAuth secret — is logged but never blocks the scan results from being saved/committed).
6. `index.html` (GitHub Pages) is the app: a **New Scan** tab (map + place search + adjustable radius slider) that triggers a scan directly from the browser, and a **Coverage Map** tab showing every scanned area across Latin America, with drill-down into each scan's individual findings. Both the "scan complete" state and the detail drill-down offer a **Download PDF Report** button — a client-side jsPDF report (no backend involved) listing every confirmed finding with its Street View image, severity, size, and coordinates.

## Running a scan

The live app at `arlito331.github.io/potholeradar/` is the normal way to trigger a scan:

1. On the **New Scan** tab, click "Set token" once and paste a GitHub personal access token with **`repo`** + **`workflow`** scope (stored only in your browser's localStorage — never sent anywhere except `api.github.com`).
2. Pick a country (used to bias the place search and for record-keeping), then either search a place name, click the map, or drag the pin to set the center point. Adjust the radius slider.
3. Click **"Run Scan →"**. The page calls GitHub's `workflow_dispatch` API directly, then polls the run's status live until it completes, with a link to the full GitHub Actions log the whole time.
4. Once complete, switch to **Coverage Map** to see it plotted, or click "View on Coverage Map" from the success message.

This calls the same GitHub Actions workflow you can also trigger manually from **Actions → PotholeRadar Scan → Run workflow** on GitHub itself, with the same inputs (`country`, `city`, `radius_km`, `max_points`, and optionally `lat`/`lng` if you already know the exact center point) — the app is just a nicer front door to it, not a separate system.

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

Each scanned point costs up to 5 Street View Static image fetches + 1 Claude Vision call (5 images). The free metadata pre-check filters out points with no coverage before any billed image fetch happens. The app's `max_points` dropdown is capped at 20 for this test round (1/5/10/15/20) — the workflow itself still accepts any value up to the hard ceiling of 500 if triggered directly from GitHub Actions.

## Detection tuning notes

Early real scans under-found relative to known ground truth, for two compounding reasons: (1) a blind lat/lng grid wastes a lot of its point budget on locations with no Street View coverage at all (water, buildings, private property) — e.g. only 9/20 points had coverage in one early test — so raise `max_points` generously until road-network snapping (below) exists; (2) the Vision prompt was inherited from PotholeWatch's "confirm one specific reported incident" use case, where erring conservative makes sense, but for a blind proactive sweep that same strictness caused real, partially-obscured potholes to go unconfirmed. The prompt in `identify_pothole_in_images` was rebalanced to still reject clear non-potholes (cracks, wear, patches) but confirm genuine holes even when small, shallow, or partially obscured by water/shadow, rather than only obvious severe cases.

## Roadmap (not built yet)

- Polygon/street drawing on the map instead of a circle (a District/Corregimiento/Barrio administrative-boundary approach was prototyped and shelved in favor of the simpler, universal map+search+radius picker — the real-boundary accuracy is still worth revisiting later)
- OpenStreetMap/Overpass road-network snapping — sample grid points along actual streets instead of a raw lat/lng mesh (the real fix for wasted no-coverage points, biggest accuracy/cost improvement available)
- Waze/Google Places hazard corroboration as a cross-check on already-confirmed findings
- Historical scan comparison/diffing (new pothole vs. previously seen at the same location)
- Scheduled/cron scans (currently manual-trigger only)
- Moving base64 images out of `latest_scan.json` into external storage if scan volume grows
- Raising/removing the 20-finding-per-scan test cap once the pipeline is validated
- Connecting confirmed findings to the Case Study Generator to show PowerFix cost-savings potential per area (Phase 2)
