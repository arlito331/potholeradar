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
