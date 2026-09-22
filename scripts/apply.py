#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤 5/5 —— 执行重命名，以及一键回滚。

这是唯一会改动文件的步骤。默认是 dry-run，不加 --apply 不会动任何东西。

做法是「两阶段改名」，避免新旧名互相碰撞：

    阶段 1: 所有原文件  ->  __tmp__<随机串>.ext
    阶段 2: __tmp__...  ->  最终名

举例说明为什么需要两阶段：假设要把 A.mp4 改成 B.mp4，同时把 B.mp4 改成 C.mp4。
直接改的话第一步就会覆盖掉还没处理的 B.mp4。先全部挪到临时名再落位，就没有这个问题。

执行过程中每处理一批就复核一次目录文件数，数量对不上立刻停下并回滚本批。

回滚：读 _rename_done.tsv，倒序把「新名 -> 原名」改回去。
映射表 + 回滚脚本就是完整的备份 —— 重命名不改文件内容，所以不需要复制整份素材。

用法：
    python apply.py --src "D:/素材" --mapping _mapping.tsv            # 预览，不动文件
    python apply.py --src "D:/素材" --mapping _mapping.tsv --apply    # 真正执行
    python apply.py --src "D:/素材" --reverse                         # 一键还原
"""
import argparse
import io
import os
import sys
import uuid

DONE_NAME = "_rename_done.tsv"


def read_mapping(path):
    rows = []
    with io.open(path, "r", encoding="utf-8-sig") as f:
        for i, line in enumerate(f):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if i == 0 and parts[0].strip() == "old":
                continue                      # 表头
            if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
                rows.append((parts[0].strip(), parts[1].strip()))
    return rows


def count_files(src):
    """
    统计素材文件数，用于每批的一致性校验。

    必须排除回滚表自己 —— 它是在执行过程中写进素材目录的，
    如果算进去，文件数会凭空 +1，校验永远失败（实测踩过）。
    """
    return sum(1 for f in os.listdir(src)
               if os.path.isfile(os.path.join(src, f)) and f != DONE_NAME)


def do_apply(src, rows, batch):
    # 回滚表就放在素材目录里 —— 回滚时最容易找到，也让用户一眼看到这个目录被动过
    done_path = os.path.join(src, DONE_NAME)
    if os.path.exists(done_path):
        sys.exit("回滚表已存在：%s\n说明这个目录之前已经执行过一次重命名。\n"
                 "想接着改请先回滚（--reverse），或手工删掉这张表。" % done_path)

    before = count_files(src)

    # 前置校验：源文件都在，且不会互相踩
    missing = [o for o, _n in rows if not os.path.exists(os.path.join(src, o))]
    if missing:
        sys.exit("以下源文件不存在，已中止：%s" % ", ".join(missing[:5]))

    tmp_pairs = []
    done_rows = []

    # ---- 阶段 1：原文件 -> 临时名 ----
    print("阶段 1/2  移到临时名 ...")
    for i, (old, new) in enumerate(rows, 1):
        ext = os.path.splitext(old)[1]
        tmp = "__tmp__%s%s" % (uuid.uuid4().hex, ext)
        os.rename(os.path.join(src, old), os.path.join(src, tmp))
        tmp_pairs.append((tmp, new, old))
        if i % batch == 0:
            now = count_files(src)
            if now != before:
                sys.exit("阶段 1 文件数异常（%d -> %d），已中止。手动检查临时文件。" % (before, now))
            print("  ...%d/%d" % (i, len(rows)))

    # ---- 阶段 2：临时名 -> 最终名 ----
    print("阶段 2/2  落到最终名 ...")
    for i, (tmp, new, old) in enumerate(tmp_pairs, 1):
        dst = os.path.join(src, new)
        if os.path.exists(dst):
            sys.exit("目标名已被占用：%s。已中止，请运行 --reverse 还原。" % new)
        os.rename(os.path.join(src, tmp), dst)
        done_rows.append((new, old))

        # 增量写回滚表，中途断电也能还原
        with io.open(done_path, "a", encoding="utf-8") as f:
            f.write("%s\t%s\n" % (new, old))

        if i % batch == 0:
            now = count_files(src)
            if now != before:
                sys.exit("阶段 2 文件数异常（%d -> %d），已中止，请运行 --reverse 还原。" % (before, now))
            print("  ...%d/%d" % (i, len(rows)))

    after = count_files(src)
    print("\n完成")
    print("  文件数   %d -> %d  %s" % (before, after, "OK" if before == after else "**异常**"))
    print("  回滚表   %s" % done_path)
    print("\n回滚命令： python apply.py --src \"%s\" --reverse" % src)


def do_reverse(src):
    done_path = os.path.join(src, DONE_NAME)
    if not os.path.exists(done_path):
        sys.exit("找不到回滚表：%s" % done_path)

    rows = []
    with io.open(done_path, "r", encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2 and p[0].strip():
                rows.append((p[0].strip(), p[1].strip()))

    if not rows:
        sys.exit("回滚表是空的，没有可还原的记录。")

    before = count_files(src)
    ok = fail = 0
    # 倒序还原，避免中途出现文件名互相占用的中间态
    for new, old in reversed(rows):
        s = os.path.join(src, new)
        d = os.path.join(src, old)
        if not os.path.exists(s):
            fail += 1
            continue
        if os.path.exists(d):
            fail += 1
            continue
        os.rename(s, d)
        ok += 1

    after = count_files(src)
    print("回滚完成  还原 %d  跳过 %d" % (ok, fail))
    print("文件数 %d -> %d  %s" % (before, after, "OK" if before == after else "**异常**"))

    # 把回滚表归档掉：留下记录，但不阻塞下一次 apply（否则下次会被"回滚表已存在"拦住）
    if ok:
        bak = os.path.join(src, "_rename_done.reversed.tsv")
        try:
            if os.path.exists(bak):
                os.remove(bak)
            os.rename(done_path, bak)
            print("\n回滚表已归档: %s" % bak)
        except Exception as e:
            print("\n回滚表归档失败（可手工处理）: %s" % e)


def main():
    ap = argparse.ArgumentParser(description="执行重命名 / 一键回滚")
    ap.add_argument("--src", required=True, help="素材目录")
    ap.add_argument("--mapping", default=None, help="plan.py 产出的 _mapping.tsv")
    ap.add_argument("--apply", action="store_true", help="真正执行（不加则只预览）")
    ap.add_argument("--reverse", action="store_true", help="回滚到原名")
    ap.add_argument("--batch", type=int, default=10, help="每批复核文件数的间隔")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        sys.exit("目录不存在: %s" % src)

    if args.reverse:
        print("** 回滚模式：把 _rename_done.tsv 里记录的新名改回原名 **\n")
        do_reverse(src)
        return

    if not args.mapping:
        sys.exit("需要 --mapping 指定映射表（plan.py 的产物）")
    rows = read_mapping(args.mapping)
    if not rows:
        sys.exit("映射表里没有有效数据: %s" % args.mapping)

    print("待处理 %d 个文件" % len(rows))
    for old, new in rows[:5]:
        print("  %s  ->  %s" % (old, new))
    if len(rows) > 5:
        print("  ... 其余 %d 条" % (len(rows) - 5))

    if not args.apply:
        print("\n** 当前是预览模式，素材文件没有被改动。**")
        print("确认无误后加 --apply 执行：")
        print("  python apply.py --src \"%s\" --mapping \"%s\" --apply" % (src, args.mapping))
        return

    print("\n** 即将执行重命名。此操作不可用 Ctrl+Z 撤销，但可用 --reverse 还原。**\n")
    do_apply(src, rows, args.batch)


if __name__ == "__main__":
    main()
