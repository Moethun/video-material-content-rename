---
name: video-material-content-rename
description: Batch-rename video and image footage by recognizing what is actually on screen, and transcribe speech to build a searchable footage index. Use when the user hands over a folder of machine-named clips (C0769.MP4, DJI_0001.MP4, MVI_1234.MP4) and asks to rename by content / summarize what is in these clips / organize this footage / find which clips have narration / build a footage index. Covers the full pipeline: frame extraction, contact-sheet tiling, visual identification, faster-whisper transcription, safe two-phase rename, and one-key undo.
---

English | [中文](SKILL.zh-CN.md)

# Batch content-based rename + speech transcription

Turn `C0769.MP4` into `vlogger_talking_01.MP4`, and attach an audio layer to every clip:
**is anyone speaking, and what did they say?**

The picture tells you *what was shot*; the audio tells you *what was said*. Only both signals
together give you a complete picture of a clip.

## Scope

This is not a "fully automatic AI renamer" — it's an **AI-in-the-loop ingest workflow**:

```
probe  recon + dedup  →  frames  extract + tile  →  [AI reads contact sheets]  →  plan  build plan  →  apply  execute
                                                              ↑
                                              transcribe  speech-to-text (parallel signal)
```

Mechanical work goes to scripts; semantic recognition goes to an AI reading contact sheets.
The core design is **collapsing hundreds of image reads into a dozen** — 286 clips tile into
18 contact sheets, so the model makes 18 reads to identify all of them. An order of magnitude
cheaper.

## Rule one: safety before action

1. **The first pass is read-only recon** (`probe.py`): file count / total size / format
   breakdown / subdirectories / already-named files. Produce a report, touch nothing.
2. **Never dedupe by file size.** Burst slices from one device at identical settings come out
   byte-identical in length (in one measured batch, 40 of 286 clips were exactly 29,365,858
   bytes) yet show entirely different things. Full MD5 is mandatory.
3. **Renaming is destructive.** Before executing you must give the user the complete
   old→new mapping, a bolded risk warning, and get explicit confirmation.
4. **Renaming doesn't alter file contents, so copying the whole footage folder as a backup is
   unnecessary.** The mapping table plus the undo table *are* the complete backup. Say this to
   the user explicitly.
5. **Two-phase rename prevents collisions** (built into `apply.py`): every original file moves
   to a temporary name first, then to its final name. Otherwise a swap like `A→B` while `B→C`
   overwrites a file that hasn't been processed yet.
6. **Batched admission**: re-verify the directory file count every 10 files; halt and roll back
   on any anomaly.
7. Files the user already named well are **left untouched** by default — only machine-numbered
   names get processed. But ask first.

## Setup

```bash
pip install -r requirements.txt
```

- **No system ffmpeg required**: `imageio-ffmpeg` bundles the binary; get its path via
  `imageio_ffmpeg.get_ffmpeg_exe()`. Note it **does not ship `ffprobe`** — read
  duration/audio-track info from `ffmpeg -i <file>` stderr (no output arguments).
- **Use `faster-whisper`, not `openai-whisper`**: the latter pulls in torch (2GB+ installed,
  hundreds of MB resident), the former needs only ctranslate2 + onnxruntime (~75MB), ships as
  pure wheels with nothing to compile, and runs 4–5× faster on CPU.
- The first run downloads a model from HuggingFace. On mainland China networks set
  `HF_ENDPOINT=https://hf-mirror.com` (the script already defaults to this).

## Pipeline

### 1. Recon and dedup — `scripts/probe.py`

```bash
python scripts/probe.py --src "D:/footage"
```

Produces `_report.txt` (human-readable) and `_hashmap.tsv`. Zero duplicates is the common case
— but it has to be verified before you can claim it.

### 2. Frame extraction + contact sheets — `scripts/frames.py`

```bash
python scripts/frames.py --src "D:/footage" --cols 4 --rows 4
```

- Sample point defaults to **50%** of the clip — past the black leader and the focus hunt.
- Each tile is labeled with "index + original filename" underneath; thumbnails are
  proportionally scaled then center-cropped, never distorted.
- The CJK label bar auto-detects an available font (Microsoft YaHei / PingFang / Noto CJK /
  WenQuanYi).
- 266 frames → 18 contact sheets (4×4). **Have the AI read the contact sheets, not the
  individual frame images.**

### 3. Speech transcription — `scripts/transcribe.py`

```bash
python scripts/transcribe.py --src "D:/footage" --out transcript.tsv --model small
```

Adds the auditory signal; outputs
`name | duration | voice(Y/N) | nsegs | avg_logprob | text`.

**Model tier (measured on Chinese)**

| Model | Verdict |
|---|---|
| tiny | **Unusable** — short audio collapses into a repetition hallucination loop |
| base | Marginal — still repeats; short clips carry no information |
| **small** | **Recommended** — clips ≥10s produce clean verbatim transcripts |
| medium / large | Only when the narration is the core asset and time doesn't matter |

**Duration vs. speech (measured over 286 clips — usable directly as a pre-filter)**

| Clip duration | Share with speech |
|---|---|
| <5s | ~35% |
| 5–10s | ~67% |
| **≥10s** | **100%** |

`--only-longer-than 10` transcribes long clips only, at a 100% hit rate, more than halving the
compute.

**Why these parameters (every one of them learned the hard way)**

- `initial_prompt=None` — **never pass a domain primer.** If you do, the model dumps the prompt
  text verbatim into segments that contain no speech (measured: the same sentence appeared from
  nowhere four times). Using a primer to fix proper nouns buys contamination; a post-hoc
  term-correction table is cleaner.
- `condition_on_previous_text=False` — leave it on and short audio falls into a repetition loop
  every time.
- `vad_filter=True` + `min_silence_duration_ms=500` — the VAD gate is the most valuable part of
  this pipeline; it reliably separates talking takes from B-roll.
- `no_speech_threshold=0.6` / `log_prob_threshold=-1.0` / `compression_ratio_threshold=2.4` /
  `temperature=[0,0.2,0.4]` — a joint set of hallucination-suppression thresholds; drop any one
  and garbage leaks through.
- **Normalize simplified/traditional Chinese**: Whisper's Chinese output is unstable — one
  measured segment mixed scripts inside a single line. Run `zhconv` before term correction.
- Term-correction table: see `examples/terms.example.tsv`; swap in your own domain terms.

**Pipe the audio, don't write temp files**: `ffmpeg ... -f s16le -ar 16000 -ac 1 -` writes to
stdout, and `np.frombuffer` converts to float32 for `model.transcribe(arr)`. Some environments
run "safe delete" hooks that intercept — or abort — scripts that repeatedly delete temp files in
bulk (measured: the script was killed mid-run).

### 4. Build the plan + dry-run validation — `scripts/plan.py`

```bash
python scripts/plan.py --src "D:/footage" --labels labels.txt --out-dir "D:/out" --transcript transcript.tsv
```

**This step modifies nothing** — it only produces a plan plus a review sheet.

Label file, two auto-detected formats:

- **Positional**: one label per line, in filename-sorted order
- **Mapped**: `filename<TAB>label` (order-proof, recommended)

New-name rule: `label_NN.ext`, the counter incrementing **within each label** in original
filename order — so clips of the same kind keep their shooting order and stay easy to pull in
sequence while editing.

The dry run checks: source exists / target name unique / no illegal characters / no collision
with files that aren't part of the rename / label length. Keep the label count at
**20–40 categories**, 3–50 clips each: more and everything becomes a singleton, fewer and the
labels lose discriminating power.

### 5. Execute and undo — `scripts/apply.py`

```bash
python scripts/apply.py --src "D:/footage" --mapping _mapping.tsv            # preview
python scripts/apply.py --src "D:/footage" --mapping _mapping.tsv --apply    # execute
python scripts/apply.py --src "D:/footage" --reverse                          # one-key undo
```

The undo table `_rename_done.tsv` is written into the footage directory and flushed as it runs,
so even a power cut mid-operation stays recoverable. After an undo it self-archives to
`_rename_done.reversed.tsv`, so it won't block the next run.

## What the speech results are for

**Do:**

- **Split talking takes from B-roll** — `voice=Y` has speech, `N` is pure B-roll. This is the
  single most valuable output; filter on it directly while editing.
- **Narration library** — filter `text` to length ≥20 and export in descending length order;
  that's a reusable voiceover script library.
- **Duplicate setup detection** — the same passage often gets shot from several angles.
  Cluster with 2-gram Jaccard ≥0.45 and pick one take per group.
- **Footage search** — once the text is indexed you can find clips by keyword.

**Don't:**

- **Don't name files from transcript text.** Short clips yield fragments like
  "put, next, take out" — using that as a filename wrecks naming quality.
  **The picture remains the primary basis for naming**; speech is supporting signal and index
  only.
- Don't expect complete sentences from clips under 10 seconds.
- Don't fact-check anything against the transcript. It's a phonetic reconstruction —
  homophone errors and dropped characters are normal. Fine for retrieval and reference, not for
  quotation.

## Other pitfalls

- **Legacy Python `%` formatting breaks HTML generation**: `100%` and `aspect-ratio` inside CSS
  make `%` followed by `;` raise `ValueError: unsupported format character ';'`. Use
  `.replace("__PLACEHOLDER__", ...)` instead.
- **PowerShell 5.1 turns redirected Python stdout into mojibake** (display layer only — the
  bytes are fine), and may treat the output file as binary. Most reliable: have Python write its
  own result files with `encoding="utf-8"`.
- **Add a BOM to any tsv/csv destined for Excel** (`utf-8-sig`), or Excel shows mojibake.
- **A background task reporting `completed` while the log stops mid-way** means the script was
  interrupted by a hook or an exception — check the real output file's line count instead of
  trusting the task status.
- **Run frame extraction in incremental mode** (skip when the frame is newer than the source) so
  you never clear the directory each round and trip bulk-delete limits.

## Deliverables

- `重命名对照表.html` — review sheet: thumbnail + old→new name (+ speech text)
- `重命名对照表.tsv` — table version
- `素材语音索引.tsv` — filename / duration / has speech / transcript text
- `口播文案清单.txt` — plain-text narration library
- Always report three verification numbers: **file count unchanged / total size unchanged / no
  machine-numbered names left**

> The scripts emit Chinese filenames for the last four artifacts. Rename them to whatever your
> audience reads; nothing downstream depends on the names.

## Handing off to downstream tools

A tool that rough-cuts by matching clips to a script's slot list can consume this skill's speech
index directly, instead of extracting frames and re-identifying everything. Read the `voice=Y`
takes first — narration lines up naturally.
