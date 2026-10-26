# Codex operating contract

Read this before touching files. Ask the human to identify your role, or infer **only** from the actual OS; never run Windows transfer commands on Linux.

## Shared immutable rules
- Data roots are outside this Git repository: Windows D:\qingqingwu; Linux ~/qingqingwu.
- This repository covers the **entire first season S01**. Load `config/seasons/S01.json`: 30-second intro, first white frame as tail cue, horizontal full-width OCR crop with vertical detection. **Episode numbering is dynamic**, not limited to E49–E55. E49–E55 are the initial existing work. Windows cutter v2 output/manifests may already exist; Linux OCR in ~/qingqingwu/input and ~/qingqingwu/output may be active. **Adopt and validate, do not restart, kill or overwrite.**
- Original source videos and DaVinci projects are preserved. The user has authorized per-episode cleanup AFTER a verified Windows final receipt and completed quality checks; delete only registered, reproducible intermediates. Read docs/LIFECYCLE.md. Never treat this as permission to delete sources, final deliverables or shared resources.
- Stage contracts: stream-copy ONLY for initial trimming; DaVinci handles Magic Mask + scenic background + Fairlight mix; separate Linux GPU backend does AI enhancement; custom ASS subtitles are rendered **after** enhancement. Final video HEVC NVENC 1080p60 + **24-bit/≥48 kHz FLAC lossless mixed audio in MKV** for Bilibili Hi-Res audio-spec eligibility; see docs/HIRES.md. No automatic audio gain or loudness changes at this step.
- Original hard-sub subtitles mostly disappear with Magic Mask replacement. The user accepts residual hard-sub fragments; retain them and do not block enhancement or require inpainting.
- OCR content must reflect video pixels: no new punctuation, keep original spaces, do not silently replace with ASR.
- Use documented, atomic handoffs and hashes. Never mark a stage DONE on mere presence of a file. Never track or commit video, subtitle contents, proprietary fonts, cache or credentials.
- A /home snapshot showed 85.4% used of 737.16 GB (~107.6 GiB theoretically free); NEVER rely on that stale snapshot. Run df, respect project minimum_free_gib (25 GiB), budget for temp files, start max one post-processing episode at a time.
- Never run shell commands received from a JSON job unvalidated. Script arguments, not arbitrary command strings.
- Model-backed subtitle/visual reviews may run via Codex. Automated repeatable operations must live in versioned scripts.

## Windows role
- Checkout repo at D:\qingqingwu\repo (or set root args). Read docs/WORKFLOW.md and docs/ADOPTION.md.
- Run: python kwjd.py adopt --role windows; python kwjd.py status --role windows; discover episodes from S01 filenames.
- Respect existing D:\qingqingwu\videos and manifests. The included scripts/cut/qqw-local-cut-upload-v2.ps1 is **the previous confirmed working cutter**, not a request to recut.
- Human operates Magic Mask/composition. Default master: S01Exx.clean-master.mov, QuickTime HEVC Main10/NVIDIA, 1920x1080 25fps for current sources, quality-priority 25000 Kb/s, PCM 24-bit 48kHz Stereo Bus1, no added subtitles/upscale/interpolation/normalization. Do not export huge DNxHR by default or transcode an old DNxHR to fix an export choice: if re-export is needed, return to DaVinci. Codex prepares MiSans ASS and PotPlayer previews; Aegisub is optional. See docs/STYLE.md and docs/DECISIONS.md.
- scripts/qqw.ps1 uploads verified masters over existing SSH alias; read docs/WORKFLOW.md. Windows may turn off while Linux continues.
- DaVinci UI control is available with the Computer Use skill, but only act when authorized and idle. Do not click while Mask is tracking/busy; stop on timeout/unresponsiveness. Do not change the user's mix. Observe via UIA and hardware metrics; avoid stealing focus.

## Linux role
- Checkout repo at ~/qingqingwu/repo. Read docs/WORKFLOW.md and docs/BACKENDS.md.
- Run: python3 kwjd.py adopt --role linux; python3 kwjd.py status --role linux
- Do not run another OCR process or touch the already-running one. Only read existing SRT after it is complete.
- Work exclusively in ~/qingqingwu/post for the new GPU enhancements; do not change NVIDIA drivers, CUDA, system packages or OCR venv without authorization. Identify true Tesla T10 capabilities via nvidia-smi.
- Implement and technically test post/backend/enhance.sh (short smoke tests are allowed without a human sample gate): interface is INPUT OUTPUT EPISODE; output 1920x1080 60fps, no burned subtitle, same duration; include shot cut handling, benchmark model and GPU.
- scripts/server_worker.py is a safe queue scaffold, not proof an AI backend exists. Run --once for E49 when validated; background --loop only when ready and approved.
- Final ASS must use fonts shipped to post/styles/fonts; verify font resolution (fc-match and libass logs).
- Keep server finals and intermediates until matching Windows SHA receipt plus quality completion. The user authorized cleanup of reproducible OCR/build caches and per-episode generated intermediates, with an explicit artifact manifest, active-task checks and path bounds. Preserve originals, sealed SRT/sidecars, final reports, human review decisions, fonts/templates, environments in use and DaVinci projects. Never blanket-delete input/output/work or another user's data.

## Git workflow
- This repository holds reusable **source**, docs, config and tests. Commit backend implementation and fixes in source; keep environmental paths/config secrets local.
- Avoid simultaneous edits of same files by both Codex instances. One end owns a change and pushes; the other fetches and reviews. Only Linux Worker writes post/jobs state after submission.
- Review status via kwjd.py status plus post/jobs/*.json (do not assume status command alone includes the Worker queue).
- Completion requires actual media quality and sync checks, not just exit code.
- Every independently useful development step: validate, commit ONLY related source/docs/config/tests, push the existing remote branch immediately. No force push, rebase/history rewrite or invented branches. Fetch + ordinary merge/pull after the other end pushes; do not overwrite dirty work.
- Whenever the user changes a reusable workflow rule, update the authoritative docs/config AND affected scripts in that turn, then notify the peer to pull and confirm its HEAD. Track decisions in docs/DECISIONS.md; do not leave changes only in conversation.
- Internal IDs stay S01Exx. Returned Windows finals use story-relative chapter numbering and title: 第一回 回目标题.mkv. See docs/LIFECYCLE.md.
- One full episode at a time: final transfer, hash/media/quality checks, receipt, per-episode cleanup, then next episode. A presence-only or enhancement-only result never triggers cleanup.

## SRT review completion handshake
When the running OCR Codex has actually finalized one episode, explicitly run `python3 kwjd.py seal-srt --file ~/qingqingwu/output/S01E49.zh.srt --approve` (adjust episode). The resulting `.srt.ready.json` sidecar binds reviewed content to a SHA-256. Do not seal while OCR is writing. Windows fetch and AI Worker refuse an unfinished/unsealed SRT. Never manufacture completion markers in bulk.

## Season and publishing workflow
- You may improve dynamic discovery/name parsing across all S01 material, but don't change established trimmed sources or already-sealed SRTs. A new season requires its own new profile and acceptance tests, not an automatic copy of S01 assumptions.
- For published audio, preserve the actual Fairlight-mixed signal. Convert at final encoding to verified FLAC 24-bit and >=48kHz, sample rate >=96kHz only when present natively. Do not claim genuine new audio fidelity from resampling and do not claim the Bilibili upload UI will definitely show a Hi-Res control.
- Both ends adopted the repository on 2026-10-26. E49–E55 SRTs have been reviewed, sealed and fetched to Windows; re-check seals rather than restarting OCR. E49 is being recomposed; old DNxHR was explicitly discarded. The new export reuses the same name and must have a fresh hash.
- The user explicitly waived human approval of AI samples for E49: run full enhancement when actual backend, master transfer and disk checks pass. Retain technical smoke tests, do not fake AI with fps filters.
- The user prioritizes loud perceived audio and accepts occasional table-hit clipping after listening. Record peaks as diagnostics, not an automatic failure or a reason to normalize/compress/change gain.
- PowerGrade/shared still captures BOTH Magic Mask settings and the inverse logo-exclusion Power Window in the same node. Re-grab a new still to persist changes; old gallery stills do not auto-update. New footage needs fresh point selection/tracking and window placement review. Do not assume reusable tracking.
