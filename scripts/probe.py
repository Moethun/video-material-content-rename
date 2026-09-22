#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤 1/5 —— 侦察与查重。

在看任何画面之前，先把这批素材摸清楚：
  - 有多少文件、多大、什么格式、有没有子目录
  - 有没有已经命好名的文件（这些默认不动）
  - **全量 MD5 查重**

为什么要全量哈希而不是比文件大小：
    同一台设备、同一参数连拍的切片，会出现大量字节数完全相同的文件，
    但画面内容各不相同。实测 286 个素材里有 40 个都是 29365858 字节，
    按大小判重会误删真实素材。必须哈希。

输出（默认写到 --out-dir，不碰素材本身）：
    _report.txt    人看的侦察报告
    _hashmap.tsv   hash <TAB> path，供后续步骤复用

用法：
    python probe.py --src "D:/素材" 
    python probe.py --src "D:/素材" --no-hash        # 只侦察不哈希
    python probe.py --src "D:/素材" --recursive
"""
import argparse
import hashlib
import io
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".flv", ".wmv", ".webm",
             ".mpg", ".mpeg", ".ts", ".mts", ".3gp")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".tif", ".tiff")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.2f %s" % (n, unit)
        n /= 1024.0
    return "%.2f PB" % n


def md5_of(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def walk(src, recursive):
    if recursive:
        for root, _dirs, files in os.walk(src):
            for f in files:
                yield os.path.join(root, f)
    else:
        for f in os.listdir(src):
            p = os.path.join(src, f)
            if os.path.isfile(p):
                yield p


def main():
    ap = argparse.ArgumentParser(description="素材侦察 + MD5 查重")
    ap.add_argument("--src", required=True, help="素材目录")
    ap.add_argument("--out-dir", default=None,
                    help="报告输出目录，默认与 --src 同级（不写进素材目录）")
    ap.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    ap.add_argument("--no-hash", action="store_true", help="跳过 MD5（只侦察）")
    ap.add_argument("--all-files", action="store_true",
                    help="连非视频/图片/音频文件也纳入统计")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        sys.exit("目录不存在: %s" % src)

    out_dir = args.out_dir or os.path.dirname(src)
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "_report.txt")
    hash_path = os.path.join(out_dir, "_hashmap.tsv")

    log = io.open(report_path, "w", encoding="utf-8")

    def w(s=""):
        log.write(str(s) + "\n")
        print(s)

    w("素材侦察报告")
    w("源目录: %s" % src)
    w("生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    w("=" * 62)

    all_paths = list(walk(src, args.recursive))
    if args.all_files:
        targets = all_paths
    else:
        keep = VIDEO_EXT + IMAGE_EXT + AUDIO_EXT
        targets = [p for p in all_paths if p.lower().endswith(keep)]

    # ---- 结构 ----
    w("\n[结构]")
    w("  文件总数: %d" % len(targets))
    w("  目录总数: %d" % sum(1 for _ in os.walk(src)))
    if not args.recursive:
        subdirs = [d for d in os.listdir(src) if os.path.isdir(os.path.join(src, d))]
        w("  顶层子目录: %d %s" % (len(subdirs), subdirs[:10] if subdirs else ""))

    total = sum(os.path.getsize(p) for p in targets)
    w("  总大小: %s" % human(total))

    # ---- 格式分布 ----
    w("\n[格式分布]")
    ext_counter = Counter(os.path.splitext(p)[1].lower() for p in targets)
    for ext, n in ext_counter.most_common():
        w("  %-8s %d" % (ext or "(无扩展名)", n))

    # ---- 命名状况：哪些已经是人类可读的名字 ----
    def looks_named(fn):
        """机器编号名的特征：纯字母数字下划线横线，且没有中文。"""
        stem = os.path.splitext(fn)[0]
        if any("\u4e00" <= ch <= "\u9fff" for ch in stem):
            return True
        # 有空格、点、长度较长、非全大写编号的都算人类命名的
        if len(stem) > 14 or " " in stem or "." in stem:
            return True
        return False

    named = [p for p in targets if looks_named(os.path.basename(p))]
    coded = [p for p in targets if not looks_named(os.path.basename(p))]
    w("\n[命名状况]")
    w("  像机器编号名: %d  （这些是重命名的对象）" % len(coded))
    w("  已是人类可读名: %d  （默认保持原样，但要主动和用户确认）" % len(named))
    for p in coded[:5]:
        w("    编号名示例: %s" % os.path.basename(p))
    for p in named[:5]:
        w("    已有名示例: %s" % os.path.basename(p))

    # ---- MD5 查重 ----
    w("\n[查重]")
    if args.no_hash:
        w("  已跳过（--no-hash）")
    else:
        w("  正在计算 %d 个文件的 MD5 ..." % len(targets))
        hashes = defaultdict(list)
        for i, p in enumerate(targets, 1):
            try:
                hashes[md5_of(p)].append(p)
            except Exception as e:
                w("    读取失败: %s (%s)" % (p, e))
            if i % 50 == 0:
                w("    ...%d/%d" % (i, len(targets)))

        with io.open(hash_path, "w", encoding="utf-8") as f:
            for h, paths in hashes.items():
                for p in paths:
                    f.write("%s\t%s\n" % (h, p))

        dups = {h: ps for h, ps in hashes.items() if len(ps) > 1}
        w("  唯一哈希数: %d / 文件数: %d" % (len(hashes), len(targets)))
        if dups:
            wasted = sum(os.path.getsize(ps[0]) * (len(ps) - 1) for ps in dups.values())
            w("  **发现重复: %d 组，可回收 %s**" % (len(dups), human(wasted)))
            for h, ps in list(dups.items())[:20]:
                w("    [%s] x%d: %s" % (h[:8], len(ps),
                                        " | ".join(os.path.basename(x) for x in ps)))
        else:
            w("  无重复文件（外协/已筛过的素材库通常是这个结果）")
        w("  哈希表已写出: %s" % hash_path)

    # ---- 安全提示 ----
    w("\n[下一步]")
    w("  1) python frames.py --src \"%s\"        # 抽帧 + 拼联系表" % src)
    w("  2) python transcribe.py --src \"%s\" --out transcript.tsv" % src)
    w("  3) 让 AI 看联系表，产出内容标签，交给 plan.py 生成重命名方案")
    w("\n  注意：本工具只读取素材，不改动任何文件。重命名是独立的最后一步。")

    log.close()
    print("\n报告: %s" % report_path)


if __name__ == "__main__":
    main()
