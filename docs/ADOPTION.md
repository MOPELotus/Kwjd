# Adopt existing work / 原地接管

**Do not move or regenerate the work that already exists.** The checkout should live as a subdirectory of the current data root, not replace it.

### Windows
\`\`\`powershell
cd D:\qingqingwu
git clone https://github.com/MOPELotus/Kwjd.git repo
cd repo
python kwjd.py adopt --role windows
python kwjd.py status --role windows
\`\`\`
- The legacy cutter outputs \`D:\qingqingwu\videos\*.mp4\`, \`manifests\S01E49.json\`, etc. The scanner finds these and records whether the existing v2 manifest claims validated stream-copy. It does NOT rerun anything.
- Source is \`D:\K坑王驾到S1\` and contains other unrelated episodes; cutter restricts to E49..E55.
- If a previous successful cut exists, use it. Launching \`cut\` yourself runs existing v2 logic which skips video + manifest unless forced. No automated recuts on startup.
- After E49 OCR finished, \`fetch-srt\` downloads the remote file. Since its existence is not proof that Codex review is done, **ask remote Codex to mark review complete before fetching**.
- Once you export \`D:\qingqingwu\masters\S01E49.clean-master.mp4\`, use \`send-master\`, then \`submit\`.

### Linux
\`\`\`bash
cd ~/qingqingwu
git clone https://github.com/MOPELotus/Kwjd.git repo
cd repo
python3 kwjd.py adopt --role linux
python3 kwjd.py status --role linux
df -h ~
\`\`\`
- \`input/S01E49.mp4\` … \`S01E55.mp4\` are the existing trimmed video uploads for OCR. Do not overwrite or reupload.
- \`output/S01E49.zh.srt\` … \`S01E55.zh.srt\` are produced by the **already running** OCR Codex; do not kill that process, reset its environment, or assume unfinished files are valid.
- The new project creates ONLY \`post/\` (incoming, styles, jobs, cache, logs, final, reports, backend).
- Another running Codex's internal logs, venv, SRT temp files and process state are not imported into Git; they're left in place.
- \`adopt\` writes a non-authoritative snapshot \`.kwjd/adoption-linux.json\`. Live \`status\` re-scans the filesystem.
- To bring up queued AI jobs, implement and validate the backend per BACKENDS.md first. Never mark AI output done from file existence alone.

### Multiple projects later
Edit \`config/project.json\` episode range, target settings, source directories, and filenames. E49–E55 are the first instance, not assumed to be every story. The current Windows cutter and controller are story-specific; future refactoring should separate generic orchestration from story adapters, preserving reproducibility.

### Restarts
- \`status\` is side-effect free.
- \`adopt --refresh\` intentionally replaces only its own discovery snapshot, not media or job state.
- Job files live in \`post/jobs\` and are written atomically. Running \`server_worker.py --once\` handles the first eligible job. \`--loop\` can remain alive across SSH disconnect when started via tmux/systemd user.
- Unexpected \`FAILED\` job states require investigation; do not automatically requeue and destroy evidence.
