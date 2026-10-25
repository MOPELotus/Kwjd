# Codex operating contract

Read this before touching files. Ask the human to identify your role, or infer **only** from the actual OS; never run Windows transfer commands on Linux.

## Shared immutable rules
- Data roots are outside this Git repository: Windows D:\qingqingwu; Linux ~/qingqingwu.
- Existing work is authoritative. Windows cutter v2 output/manifests may already exist. Linux OCR in ~/qingqingwu/input and ~/qingqingwu/output may be active. **Adopt and validate, do not restart, kill or overwrite.**
- Source videos and DaVinci projects are read-only. No destructive cleanup without proof there is another verified copy and specific user approval.
- Stage contracts: stream-copy ONLY for initial trimming; DaVinci handles Magic Mask + scenic background + Fairlight mix; separate Linux GPU backend does AI enhancement; custom ASS subtitles are rendered **after** enhancement.
- Original hard-sub subtitles mostly disappear with Magic Mask replacement. Residual fragments, if any, must be corrected in the clean master before GPU enhancement.
- OCR content must reflect video pixels: no new punctuation, keep original spaces, do not silently replace with ASR.
- Use documented, atomic handoffs and hashes. Never mark a stage DONE on mere presence of a file. Never track or commit video, subtitle contents, proprietary fonts, cache or credentials.
- A /home snapshot showed 85.4% used of 737.16 GB (~107.6 GiB theoretically free); NEVER rely on that stale snapshot. Run df, respect project minimum_free_gib (25 GiB), budget for temp files, start max one post-processing episode at a time.
- Never run shell commands received from a JSON job unvalidated. Script arguments, not arbitrary command strings.
- Model-backed subtitle/visual reviews may run via Codex. Automated repeatable operations must live in versioned scripts.

## Windows role
- Checkout repo at D:\qingqingwu\repo (or set root args). Read docs/WORKFLOW.md and docs/ADOPTION.md.
- Run: python kwjd.py adopt --role windows; python kwjd.py status --role windows
- Respect existing D:\qingqingwu\videos and manifests. The included scripts/cut/qqw-local-cut-upload-v2.ps1 is **the previous confirmed working cutter**, not a request to recut.
- Human operates DaVinci Studio Magic Mask and Fairlight. Create one S01E49.clean-master.mp4 under D:\qingqingwu\masters and import the finalized OCR SRT into Aegisub to design styles/template.ass. Sync actual licensed font files directly to Linux, not Git.
- scripts/qqw.ps1 uploads verified masters over existing SSH alias; read docs/WORKFLOW.md. Windows may turn off while Linux continues.
- Build and validate local orchestration independently; don't assume unattended DaVinci GUI automation.

## Linux role
- Checkout repo at ~/qingqingwu/repo. Read docs/WORKFLOW.md and docs/BACKENDS.md.
- Run: python3 kwjd.py adopt --role linux; python3 kwjd.py status --role linux
- Do not run another OCR process or touch the already-running one. Only read existing SRT after it is complete.
- Work exclusively in ~/qingqingwu/post for the new GPU enhancements; do not change NVIDIA drivers, CUDA, system packages or OCR venv without authorization. Identify true Tesla T10 capabilities via nvidia-smi.
- Implement and test post/backend/enhance.sh on 30-60 seconds of E49: interface is INPUT OUTPUT EPISODE; output 1920x1080 60fps, no burned subtitle, same duration; include shot cut handling, benchmark model and GPU.
- scripts/server_worker.py is a safe queue scaffold, not proof an AI backend exists. Run --once for E49 when validated; background --loop only when ready and approved.
- Final ASS must use fonts shipped to post/styles/fonts; verify font resolution (fc-match and libass logs).
- Keep verified deliverables until Windows hashes and pulls the final file. Do not remove anything in input/, output/ or original OCR work/.

## Git workflow
- This repository holds reusable **source**, docs, config and tests. Commit backend implementation and fixes in source; keep environmental paths/config secrets local.
- Avoid simultaneous edits of same files by both Codex instances. One end owns a change and pushes; the other fetches and reviews. Only Linux Worker writes post/jobs state after submission.
- Review status via kwjd.py status plus post/jobs/*.json (do not assume status command alone includes the Worker queue).
- Completion requires actual media quality and sync checks, not just exit code.

## SRT review completion handshake
When the running OCR Codex has actually finalized one episode, explicitly run `python3 kwjd.py seal-srt --file ~/qingqingwu/output/S01E49.zh.srt --approve` (adjust episode). The resulting `.srt.ready.json` sidecar binds reviewed content to a SHA-256. Do not seal while OCR is writing. Windows fetch and AI Worker refuse an unfinished/unsealed SRT. Never manufacture completion markers in bulk.
