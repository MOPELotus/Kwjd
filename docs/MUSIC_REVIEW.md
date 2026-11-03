# LAN music review

Run `py scripts/music_review_server.py --root D:/qingqingwu --host 0.0.0.0 --port 7833`.
The local page is `http://localhost:7833`; other LAN devices use the computer's LAN
IPv4 address and the same port. This is a local service, with no hosted deployment
or external assets. It reads the existing selection catalog and canonical audio.

The three game tabs show track titles, playback and five choices: calm, cheerful,
intense/tense, vocal and other. Calm/cheerful are video candidates; the other three
are excluded. Model calm/cheerful/intense/tense/vocal results provide defaults;
mixed and borderline results stay empty. Selected defaults are already effective
classifications; there is no per-track confirmation step or human-review counter.
Clicking a label saves an override immediately. Leave empty explicitly clears a
classification without reverting to a model default. Next simply plays the next
track without creating a review. Filters and counters use the effective label,
regardless of whether it came from the model or a human override. Export includes
all current classifications and their origin, including empty classifications.
Using a model default does not imply that the user manually listened to that track.

Second-pass triage (`scripts/refine_mihoyo_music.py`) first merges compatible
calm/cheerful and intense/tense excerpt votes, then resamples remaining conflicts
at five positions with new prompt ensembles. Quiet reflective/sad music has its
own calm descriptions; normalized onset and pulse measurements only help resolve
close calm/cheerful subtype choices. Aggregate family evidence replaces the old
all-excerpts-must-agree rule. Strong quiet/action transitions and close family
ties remain empty. This is still sampled model classification, not human listening
or calibrated accuracy. Original reviews remain unchanged; source-SHA-bound second
pass receipts and an overrides index live under `selection/refinement-v2`.
Catalog rebuilds apply this index; the web server continues to prioritize stored
human overrides, including explicit empty labels. Refinement does not edit the
human review files or infer regions/day-night from musical mood.

Human decisions are stored separately in
`.kwjd/mihoyo-music/selection/human-reviews.json`, with an append-only recovery
journal alongside it. No model results, source audio or download receipts change.
Decisions are bound to the track key and audio SHA; a changed audio version does
not silently inherit a human review. Optimistic revisions prevent another open
page from silently overwriting a later decision. No review data belongs in Git.

The server supports byte ranges for FLAC/OGG playback and seeking, serves only
catalogued audio inside the library, rejects arbitrary paths and cross-origin
writes, and exposes no source filesystem paths in the track API. Use a trusted LAN;
the page intentionally has no sign-in flow. Browser checks should use a separate
temporary review store so QA does not fabricate real human classification.
