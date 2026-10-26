# Linux enhancement backend contract (implementation handoff)

## Current authorization and independent stage (2026-10-26)

The user now accepts residual hard subtitles and explicitly waived the manual
sample-approval gate for enhancement. Technical validation is still required.
The superseded E49 DNxHR master must not be uploaded or processed: its SHA256 is
`bc33db2cff031025a76c18e2ebfc4f95a6c7fdb0c2d6183ae13f238a257a10ed`.
The replacement reuses `S01E49.clean-master.mov`; identify it by a newly
confirmed transfer hash, never its filename. Windows reports8,631,619,470 bytes,
SHA256 `07bd52ab2a3a7c540c4edb3c53aba63dfdf61f1d9db628cf6f03162946600e9d`;
the Linux upload receipt must still confirm those actual received bytes.
Windows prepares the new Magic Mask
export directly as high-quality HEVC10bit + PCM24bit/48kHz stereo. No transcode of
the superseded DNxHR master is requested. The user approved the current MiSans84
appearance and position. Windows supplies that exact template/static font and a
matching approved SHA sidecar; no additional manual approval gate is needed.
Older52/72-point templates must not be treated as approved.
The user accepts existing table-hit audio peaks. Do not normalize, lower gain,
apply a limiter, or block processing solely because those accepted peaks reach
full scale. Decode integrity and audio/video synchronization checks still apply.

Linux owns `scripts/server_worker.py`, `scripts/enhance_stage.py`,
`scripts/enhance_backend.py`, `scripts/prepare_backend.py`, corresponding tests,
and this backend document. Windows owns its transfer/style/export scripts.

```bash
python3 scripts/prepare_backend.py --configure-only  # existing verified local assets
python3 scripts/server_worker.py --once --episode S01E49 --stage enhance
```

Do not execute the second command until Windows confirms the replacement upload
and hash. Jobs require `master_sha256`, or a companion
`S01E49.clean-master.mov.verified.json` containing `transfer_verified: true` and
the confirmed `sha256`. Linux checks the full hash, rejects retired hashes via
local `post/backend/master-policy.json`, and otherwise waits in
`WAIT_MASTER_VERIFICATION`. This is separate from reviewed SRT seals.

Enhancement has no SRT, ASS or font dependency. `ENHANCED_READY` is a distinct
stage with a source/output hash-bound `.enhanced.mkv.ready.json`; orphan or
mismatched files are retained for investigation, never overwritten or trusted.
Enhanced video and preserved audio remain while the final stage is incomplete.
Later `--stage all` reuses the verified intermediate, then checks the SRT seal and
style. A synced template alone is not approval: its `.ass.ready.json` must have
`approved: true` and a matching `sha256`, otherwise `WAIT_STYLE_APPROVAL`; the
current approved84 template is already authorized and only needs safe transfer.
The approval's `fonts` dictionary binds every shipped font filename/SHA; the
Worker checks the complete font set and the approval's master hash. Fontconfig
and a real libass render must select the approved packaged font. E49 actually
selected `MiSansSubtitle-Demibold`. Future episodes may reuse this delegated84
design after Codex checks their actual picture/readability and seals the matching
new master approval; do not repeatedly request human approval for the same style.
Final reports bind the actual enhancement checkpoint's `master_sha256`, enhanced
video, reviewed SRT and approved style hashes. Existing finals must pass those
same bindings and current approval checks before reuse; changed/revoked approvals
block reuse, and orphan final partials are retained. Production readiness still requires the actual-master end-to-end
technical and sustained-performance record; the historical portable figures
below alone are insufficient. Shared artifact registration is now wired at source admission
and after all final-stage writes/releases. Compact ASS/hash evidence is archived
outside the registered cache target before registration, with per-file SHA checks,
under `post/reports/stages/<episode>/<master_sha256>/`. Final reports expose this
retained directory and final ASS path. Idle persistent polling releases the real
Worker flock so verified delivery cleanup can acquire it; active processing holds
it throughout its stage. Retired
master rejection now also includes a built-in known-retired hash for direct
backend calls and missing local policy files. A persistent Worker waits in
`WAIT_BACKEND_TECHNICAL` until machine-local `backend/production-ready.json`
binds this master and backend fingerprint to actual technical checks, measured
source fps and `performance_acceptable: true`. This is autonomous technical
validation evidence, not a restored human sample-signoff requirement.

### CUDA candidate and durable segment recovery

The new production candidate keeps official general Real-ESRGAN SRVGG restoration
and official vs-mlrt RIFE v4.6 ONNX in one persistent CUDA process. FP16/channels-last
SRVGG uses the general/WDN official models with DNI0.35, antialiased4x-to-native
reduction and35% blend. The original FP32 ONNX RIFE is faster than the tested
FP16 conversion on this T10; GPU tensor I/O avoids per-frame PNG exchanges and
CPU model input round trips. Neural Conv/GridSample/Resize operators must be
proved CUDA by the first real ORT profile; only tiny shape/scalar metadata may
remain on CPU. There is no CPU neural fallback. Physical GPU1 is checked as Tesla
T10 for RIFE. A bounded shared-restoration candidate uses GPU0 with a0.75GiB
PyTorch allocator cap,40-pixel tile overlap and a4GiB global reserve plus
remaining allocation allowance and128MiB buffer. This cap is not total process
VRAM: CUDA context/library overhead also consumes physical memory. Other users'
processes are never stopped or reconfigured. Memory pressure or CUDA OOM pauses
with exit76/WAIT_GPU and keeps completed segments. Sharing cannot promise zero
compute-latency impact. The all-on-A4000 candidate was rejected when its shared-memory gate could not be maintained.

`prepare_cuda_backend.py --configure-only --revise-candidate` creates a local
candidate launcher/profile; `--activate` selects it only after actual-master
technical tests. Pinned source/model hashes, exact isolated runtime versions and
restoration/RIFE/pipeline source hashes bind the backend fingerprint. Installation
does not manufacture production readiness. Standalone warm measurements are
approximately0.27s/restored source frame and0.046s/generated RIFE frame, but these
are **not** whole-pipeline or episode ETA. Record a longer continuous actual-master
fullchain test, including startup, frame I/O, encode and complete decode validation,
before publishing an ETA or admitting the full episode.

`segmented_backend.py` stores compressed10-second payloads (250 native frames;
last payload may be shorter). The exact25-to60 clock and real next-source-frame
lookahead preserve segment boundaries; scene cuts hold the preceding restored
frame instead of interpolating across shots. Completed segments bind the master,
backend fingerprint, frame range/count and compressed SHA. Retries reuse only
matching segments, fully decode owned interrupted segments, and retain unknown
or changed assets. Pending publication receipts recover crashes before/after the
exclusive hard-link publish without rerunning completed AI. Exit75 pauses for
transient disk pressure and keeps checkpoints. Full concatenations are decoded
and counted before replacing the segments. Source assets remain until the later
lossless-audio/verified-picture handoff. No full raw-frame dump is generated.

The CUDA path still decodes RGB8 for these models, so its10bit HEVC output must
not be claimed as an end-to-end10bit AI process. This is a documented precision
limit; evaluate gradients and edges on the actual master.

The actual source clock also needs validation after DaVinci export. E49 source
video originally begins at PTS0.099 while the new master normalizes picture
content to zero; six visually confirmed cuts from59s to2813s show an exact
-99ms picture mapping, whereas dialogue cross-correlation remains within2ms.
`subtitle_timing.py` requires a reviewed master/SRT/evidence-SHA-bound receipt
before shifting ONLY the derived ASS; the sealed original-input SRT and all
master video/audio samples remain unchanged. Final reports and reuse checks
bind that mapping, and approval/mapping changes during encoding block publication.
No unproved master audio/video retiming is applied.

Cross-filesystem concatenation avoids retaining two full picture copies on root.
Each original segment remains until the home concat has passed full decode/count,
source hash and compressed SHA checks; segments are then released, the concat is
copied bit-exact back to root, fsynced and hashed, and only then is the home copy
released. Hash-bound transfer receipts recover an interrupted copy without AI
reruns. Plans and ongoing checks reserve25GiB separately on each filesystem and
credit only current matching verified segment bytes. Unknown or replaced files
are retained. The conservative fallback budgets two same-volume full copies.

The audio handoff similarly journals the source/enhancement and exact owned
partial inode before encoding. Completed audio/publication interruptions recover
only after renewed probe, PCM equality and enhanced full-frame checks. A damaged
or decodable-but-short owned unverified partial is SHA-audited and isolated before
an audio-only retry. Verified/public/different-inode assets are never discarded.
No successful source handoff depends on a fresh full AI run.

The read-only `post_monitor.py` user service records30-second job/disk/physical
GPU telemetry to `reports/live_status.json` and `logs/telemetry.jsonl`. Worker READY
publication writes a persistent deduplicated PENDING_NOTIFICATION record. This
record does not claim that an unavailable cross-thread tool sent a message.
Finals remain until receipt-gated Windows acceptance.

### Retained portable GPU backend (fallback profile)


The retained earlier implementation uses official
[RIFE NCNN Vulkan](https://github.com/nihui/rife-ncnn-vulkan) and
[Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) portable models, with pinned
archive and installed-file SHA256s. It changes no system package, driver, CUDA,
or OCR venv. Its isolated venv uses NumPy/Pillow; FFmpeg streams decode and encode.
The Vulkan loader is restricted to the NVIDIA ICD, GPU index/name are checked,
and CPU/software fallback is rejected. Installed runtime configuration chooses
the mostly free Tesla T10, preserving A4000 service workloads.

Real-ESRGAN general x4plus restores source frames, reduces to1080p, and uses an
explicit35% blend to limit oversmoothing; this is a starting policy, not proof
of visual improvement on the replacement master. RIFE v4.6 creates60fps frames.
NCNN portable models consume8bitRGB: encoding HEVC10bit afterward does not
recover discarded source precision. Evaluate banding, faces and residual text
on the actual master before production; do not market this as full10bit AI.

Actual input packet PTS must be continuous25fps. Rational25→60 frame mapping
avoids cumulative time drift, including block overlap/end padding. The detector
holds the earlier frame across detected scene cuts, instead of blending shots;
threshold-based detection can still miss subtle cuts and requires real footage
inspection. There is no ordinary FFmpeg fps filter presented as AI.

The bounded batch holds at most55 source frames, with two-second payload and
five-frame overlap. Only one block's PNGs exists, never a whole episode frame
dump. A selected-binary initialization per output frame was measured too slow;
the batch path amortizes that overhead. Synthetic tests establish execution and
frame/codec contracts, not whole-episode throughput or picture quality.
Actual Tesla T10 synthetic execution produced60 frames from25 native1080p/25fps
frames in531.5 seconds, including6.3 seconds in the RIFE invocation. This was a
cold-start synthetic test, not the replacement episode or a visual-quality
approval. Restoration dominates this measurement; the present configuration is
not yet demonstrated practical for an hour-long episode. Record actual-master
quality and throughput before selecting the production restoration policy; do
not silently disable restoration or substitute animation models for live action.
The retained portable launcher by itself does not provide durable per-block recovery;
the segmented facade adds that recovery to either implementation.
Increasing the restore tile to512 was separately measured at28.2 seconds for one
native1080p synthetic frame; that experiment did not remove the bottleneck.

### Storage and retention

The logical root remains `~/qingqingwu/post`. On this server it points to the
user-owned `/var/tmp/kwjd-lotus/post` on the root filesystem; `incoming` and `final`
point to user-owned `~/qingqingwu/post-home/{incoming,final}` on `/home`. This
placement is local configuration, not a reason to hard-code it in Windows.
`/var/tmp` is temporary working storage, not an archival copy. Preserve original
source footage/DaVinci projects and fetch/hash verified finals. The latest user
authorization permits Windows to delete its generated master and only
`D:/ResolveWork/CacheClip` after confirmed upload and matching hashes at both ends;
it does not permit deleting all of `D:/ResolveWork`. No system cleanup policy changes.

Every filesystem must retain25GiB independently. Do not add the two free-space
values to claim one large file fits. Enhancement plans cap video40Mbps and budget
45Mbps plus8GiB for bounded scratch/logs. Final encoding caps video50Mbps and
reserves a separate output budget. No raw full-video intermediate is generated.
Budgets must be refreshed after the actual upload and before processing.
Scratch selection searches actual user-owned writable disk mounts, tests write
access and reserves the full8GiB independently. RAM/virtual mounts are excluded.
It automatically switches eligible scratch placement if the preferred disk is
too full. Output volumes are still independently checked; if no safe placement
is available the job waits for disk rather than starting an unbounded task.

Linux stages are unattended after confirmed transfer; do not repeatedly ask
Windows for ordinary technical choices. `linux_stage_handoff.py` preserves the
mixed track as24bit FLAC at its native sample rate/channels, compares decoded
s32 PCM SHA256s against the master, fully decodes the enhanced picture and saves
the source probe/timing/hash evidence. Only then, while holding the Worker lock,
may the exact matching `incoming/<ep>.clean-master.ext` copy be released. An
unmatched same-name re-export is never deleted. A failed next stage retains the
verified enhancement, lossless audio and recovery metadata.
The latest Windows restriction also requires `post/receipts/<ep>.upload.json`
with matching `episode`/`sha256`, `verified: true` and `local_master_deleted: true`
before the Linux master release. Otherwise processing retains that incoming copy.
Release isolates a file with an atomic same-filesystem rename, hashes the private
object and only then deletes it; a racing new upload is preserved. Private release
journals recover interrupted isolation without losing track of large artifacts.

Final encoding uses the preserved mixed track, retaining its source timing
offset and native sample rate. It does not use `-shortest`: enhanced/final full
decoded frame counts must match, including the tail. After full final-video
decode, HEVC10bit1080p60/FLAC checks, decoded PCM
equivalence and immutable publication/reporting, the matching enhanced video
and separate audio may be released with a stage journal. Compact source probes,
hash receipts, ASS and human evidence remain. The unique final is never removed
by stage cleanup; it awaits the verified Windows final-delivery receipt.

At3517.56s, a one-copy enhancement allowance is approximately26.43GiB on
root; the external concat separately reserves approximately18.43GiB plus metadata
on home, and is removed before the final's25.57GiB allowance is needed. The
same-volume fallback reserves approximately44.86GiB for both copies plus scratch.
The received8.04GiB HEVC master already occupies home and must not be counted
as available space. Refresh actual per-filesystem free bytes before every stage
and while writing; the retired76.09GiB DNxHR does not fit with the reserve. These
are upper planning allowances, not expected file sizes or throughput promises.

Process episodes serially through final Windows delivery. Windows owns the shared
`scripts/episode_lifecycle.py` receipt/cleanup contract and `qqw fetch-final`;
Linux must reuse that module rather than invent a second receipt schema. Before
advancing to the next episode, require a receipt proving Windows saved and
SHA256-verified and quality-checked the final bytes, then finish cleanup of only
explicitly inventoried remaining workflow artifacts. This final delivery gate
is separate from the earlier technically verified stage releases. File presence alone
does not authorize cleanup or advancement. Deleting the server final copy also
requires that matching verified Windows receipt. Preserve original source video,
DaVinci projects, sealed SRTs, ASS templates/fonts, human review evidence and the
Windows final. Both supported local storage configuration formats must agree:
`post/backend/storage.json` uses `zones`, `.kwjd/storage.json` uses `areas`, with
schema_version1 and exact absolute zone targets. Only those configured top-level
links are admitted; links inside an artifact remain forbidden. No actual episode
or stage cleanup has been run on E49 during implementation or tests.

Keep internal IDs such as `S01E49`. Windows delivery names derive the story's
ordinal and title from `InputName`, not the season episode number: for example
`第一回 家门遭横祸，俊才跌凡尘.mkv`. Linux must follow the shared naming contract.

The orchestration and ASS stages exist. GPU inference must be **measured and implemented on the actual Linux machine**, not guessed by ChatGPT or faked via FFmpeg fps interpolation.

## Interface
Create an executable file on the remote machine:

`~/qingqingwu/post/backend/enhance.sh INPUT_MASTER OUTPUT_ENHANCED EPISODE`

- INPUT_MASTER: direct DaVinci HEVC10bit master with PCM24bit/48kHz stereo mixed audio; user-accepted hard-subtitle remnants need no further removal.
- OUTPUT_ENHANCED: intermediate MKV, **video 1920x1080, constant 60 fps, same presentation duration (tolerance 0.5 seconds), no new subtitle burn-in; user-accepted original fragments retained**.
- EPISODE: any matching first-season ID (`S01E01`, `S01E49`, ...), dynamically discovered from queued jobs.
- Exit nonzero on failure. Never replace an already validated final video.
- Worker uses enhanced video for picture and the sample-identical preserved master mix for audio; it burns approved ASS after all AI processing.

## Model selection to validate
- Decode with FFmpeg/VapourSynth, stream frames. No whole-episode PNG dumps.
- For interpolation prefer RIFE via vs-mlrt/CUDA/TensorRT (if actually compatible), preserving exact timebase. Detect scene cuts to prevent ghosting at audience/stage switches.
- For real-life upscaling compare appropriate Real-ESRGAN/general restoration with **no upscale** baseline on a 30–60s representative sample. If the mastered content is already good 1080p, bypass harmful upscale. Explicitly test Magic Mask edges and BGM background motion.
- Target order to benchmark: AI repair / upscale first → RIFE second vs RIFE first → upscale second. Choose the faster/better measured path, not a hard-coded dogma.
- Check actual hardware, memory, driver and backend compatibility. The prepared portable backend uses the physically tested Tesla T10 via NVIDIA Vulkan and NVENC, protecting existing A4000 service workloads. CUDA is not required by these NCNN binaries. Do not mandate a dual-GPU pipeline if slower than one.
- Keep audio sync. No audio re-encoding during AI video inference.
- Final encode: libass + correct font files **after AI** → HEVC NVENC. **Audio from DaVinci mixed master must become lossless 24-bit FLAC at >=48 kHz** (preserve native 96 kHz+ when present), not AAC or an ordinary `-c:a copy` passthrough. Output MKV and verify codec/sample-rate/bits-per-raw-sample with ffprobe. See docs/HIRES.md.

## Operational requirements
- No sudo assumed; use venv, user-owned binaries, installed compatible CUDA.
- Do not touch or disrupt existing OCR process/environment.
- Store intermediate under post/cache/S01Exx, logs under post/logs.
- Compare visuals to DaVinci's previous in-editor interpolation, check faces, hands and scene cuts. Measure speed, VRAM and disk.
- Current user waiver removes the enhancement sample-signoff gate. Technical checks remain; final ASS/font approval is independent.
- Reserve >=25 GiB **after estimated peak usage**. Refresh df; previous /home usage was 85.4% of 737.16GB.
- A possible two-pass intermediate+sub burn exists in current Worker to make recovery simpler. If implementing fused one-pass vs-mlrt→libass→NVENC, preserve same interface or adjust Worker with tests.

## Important honesty
On a fresh machine the Worker reports WAIT_BACKEND until the real backend is installed. The prepared NCNN implementation has synthetic execution evidence, but actual-master quality and throughput remain to be measured. Never replace model inference with a simple FFmpeg fps filter and call it AI enhancement.

## Season profile

Read `config/seasons/S01.json` (cut starts after 30s, tail first white frame, OCR horizontal full-width/vertical auto). These rules apply across the whole first season, not just E49–E55. The existing in-progress E49–E55 items are merely the initial adoption set. Derive episode IDs from source file names / job files, do not code fixed episode ranges.
