"""Publish a closed-library catalog only from complete, hash-bound boundary reviews."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

import season_cut as base
from cut_known import policy_hash

EXPECTED = {"S01": 58, "S02": 104, "S03": 46, "S04": 52}


def validate_catalog(catalog):
    if catalog.get("status") != "FROZEN_BOUNDARIES_REVIEWED":
        raise ValueError("Not a frozen reviewed catalog")
    entries = catalog["entries"]
    ids = [e["episode"] for e in entries]
    expected = {season + f"E{i:02d}" for season, count in EXPECTED.items() for i in range(1, count+1)}
    if len(ids) != 260 or set(ids) != expected:
        raise ValueError("Catalog must contain each of the exact 260 known episodes once")
    for entry in entries:
        if (not entry["boundary_reviewed"] or not entry["review_note"].strip()
                or not re.fullmatch("[0-9a-f]{64}", entry["source_sha256"])
                or entry["source_bytes"] <= 0):
            raise ValueError("Missing review/source proof")
        season = entry["episode"][:3]
        profile = base.read(base.REPO / "config/seasons" / (season + ".json"))
        if policy_hash(profile) != catalog["cut_policy_sha256"][season]:
            raise ValueError("Stale cut policy")
        if entry["tail_kind"] not in profile["cut_review"]["tail_kinds"]:
            raise ValueError("Unsupported tail kind")
        base.validate_bounds(entry["opening_seconds"], entry["keep_through_seconds"],
            entry["first_forbidden_seconds"], entry["duration_seconds"],
            profile["cut_review"]["tail_safety_seconds"])
    return True


def freeze(audit_root, output):
    audit_root = Path(audit_root)
    entries, proofs = [], {}
    for season, count in EXPECTED.items():
        review_path = audit_root / "reviews" / "final" / (season + ".json")
        review = base.read(review_path)
        if review["episodes_reviewed"] != count:
            raise ValueError("Incomplete season review")
        for sheet in review["sheets"]:
            if base.digest(audit_root / "sheets" / sheet["name"]) != sheet["sha256"]:
                raise ValueError("Reviewed boundary sheet changed")
        for i in range(1, count+1):
            eid = season + f"E{i:02d}"
            scan_path = audit_root / "episodes" / eid / "scan.json"
            if base.digest(scan_path) != review["scan_sha256"][eid]:
                raise ValueError("Episode scan changed after review")
            scan = base.read(scan_path)
            override = review.get("overrides", {}).get(eid, {})
            cue = override.get("first_forbidden_seconds", scan["tail"]["first_forbidden_seconds"])
            if cue is None:
                raise ValueError(f"Unresolved episode: {eid}")
            note = ("Head, closing frame, safe end and forbidden-marker images reviewed; "
                    "bounded source-frame review, not full playback or audio audition.")
            if override:
                note += " " + override["reason"]
            entry = dict(episode=eid, source_name=scan["source_name"],
                         source_sha256=scan["source_sha256"], source_bytes=scan["source_bytes"],
                         duration_seconds=scan["duration_seconds"],
                         opening_seconds=override.get("opening_seconds", scan["opening_seconds"]),
                         first_forbidden_seconds=cue,
                         keep_through_seconds=override.get("keep_through_seconds", cue - 2.0),
                         tail_kind=override.get("tail_kind", scan["tail"]["kind"]),
                         detector=scan["tail"]["detector"], boundary_reviewed=True,
                         special_case=bool(override), review_note=note,
                         recording_type=override.get("recording_type", "theatre"),
                         closing_type=override.get("closing_type", "closing_wide"),
                         scan_sha256=base.digest(scan_path))
            entries.append(entry)
        proofs[season] = base.digest(review_path)
    catalog = dict(schema_version=1, status="FROZEN_BOUNDARIES_REVIEWED", episode_count=260,
                   source_scope="exact_known_S01_S04_original_file_sha256_only",
                   review_scope="all 260 source boundary rows viewed; not complete programme playback/audio",
                   season_counts=EXPECTED, review_receipt_sha256=proofs,
                   cut_policy_sha256={s: policy_hash(base.read(base.REPO / "config/seasons" / (s+".json")))
                                      for s in EXPECTED},
                   entries=entries)
    validate_catalog(catalog)
    base.save_new(output, catalog)
    return catalog


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audit-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = freeze(a.audit_root, a.output)
    print(json.dumps(dict(count=len(result["entries"]),
        kinds=dict(Counter(e["tail_kind"] for e in result["entries"])),
        special_cases=[e["episode"] for e in result["entries"] if e["special_case"]]), indent=2))


if __name__ == "__main__":
    main()
