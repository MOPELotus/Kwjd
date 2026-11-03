# Music and scenery selection

`scripts/triage_mihoyo_music.py` reads the existing download plan and verified
receipts, never scans providers or changes source media. Its default operation
exports a searchable local HTML index, UTF-8 BOM CSV and evidence JSON.

Track geography has two separate fields: literal place-name clues in the title,
and sourced album-region context. Neither proves a track's precise in-game use.
Unknown locations and times of day remain unknown. The visible index reports
regional asset availability and time-of-day clues, without assigning a particular
clip to a track. Internal asset matches are same-game regional candidates,
not a guarantee of matching framing, time of day or suitability. Static/engine-only
assets and preset dependencies are distinguished from raw videos and accepted
normalized outputs. Expand the asset details to inspect individual status/path.

`--classify` applies LAION CLAP (`laion/clap-htsat-unfused`) on CPU, with four torch
threads. Use `--model` for a locally downloaded model and `--revision` for the
downloaded commit. The model and tokenizer are external dependencies, outside Git.
Official model: https://huggingface.co/laion/clap-htsat-unfused

Each new analysis verifies the canonical file SHA against its receipt, decodes up
to three distributed 10-second mono/48kHz excerpts and compares audio/text features
against calm, cheerful, intense, tense and vocal prompts. This is model-assisted
sampled triage, not human listening. A cosine margin is not a calibrated confidence
or probability. Disagreement among excerpts or a small margin stays pending. No
model result automatically approves a track for publication. Candidate playlists
are exported by game, separate from the original music files. Long mixes can change
substantially outside the samples; narration masking and volume also need review.

Analysis receipts are cached by actual audio SHA. Changed source audio cannot reuse
an old result. Each receipt records sampled windows, model revision, raw similarities
and `human_listened=false`. Failures retain a safe exception type without signed
URLs or credentials. An incomplete batch remains `INCOMPLETE`; it is not reported
as complete merely because iteration ended. `--limit` is for a bounded smoke run.

Run unit checks with `python -m unittest discover -s tests -p test_music_triage.py`.
The complete inventory and a real bounded model smoke must also be checked before
starting a large batch. Local model weights, music, receipts and rendered indexes
are intentionally outside the repository.
