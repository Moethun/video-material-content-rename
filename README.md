# video-material-rename

> Give a folder full of machine-named clips like `C0769.MP4` a real set of filenames — derived from **what's on screen and what's being said** — plus a searchable index of the footage.

An AI-in-the-loop toolkit for footage that arrives with machine-generated filenames.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

English | [中文](README.zh-CN.md)

![video-material-rename — name your footage by what's actually in it](assets/social-preview.png)

---

## The problem

Footage from a trip, a shoot day, or a week of phone capture usually lands looking like this:

```
C0769.MP4  C0770.MP4  C0778.MP4  DJI_0001.MP4  MVI_1234.MP4 ...
```

286 clips sitting there. Nobody knows which one is the vlogger talking to camera, which is a
detail close-up, which has narration, and which is just an empty street shot. So you open them
one by one — and two hours are gone.

This tool turns that folder into:

```
city_walk_01.MP4   cafe_latte_03.MP4   skyline_sunset_07.MP4 ...
```

Plus an index telling you what each clip contains. **Runs entirely locally. Zero API cost.**

## How it differs from other tools

Batch renamers on the market key off one of three things:

| Kind | Typical approach | Does it look at the picture? |
|---|---|---|
| Rule-driven | Find & replace, add sequence, add date | No |
| Metadata-driven | EXIF timestamps, GPS, file type | No |
| Text-driven | Case numbers in PDF bodies, invoice fields | No (text, not images) |

**None of them actually inspect the video frame.** This tool names files from
**two signals: what's on screen, and what's being said.**

## How it works

```
probe.py        Recon + full MD5 dedup           →  _report.txt
   ↓
frames.py       Extract frames + build contact sheets  →  N contact sheets
   ↓
[AI reads sheets]  Identify each shot             →  content labels
   ↓                                              ↑
plan.py         Build plan + dry-run validation   |   transcribe.py
   ↓                                              |   ASR / VAD gating
apply.py        Two-phase rename + one-key undo   |   (parallel audio signal)
```

**Key design: contact sheets cut down the number of image reads.** Reading 286 individual
frame images means 286 model calls. Extract frames, tile them into 18 contact sheets, and
the model needs 18 reads to identify and compare every clip side by side. An order of
magnitude cheaper.

This is not a "fully automatic AI renamer". It's an **AI-in-the-loop workflow**:
the mechanical work (frame extraction, hashing, renaming) goes to scripts, and the
semantic work (recognizing what's in a shot) goes to a model that can see.

## Quick start

```bash
git clone https://github.com/Moethun/video-material-rename.git
cd video-material-rename
pip install -r requirements.txt

# No footage handy? Generate a test set first.
python examples/make_demo_media.py --out demo

# End-to-end run
python scripts/probe.py      --src demo/media --out-dir demo
python scripts/frames.py     --src demo/media --out-dir demo --cols 3 --rows 2
python scripts/transcribe.py --src demo/media --out demo/transcript.tsv --model tiny
python scripts/plan.py       --src demo/media --labels demo/labels.txt --out-dir demo
python scripts/apply.py      --src demo/media --mapping demo/_mapping.tsv            # preview
python scripts/apply.py      --src demo/media --mapping demo/_mapping.tsv --apply    # execute
python scripts/apply.py      --src demo/media --reverse                              # undo
```

For real footage, swap `demo/media` for your own directory. **Every step before `plan.py`
is read-only — nothing touches your files.**

## The five scripts

### `probe.py` — recon and dedup

```bash
python scripts/probe.py --src "D:/footage"
```

Reports file count, total size, format breakdown, naming state — and runs a **full MD5
pass**.

> **Why hashing is mandatory instead of comparing file sizes**: burst-capture slices from
> the same camera at the same settings frequently come out byte-identical in length. In one
> real batch, 40 of 286 clips were exactly 29,365,858 bytes — and every one of them showed
> something different. Deduping by size would have deleted real footage.

### `frames.py` — frame extraction and contact sheets

```bash
python scripts/frames.py --src "D:/footage" --cols 4 --rows 4 --at 0.5
```

Grabs each frame at **50% of the clip duration** (past the black leader and the focus hunt),
then tiles them into labeled contact sheets. Auto-detects available CJK fonts across
platforms (Microsoft YaHei / PingFang / Noto CJK / WenQuanYi).

### `transcribe.py` — speech transcription

```bash
python scripts/transcribe.py --src "D:/footage" --out transcript.tsv --model small
python scripts/transcribe.py --src "D:/footage" --out t.tsv --only-longer-than 10
```

Adds the audio signal: **is anyone speaking, and what did they say?**

Uses `faster-whisper` rather than `openai-whisper` — no torch dependency (saves ~2GB),
4–5× faster on CPU int8. Resumable if interrupted.

### `plan.py` — build the plan and dry-run it

```bash
python scripts/plan.py --src "D:/footage" --labels labels.txt --out-dir "D:/out" --transcript transcript.tsv
```

**Modifies nothing.** Produces a plan plus an HTML review sheet (thumbnail + old name → new
name) for you to check.

The label file accepts two formats, auto-detected:

```text
# Positional: one label per line, in filename-sorted order
city walk
cafe latte

# Mapped: filename <TAB> label  (order-proof, recommended)
cafe latte	C0769.MP4
```

### `apply.py` — execute and undo

```bash
python scripts/apply.py --src "D:/footage" --mapping _mapping.tsv            # preview
python scripts/apply.py --src "D:/footage" --mapping _mapping.tsv --apply    # execute
python scripts/apply.py --src "D:/footage" --reverse                          # one-key undo
```

**Two-phase rename prevents collisions**: every original file is first moved to a temporary
name, then to its final name. Otherwise a swap like `A→B` while `B→C` overwrites a file that
hasn't been processed yet.

The undo table is flushed to disk as it runs, so a power cut mid-operation is still
recoverable. After an undo it self-archives, so it won't block the next run.

> Renaming doesn't alter file contents, which means **you don't need to copy the whole
> footage folder as a backup** — `_mapping.tsv` + `_rename_done.tsv` are a complete
> restore record.

## Measured results

Numbers below come from one real project: **a travel-vlog shoot**, 286 handheld clips,
1080p, 2–45 seconds each, 10.59 GB total. (The term-correction table in that project
contained real proper nouns, so the examples shipped here are fictional.)

**Speech recognition (small model, CPU int8, 2 threads)**

| Metric | Result |
|---|---|
| Total transcription time | 12 min 53 s (27.9 min of audio) |
| Clips with speech | 150 / 286 (52%) |
| Silent B-roll | 135 / 286 |
| Usable copy (≥20 chars) | 74 clips |
| Failures | 1 |

**Duration strongly predicts speech (usable as a pre-filter)**

| Clip duration | Share with speech |
|---|---|
| <5s | 35% |
| 5–10s | 67% |
| **≥10s** | **100%** |

The pattern is intuitive once you see it: long takes are the creator talking to camera, short
takes are quick street B-roll cuts.

**Model tier comparison**

| Model | Verdict |
|---|---|
| tiny | **Unusable** — short audio collapses into repetition hallucination |
| base | Marginal — still repeats; short clips carry no information |
| **small** | **Recommended** — clips over ~10s produce clean verbatim transcripts |
| medium / large | Only when the narration is the core asset and time doesn't matter |

More field notes and dead ends: [`docs/FINDINGS.md`](docs/FINDINGS.md).

## Using it as an AI Agent Skill

This repo doubles as an [Agent Skill](https://docs.claude.com/en/docs/claude-code/skills).
Drop the directory into your skills folder, or point an AI at `SKILL.md`, and it will
follow this workflow.

`SKILL.md` documents the reasoning behind every decision: why faster-whisper, why you must
never pass an `initial_prompt`, why transcript text must not be used as the filename, and
why these specific threshold values. A Chinese version lives in
[`SKILL.zh-CN.md`](SKILL.zh-CN.md).

## What to do with the speech results

**Do:**
- **Split talking takes from B-roll** — filter on `voice=Y/N` directly while editing
- **Build a narration library** — filter for text ≥20 chars; that's reusable voiceover script
  material
- **Detect duplicate setups** — the same line often gets shot from several angles; cluster
  the text and pick one take per group
- **Search footage** — find which clips mention a given place or person

**Don't:**
- **Don't name files from transcript text.** Short clips yield fragments like
  "put, next, take out" — using that as a filename wrecks naming quality.
  **The picture remains the primary basis for naming**; audio is supporting signal and index.
- Don't fact-check anything against the transcript. It's a phonetic reconstruction —
  homophone errors are normal, not exceptional.

## Repository layout

```
video-material-rename/
├── SKILL.md                    Agent Skill entry point (all decision rationale)
├── SKILL.zh-CN.md              Agent Skill entry point（中文）
├── README.md                   English
├── README.zh-CN.md             中文
├── LICENSE                     MIT
├── requirements.txt
├── .gitignore
├── assets/
│   └── social-preview.png      Social preview card (1280×640)
├── scripts/
│   ├── probe.py                Recon + MD5 dedup
│   ├── frames.py               Frame extraction + contact sheets
│   ├── transcribe.py           Speech transcription
│   ├── plan.py                 Build plan + dry-run validation
│   └── apply.py                Execute + undo
├── examples/
│   ├── make_demo_media.py      Generate test footage
│   └── terms.example.tsv       Term-correction table example
└── docs/
    ├── FINDINGS.md             Measured data and field notes
    └── FINDINGS.zh-CN.md       实测数据与踩坑记录（中文）
```

## FAQ

**Which formats are supported?**
Video: `.mp4 .mov .mkv .avi .m4v .flv .wmv .webm .mpg .ts .mts .3gp`. Images:
`.jpg .png .heic .webp` and others.

**Does it need a network connection?**
The first run downloads a Whisper model (small ≈480MB, tiny ≈75MB). After that it's fully
offline. Mainland China networks are routed through `hf-mirror.com` by default.

**What does it cost to run?**
Nothing. All inference is local; no paid API is called. The only cost is electricity and
CPU time.

**What if it gets a name wrong?**
`apply.py --reverse` restores every original filename in reverse order.

**Why not just have gpt-4o or Claude name the clips directly?**
You can, and recognition quality will be better. The point of this tool is to make that step
**as cheap as possible** — 286 images collapsed into 18 contact sheets, so whatever model you
use costs an order of magnitude less. The scripts aren't bound to any particular model; the
AI step is swappable.

## License

MIT — see [LICENSE](LICENSE).
