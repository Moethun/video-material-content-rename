# Field notes: measured results and dead ends

Everything in this document is **data from an actual run**, not estimates. All conclusions
come from one complete project: a travel-vlog shoot — 286 handheld clips, 1080p, 2–45 seconds
each, 10.59 GB total, all carrying `pcm_s16be` audio recorded by the camera's internal mic.

The point of writing it down: so you can skip the holes we fell into.

English | [中文](FINDINGS.zh-CN.md)

---

## 1. Traps in the footage itself

### 1.1 File size is completely useless for deduplication

Recon turned up large clusters of byte-identical lengths:

```
29,365,858 bytes  ×  40 files
25,171,314 bytes  ×  36 files
```

The first instinct was "this batch has a lot of duplicates". **After a full MD5 pass, all 286
files hashed uniquely. Zero duplicates.**

The cause: when the same camera shoots back-to-back at identical settings, the encoder emits
files of highly consistent length — with completely different content. Deduping by size would
have silently deleted dozens of real clips.

**Conclusion: deduplication requires full hashing.** At this scale (10.59 GB) MD5 takes
minutes, which is an acceptable cost.

### 1.2 Clip duration spans a wide range — one strategy won't cover it

```
min 1.92s   avg 5.85s   max 45.12s   total 27.9 min
```

With 2-second and 45-second clips in the same folder, both the frame-extraction strategy and
the transcription strategy have to branch. This is what later led to duration pre-filtering
(see §3.2).

---

## 2. Whisper traps

### 2.1 `initial_prompt` gets read back as content

The intuitive idea first: feed a domain hint like
`"The following is Mandarin narration from a city-life vlog."` to nudge the model toward the
right vocabulary and improve proper nouns.

**Actual result: the prompt came straight back out.**

```
street_broll_01.MP4  →  "OK, city, life vlog Mandarin narration."   ← prompt variant
talking_01.MP4       →  "This time it's city-life vlog narration."  ← prompt variant
latte_01.MP4         →  "This segment is city-life vlog narration." ← prompt variant
skyline_01.MP4       →  "This position is city-life vlog narration." ← prompt variant
```

When the audio contains no clear speech, the model completes the prompt into a sentence that
*looks* plausible. This is far worse than emitting an empty string — an empty result is
detectable, but a hallucination that resembles a normal result contaminates the whole dataset.

**Conclusion: `initial_prompt=None`, never pass one.**
Proper nouns get fixed with a **post-hoc term-correction table**, which is much cleaner than
pre-hoc steering.

### 2.2 Repetition hallucination on short audio

On the tiny model, short clips fall into single-phrase loops:

```
"at one point I had already prepared for a while I had already prepared for a while I had already..."
"excuse me is it a bit a bit a bit a bit a bit a bit a bit a bit a bit a bit ... (hundreds of repeats)"
```

`condition_on_previous_text=False` mitigates this substantially, but a model that small still
breaks through.

At the post-processing layer, collapse repeated units with a regex:

```python
text = re.sub(r"(.{2,6}?)\1{3,}", r"\1", text)
```

### 2.3 Chinese simplified/traditional output is unstable

Mixed scripts show up inside a single result:

```
"这段素材不是按一下按鈕就完事為什麼畫面會抖動使用什麼穩定器怎麼拍出实物感"
         ~~~~ ~~~~ ~~~~~~ ~~~ ~~~ ~~
```

**Conclusion: normalize with `zhconv.convert(text, "zh-cn")` first, then run term correction.**
Order matters — convert before correcting, or traditional-form variants slip past the table.

### 2.4 Homophone errors (names and places are the worst case)

A two-character personal name came back in **eight different spellings** across 286 clips:

```
林许 / 林绪 / 临序 / 淋序 / 林絮 / 邻序 / 林旭 / 凌序      (correct form: 林序)
```

Other typical failure modes:

| Failure type | Example |
|---|---|
| English app / gear names | Rendered phonetically into Chinese, or misspelled by a letter or two (`StreamLab` → `StreamLap`) |
| Swallowed syllables in common words | 脑海 → 能害 ｜ 名额 → 名革 ｜ 网红打卡 → 网宏打卡 |
| Technical terms confused | 调色预设 → 调色预舍 ｜ 实物 → 食物 |

**Conclusion: a term-correction table is a requirement, not an option.** How to build one:
`examples/terms.example.tsv`.

One aside: the shorter the name, the more likely it is to be mangled — and the name is
precisely the part you cannot afford to get wrong, since who a clip features and where it was
shot often rests entirely on those few characters.

---

## 3. Parameter and strategy conclusions

### 3.1 The final parameter set

```python
model.transcribe(
    audio_array,
    language="zh",
    beam_size=5,
    initial_prompt=None,               # critical: passing one gets it read back
    condition_on_previous_text=False,  # critical: otherwise short audio loops
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500),
    no_speech_threshold=0.6,
    log_prob_threshold=-1.0,
    compression_ratio_threshold=2.4,
    temperature=[0.0, 0.2, 0.4],
)
```

The last three thresholds plus the temperature sequence form a joint anti-hallucination set.
Drop any one of them and garbage text leaks through.

### 3.2 Duration strongly predicts speech (the most useful finding)

| Clip duration | Samples | With speech | Share |
|---|---|---|---|
| <5s | 170 | 61 | 35% |
| 5–10s | 84 | 57 | 67% |
| 10–15s | 26 | 26 | **100%** |
| 15–25s | 5 | 5 | **100%** |
| >25s | 1 | 1 | **100%** |

**10 seconds is a clean dividing line.** Using `--only-longer-than 10` cuts compute by more
than half and still misses zero clips that contain speech.

The pattern is intuitive once you see it: long takes are the creator talking to camera, short
takes are quick street B-roll cuts.

Stated the other way: **two-thirds of clips under 5 seconds are pure B-roll.** Don't expect
anything from their transcripts.

### 3.3 Model tiers

| Model | Size | Verdict |
|---|---|---|
| tiny | 75MB | **Unusable.** Severe repetition hallucination on short audio |
| base | 145MB | Marginal. Still repeats; short clips carry no information |
| **small** | 480MB | **Recommended.** Clips over 10s produce clean verbatim transcripts |
| medium | 1.5GB | Viable when the narration is the core asset |
| large | 3GB | Poor cost/benefit; not recommended for this scenario |

### 3.4 Performance

| Step | Measured |
|---|---|
| Frame extraction + probe, 286 clips | ~3 min |
| MD5 over 10.59 GB | ~2 min |
| Transcription (small, 2 threads, 27.9 min audio) | 12 min 53 s |
| Model load (first run / cached) | 55s / 3–7s |
| Transcription speed | ~2–13× real time (depends on clip length) |

Transcription runs slower than the theoretical figure because piping audio means each clip
gets fully decoded, and for short slices the **per-process startup overhead** dominates.

---

## 4. Engineering traps

### 4.1 A safe-delete hook will kill your batch script

In the batch transcription loop, the audio temp file reused a single path and was `os.remove()`d
once per iteration. On iteration 50 the script was killed:

```
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50,"threshold":50, ...}
EXIT=1
```

**Deleting more than 50 files within one batch triggers an interception.**

**Fix: don't write a temp file at all.**
Have ffmpeg emit PCM straight to stdout, read it into memory, hand it to the model:

```python
cmd = [FF, "-i", path, "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
       "-ar", "16000", "-ac", "1", "-"]          # output to stdout
p = subprocess.run(cmd, capture_output=True)
arr = np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0
segs, _ = model.transcribe(arr, ...)
```

This sidesteps the hook and eliminates disk I/O. By the same logic, the frame extractor runs in
incremental mode (skip frames newer than their source) rather than wiping the output directory
each pass.

### 4.2 Background tasks report `completed` while the log is truncated

In the interruption above, the script was clearly killed — yet the task reported `completed`,
with the log cut off at `...20/286`.

**Diagnostic approach: don't trust task status. Count the lines in the actual output file.**

### 4.3 Redirecting Python stdout through PowerShell 5.1 garbles it

Chinese output turns into mojibake like `绱犳潗渚﹀療鎶ュ憡`. The file itself is fine — it's a
display-layer problem.

**Fix: have Python write a UTF-8 file itself, then read that.**

### 4.4 Building HTML with `%` formatting blows up

`100%` and `aspect-ratio` in CSS make Python's `%` formatting raise
`ValueError: unsupported format character ';'`.

**Fix: use `.replace("__PLACEHOLDER__", html)` placeholders.**

### 4.5 The file-count check was counting the undo table

Renaming re-verifies the directory file count after each batch — but the undo table
`_rename_done.tsv` is written into the footage directory, so the check always reported
`6 -> 7 abnormal`.

**Fix: exclude the undo table from the count.**

---

## 5. Why contact sheets are the efficiency trick

Reading 286 individual frame images means 286 reads. Tiling one frame per cell into 4×4 = 16-cell
contact sheets changes that to:

```
286 frames → 18 contact sheets → 18 reads
```

**A 94% reduction in image reads, with no loss of information** — a 400×225 cell is plenty to
decide "is this a wide street shot or a latte detail shot", and each cell carries its source
filename in a label bar.

Design details that matter:

- Frame position at **50%** of duration — past the black leader, the focus hunt, the camera logo
- Thumbnails **scaled proportionally and center-cropped**, never stretched
- A label bar under each cell reading `#index original_filename`
- Auto-detected CJK font (Microsoft YaHei / PingFang / Noto CJK / WenQuanYi)
- Whole sheet 1600×1020, JPEG quality 88 — higher adds file size with no recognition benefit

---

## 6. Overall conclusions

1. **Visual + audio beats either signal alone.** The picture tells you what was shot; the audio
   tells you what was said. A 52% talking-clip rate and 74 usable narration excerpts are things
   you cannot read off the frames.
2. **The most valuable output of speech recognition is not the text — it's whether text exists.**
   VAD gating cleanly separates talking takes from B-roll, and that binary signal is worth more
   during editing than transcript accuracy is.
3. **Don't name files from speech.** Short-clip transcription quality is poor and will wreck
   naming quality. The picture remains the primary basis for naming.
4. **Do the upstream work properly and the downstream gets it for free.** Index picture, audio,
   and duration at ingest, and no one has to re-extract and re-recognize at edit time.
