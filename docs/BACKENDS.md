# Linux enhancement backend contract (implementation handoff)

The orchestration and ASS stages exist. GPU inference must be **measured and implemented on the actual Linux machine**, not guessed by ChatGPT or faked via FFmpeg fps interpolation.

## Interface
Create an executable file on the remote machine:

`~/qingqingwu/post/backend/enhance.sh INPUT_MASTER OUTPUT_ENHANCED EPISODE`

- INPUT_MASTER: already clean DaVinci output, containing mixed audio.
- OUTPUT_ENHANCED: intermediate MKV, **video 1920x1080, constant 60 fps, same presentation duration (tolerance 0.5 seconds), no embedded ASS, no original hard subs**.
- EPISODE: any matching first-season ID (`S01E01`, `S01E49`, ...), dynamically discovered from queued jobs.
- Exit nonzero on failure. Never replace an already validated final video.
- Worker uses enhanced video for picture, mixed audio comes directly from master; it then burns approved ASS after all AI processing.

## Model selection to validate
- Decode with FFmpeg/VapourSynth, stream frames. No whole-episode PNG dumps.
- For interpolation prefer RIFE via vs-mlrt/CUDA/TensorRT (if actually compatible), preserving exact timebase. Detect scene cuts to prevent ghosting at audience/stage switches.
- For real-life upscaling compare appropriate Real-ESRGAN/general restoration with **no upscale** baseline on a 30–60s representative sample. If the mastered content is already good 1080p, bypass harmful upscale. Explicitly test Magic Mask edges and BGM background motion.
- Target order to benchmark: AI repair / upscale first → RIFE second vs RIFE first → upscale second. Choose the faster/better measured path, not a hard-coded dogma.
- NVIDIA RTX A4000 primary GPU; identify Tesla T10 through nvidia-smi, test CUDA backend compatibility. Do not mandate a dual-GPU pipeline if slower than one.
- Keep audio sync. No audio re-encoding during AI video inference.
- Final encode: libass + correct font files **after AI** → HEVC NVENC. **Audio from DaVinci mixed master must become lossless 24-bit FLAC at >=48 kHz** (preserve native 96 kHz+ when present), not AAC or an ordinary `-c:a copy` passthrough. Output MKV and verify codec/sample-rate/bits-per-raw-sample with ffprobe. See docs/HIRES.md.

## Operational requirements
- No sudo assumed; use venv, user-owned binaries, installed compatible CUDA.
- Do not touch or disrupt existing OCR process/environment.
- Store intermediate under post/cache/S01Exx, logs under post/logs.
- Compare visuals to DaVinci's previous in-editor interpolation, check faces, hands and scene cuts. Measure speed, VRAM and disk.
- Before processing full E49: get explicit sample signoff for at least one 30–60s clip and fonts.
- Reserve >=25 GiB **after estimated peak usage**. Refresh df; previous /home usage was 85.4% of 737.16GB.
- A possible two-pass intermediate+sub burn exists in current Worker to make recovery simpler. If implementing fused one-pass vs-mlrt→libass→NVENC, preserve same interface or adjust Worker with tests.

## Important honesty
The included `scripts/server_worker.py` cannot perform AI enhancement until the real `enhance.sh` is implemented and made executable. It will report WAIT_BACKEND. Do NOT replace it with a simple FFmpeg fps filter and call that AI enhancement. Backend quality and throughput are unknown until measured.

## Season profile

Read `config/seasons/S01.json` (cut starts after 30s, tail first white frame, OCR horizontal full-width/vertical auto). These rules apply across the whole first season, not just E49–E55. The existing in-progress E49–E55 items are merely the initial adoption set. Derive episode IDs from source file names / job files, do not code fixed episode ranges.
