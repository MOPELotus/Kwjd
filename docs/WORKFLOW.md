# End-to-end workflow / 双端生产线

## Stages
1. **Original → trimmed** (Windows, FFmpeg stream copy only): first 30s removed, optionally a little more to land on a valid keyframe; tail ends BEFORE first white-frame cue (extra early cut permitted, no tail residue). Confirmed cutter is in scripts/cut/qqw-local-cut-upload-v2.ps1. Assets/manifests remain D:\qingqingwu\videos and manifests. Already completed stages **must not be rerun**.
2. **Trimmed → OCR SRT** (Linux): S01E49–S01E55 videos already uploaded to ~/qingqingwu/input/; existing remote Codex is processing the SRTs in ~/qingqingwu/output/. Do not restart. VideOCR crop is nearly full **horizontal** width, vertical band detected from diverse clips; keep short/long/two-line subtitles. No invented punctuation, preserve visible spaces, reject audience T-shirt logos via geometry/image review.
3. **Trimmed → clean master** (Windows, human): DaVinci Studio Magic Mask with selected foreground person, scenic+BGM background below, Fairlight background -14dB and show +10dB **as existing project track gain choices** (inspect main bus for clipping; these are not LUFS standards). Remove any hard-sub fragments attached to the table. Export at native frame rate/resolution, no built-in AI upscale, optical flow, or new subtitles. Name S01E49.clean-master.mp4 (or MKV/MOV). Keep original mixed audio.
4. **Design ASS once** (Windows, human): fetch E49 .zh.srt after OCR complete; use Aegisub to create D:\qingqingwu\styles\template.ass; preview representative longest/two-line subtitles at 1920x1080. Put exact approved font files in styles\fonts (do NOT add to Git). Copy styles to remote post/styles; verify fontconfig resolution on remote. SRT→ASS conversion uses kwjd.py make-ass, preserving content/timing.
5. **Send master** (Windows automation): verify DaVinci exported and file is stable. scripts/qqw.ps1 send-master -Episode 49 uploads to ~/qingqingwu/post/incoming with SHA-256 and atomic publish. Don't transmit original video again.
6. **Queue remote**: Windows scripts/qqw.ps1 submit -Episode 49 creates post/jobs/S01E49.json. Worker checks master, OCR SRT, template, backend and disk. Missing dependencies become WAIT_*; no regeneration.
7. **Enhance** (Linux, independent tested backend): VapourSynth/vs-mlrt + appropriate live-action upscaling if source benefits, plus scene-cut-aware RIFE to exactly 60fps with unchanged playback duration. Never feed subtitles to AI. User must approve a 30-60s sample before full E49.
8. **Burn + final encode** (Linux): convert SRT to ASS from approved template; FFmpeg libass with explicit fontsdir AFTER AI. HEVC NVENC target 1080p60 and keep mixed audio from DaVinci master. Worker currently uses a validated intermediate-enhanced-file interface and a final encode pass; production Codex may optimize to fused streaming if tested and correct.
9. **Fetch + validate** (Windows): scripts/qqw.ps1 fetch-final -Episode 49; SHA-256 verify. User checks frame edges, ghosting, typography, lip-sync and audio peaks. Mark completed only after approval.

## Progress & current facts
- Seven episodes, **S01E49–S01E55**, total trimmed-source set previously measured 5.10 GiB.
- Windows confirmed cutter v2 selecting first of two white-frame groups 4.84s apart. It has been used successfully. Do not recut without evidence.
- Remote subtitles are in progress, NOT yet presumed complete. A SRT file's presence does not guarantee OCR/Codex review is done.
- SSH uses Windows alias TodayEatUbuntu-7K62, no FRP transit. SCP -O test ≈4.73 MiB/s up, 1.07 MiB/s down for an 8 MiB sample. Measure bulk transfer again if necessary.
- /home most recent reported usage 85.4% of 737.16 GB. Recalculate free disk at runtime; 25 GiB minimum reserve. One big episode at a time; never cache PNG image sequences for the whole episode.

## Commands
Windows:
- python kwjd.py adopt --role windows
- pwsh ./scripts/qqw.ps1 -Command status
- pwsh ./scripts/qqw.ps1 -Command cut -Episode 49  **only if not yet cut**
- pwsh ./scripts/qqw.ps1 -Command upload-cut -Episode 49  **only if not yet uploaded**
- pwsh ./scripts/qqw.ps1 -Command fetch-srt -Episode 49
- pwsh ./scripts/qqw.ps1 -Command sync-style
- pwsh ./scripts/qqw.ps1 -Command send-master -Episode 49
- pwsh ./scripts/qqw.ps1 -Command submit -Episode 49
- pwsh ./scripts/qqw.ps1 -Command fetch-final -Episode 49

Linux:
- python3 kwjd.py adopt --role linux
- python3 kwjd.py status --role linux
- python3 kwjd.py doctor --role linux
- python3 kwjd.py validate-srt ~/qingqingwu/output/S01E49.zh.srt
- python3 kwjd.py make-ass --srt ~/qingqingwu/output/S01E49.zh.srt --template ~/qingqingwu/post/styles/template.ass --output ~/qingqingwu/post/cache/S01E49/S01E49.ass
- python3 scripts/server_worker.py --once  (only when backend ready)
- python3 scripts/server_worker.py --loop --poll 90 (run under tmux/systemd-user after test)

## What can be automated?
A local Codex watches for finished DaVinci output; do not upload until the file is stable and validated. The scripted uploader/worker can continue independently without a live Codex chat. Windows can shut down during server processing. On next Windows boot, fetch verified final and then start the next DaVinci master. Both ends can concurrently work on DIFFERENT stages/episodes.

## Avoiding rework
Never rerun OCR to process a clean-master: OCR timecodes belong to the already trimmed source and are retained. Do not apply another 30s offset. Do not recode just to cut source. All later image composition, AI enhancement and subtitle burn-in are inherently pixel transforms and require encoding.

## Mandatory OCR completion seal
After the existing Linux Codex finishes visual/text review and validates each subtitle, run:

```bash
python3 ~/qingqingwu/repo/kwjd.py validate-srt ~/qingqingwu/output/S01E49.zh.srt
python3 ~/qingqingwu/repo/kwjd.py seal-srt --file ~/qingqingwu/output/S01E49.zh.srt --approve
```

This writes `S01E49.zh.srt.ready.json` with SHA-256 and count. Only do this **after** Codex confirms OCR is complete. Worker and Windows fetch will refuse unsealed or changed SRT. Run once per finished episode, never during OCR generation.
