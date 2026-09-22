#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤 4/5 —— 生成重命名方案，并做干跑校验。

**这一步绝对不改文件**，只产出方案和给用户核对的对照表。
真正动手是最后一步 apply.py。

输入是「内容标签」：由 AI 看完联系表后给出，或人工写。
支持两种标签文件格式，自动识别：

  A. 位置模式（每行一个标签，顺序 = 文件名排序顺序）
        街拍空镜
        博主口播
        咖啡拉花特写

  B. 映射模式（文件名 <TAB> 标签，不怕错位，推荐）
        街拍空镜\tC0769.MP4
        ...

新名规则：`标签_序号.扩展名`，序号在**同一标签内**按原文件名顺序递增。
这样同类素材的先后顺序与拍摄顺序一致，剪辑时能按序取。

干跑会检查：
  - 源文件是否存在
  - 目标名是否有非法字符 \\ / : * ? " < > |
  - 目标名是否唯一
  - 是否与目录里现存的其他文件冲突
  - 标签长度是否超标（默认 10 字）

用法：
    python plan.py --src "D:/素材" --labels labels.txt --out-dir "D:/out"
    python plan.py --src "D:/素材" --labels labels.tsv --max-len 12
    python plan.py --src "D:/素材" --labels labels.txt --transcript transcript.tsv
"""
import argparse
import base64
import io
import json
import os
import sys
from collections import Counter, defaultdict

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".flv", ".wmv", ".webm",
             ".mpg", ".mpeg", ".ts", ".mts", ".3gp")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".tif", ".tiff")
MEDIA_EXT = VIDEO_EXT + IMAGE_EXT

ILLEGAL = set('\\/:*?"<>|')


def load_labels(path, files):
    """
    返回 {文件名: 标签}。
    自动识别位置模式 / 映射模式。
    """
    with io.open(path, "r", encoding="utf-8-sig") as f:
        raw = [l.rstrip("\n") for l in f if l.strip()]

    if not raw:
        sys.exit("标签文件是空的: %s" % path)

    # 有 tab 且第一列能在文件列表里找到 -> 映射模式
    first = raw[0].split("\t")
    basenames = {os.path.basename(p) for p in files}

    if len(first) >= 2 and (first[1].strip() in basenames or first[0].strip() in basenames):
        mapping = {}
        for line in raw:
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            a, b = parts[0].strip(), parts[1].strip()
            if b in basenames:
                mapping[b] = a          # 标签<TAB>文件名
            elif a in basenames:
                mapping[a] = b          # 文件名<TAB>标签
        if mapping:
            return mapping
        sys.exit("映射模式下没解析出任何有效行")

    # 位置模式
    if len(raw) != len(files):
        sys.exit("标签数与文件数不符：标签 %d 个，文件 %d 个。\n"
                 "位置模式要求严格一一对应，数量不等会导致整体错位。\n"
                 "建议改用映射模式：文件名<TAB>标签。" % (len(raw), len(files)))
    return {os.path.basename(p): raw[i].strip() for i, p in enumerate(files)}


def build_html(rows, out_path, title):
    """rows: [{old, new, label, thumb_b64, voice, text}]"""
    cards = []
    for r in rows:
        thumb = ('<img src="data:image/jpeg;base64,%s" loading="lazy">' % r["thumb_b64"]
                 if r.get("thumb_b64") else '<div class="noimg">无缩略图</div>')
        extra = ""
        if r.get("voice") is not None:
            badge = '<span class="v">有口播</span>' if r["voice"] == "Y" \
                else '<span class="n">空镜</span>'
            extra = '<div class="tt">%s %s</div>' % (badge, r.get("text", "") or "")
        cards.append("""
    <div class="card">
      %s
      <div class="nm"><b>%s</b></div>
      <div class="old">%s</div>
      %s
    </div>""" % (thumb, r["new"], r["old"], extra))

    # 注意：CSS 里有 100%% 这类内容，绝不能用 % 格式化拼装，必须用 replace
    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>__TITLE__</title>
<style>
 body{margin:0;padding:24px;background:#f6f6f7;color:#1a1a1a;
      font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
 h1{font-size:20px;margin:0 0 6px}
 .sub{color:#666;font-size:13px;margin-bottom:18px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
 .card{background:#fff;border:1px solid #e4e4e7;border-radius:10px;overflow:hidden}
 .card img{width:100%;display:block;background:#000;aspect-ratio:16/9;object-fit:cover}
 .noimg{aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;
        background:#ececef;color:#999;font-size:13px}
 .nm{padding:8px 10px 2px;font-size:14px;color:#0b57d0;word-break:break-all}
 .old{padding:0 10px 8px;font-size:12px;color:#8a8a8e;word-break:break-all}
 .tt{padding:0 10px 10px;font-size:12px;color:#555;line-height:1.5;word-break:break-word}
 .v{background:#e7f5ea;color:#1a7f37;border-radius:4px;padding:1px 5px;margin-right:5px}
 .n{background:#f0f0f2;color:#777;border-radius:4px;padding:1px 5px;margin-right:5px}
</style></head><body>
<h1>__TITLE__</h1>
<div class="sub">共 __COUNT__ 条。每条显示缩略图、新名、原名。核对无误后再执行重命名。</div>
<div class="grid">__CARDS__
</div></body></html>"""

    html = html.replace("__TITLE__", title)
    html = html.replace("__COUNT__", str(len(rows)))
    html = html.replace("__CARDS__", "\n".join(cards))

    with io.open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="生成重命名方案 + 干跑校验")
    ap.add_argument("--src", required=True, help="素材目录")
    ap.add_argument("--labels", required=True, help="标签文件")
    ap.add_argument("--out-dir", default=None, help="产物目录，默认与 src 同级")
    ap.add_argument("--max-len", type=int, default=10, help="标签最大字数")
    ap.add_argument("--sep", default="_", help="标签与序号之间的分隔符")
    ap.add_argument("--transcript", default=None,
                    help="可选：transcribe.py 产出的 tsv，会展示在对照表里")
    ap.add_argument("--no-html", action="store_true", help="不生成 HTML 对照表")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    out_dir = args.out_dir or os.path.dirname(src)
    os.makedirs(out_dir, exist_ok=True)

    files = sorted(os.path.join(src, f) for f in os.listdir(src)
                   if os.path.isfile(os.path.join(src, f))
                   and f.lower().endswith(MEDIA_EXT))
    if not files:
        sys.exit("目录里没有找到媒体文件: %s" % src)

    labels = load_labels(args.labels, files)

    # ---- 生成新名：类内序号 ----
    counter = defaultdict(int)
    rows = []
    missing_label = []
    for p in files:
        base = os.path.basename(p)
        stem, ext = os.path.splitext(base)
        label = labels.get(base)
        if not label:
            missing_label.append(base)
            label = stem
        counter[label] += 1
        new = "%s%s%02d%s" % (label, args.sep, counter[label], ext)
        rows.append({"old": base, "new": new, "label": label, "path": p})

    # ---- 校验 ----
    errors, warns = [], []

    if missing_label:
        errors.append("以下文件没有标签，会用原文件名兜底：%s%s"
                      % (", ".join(missing_label[:5]),
                         " ..." if len(missing_label) > 5 else ""))

    targets = Counter(r["new"] for r in rows)
    dup = [n for n, c in targets.items() if c > 1]
    if dup:
        errors.append("目标名重复：%s" % ", ".join(dup[:5]))

    for r in rows:
        bad = ILLEGAL & set(r["new"])
        if bad:
            errors.append("目标名含非法字符 %s：%s" % ("".join(bad), r["new"]))
        if len(r["label"]) > args.max_len:
            warns.append("标签超过 %d 字：%s" % (args.max_len, r["label"]))
        if not os.path.exists(r["path"]):
            errors.append("源文件不存在：%s" % r["old"])

    # 与目录里现存的其他文件冲突（注意排除掉自己被改名的那些）
    existing = {f for f in os.listdir(src) if os.path.isfile(os.path.join(src, f))}
    originals = {r["old"] for r in rows}
    collateral = [n for n in targets if n in existing and n not in originals]
    if collateral:
        errors.append("目标名与目录里未参与重命名的文件冲突：%s"
                      % ", ".join(collateral[:5]))

    # ---- 输出 ----
    mapping_path = os.path.join(out_dir, "_mapping.tsv")
    with io.open(mapping_path, "w", encoding="utf-8") as f:
        f.write("old\tnew\tlabel\n")
        for r in rows:
            f.write("%s\t%s\t%s\n" % (r["old"], r["new"], r["label"]))

    print("素材 %d 个，归入 %d 个标签" % (len(rows), len(counter)))
    print("\n标签分布（前 25）：")
    for lb, n in Counter(r["label"] for r in rows).most_common(25):
        print("  %-16s %d" % (lb, n))

    # 语音信息（可选）
    voice_map = {}
    if args.transcript and os.path.exists(args.transcript):
        with io.open(args.transcript, "r", encoding="utf-8") as f:
            next(f, None)
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 6:
                    voice_map[p[0]] = (p[2], p[5])
        print("\n已载入语音索引 %d 条" % len(voice_map))

    # HTML 对照表
    if not args.no_html:
        meta_path = os.path.join(out_dir, "_meta.json")
        frame_map = {}
        if os.path.exists(meta_path):
            try:
                with io.open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                frame_map = {k: v.get("frame") for k, v in meta.items()}
            except Exception:
                pass

        for r in rows:
            fr = frame_map.get(r["old"])
            if fr:
                fp = os.path.join(out_dir, "_frames", fr)
                if os.path.exists(fp):
                    try:
                        with open(fp, "rb") as fh:
                            r["thumb_b64"] = base64.b64encode(fh.read()).decode()
                    except Exception:
                        pass
            if r["old"] in voice_map:
                r["voice"], r["text"] = voice_map[r["old"]]

        html_path = os.path.join(out_dir, "重命名对照表.html")
        build_html(rows, html_path, "重命名对照表（%d 条）" % len(rows))
        print("\n对照表: %s" % html_path)

    # tsv 版
    tsv_path = os.path.join(out_dir, "重命名对照表.tsv")
    with io.open(tsv_path, "w", encoding="utf-8-sig") as f:
        f.write("原名\t新名\t标签\n")
        for r in rows:
            f.write("%s\t%s\t%s\n" % (r["old"], r["new"], r["label"]))
    print("表格版: %s" % tsv_path)
    print("映射表: %s" % mapping_path)

    # ---- 校验结论 ----
    print("\n" + "=" * 56)
    if errors:
        print("干跑失败，先修掉这些问题：")
        for e in errors:
            print("  [错误] %s" % e)
    else:
        print("干跑通过：源文件齐全、目标名唯一、无非法字符、无冲突。")
    if warns:
        print("提示：")
        for x in warns[:10]:
            print("  [提示] %s" % x)
    print("=" * 56)
    if not errors:
        print("\n下一步（确认对照表无误后执行）：")
        print("  python apply.py --src \"%s\" --mapping \"%s\" --apply" % (src, mapping_path))
        print("\n这一步只是生成方案，素材文件尚未被改动。")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
