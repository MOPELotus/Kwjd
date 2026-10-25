# One-time custom ASS style

Use **Aegisub** on Windows to load a checked SRT and a matching trimmed or clean video as preview. Design the final 1920×1080 style: Chinese font family, size, border, shadow, bottom margin and safe line wrapping.

1. Save an ASS file containing a **Default** style to `D:\qingqingwu\styles\template.ass`. Ensure `PlayResX: 1920` and `PlayResY: 1080` in [Script Info]; do not embed sample dialogue into this master style.
2. Put the actual chosen font files (licensed for your use) under `D:\qingqingwu\styles\fonts`. Fonts are never committed to GitHub.
3. `pwsh ./scripts/qqw.ps1 -Command sync-style` copies template and fonts via SSH to `~/qingqingwu/post/styles/`.
4. On Linux, check `fc-match 'FONT FAMILY'`, inspect FFmpeg/libass output and sample PNG frames. If desired use a temporary FONTCONFIG_FILE user configuration; don't silently substitute another font.
5. Build per-episode ASS with `python3 kwjd.py make-ass --srt ~/qingqingwu/output/S01E49.zh.srt --template ~/qingqingwu/post/styles/template.ass --output ~/qingqingwu/post/cache/S01E49/S01E49.ass`.
6. Do not add original-video-absent punctuation, remove intentional spaces, or rewrite OCR text. When final style changes, keep SRT untouched and regenerate ASS.

ASS uses centiseconds, so generated event timing is rounded to 10ms; verify fast subtitle changes. The initial render uses the worker's HEVC encoding stage after AI enhancement.
