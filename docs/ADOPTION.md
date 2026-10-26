# Adopt existing work / 原地接管

**Do not move or regenerate the work that already exists.** The checkout should live as a subdirectory of the current data root, not replace it.

### Windows
```powershell
cd D:\qingqingwu
git clone https://github.com/MOPELotus/Kwjd.git repo
cd repo
python kwjd.py adopt --role windows
python kwjd.py status --role windows
```
- The legacy cutter outputs `D:\qingqingwu\videos\*.mp4`, `manifests\S01E49.json`, etc. The scanner finds these and records whether the existing v2 manifest claims validated stream-copy. It does NOT rerun anything.
- Source is `D:\K坑王驾到S1` and contains the entire S01. The existing v2 white-frame cutter now dynamically selects every matching **S01** file, while `-OnlyEpisode 49` explicitly limits a run. This does not select any other season.
- If a previous successful cut exists, use it. Launching `cut` yourself runs existing v2 logic which skips video + manifest unless forced. No automated recuts on startup.
- After E49 OCR finished, `fetch-srt` downloads the remote file. Since its existence is not proof that Codex review is done, **ask remote Codex to mark review complete before fetching**.
- Once you export `D:\qingqingwu\masters\S01E49.clean-master.mov`, use `send-master`, then `submit`.

### Linux
```bash
cd ~/qingqingwu
git clone https://github.com/MOPELotus/Kwjd.git repo
cd repo
python3 kwjd.py adopt --role linux
python3 kwjd.py status --role linux
df -h ~
```
- `input/S01E49.mp4` … `S01E55.mp4` are the existing trimmed video uploads for OCR. Do not overwrite or reupload.
- `output/S01E49.zh.srt` … `S01E55.zh.srt` are produced by the **already running** OCR Codex; do not kill that process, reset its environment, or assume unfinished files are valid.
- The new project creates ONLY `post/` (incoming, styles, jobs, cache, logs, final, reports, backend).
- Another running Codex's internal logs, venv, SRT temp files and process state are not imported into Git; they're left in place.
- `adopt` writes a non-authoritative snapshot `.kwjd/adoption-linux.json`. Live `status` re-scans the filesystem.
- To bring up queued AI jobs, implement and validate the backend per BACKENDS.md first. Never mark AI output done from file existence alone.

### Multiple projects later
Current profile is the **entire first season** (`config/seasons/S01.json`), not a seven-episode project. Episodes are discovered from existing S01 files; `bootstrap_episodes` lists E49–E55 only to make existing in-progress work visible. The cutter's known-good first-season crop/white-frame logic applies to other S01 episodes too. A future new season should get its own profile and cutter validation, not silently reuse the S01 rules.

### Restarts
- `status` is side-effect free.
- `adopt --refresh` intentionally replaces only its own discovery snapshot, not media or job state.
- Job files live in `post/jobs` and are written atomically. Running `server_worker.py --once` handles the first eligible job. `--loop` can remain alive across SSH disconnect when started via tmux/systemd user.
- Unexpected `FAILED` job states require investigation; do not automatically requeue and destroy evidence.

### How to bridge an already-running OCR Codex
Do NOT send a second OCR command. In that existing remote Codex conversation, after a particular episode has completed final review, ask it to run:

```bash
cd ~/qingqingwu/repo
python3 kwjd.py seal-srt --file ~/qingqingwu/output/S01E49.zh.srt --approve
```

Repeat for E50–E55 only as each one actually finishes. Do not seal incomplete results. The sidecar `*.srt.ready.json` is required by both the Windows fetch command and the Linux Worker; it protects against an unfinished SRT being picked up by another process.

## Dynamic episode naming

- Full original filenames can be e.g. `坑王驾到S01E01. 2016-09-25丨探地穴（一）....mp4`; use the `S01E01` stable identifier for intermediate/remote files and retain the original title in the local trimmed video.
- The controller finds `S01E##` (or 3-digit extension) from existing files under `videos/`, `masters/`, `subtitles/`, etc.; the Linux worker scans matching `post/jobs/S01E*.json` and will not assume E49–55 is the complete season.
- Examples: `pwsh ./scripts/qqw.ps1 -Command cut -Episode 8`; `pwsh ./scripts/qqw.ps1 -Command send-master -Episode 8` when E08 has a clean master.
- The original E49–55 videos and already-running SRTs are untouched; `adopt` remains non-destructive. Never bulk-create reviewed-SRT seals from file presence alone.

## 现行约定优先

E49–55已在2026-10-26封存取回，后续复用校验，不重跑。直接HEVC10bit+PCM导母片，同名重导以新SHA区分；残留字幕/拍桌峰值用户接受，E49免样片人工审批；Codex做MiSans样式，增强先行最后烧录；第X回 回目标题命名、回传验收再清上集、每步Git同步，详见[工作流](WORKFLOW.md)、[决策](DECISIONS.md)、[回传清理](LIFECYCLE.md)。
