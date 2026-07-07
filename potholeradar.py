"""
PotholeRadar v1.0 — Proactive Geographic Pothole Scanner
==========================================================
Given a country + city + radius, geocodes the area, builds a grid of
scan points, checks Street View coverage, fetches Street View imagery
at each covered point, and uses Claude Vision to detect real potholes.

Unlike PotholeWatch (which reacts to news/social reports of specific
incidents), PotholeRadar proactively sweeps an area with no prior
report required.
"""

import os
import re
import json
import math
import time
import base64
import argparse
from datetime import datetime, timezone

import requests
from anthropic import Anthropic
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ============================================================
# CONFIG
# ============================================================

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_MAPS_API_KEY = os.environ["GOOGLE_MAPS_API_KEY"]
GMAIL_CLIENT_ID     = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
GMAIL_SENDER        = os.environ.get("GMAIL_SENDER", "PotholeRadar <ashourilevy@gmail.com>")

ALERT_RECIPIENTS = ["joel@powerfixinc.com", "1@powerfixinc.com"]

CLAUDE_MODEL = "claude-opus-4-5"

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# Brand — blue radar theme (distinct from PotholeWatch's orange)
BG      = "#0D0D0D"
CARD_BG = "#1A1A1A"
TEXT    = "#FFFFFF"
MUTED   = "#999999"
DIM     = "#666666"
ACCENT  = "#3F7FE0"
SOFT    = "#262626"
SEVERITY_COLORS = {
    "critical": "#FF3B3B",
    "severe":   "#FF3B3B",
    "moderate": "#3F7FE0",
    "minor":    "#F0A030",
}

DEFAULT_SPACING_M  = 25  # real Street View panoramas run every ~10-15m along a road;
                          # 130m spacing meant a small targeted scan could easily miss
                          # the exact spot entirely. max_points still caps the total via
                          # thinning, so this just makes small-radius scans much denser.
DEFAULT_MAX_POINTS = 150
HARD_MAX_POINTS    = 500
MAX_FINDINGS       = 20  # test-round cap: stop scanning once this many potholes are confirmed
MIN_CONFIDENCE     = 70  # code-level floor: several real false positives clustered at 62-65%
                          # confidence_visual (inferring from water/drainage/patches rather than
                          # directly seeing a hole) — don't rely solely on the model's own
                          # pothole_confirmed flag, also require it to say it's reasonably sure

SCAN_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scans")
HISTORY_DIR = os.path.join(SCAN_DIR, "history")
LATEST_PATH = os.path.join(SCAN_DIR, "latest_scan.json")


# ============================================================
# GEOCODING
# ============================================================

def geocode_city(city, country, google_key):
    """
    Resolve "city, country" to a center lat/lng.
    Tier 1: Google Geocoding API. Tier 2: Nominatim (OSM) fallback.
    Returns (lat, lng, formatted_address) or (None, None, None).
    """
    query = f"{city}, {country}"

    if google_key:
        try:
            r = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": query, "key": google_key},
                timeout=10,
            )
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    loc = results[0]["geometry"]["location"]
                    return loc["lat"], loc["lng"], results[0].get("formatted_address", query)
        except Exception as e:
            print(f"   Google geocoding failed: {e}")

    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": "PotholeRadar/1.0"},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            best = r.json()[0]
            return float(best["lat"]), float(best["lon"]), best.get("display_name", query)
    except Exception as e:
        print(f"   Nominatim geocoding failed: {e}")

    return None, None, None


def distance_km(lat1, lng1, lat2, lng2):
    if not all([lat1, lng1, lat2, lng2]):
        return 999
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


# ============================================================
# GRID GENERATION
# ============================================================

def generate_grid(center_lat, center_lng, radius_km, spacing_m=DEFAULT_SPACING_M, max_points=DEFAULT_MAX_POINTS):
    """
    Build a lat/lng grid covering a circle of `radius_km` around the
    center, spaced `spacing_m` meters apart, capped at `max_points`
    (thinned evenly by distance from center so a capped scan still
    covers the outer ring, not just the middle).
    """
    lat_step = spacing_m / 111_320.0
    lng_step = spacing_m / (111_320.0 * math.cos(math.radians(center_lat)))
    n = int(math.ceil(radius_km * 1000 / spacing_m))

    points = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            lat = center_lat + i * lat_step
            lng = center_lng + j * lng_step
            if distance_km(center_lat, center_lng, lat, lng) <= radius_km:
                points.append((lat, lng))

    if len(points) > max_points:
        points.sort(key=lambda p: distance_km(center_lat, center_lng, *p))
        step = len(points) / max_points
        points = [points[int(i * step)] for i in range(max_points)]

    return points


def sample_along_path(path, spacing_m=DEFAULT_SPACING_M, max_points=DEFAULT_MAX_POINTS):
    """
    Walk a polyline (list of (lat, lng), e.g. a clicked OSM road) and pick
    points every `spacing_m` meters, capped at `max_points`. Used for
    street-mode scans where the user selected an exact road on the map
    instead of a radius around a center point.
    """
    if len(path) < 2:
        return list(path)

    cumulative = [0.0]
    for i in range(1, len(path)):
        cumulative.append(cumulative[-1] + distance_km(*path[i - 1], *path[i]) * 1000)
    total = cumulative[-1]
    if total == 0:
        return [path[0]]

    n_points = min(max_points, max(2, int(total / spacing_m) + 1))
    step = total / (n_points - 1)

    points = []
    seg = 0
    for i in range(n_points):
        target = i * step
        while seg < len(cumulative) - 2 and cumulative[seg + 1] < target:
            seg += 1
        seg_len = cumulative[seg + 1] - cumulative[seg]
        t = 0 if seg_len == 0 else (target - cumulative[seg]) / seg_len
        lat = path[seg][0] + t * (path[seg + 1][0] - path[seg][0])
        lng = path[seg][1] + t * (path[seg + 1][1] - path[seg][1])
        points.append((lat, lng))

    return points


def decimate(path, n):
    """Thin a polyline to ~n points for cheap storage/display (not for scanning)."""
    if len(path) <= n:
        return path
    step = len(path) / n
    return [path[int(i * step)] for i in range(n)]


# ============================================================
# STREET VIEW
# ============================================================

def streetview_has_coverage(lat, lng, google_key):
    """Free metadata pre-check — skip points with no panorama before spending a billed image fetch."""
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/streetview/metadata",
            params={"location": f"{lat},{lng}", "key": google_key},
            timeout=10,
        )
        status = r.json().get("status") if r.status_code == 200 else f"HTTP {r.status_code}"
        if status != "OK":
            print(f"    (metadata status: {status})", end=" ")
        return status == "OK"
    except Exception as e:
        print(f"    (metadata request failed: {e})", end=" ")
        return False


def fetch_street_view_angles(lat, lng, google_key):
    """
    Fetch close-in pavement shots, calibrated to avoid two proven failure
    modes on opposite ends of the same dial:

    1. Wide 90 FOV shots let the model "confirm" potholes from a
       barely-resolvable dark smudge far in the distance (a real false
       positive traced to a gas-station-entrance shot).
    2. Overcorrecting to pitch -50/-85 with a 40 FOV swung too far the
       other way: Street View panoramas are stitched from oblique
       captures, and the area close to/beneath the capture rig is a
       known "nadir" artifact zone — there's no real camera data there,
       so the stitching software smears it into a flat, textureless
       gray-brown blur. Two independent scan points produced that exact
       blur (visually confirmed by decoding the stored debug images),
       and the model consistently misread it as "murky water" or a
       "camera malfunction" — a real street came back with zero
       findings because every image at each point was unusable noise,
       not because the pavement was actually clear.

    These angles sit in the middle: pitched down enough to frame
    pavement close-up rather than a wide establishing shot, but shallow
    enough to stay out of the corrupted near-nadir zone.
    """
    angles = [
        {"heading": 0,   "pitch": -25, "fov": 55, "label": "North (close)"},
        {"heading": 45,  "pitch": -25, "fov": 55, "label": "Northeast (close)"},
        {"heading": 90,  "pitch": -25, "fov": 55, "label": "East (close)"},
        {"heading": 135, "pitch": -25, "fov": 55, "label": "Southeast (close)"},
        {"heading": 180, "pitch": -25, "fov": 55, "label": "South (close)"},
        {"heading": 225, "pitch": -25, "fov": 55, "label": "Southwest (close)"},
        {"heading": 270, "pitch": -25, "fov": 55, "label": "West (close)"},
        {"heading": 315, "pitch": -25, "fov": 55, "label": "Northwest (close)"},
        {"heading": 0,   "pitch": -40, "fov": 45, "label": "Down"},
    ]
    images = []
    for angle in angles:
        try:
            r = requests.get(
                "https://maps.googleapis.com/maps/api/streetview",
                params={"size": "640x640", "location": f"{lat},{lng}",
                        "heading": angle["heading"], "pitch": angle["pitch"],
                        "fov": angle["fov"], "source": "outdoor", "key": google_key},
                timeout=15,
            )
            if r.status_code == 200 and len(r.content) > 8000:
                images.append({**angle, "b64": base64.b64encode(r.content).decode("utf-8")})
        except Exception:
            pass
    return images


# ============================================================
# CLAUDE VISION — POTHOLE DETECTION
# ============================================================

def identify_pothole_in_images(images, lat, lng):
    """
    Claude Vision reviews all Street View angles at this grid point and
    decides whether a real pothole is visible. Unlike PotholeWatch's
    incident-confirmation version of this prompt, there is no prior
    report anchoring this call — it's a blind systematic sweep.
    Returns (best_image_b64, result_dict, confirmed_bool).
    """
    if not images:
        return None, {}, False

    try:
        content = []
        for i, img in enumerate(images):
            content.append({"type": "text",
                "text": f"Image {i+1}/{len(images)} — Street View facing {img['label']} at {lat:.5f}, {lng:.5f}:"})
            content.append({"type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img["b64"]}})

        content.append({"type": "text", "text": f"""
You are a road damage expert conducting a systematic infrastructure sweep.
There is no prior report for this location — you are scanning it blind as
part of an area survey. Examine ALL {len(images)} images carefully.

CRITICAL DISTINCTION — Pothole vs Damaged Road:

A TRUE POTHOLE (hueco/bache) is:
✅ A HOLE or DEPRESSION where asphalt is PHYSICALLY MISSING
✅ Exposed base layer (light grey/beige concrete or aggregate) visible INSIDE a depression
✅ Clear depth visible — you can see INTO the hole
✅ Jagged broken edges surrounding the missing asphalt
✅ Often oval/irregular shaped, 20cm to 2m diameter

Typical signs:
- The hole shows light grey/beige base material against dark surrounding asphalt
- Edges are crumbling and broken, not smooth
- Sometimes debris (gravel, dirt, straw) collects inside
- Multiple potholes often cluster together

NOT a pothole — do NOT confirm these:
❌ Surface cracks (even large ones) without missing asphalt
❌ Rough or worn road texture
❌ Patch repairs (darker square/rectangle repairs)
❌ Standing water or a puddle where you CANNOT see a hole or missing
   asphalt beneath it — water pools on flat, sloped, or unevenly worn
   pavement all the time without an actual hole underneath. Water alone,
   without visible depth/edges/missing material, is NOT enough.
❌ Road markings or paint
❌ Normal concrete expansion joints
❌ General road deterioration without visible holes
❌ Depressions, grates, or dark patches immediately next to a drainage
   opening/gutter/storm drain — these are usually intentional drainage
   features (built to be lower, to channel water), not damage. Be extra
   skeptical near drainage infrastructure specifically.
❌ A patch repair area where you're inferring a "separate" hole next to
   or within it — patches are often uneven or discolored at their own
   edges, which is easy to misread as an adjacent distinct pothole. If a
   patch is present, only confirm a pothole if it is clearly and fully
   separate from the patch, not touching or bordering it.
❌ A flat, blurry, textureless gray/brown smear with no discernible
   pavement texture, lane markings, or edges — this is a known Street
   View stitching artifact from very steep/close camera angles, not
   water, not a hole, not evidence of anything. If an image looks like
   this, disregard that specific image entirely and base your judgment
   on the other, clearer angles — don't guess at what an unreadable
   image might be showing.

This is a systematic sweep, not a spot-check — the goal is to find every
real pothole in these images, not just the most obvious one. Look
carefully at all {len(images)} angles, including partial views near the
frame edges and smaller/shallower holes, not only large dramatic ones.
A pothole confirmed at 0.2m across with a shallow 3cm depth is just as
valid a finding as a large severe one — use the severity field to convey
how bad it is, don't let severity affect whether you confirm it at all.
If water is pooling, you must still be able to see a genuine hole
(depth, broken edges, or exposed base material breaking the water's
surface) to confirm it — inferring a hole merely because water is
sitting somewhere is exactly the kind of guess this task must avoid.

pothole_confirmed = true only if you can actually SEE a hole with
missing asphalt — not infer one from indirect evidence like pooling,
shadows, or discoloration alone. You don't need every listed criterion
(jagged edges, exact color, exact size) to be textbook-perfect, but the
core fact — there is a real hole, not just surface wear or water —
must be something you can directly point to in the image, not guess at.

Distance matters: only confirm a pothole in the near-to-middle part of
the frame, close enough that its edges, depth, and base material are
actually resolvable as distinct details — not a small dark patch or
smudge far in the background that you're guessing might be a hole. If
something is too far away or too small in frame to be sure, don't
confirm it, and don't let it inflate confidence_visual either.

Respond ONLY in this JSON format:
{{
  "best_image_index": 0,
  "pothole_found": true/false,
  "pothole_confirmed": true/false,
  "best_heading": 0,
  "best_label": "one of the exact image labels shown above (e.g. 'North (close)' or 'Down')",
  "severity": "none/minor/moderate/severe/critical",
  "description": "Detailed 2-3 sentence description: where in frame, road surface condition, type of damage.",
  "estimated_diameter_m": 0.0,
  "estimated_depth_cm": 0,
  "location_in_frame": "center-lane/right-lane/left-lane/shoulder/multiple",
  "road_condition": "Brief overall road condition assessment",
  "accident_risk": "low/medium/high/critical",
  "powerfix_opportunity": true/false,
  "confidence_visual": 0-100,
  "per_image_notes": ["note for image 1", "note for image 2", "..."]
}}"""
        })

        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=1200,
            messages=[{"role": "user", "content": content}],
        )
        text = response.content[0].text.strip()
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            result = json.loads(match.group())
            best_idx = min(result.get("best_image_index", 0), len(images) - 1)
            confirmed = result.get("pothole_confirmed", False)
            return images[best_idx]["b64"], result, confirmed
    except Exception as e:
        print(f"   Vision analysis failed: {e}")

    return images[0]["b64"] if images else None, {}, False


# ============================================================
# EMAIL DIGEST
# ============================================================

def _finding_card(f):
    color = SEVERITY_COLORS.get(f.get("severity", "minor"), ACCENT)
    img_html = (f'<img src="data:image/jpeg;base64,{f["street_view_image_b64"]}" '
                f'style="width:100%;border-radius:6px;margin-bottom:8px;display:block;" />') if f.get("street_view_image_b64") else ""
    return f"""
<div style="background:{CARD_BG};border-radius:8px;padding:20px;margin-bottom:16px;border-left:4px solid {color};">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
    <span style="font-size:10px;letter-spacing:3px;color:{color};font-weight:700;text-transform:uppercase;">{f.get('severity','?')}</span>
    <span style="color:{DIM};">·</span>
    <span style="font-size:10px;color:{MUTED};">{f.get('confidence_visual',0)}% confidence</span>
  </div>
  <div style="background:{SOFT};padding:14px;border-radius:6px;margin:10px 0;">
    {img_html}
    <div style="font-size:14px;line-height:1.6;color:{TEXT};">{f.get('description','')}</div>
  </div>
  <div style="font-size:12px;color:{MUTED};">Ø{f.get('estimated_diameter_m',0)}m · {f.get('estimated_depth_cm',0)}cm deep · {f.get('location_in_frame','')} · accident risk: {f.get('accident_risk','')}</div>
  <a href="{f.get('maps_link','')}" style="display:inline-block;margin-top:10px;font-size:11px;color:{ACCENT};text-decoration:none;border:1px solid rgba(63,127,224,0.4);padding:4px 10px;border-radius:3px;">📍 Open in Google Maps →</a>
</div>"""


def build_digest(scan):
    findings = scan["findings"]
    count = len(findings)
    return f"""<!DOCTYPE html><html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;background:{BG};padding:24px;color:{TEXT};margin:0;">
<div style="max-width:720px;margin:auto;">
  <div style="margin-bottom:24px;padding:24px;background:{CARD_BG};border-radius:8px;border-top:4px solid {ACCENT};">
    <div style="font-size:11px;letter-spacing:4px;color:{ACCENT};font-weight:700;">POTHOLERADAR · v1.2</div>
    <h1 style="margin:10px 0 6px;font-size:26px;color:{TEXT};font-weight:700;">{count} pothole{'s' if count != 1 else ''} found</h1>
    <div style="color:{MUTED};font-size:12px;margin-top:6px;">
      {scan['city']}, {scan['country']} · {(str(scan['radius_km'])+'km radius') if scan.get('mode') != 'street' else 'street'} scan ·
      {scan['points_scanned']}/{scan['points_total']} points scanned ({scan['points_skipped_no_coverage']} skipped, no Street View coverage)
    </div>
  </div>
  {''.join(_finding_card(f) for f in findings)}
  <div style="text-align:center;font-size:11px;color:{DIM};padding:24px 0;border-top:1px solid {SOFT};margin-top:8px;">
    <div style="font-size:10px;letter-spacing:3px;color:{ACCENT};font-weight:700;margin-bottom:6px;">POWERFIX · REPAIR. REINVENTED.</div>
    <div>PotholeRadar v1.0 — proactive area sweep, no prior report required.</div>
  </div>
</div></body></html>"""


def send_email(subject, html_body):
    creds = Credentials(token=None, refresh_token=GMAIL_REFRESH_TOKEN,
                         token_uri="https://oauth2.googleapis.com/token",
                         client_id=GMAIL_CLIENT_ID, client_secret=GMAIL_CLIENT_SECRET)
    service = build("gmail", "v1", credentials=creds)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_SENDER
    msg["To"] = ", ".join(ALERT_RECIPIENTS)
    msg.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="PotholeRadar — proactive geographic pothole scanner")
    parser.add_argument("--country", required=True)
    parser.add_argument("--city", default="", help="Display label for the scanned area (e.g. a searched landmark/neighborhood name)")
    parser.add_argument("--radius-km", type=float, default=3)
    parser.add_argument("--max-points", type=int, default=DEFAULT_MAX_POINTS)
    parser.add_argument("--spacing-m", type=float, default=DEFAULT_SPACING_M)
    parser.add_argument("--lat", default="", help="Center latitude, if already resolved (e.g. from a map search) — skips geocoding")
    parser.add_argument("--lng", default="", help="Center longitude, if already resolved — skips geocoding")
    parser.add_argument("--street-path", default="",
                         help="JSON array of [lat,lng] pairs (a clicked road) — switches to street mode instead of a radius sweep")
    args = parser.parse_args()

    max_points = min(args.max_points, HARD_MAX_POINTS)
    scan_time = datetime.now(timezone.utc)
    scan_id = f"PR-{scan_time.strftime('%Y%m%d-%H%M%S')}"
    area_label = args.city or f"{args.lat}, {args.lng}"

    street_path = None
    if args.street_path.strip():
        try:
            raw = json.loads(args.street_path)
            street_path = [(float(p[0]), float(p[1])) for p in raw]
        except Exception as e:
            print(f"❌ Could not parse --street-path: {e}")
            return
        if len(street_path) < 2:
            print("❌ Street path needs at least 2 points — aborting.")
            return

    if street_path:
        center_lat, center_lng = street_path[len(street_path) // 2]
        print(f"🔵 PotholeRadar scan {scan_id} — street '{area_label}' in {args.country} (max {max_points} points)")
        points = sample_along_path(street_path, args.spacing_m, max_points)
        print(f"🛣️  Street: {len(points)} points along a {len(street_path)}-node road")
    else:
        print(f"🔵 PotholeRadar scan {scan_id} — {area_label}, {args.country} ({args.radius_km}km radius, max {max_points} points)")

        if args.lat and args.lng:
            center_lat, center_lng = float(args.lat), float(args.lng)
            print(f"📍 Center (from map search): {center_lat:.5f}, {center_lng:.5f} ({area_label})")
        else:
            center_lat, center_lng, formatted = geocode_city(args.city, args.country, GOOGLE_MAPS_API_KEY)
            if center_lat is None:
                print(f"❌ Could not geocode '{args.city}, {args.country}' — aborting.")
                return
            area_label = formatted
            print(f"📍 Center: {center_lat:.5f}, {center_lng:.5f} ({formatted})")

        points = generate_grid(center_lat, center_lng, args.radius_km, args.spacing_m, max_points)
        print(f"🗺️  Grid: {len(points)} points to check")

    findings = []
    debug_points = []  # one record per scanned point regardless of outcome, so a
                        # "0 found" run can actually be inspected instead of guessed at
    errors = []
    points_scanned = 0
    points_skipped = 0

    for idx, (lat, lng) in enumerate(points):
        print(f"  [{idx+1}/{len(points)}] {lat:.5f}, {lng:.5f} ...", end=" ")
        try:
            if not streetview_has_coverage(lat, lng, GOOGLE_MAPS_API_KEY):
                print("no coverage, skipped")
                points_skipped += 1
                continue

            images = fetch_street_view_angles(lat, lng, GOOGLE_MAPS_API_KEY)
            if not images:
                print("no images fetched, skipped")
                points_skipped += 1
                continue

            best_b64, result, confirmed = identify_pothole_in_images(images, lat, lng)
            points_scanned += 1
            low_confidence = confirmed and result.get("confidence_visual", 0) < MIN_CONFIDENCE
            if low_confidence:
                confirmed = False

            debug_img = next((im["b64"] for im in images if im["label"] == "Down"), images[0]["b64"])
            debug_points.append({
                "lat": lat, "lng": lng,
                "pothole_confirmed": confirmed,
                "confidence_visual": result.get("confidence_visual", 0),
                "description": result.get("description", ""),
                "maps_link": f"https://maps.google.com/?q={lat},{lng}",
                "debug_image_b64": debug_img,
            })

            if confirmed:
                print(f"🕳️  POTHOLE — {result.get('severity','?')}, Ø{result.get('estimated_diameter_m',0)}m")
                findings.append({
                    "lat": lat, "lng": lng,
                    "severity": result.get("severity", "minor"),
                    "pothole_confirmed": True,
                    "description": result.get("description", ""),
                    "estimated_diameter_m": result.get("estimated_diameter_m", 0),
                    "estimated_depth_cm": result.get("estimated_depth_cm", 0),
                    "location_in_frame": result.get("location_in_frame", ""),
                    "accident_risk": result.get("accident_risk", ""),
                    "confidence_visual": result.get("confidence_visual", 0),
                    "best_heading": result.get("best_heading", 0),
                    "best_label": result.get("best_label", ""),
                    "street_view_image_b64": best_b64,
                    "maps_link": f"https://maps.google.com/?q={lat},{lng}",
                })
                if len(findings) >= MAX_FINDINGS:
                    print(f"\n🛑 Reached the {MAX_FINDINGS}-finding test cap — stopping the scan early.")
                    break
            elif low_confidence:
                print(f"clear (model flagged it but only {result.get('confidence_visual',0)}% confident — below the {MIN_CONFIDENCE}% floor)")
            else:
                print("clear")
        except Exception as e:
            print(f"error: {e}")
            errors.append({"lat": lat, "lng": lng, "stage": "scan", "message": str(e)})
            points_skipped += 1

        time.sleep(0.3)

    scan = {
        "scan_id": scan_id,
        "mode": "street" if street_path else "radius",
        "country": args.country, "city": area_label,
        "center_lat": center_lat, "center_lng": center_lng,
        "radius_km": args.radius_km if not street_path else None,
        "path": decimate(street_path, 60) if street_path else None,
        "grid_spacing_m": args.spacing_m, "max_points": max_points,
        "scan_time": scan_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "points_total": len(points), "points_scanned": points_scanned,
        "points_skipped_no_coverage": points_skipped,
        "potholes_found": len(findings),
        "findings": findings,
        "debug_points": debug_points,
        "errors": errors,
    }

    os.makedirs(SCAN_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(LATEST_PATH, "w") as fh:
        json.dump(scan, fh, indent=2)
    with open(os.path.join(HISTORY_DIR, f"{scan_id}.json"), "w") as fh:
        json.dump(scan, fh, indent=2)

    manifest_path = os.path.join(HISTORY_DIR, "manifest.json")
    manifest = []
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as fh:
                manifest = json.load(fh)
        except Exception:
            manifest = []
    manifest.append({
        "scan_id": scan_id,
        "mode": scan["mode"],
        "country": args.country, "city": area_label,
        "center_lat": center_lat, "center_lng": center_lng, "radius_km": scan["radius_km"],
        "path": scan["path"],
        "scan_time": scan["scan_time"],
        "points_scanned": points_scanned, "potholes_found": len(findings),
    })
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"\n✅ Scan complete: {len(findings)} pothole(s) found, {points_scanned} scanned, {points_skipped} skipped, {len(errors)} error(s)")

    if findings:
        try:
            subject = f"PotholeRadar: {len(findings)} pothole(s) found in {area_label}, {args.country}"
            send_email(subject, build_digest(scan))
            print("📧 Email digest sent")
        except Exception as e:
            # The scan results are already written to disk at this point — a
            # broken email config must never take those down with it.
            print(f"⚠️  Email digest failed to send (scan results are still saved): {e}")


if __name__ == "__main__":
    main()
