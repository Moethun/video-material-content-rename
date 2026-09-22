#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤 2/5 —— 抽帧 + 拼联系表。

这是整个流程里最重要的一个设计：**把几百个画面压缩成十几张图**。

如果逐个读帧图，286 个素材要读 286 次，成本高到没法做。
改成 4x4 拼一张联系表，286 帧 → 18 张，读 18 次就够，
AI 能在一次读图里同时看到 16 个画面并横向比较。

抽帧位置默认取 50%：避开片头的黑场、对焦过程、开机 logo。

输出（默认写到 --src 同级目录）：
    _frames/     单帧图（增量模式，已存在且比源文件新则跳过）
    _sheets/     联系表 sheet_01.jpg ...
    _meta.json   每个素材的时长/分辨率

用法：
    python frames.py --src "D:/素材"
    python frames.py --src "D:/素材" --cols 5 --rows 4 --at 0.6
    python frames.py --src "D:/素材" --threads 2
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys

try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FF = "ffmpeg"

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("缺少 Pillow，请先 pip install pillow")

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".flv", ".wmv", ".webm",
             ".mpg", ".mpeg", ".ts", ".mts", ".3gp")

# 各平台的中文字体候选，按顺序找第一个存在的
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",                                    # Windows 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",                                  # Windows 黑体
    "/System/Library/Fonts/PingFang.ttc",                            # macOS
    "/System/Library/Fonts/STHeiti Medium.ttc",                      # macOS
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",        # Linux Noto
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",                  # Linux 文泉驿
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def find_font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def probe(path):
    """读容器头取时长和分辨率，只读头部不解码，毫秒级。"""
    dur, w, h = 0.0, 0, 0
    try:
        r = subprocess.run([FF, "-hide_banner", "-i", path],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=30)
        err = r.stderr or ""
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", err)
        if m:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return dur, w, h


def extract_frame(src, dst, at, threads, cell_w):
    """-ss 放在 -i 前面走快速 seek；scale 保持宽高比。"""
    cmd = [FF, "-hide_banner", "-loglevel", "error",
           "-ss", "%.3f" % at, "-i", src,
           "-frames:v", "1",
           "-vf", "scale=%d:-2" % cell_w,
           "-q:v", "4", "-threads", str(threads), "-y", dst]
    subprocess.run(cmd, capture_output=True)
    return os.path.exists(dst)


def build_sheet(frames, out_path, cols, rows, cell_w, cell_h, label_h, font):
    """frames: [(原文件名, 帧图路径), ...]"""
    sw = cell_w * cols
    sh = (cell_h + label_h) * rows
    sheet = Image.new("RGB", (sw, sh), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)

    for idx, (name, fp) in enumerate(frames):
        r, c = divmod(idx, cols)
        x = c * cell_w
        y = r * (cell_h + label_h)
        try:
            im = Image.open(fp).convert("RGB")
            # 等比缩放后居中裁切，避免变形
            ratio = max(cell_w / im.width, cell_h / im.height)
            im = im.resize((max(1, int(im.width * ratio)),
                            max(1, int(im.height * ratio))), Image.LANCZOS)
            left = (im.width - cell_w) // 2
            top = (im.height - cell_h) // 2
            im = im.crop((left, top, left + cell_w, top + cell_h))
            sheet.paste(im, (x, y))
        except Exception:
            draw.rectangle([x, y, x + cell_w, y + cell_h], fill=(60, 20, 20))

        draw.rectangle([x, y + cell_h, x + cell_w, y + cell_h + label_h],
                       fill=(28, 28, 28))
        label = "#%d %s" % (idx + 1, name)
        draw.text((x + 6, y + cell_h + 4), label, fill=(240, 240, 240), font=font)
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h + label_h - 1],
                       outline=(90, 90, 90))

    sheet.save(out_path, "JPEG", quality=88)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="抽帧并拼联系表")
    ap.add_argument("--src", required=True, help="素材目录")
    ap.add_argument("--out-dir", default=None, help="产物目录，默认与 src 同级")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--rows", type=int, default=4)
    ap.add_argument("--cell-w", type=int, default=400)
    ap.add_argument("--cell-h", type=int, default=225)
    ap.add_argument("--at", type=float, default=0.5,
                    help="抽帧位置，占时长的比例。0.5 避开片头黑场")
    ap.add_argument("--threads", type=int, default=2,
                    help="每个 ffmpeg 进程的线程数，压住 CPU 占用")
    ap.add_argument("--recursive", action="store_true")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    out_dir = args.out_dir or os.path.dirname(src)
    frames_dir = os.path.join(out_dir, "_frames")
    sheets_dir = os.path.join(out_dir, "_sheets")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(sheets_dir, exist_ok=True)

    if args.recursive:
        paths = []
        for root, _d, files in os.walk(src):
            paths += [os.path.join(root, f) for f in files]
    else:
        paths = [os.path.join(src, f) for f in os.listdir(src)]
    files = sorted(p for p in paths
                   if os.path.isfile(p) and p.lower().endswith(VIDEO_EXT))

    if not files:
        sys.exit("目录里没有找到视频文件: %s" % src)

    print("素材 %d 个，开始探测时长与抽帧 ..." % len(files))

    meta = {}
    picked = []   # (显示名, 帧路径)
    label_h = max(24, int(args.cell_w * 0.062))
    font = find_font(max(12, int(label_h * 0.55)))

    for i, p in enumerate(files, 1):
        name = os.path.basename(p)
        dur, vw, vh = probe(p)
        at = dur * args.at if dur > 0 else 0.0

        fp = os.path.join(frames_dir, "%04d.jpg" % i)
        need = True
        if os.path.exists(fp) and os.path.getmtime(fp) >= os.path.getmtime(p):
            need = False          # 增量：比源文件新就跳过（避免批量删除触发安全策略）
        if need:
            extract_frame(p, fp, at, args.threads, args.cell_w)

        meta[name] = {"duration": round(dur, 2), "width": vw, "height": vh,
                      "frame": os.path.basename(fp)}
        if os.path.exists(fp):
            picked.append((name, fp))

        if i % 25 == 0:
            print("  ...%d/%d" % (i, len(files)))

    # 输出元数据
    meta_path = os.path.join(out_dir, "_meta.json")
    with io.open(meta_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=1))

    # 拼联系表
    per = args.cols * args.rows
    n_sheets = 0
    for start in range(0, len(picked), per):
        chunk = picked[start:start + per]
        n_sheets += 1
        out = os.path.join(sheets_dir, "sheet_%02d.jpg" % n_sheets)
        build_sheet(chunk, out, args.cols, args.rows,
                    args.cell_w, args.cell_h, label_h, font)
        print("  联系表 %s  (%d 格: #%d-%d)"
              % (os.path.basename(out), len(chunk), start + 1, start + len(chunk)))

    # 时长分布，帮助判断要不要分档抽帧
    durs = [m["duration"] for m in meta.values() if m["duration"] > 0]
    print("\n完成")
    print("  帧图      %s" % frames_dir)
    print("  联系表    %s  （共 %d 张，每张 %d 格）"
          % (sheets_dir, n_sheets, per))
    print("  元数据    %s" % meta_path)
    if durs:
        print("  时长      min %.1fs / avg %.1fs / max %.1fs"
              % (min(durs), sum(durs) / len(durs), max(durs)))
    print("\n下一步：把 %s 里的图交给 AI 逐张识别，产出内容标签。" % sheets_dir)


if __name__ == "__main__":
    main()
