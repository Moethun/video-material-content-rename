#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤 3/5 —— 语音转写，给素材补一层「听觉」信息。

画面告诉你「拍的是什么」，声音告诉你「讲的是什么」。两个信号合起来才是完整的素材画像。

做三件事：
  1. 探音轨（读容器头，毫秒级）；没有音轨的直接判无语音，不浪费算力
  2. VAD 过滤 + Whisper 转写；模型只加载一次，批量跑完
  3. 简繁统一 + 术语纠错（品牌名/专有名词是 Whisper 的重灾区）

输出 tsv： name | duration | voice(Y/N/ERR) | nsegs | avg_logprob | text

用法：
    python transcribe.py --src "D:/素材" --out transcript.tsv
    python transcribe.py --src "D:/素材" --out t.tsv --model medium
    python transcribe.py --src "D:/素材" --out t.tsv --terms my_terms.tsv
    python transcribe.py --src "D:/素材" --out t.tsv --only-longer-than 10   # 只转 >=10 秒

依赖：
    pip install faster-whisper imageio-ffmpeg zhconv

为什么用 faster-whisper 而不是 openai-whisper：
    后者依赖 torch（装完 2GB+，常驻内存几百 MB）；前者只需 ctranslate2 + onnxruntime
    （约 75MB），纯 wheel 无需编译，CPU 上快 4-5 倍。

模型档位（中文实测结论，源数据见 docs/FINDINGS.md）：
    tiny    不可用 —— 短音频陷入重复幻觉（同一句刷屏）
    base    勉强   —— 仍有重复，短片段基本无信息
    small   推荐   —— 10 秒以上的片段可直接出逐字稿
    medium / large  仅在口播是核心资产、且不在乎耗时和内存时用

时长 vs 有语音（286 个素材实测，可直接当预筛规则）：
    <5s   约 35% 有语音
    5-10s 约 67%
    >=10s  100%
    想省时间就 --only-longer-than 10 ，命中率 100%，算力能砍掉一半以上。
"""
import argparse
import io
import os
import re
import subprocess
import sys
import time

import numpy as np

try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FF = "ffmpeg"

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".flv", ".wmv", ".webm",
             ".mpg", ".mpeg", ".ts", ".mts", ".3gp")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")

# 内置的最小纠错表。真正业务化的词请写进 --terms 文件，
# 格式：每行「错词<TAB>正确词」，# 开头为注释。
BUILTIN_TERMS = [
    ("三围打印", "三维打印"),
    ("三微打印", "三维打印"),
]


def load_terms(path):
    """从 tsv 读取术语纠错表。"""
    table = list(BUILTIN_TERMS)
    if not path:
        return table
    if not os.path.exists(path):
        print("术语表不存在，忽略: %s" % path)
        return table
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = re.split(r"\t+", line)
            if len(parts) >= 2 and parts[0].strip():
                table.append((parts[0].strip(), parts[1].strip()))
    return table


def collapse(text):
    """折叠连续重复片段 —— Whisper 幻觉的典型特征是同一个短语刷屏。"""
    return re.sub(r"(.{2,6}?)\1{3,}", r"\1", text)


def fix_terms(text, table):
    """
    简繁统一 + 术语纠错。

    Whisper 的中文简繁输出不稳定，实测同一条里会混出
    「按一下按鈕就完事機器為什麼能打印」这种简繁混排，必须先统一再纠错。
    """
    try:
        import zhconv
        text = zhconv.convert(text, "zh-cn")
    except ImportError:
        pass          # 没装就跳过，不阻断主流程
    for a, b in table:
        text = text.replace(a, b)
    return text


def probe(path):
    """读容器头，返回 (时长, 有无音轨)。"""
    try:
        r = subprocess.run([FF, "-hide_banner", "-i", path],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=30)
        err = r.stderr or ""
    except Exception:
        return 0.0, False
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", err)
    dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0
    return dur, ("Audio:" in err)


def audio_array(path):
    """
    ffmpeg 管道直出 16kHz 单声道 PCM，读进内存交给 Whisper。

    为什么不用临时 wav 文件：
      1. 有些环境有「安全删除」类钩子，同一批次反复删文件会被拦截甚至中断脚本；
      2. 零落盘也省掉磁盘 IO，批量处理时更快。
    """
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-i", path,
           "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
           "-ar", "16000", "-ac", "1", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if not p.stdout:
        return None
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def main():
    ap = argparse.ArgumentParser(description="素材语音批量转写")
    ap.add_argument("--src", required=True, help="素材目录")
    ap.add_argument("--out", required=True, help="输出 tsv 路径")
    ap.add_argument("--model", default="small", help="tiny/base/small/medium/large")
    ap.add_argument("--lang", default="zh", help="语言，auto 表示自动检测")
    ap.add_argument("--threads", type=int, default=2,
                    help="CPU 线程数。保持 <=2 才不会拖慢前台程序")
    ap.add_argument("--terms", default=None, help="术语纠错表 tsv（错词<TAB>正确词）")
    ap.add_argument("--min-chars", type=int, default=4,
                    help="转写文本短于这个长度视为无有效人声")
    ap.add_argument("--only-longer-than", type=float, default=0,
                    help="只转写时长超过 N 秒的素材。>=10 秒命中率接近 100%%")
    ap.add_argument("--recursive", action="store_true")
    args = ap.parse_args()

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("缺少 faster-whisper，请先 pip install faster-whisper")

    terms = load_terms(args.terms)

    if args.recursive:
        paths = []
        for root, _d, files in os.walk(args.src):
            paths += [os.path.join(root, f) for f in files]
    else:
        paths = [os.path.join(args.src, f) for f in os.listdir(args.src)]
    files = sorted(p for p in paths
                   if os.path.isfile(p) and p.lower().endswith(VIDEO_EXT + AUDIO_EXT))
    if not files:
        sys.exit("目录里没有找到音视频文件: %s" % args.src)

    # 续跑支持：已经写进 tsv 的文件跳过，可以中断后接着跑
    done = set()
    if os.path.exists(args.out):
        with io.open(args.out, "r", encoding="utf-8") as f:
            next(f, None)
            for line in f:
                if line.strip():
                    done.add(line.split("\t", 1)[0])

    print("文件 %d 个，已完成 %d 个" % (len(files), len(done)))

    t0 = time.time()
    model = WhisperModel(args.model, device="cpu",
                         compute_type="int8", cpu_threads=args.threads)
    print("模型 %s 加载完成 %.1fs" % (args.model, time.time() - t0))

    fh = io.open(args.out, "a", encoding="utf-8")
    if not done:
        fh.write("name\tduration\tvoice\tnsegs\tavg_logprob\ttext\n")

    voice_n = skipped_n = 0
    for i, path in enumerate(files, 1):
        fn = os.path.basename(path)
        if fn in done:
            continue

        dur, has_audio = probe(path)

        if dur and args.only_longer_than and dur < args.only_longer_than:
            fh.write("%s\t%.2f\tSKIP\t0\t0.00\t\n" % (fn, dur))
            fh.flush()
            skipped_n += 1
            continue

        if not has_audio:
            fh.write("%s\t%.2f\tN\t0\t0.00\t\n" % (fn, dur))
            fh.flush()
            continue

        arr = audio_array(path)
        if arr is None or len(arr) < 1600:
            fh.write("%s\t%.2f\tERR\t0\t0.00\t\n" % (fn, dur))
            fh.flush()
            continue

        try:
            segs, _info = model.transcribe(
                arr,
                language=None if args.lang == "auto" else args.lang,
                beam_size=5,
                # 关键：绝对不要传 initial_prompt。
                # 传了之后模型在无语音片段会把 prompt 原文当结果吐出来（实测复读 4 次）。
                initial_prompt=None,
                # 关键：不关掉的话，短音频必然陷入重复循环。
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
                compression_ratio_threshold=2.4,
                temperature=[0.0, 0.2, 0.4],
            )
            segs = list(segs)
        except Exception:
            fh.write("%s\t%.2f\tERR\t0\t0.00\t\n" % (fn, dur))
            fh.flush()
            continue

        txt = fix_terms(collapse("".join(s.text for s in segs).strip()), terms)
        if len(txt) < args.min_chars:
            txt, v = "", "N"
        else:
            v = "Y"
            voice_n += 1

        alp = (sum(s.avg_logprob for s in segs) / len(segs)) if segs else 0.0
        fh.write("%s\t%.2f\t%s\t%d\t%.2f\t%s\n"
                 % (fn, dur, v, len(segs), alp, txt.replace("\t", " ")))
        fh.flush()

        if i % 10 == 0:
            print("  ...%d/%d  有语音 %d  跳过 %d  %.0fs"
                  % (i, len(files), voice_n, skipped_n, time.time() - t0))

    fh.close()
    print("\n完成  有语音 %d  跳过 %d  耗时 %.0fs" % (voice_n, skipped_n, time.time() - t0))
    print("输出: %s" % args.out)
    print("\n语音结果怎么用：")
    print("  * 口播 / 空镜分流 —— voice=Y 是有人声的，N 是纯画面 B-roll")
    print("  * 导出文案清单 —— 筛 text 长度 >=20 的，按长度排序，就是一份文案素材库")
    print("  * 重复机位识别 —— 相同讲解往往在多机位各拍一遍，文本相近的可以只留一条")


if __name__ == "__main__":
    main()
