# Explicit master tail-duration approval

The normal master-duration tolerance remains ±0.5 seconds. An explicitly
human-approved extra background tail may exceed that tolerance for one exact
master. Never change the cut manifest's expected duration to hide the mismatch,
trim/re-encode the exported master, or reuse approval after its SHA changes.

Windows `send-master` automatically supplies an existing file at
`.kwjd/master-duration-approvals/S01Exx.json` to `validate_master.py` through
`--duration-approval`. The file must have this schema (replace placeholders with
actual measured values and the actual human decision):

```json
{
  "schema_version": 1,
  "approved": true,
  "approved_by": "human",
  "episode": "S01E50",
  "scope": "extra_tail_background_only",
  "master_sha256": "<64 lowercase hexadecimal SHA-256 characters>",
  "expected_duration_seconds": 3649.933,
  "actual_duration_seconds": 3650.440,
  "reason": "Reviewed extra background tail after the performer exits",
  "human_quote": "<the actual explicit human acceptance>"
}
```

Both durations must match measured/current manifest values within one microsecond;
the master filename and SHA must match the episode. The exception only accepts
positive extension, and requires the explicit approval/attribution/reason. A stale
or malformed approval fails even when supplied for an otherwise ordinary master.
This checks a recorded decision; it does not infer background-only content from
duration or replace visual/sync review.

The technical receipt preserves `duration_audit.default_duration_gate_passed`
(false for an accepted extension beyond0.5s), the actual delta, complete approval
snapshot, approval file SHA and path. Technical pass through this exception never
claims that the unmodified default gate passed. All other codec/audio/decode/hash
and upload/disk/lifecycle gates still apply. Local master/CacheClip cleanup must
wait for the normal verified upload; this feature does not authorize early cleanup.

E50's actual human decision accepted the reported0.507s extra background tail.
Windows owns its local hash-bound approval record and should keep the original
quote and measurements. The Linux original input probe independently records a
0.093s video start and3649.840s video duration; the reported3650.440s master is15
native frames longer than that video stream. These figures alone do not locate
all extra frames or prove synchronization.

Linux contract tests cover default refusal, exact accepted binding, stale SHA,
episode/duration/scope/approval rejection, malformed numeric fields and retained
default-failure audit. Windows must execute its actual PowerShell uploader and
full local master validation before upload. Linux has no PowerShell installed;
no Windows execution is claimed by the Python tests.
