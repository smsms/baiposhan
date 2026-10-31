#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把本仓库打包成可分发的技能 zip（顶层目录 = frontmatter 里的 name）。

用法：
    python tools/build_package.py                 # 输出到 dist/
    python tools/build_package.py --out /tmp/out  # 指定输出目录

产物可直接用于：
  - WorkBuddy 客户端「技能 · 添加技能 · 上传技能」
  - WorkBuddy 开放平台（open.workbuddy.cn）技能上架提交
"""

import argparse
import os
import re
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", ".github", "dist", "docs", "tests", "tools", "examples"}
SKIP_FILES = {"CONTRIBUTING.md", "CHANGELOG.md", "README.md", "LICENSE", "requirements.txt", ".gitignore"}
SKIP_EXT = {".pyc", ".bak"}


def read_frontmatter(path):
    text = open(path, encoding="utf-8").read()
    if not text.startswith("---"):
        raise SystemExit("SKILL.md 缺少 YAML frontmatter")
    fm = text.split("---", 2)[1]
    meta = {}
    for line in fm.splitlines():
        m = re.match(r"^([A-Za-z_\-]+):\s*(.+)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return meta


def build(out_dir):
    meta = read_frontmatter(os.path.join(REPO, "SKILL.md"))
    name = meta.get("name") or os.path.basename(REPO)
    version = meta.get("version") or "0.0.0"
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        raise SystemExit("frontmatter 的 name 必须是 hyphen-case，当前为：%s" % name)

    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, "%s-v%s.zip" % (name, version))
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(REPO):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in sorted(filenames):
                if fn in SKIP_FILES or os.path.splitext(fn)[1] in SKIP_EXT:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, REPO).replace("\\", "/")
                z.write(full, "%s/%s" % (name, rel))
                count += 1
    return zip_path, count, name, version


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="打包技能为可分发 zip")
    ap.add_argument("--out", default=os.path.join(REPO, "dist"), help="输出目录，默认 dist/")
    args = ap.parse_args()
    path, count, name, version = build(args.out)
    print("技能：%s v%s，%d 个文件" % (name, version, count))
    print("[成功] %s（%.1f KB）" % (path, os.path.getsize(path) / 1024.0))
