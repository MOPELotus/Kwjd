"""Use frozen, source-SHA-bound cuts for the known S01--S04 library."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import season_cut as base

DEFAULT_CATALOG = base.REPO / "config/cuts/four-seasons-known-v1.json"


def policy_hash(profile):
    return hashlib.sha256(json.dumps(profile["cut_review"], sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def select(source, catalog):
    episode, profile, profile_sha = base.profile_for(source)
    if catalog.get("status") != "FROZEN_BOUNDARIES_REVIEWED":
        raise ValueError("Catalog has not completed boundary review")
    entries = [item for item in catalog["entries"] if item["episode"] == episode]
    if len(entries) != 1 or not entries[0].get("boundary_reviewed"):
        raise ValueError("Missing, duplicate or unreviewed known episode")
    entry = entries[0]
    if policy_hash(profile) != catalog["cut_policy_sha256"][episode[:3]]:
        raise ValueError("Cut policy changed after freezing; validate before reuse")
    if Path(source).stat().st_size != entry["source_bytes"] or base.digest(source) != entry["source_sha256"]:
        raise ValueError("Unknown source bytes/SHA; refusing to inherit another version's cut")
    return entry, profile_sha


def cut_known(source, output, catalog_path=DEFAULT_CATALOG):
    source, output, catalog_path = Path(source).resolve(), Path(output).resolve(), Path(catalog_path).resolve()
    catalog = base.read(catalog_path)
    entry, profile_sha = select(source, catalog)
    entry_sha = hashlib.sha256(json.dumps(entry, sort_keys=True,
                              separators=(",", ":")).encode()).hexdigest()
    if output.exists():
        receipt_path = output.with_suffix(output.suffix + ".cut.json")
        binding_path = output.with_suffix(output.suffix + ".known.json")
        if not receipt_path.exists() or not binding_path.exists():
            raise ValueError("Existing media has no matching known-cut receipt; preserve it")
        receipt = base.read(binding_path)
        if (receipt.get("known_entry_sha256") != entry_sha or not receipt.get("technical_checked")
                or base.read(receipt_path).get("output_sha256") != receipt.get("output_sha256")
                or base.digest(output) != receipt.get("output_sha256")):
            raise ValueError("Existing media/receipt differs; preserve it")
        return dict(receipt, reused=True)
    # The frozen catalog is the reviewed boundary evidence. No image directories
    # or renewed per-episode timing decisions are required for an unchanged source.
    plan = {"schema_version": 1, "episode": entry["episode"], "source": str(source),
            "source_sha256": entry["source_sha256"], "profile_sha256": profile_sha,
            "audit": str(catalog_path), "audit_sha256": base.digest(catalog_path),
            "first_live_seconds": entry["opening_seconds"],
            "keep_through_seconds": entry["keep_through_seconds"],
            "first_forbidden_seconds": entry["first_forbidden_seconds"],
            "tail_kind": entry["tail_kind"],
            "visual_review_note": entry["review_note"],
            "duration_seconds": entry["duration_seconds"]}
    plan_path = output.with_suffix(output.suffix + ".known-plan.json")
    if plan_path.exists():
        if base.read(plan_path) != plan:
            raise ValueError("Existing cut plan differs; preserve it")
    else:
        base.save_new(plan_path, plan)
    receipt = base.cut(plan_path, output)
    receipt["known_entry_sha256"] = entry_sha
    receipt["known_catalog_sha256"] = base.digest(catalog_path)
    receipt["catalog_boundaries_reviewed"] = True
    receipt["boundary_selection_requires_manual_action"] = False
    # Preserve the original technical receipt and publish the known-cut binding
    # separately: do not rewrite previous evidence to claim output playback.
    binding_path = output.with_suffix(output.suffix + ".known.json")
    base.save_new(binding_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    print(json.dumps(cut_known(args.source, args.output, args.catalog), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
