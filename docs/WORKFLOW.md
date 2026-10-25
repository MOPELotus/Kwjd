# End-to-end workflow / 双端生产线

## Stages
1. **Original → trimmed** (Windows, FFmpeg stream copy only): first 30s removed, optionally a little more to land on a valid keyframe; tail ends BEFORE first white-frame cue (extra early cut permitted, no tail residue). Confirmed cutter is in scripts/cut/qqw-local-cut-upload-v2.ps1. Assets/manifests remain D:\qingqingwu\videos and manifests. Already completed stages **must not be rerun**.
2. **Trimmed → OCR SRT** (Linux): the current E49–E55 videos are already uploaded to ~/qingqingwu/input/; existing remote Codex is processing the SRTs in ~/qingqingwu/output/. Do not restart. VideOCR crop is nearly full **horizontal** width, vertical band detected from diverse clips; keep short/long/two-line subtitles. No invented punctuation, preserve visible spaces, reject audience T-shirt logos via geometry/image review.
3. **Trimmed → clean master** (Windows, human): DaVinci Studio Magic Mask with selected foreground person, scenic+BGM background below, Fairlight background -14dB and show +10dB **as existing project track gain choices** (inspect main bus for clipping; these are not LUFS standards). Remove any hard-sub fragments attached to the table. Export at native frame rate/resolution, no built-in AI upscale, optical flow, or new subtitles. Name S01E49.clean-master.mp4 (or MKV/MOV). Keep original mixed audio.
4. **Design ASS once** (Windows, human): fetch E49 .zh.srt after OCR complete; use Aegisub to create D:\qingqingwu\styles\template.ass; preview representative longest/two-line subtitles at 1920x1080. Put exact approved font files in styles\fonts (do NOT add to Git). Copy styles to remote post/styles; verify fontconfig resolution on remote. SRT→ASS conversion uses kwjd.py make-ass, preserving content/timing.
5. **Send master** (Windows automation): verify DaVinci exported and file is stable. scripts/qqw.ps1 send-master -Episode 49 uploads to ~/qingqingwu/post/incoming with SHA-256 and atomic publish. Don't transmit original video again.
6. **Queue remote**: Windows scripts/qqw.ps1 submit -Episode 49 creates post/jobs/S01E49.json. Worker checks master, OCR SRT, template, backend and disk. Missing dependencies become WAIT_*; no regeneration.
7. **Enhance** (Linux, independent tested backend): VapourSynth/vs-mlrt + appropriate live-action upscaling if source benefits, plus scene-cut-aware RIFE to exactly 60fps with unchanged playback duration. Never feed subtitles to AI. User must approve a 30-60s sample before full E49.
8. **Burn + final encode** (Linux): convert SRT to ASS from approved template; FFmpeg libass with explicit fontsdir AFTER AI. HEVC NVENC target 1080p60; take the mixed audio from DaVinci master and encode as **24-bit lossless FLAC / at least 48kHz** (not AAC), in MKV for Bilibili Hi-Res-eligible audio format. Do not change the existing Fairlight mix levels. Worker verifies real codec, bit depth and sample rate (see docs/HIRES.md). GPU enhancement may use an intermediate file and final encode; production Codex may optimize to fused streaming if validated.
9. **Fetch + validate** (Windows): scripts/qqw.ps1 fetch-final -Episode 49; SHA-256 verify. User checks frame edges, ghosting, typography, lip-sync and audio peaks. Mark completed only after approval.

## Progress & current facts
- Project profile: **all available S01 episodes**, discovered from file names; **S01E49–S01E55** are the currently adopted seven episodes, previously measured at 5.10 GiB. Other stories in S01 can be processed later without changing the season rules.
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

## Season-wide reusable profile & final audio

- `config/project.json`: season is `S01`; no fixed end episode, only `bootstrap_episodes` to display existing E49–E55. The actual episode list is dynamically discovered from filenames and job files. See `config/seasons/S01.json` for the shared cutter/OCR rules.
- The verified original cutter was generalized to **all S01 files** in the source folder. For targeted runs, always pass `-Episode 8` etc.; do not blindly cut/upload the entire season unless intended. New seasons require their own tested profile.
- Final audio targets the Bilibili Hi-Res **upload specification**: lossless FLAC, 24-bit, minimum 48 kHz, default MKV+HEVC video. If the original DaVinci master is already >=96 kHz, the Worker retains its actual rate. These settings cannot restore lost audio detail or guarantee a platform Hi-Res toggle. See `docs/HIRES.md`.
- For uploading to Bilibili, check that the current account/upload UI offers `Hi-Res 无损音质`, choose it when available, and verify after publishing. If MKV acceptance changes, test a short sample or implement ALAC/MOV compatibility; **do not fall back to AAC and claim Hi-Res**.
