# PotholeRadar

**PotholeRadar** proactively scans a geographic area — country, city, and radius — for real potholes using Google Street View imagery and Claude Vision. Unlike [PotholeWatch](https://github.com/arlito331/potholewatch), which reacts to news/social reports of specific incidents, PotholeRadar sweeps an area blind, with no prior report required.

Part of the PowerFix tool family: [PotholeWatch](https://github.com/arlito331/potholewatch) (reactive incident monitor) · [Case Study Generator](https://github.com/arlito331/powerfix-case-study) (ROI PDFs) · **PotholeRadar** (proactive area scanner).

## How it works

1. Geocode the given `city, country` to a center lat/lng (Google Geocoding, Nominatim fallback).
2. Generate a grid of scan points covering the given radius, spaced ~130m apart, capped at `max_points` (default 150, hard ceiling 500).
3. For each point: check Street View **metadata** first (free) to skip points with no panorama coverage, then fetch 5 Street View images (N/E/S/W headings + downward tilt).
4. Send all 5 images to Claude Vision with a strict "true pothole vs. damaged road" prompt — only confirmed potholes (a real hole with exposed base material, not cracks/patches/wear) are recorded.
5. Write `scans/latest_scan.json` (+ an archived copy in `scans/history/`), and email an HTML digest of findings if any were confirmed.
6. `index.html` (GitHub Pages) is a Leaflet dashboard that reads `scans/latest_scan.json` and plots findings on a map.

## Running a scan

Scans are triggered manually via GitHub Actions — go to **Actions → PotholeRadar Scan → Run workflow** and fill in:

- `country` — e.g. `Panama`
- `city` — e.g. `Panama City`
- `radius_km` — scan radius around the city center
- `max_points` — safety cap on grid points scanned (keeps cost/time bounded; hard ceiling is 500 regardless of what's entered)

To run locally instead:

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export GOOGLE_MAPS_API_KEY=...
export GMAIL_CLIENT_ID=...
export GMAIL_CLIENT_SECRET=...
export GMAIL_REFRESH_TOKEN=...
python potholeradar.py --country Panama --city "Panama City" --radius-km 2 --max-points 100
```

## Required secrets (GitHub repo settings → Secrets and variables → Actions)

- `ANTHROPIC_API_KEY`
- `GOOGLE_MAPS_API_KEY` — needs Geocoding API, Street View Static API, and Street View metadata enabled
- `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` — OAuth for sending the email digest (see PotholeWatch's `get_token.py` / `secrets/get_gmail_token.py` for how to generate these; PotholeRadar needs its own copies, independent of PotholeWatch's)

These are separate from PotholeWatch's secrets — set them up fresh on this repo even if reusing the same underlying Google/Anthropic accounts.

## Cost notes

Each scanned point costs up to 5 Street View Static image fetches + 1 Claude Vision call (5 images). The free metadata pre-check filters out points with no coverage before any billed image fetch happens. Start with a small `radius_km` and `max_points` (e.g. 1km / 50 points) to gauge cost before running a full city sweep.

## Roadmap (not built yet)

- Polygon-drawn scan areas instead of circle-by-radius
- OpenStreetMap/Overpass road-network snapping — sample grid points along actual streets instead of a raw lat/lng mesh (biggest accuracy/cost improvement available)
- Waze/Google Places hazard corroboration as a cross-check on already-confirmed findings
- Historical scan comparison/diffing (new pothole vs. previously seen at the same location)
- Scheduled/cron scans (currently manual-trigger only)
- Moving base64 images out of `latest_scan.json` into external storage if scan volume grows
- Connecting confirmed findings to the Case Study Generator to show PowerFix cost-savings potential per area (Phase 2)
