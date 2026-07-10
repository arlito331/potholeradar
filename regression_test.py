"""
PotholeRadar detection regression test
=========================================
Runs identify_pothole_in_images against a small, hand-verified set of real
coordinates, so a prompt/logic change gets checked against actual past
cases automatically — instead of testing one example at a time and only
discovering a regression after it ships.

Add a new entry to CASES every time a real false positive or false
negative gets confirmed (manual Street View/Google Earth review, or a
site visit) — that's the only way this set becomes actually useful over
time instead of static.
"""
import sys

from potholeradar import (
    GOOGLE_MAPS_API_KEY,
    fetch_street_view_angles,
    fetch_top_down_image,
    identify_pothole_in_images,
)

# (lat, lng, expected_feature_type, expected_confirmed, label)
CASES = [
    (8.987192746676248, -79.52011226430018, "drainage_infrastructure", False,
     "Calle 57 Este curb drain — confirmed false positive on a 2026-07-08 "
     "scan (missing cover misread as a pothole); the two-step "
     "classify-then-confirm prompt change exists specifically to fix this."),
    (8.987058, -79.519703, "asphalt_pothole", True,
     "Sortis Hotel spot, Calle 57 Este — user-identified real pothole via "
     "manual Google Earth Street View review; automated scans have not yet "
     "confirmed it. Tracked here as an open recall gap, not a fixed bug."),
    (8.986895, -79.519637, "asphalt_pothole", True,
     "Caliope Steakhouse spot, Calle 57 Este (~18m from the Sortis Hotel "
     "case above, same block) — user-identified real pothole via manual "
     "Google Maps Street View review (both headings show dark irregular "
     "pavement damage near the crosswalk). Automated scans have not yet "
     "confirmed it. Tracked here as an open recall gap, not a fixed bug."),
    (8.9745462, -79.5132098, "crack_only", False,
     "Av. Italia near PH Ocean Front Penthouses — user-identified severe "
     "concrete slab cracking via Google Maps Street View (Nov 2022 "
     "capture); deep open cracks across multiple slabs but no clearly "
     "resolved missing-asphalt hole in this exact frame. Inside the "
     "2026-07-08 radius scan (8.9735,-79.5131) that returned no confirmed "
     "findings there — expected values are a best guess pending the "
     "actual pipeline result, not a locked-in requirement."),
    (8.9744129, -79.5128017, "asphalt_pothole", True,
     "C. Heliodoro Patino near PH Ocean Front Penthouses — user-identified "
     "dark irregular hole next to the street-name pavement stencil via "
     "Google Maps Street View (Nov 2022 capture). Inside the same "
     "2026-07-08 radius scan. Open recall gap, not yet confirmed by the "
     "automated pipeline."),
    (8.9742028, -79.5126287, "patch_repair", False,
     "C. Heliodoro Patino, ~25m further along the same block — "
     "user-identified extensively cracked/patched road surface via "
     "Google Maps Street View (Nov 2022 capture), spanning the full lane "
     "width near parked cars. Inside the same 2026-07-08 radius scan. "
     "Open recall gap, not yet confirmed by the automated pipeline."),
    (8.974323168882501, -79.512802, "other_no_damage", False,
     "C. Heliodoro Patino, tight-spacing test point (2026-07-10) — "
     "identify_pothole_in_images() previously returned "
     "asphalt_pothole/confirmed=True with a detailed fabricated "
     "description (jagged edges, exposed base, debris) for this exact "
     "point; the saved representative image is verified by direct visual "
     "inspection to show only parked cars and a driver, no pavement "
     "damage anywhere in frame. Two other grid points 5m away resolved "
     "to the identical Street View photo and got two different "
     "conflicting verdicts (crack_only, crack_only) in the same run — "
     "confirmed hallucination under uncertainty, not a missed detection. "
     "This case exists to check that a fix stops the fabrication, not "
     "just to check the exact label."),
]


def run_case(lat, lng, expected_type, expected_confirmed, label):
    images = fetch_street_view_angles(lat, lng, GOOGLE_MAPS_API_KEY)
    if not images:
        return {"label": label, "lat": lat, "lng": lng, "status": "SKIP",
                "reason": "no Street View images fetched"}
    top_down = fetch_top_down_image(lat, lng, GOOGLE_MAPS_API_KEY)
    if top_down:
        images = images + [top_down]

    _, result, confirmed = identify_pothole_in_images(images, lat, lng)
    actual_type = result.get("feature_type", "?")
    ok = confirmed == expected_confirmed and actual_type == expected_type
    return {
        "label": label, "lat": lat, "lng": lng,
        "status": "PASS" if ok else "FAIL",
        "expected": (expected_type, expected_confirmed),
        "actual": (actual_type, confirmed, result.get("confidence_visual")),
        "description": result.get("description", ""),
    }


def main():
    results = [run_case(*case) for case in CASES]

    print(f"\n{'=' * 70}")
    for r in results:
        print(f"[{r['status']}] {r['label']}")
        print(f"    {r['lat']:.6f}, {r['lng']:.6f}")
        if r["status"] == "SKIP":
            print(f"    skipped: {r['reason']}")
        else:
            print(f"    expected: type={r['expected'][0]!r} confirmed={r['expected'][1]}")
            print(f"    actual:   type={r['actual'][0]!r} confirmed={r['actual'][1]} confidence={r['actual'][2]}")
            print(f"    {r['description'][:200]}")
        print()

    scored = [r for r in results if r["status"] != "SKIP"]
    passed = sum(1 for r in scored if r["status"] == "PASS")
    print(f"{'=' * 70}\n{passed}/{len(scored)} passed"
          f"{f' ({len(results) - len(scored)} skipped)' if len(results) != len(scored) else ''}\n")

    sys.exit(0 if passed == len(scored) else 1)


if __name__ == "__main__":
    main()
