"""
Head-to-head test: identify_pothole_in_images() (current, one-shot
multi-image classify) vs identify_pothole_in_images_screened() (parallel
path — per-image yes/no screen first, full classify only on hits).

Runs both pipelines against the same CASES from regression_test.py so the
two architectures are judged on identical inputs, including the confirmed
hallucination case (parked-SUV photo, three grid points, three
contradictory verdicts from the old pipeline in production).

This does not replace regression_test.py or touch main()'s call site —
it's purely a comparison harness to decide whether the screened pipeline
is worth switching to.
"""
import sys

from potholeradar import (
    GOOGLE_MAPS_API_KEY,
    fetch_street_view_angles,
    fetch_top_down_image,
    identify_pothole_in_images,
    identify_pothole_in_images_screened,
)
from regression_test import CASES


def run_case(lat, lng, expected_type, expected_confirmed, label, cache):
    images = fetch_street_view_angles(lat, lng, GOOGLE_MAPS_API_KEY)
    if not images:
        return {"label": label, "lat": lat, "lng": lng, "status": "SKIP",
                "reason": "no Street View images fetched"}
    top_down = fetch_top_down_image(lat, lng, GOOGLE_MAPS_API_KEY)
    if top_down:
        images = images + [top_down]

    _, old_result, old_confirmed = identify_pothole_in_images(images, lat, lng)
    _, new_result, new_confirmed = identify_pothole_in_images_screened(images, lat, lng, cache=cache)

    old_type = old_result.get("feature_type", "?")
    new_type = new_result.get("feature_type", "?")
    old_ok = old_confirmed == expected_confirmed and old_type == expected_type
    new_ok = new_confirmed == expected_confirmed and new_type == expected_type

    return {
        "label": label, "lat": lat, "lng": lng, "status": "SCORED",
        "expected": (expected_type, expected_confirmed),
        "old": (old_type, old_confirmed, old_result.get("confidence_visual"), old_ok, old_result.get("description", "")),
        "new": (new_type, new_confirmed, new_result.get("confidence_visual"), new_ok, new_result.get("description", "")),
    }


def main():
    cache = {}  # shared across cases, mirrors how a real scan run would dedupe repeat panoramas
    results = [run_case(*case, cache) for case in CASES]

    print(f"\n{'=' * 78}")
    for r in results:
        print(f"{r['label']}")
        print(f"    {r['lat']:.6f}, {r['lng']:.6f}")
        if r["status"] == "SKIP":
            print(f"    skipped: {r['reason']}\n")
            continue
        et, ec = r["expected"]
        ot, oc, ocf, ook, odesc = r["old"]
        nt, nc, ncf, nok, ndesc = r["new"]
        print(f"    expected: type={et!r} confirmed={ec}")
        print(f"    OLD [{'PASS' if ook else 'FAIL'}]: type={ot!r} confirmed={oc} confidence={ocf}")
        print(f"        {odesc[:180]}")
        print(f"    NEW [{'PASS' if nok else 'FAIL'}]: type={nt!r} confirmed={nc} confidence={ncf}")
        print(f"        {ndesc[:180]}")
        if nok and not ook:
            print("    -> screened pipeline fixed this one")
        elif ook and not nok:
            print("    -> screened pipeline REGRESSED this one")
        print()

    scored = [r for r in results if r["status"] == "SCORED"]
    old_passed = sum(1 for r in scored if r["old"][3])
    new_passed = sum(1 for r in scored if r["new"][3])
    print(f"{'=' * 78}")
    print(f"OLD (one-shot multi-image): {old_passed}/{len(scored)} passed")
    print(f"NEW (screened):             {new_passed}/{len(scored)} passed")
    print(f"Unique images screened: {len(cache)}\n")

    sys.exit(0 if new_passed >= old_passed else 1)


if __name__ == "__main__":
    main()
