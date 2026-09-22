#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成一批假的测试素材，用来验证整条流程能跑通。

会用 ffmpeg 合成 6 个短视频到 --out 目录，覆盖这些边界情况：
  - 有音轨但无人声（正弦波 / 静音）  -> VAD 应判定为无语音
  - 完全没有音轨                      -> 应走"无音轨"分支直接跳过
  - 不同时长（4s ~ 12s）
  - 不同画面（彩条 / 测试图案 / 纯色），便于肉眼确认抽帧位置

跑完之后按这个顺序验证：
    python scripts/probe.py     --src demo/media --out-dir demo
    python scripts/frames.py    --src demo/media --out-dir demo --cols 3 --rows 2
    python scripts/transcribe.py --src demo/media --out demo/transcript.tsv --model tiny
    python scripts/plan.py      --src demo/media --labels demo/labels.txt --out-dir demo
    python scripts/apply.py     --src demo/media --mapping demo/_mapping.tsv --apply
    python scripts/apply.py     --src demo/media --reverse

用法：
    python make_demo_media.py --out demo
"""
import argparse
import os
import subprocess
import sys

try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FF = "ffmpeg"

# (文件名, 视频滤镜, 音频滤镜 or None)
SPECS = [
    ("C0101.MP4", "testsrc2=size=640x360:rate=25:duration=6",  "anullsrc=channel_layout=mono:sample_rate=16000:duration=6"),
    ("C0102.MP4", "smptebars=size=640x360:rate=25:duration=8", "sine=frequency=300:sample_rate=16000:duration=8"),
    ("C0103.MP4", "rgbtestsrc=size=640x360:rate=25:duration=5", "anullsrc=channel_layout=mono:sample_rate=16000:duration=5"),
    ("C0104.MP4", "color=c=blue:size=640x360:rate=25:duration=12", "sine=frequency=800:sample_rate=16000:duration=12"),
    ("C0105.MP4", "testsrc=size=640x360:rate=25:duration=7",   None),   # 故意不带音轨
    ("C0106.MP4", "color=c=green:size=640x360:rate=25:duration=4", "anullsrc=channel_layout=mono:sample_rate=16000:duration=4"),
]

DEMO_LABELS = """蓝色画面
彩条测试
三色测试
纯蓝底
测试图案
纯绿底
"""


def main():
    ap = argparse.ArgumentParser(description="生成演示用测试素材")
    ap.add_argument("--out", default="demo", help="输出目录，会在其下建 media/")
    args = ap.parse_args()

    media = os.path.join(args.out, "media")
    os.makedirs(media, exist_ok=True)

    for name, vf, af in SPECS:
        dst = os.path.join(media, name)
        cmd = [FF, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", vf]
        if af:
            cmd += ["-f", "lavfi", "-i", af]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        if af:
            cmd += ["-c:a", "aac", "-shortest"]
        else:
            cmd += ["-an"]
        cmd.append(dst)
        r = subprocess.run(cmd, capture_output=True)
        status = "OK" if r.returncode == 0 and os.path.exists(dst) else "FAIL"
        print("  %-12s %s  %s" % (name, status, "" if af else "(无音轨)"))

    labels_path = os.path.join(args.out, "labels.txt")
    with open(labels_path, "w", encoding="utf-8") as f:
        f.write(DEMO_LABELS)

    print("\n素材目录: %s" % media)
    print("标签文件: %s" % labels_path)
    print("\n下一步:")
    print("  python scripts/probe.py --src \"%s\" --out-dir \"%s\"" % (media, args.out))


if __name__ == "__main__":
    main()
