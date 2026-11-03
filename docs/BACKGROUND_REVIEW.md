# Background review before processing

The workflow first reviews the complete batch, then processes only exceptions.
`scripts/batch_review_backgrounds.py --prepare --output PATH` groups existing
first/middle/last sampled frames into six-video review pages. It never regenerates
source media, subscribes to Workshop items or starts an encoder. A retained manifest
binds each page and source receipt by SHA. Existing manifests cannot be overwritten.

Record explicit visual decisions with `--decisions FILE`; each entry is
`[number, category, flags, findings]`. Categories are `CLEAN_CANDIDATE`, `NEEDS_CROP`,
`EXCLUDE`, `REVIEW_REQUIRED`. The tool does not infer new visual decisions. The
canonical review records exactly the three viewed frame indices (0, 4, 8), rather
than claiming all nine contact frames or whole-video review. Prior visual evidence
keeps its original scope. Unknown legacy states remain pending.

Running without either flag exports CSV, JSON, searchable HTML and playlists.
Clean, unchanged originals with common standard landscape sizes and square pixels
pass by reference, without copying or encoding. Frame rate is never an encoding
trigger: 24/25/30fps and native 720p/1440p/4K stay original. Common 16:10, 4:3 and
ultrawide source sizes also stay original; final composition/export handles framing
and 1080p60. Nonstandard sizes/SAR, visible HUD/UID, black borders and trim needs go
to an exception queue. Technical review flags stay separate from mandatory encoding.
Previously accepted encoded outputs are reused. No source files are changed or deleted.

Byte-identical source groups are reported separately. Similar scenes, different
encodes, viewpoints and day/night variants are not automatically called duplicates.
Current classification means sampled acceptance, not guaranteed absence of one-frame
overlays outside the samples or seamless looping.
