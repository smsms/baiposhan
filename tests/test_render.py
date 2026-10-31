#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""冒烟测试：不起网络、不需要 AK，校验技能结构与出图能力。

直接运行：python tests/test_render.py
也兼容 pytest：pytest tests/
"""

import os
import re
import sys
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import sketch_map  # noqa: E402

SPEC = os.path.join(REPO, "examples", "trip.example.json")
REQUIRED_FIELDS = [
    "name",
    "display_name",
    "display_name_en",
    "description",
    "description_zh",
    "description_en",
    "version",
    "author",
]


def test_frontmatter():
    text = open(os.path.join(REPO, "SKILL.md"), encoding="utf-8").read()
    assert text.startswith("---"), "SKILL.md 必须以 frontmatter 开始"
    fm = text.split("---", 2)[1]
    for field in REQUIRED_FIELDS:
        assert re.search(r"^%s:" % field, fm, re.M), "缺少必填字段 %s" % field
    name = re.search(r"^name:\s*(\S+)", fm, re.M).group(1)
    assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name), "name 不是 hyphen-case：%s" % name


def test_layout_is_continuous_with_arrows():
    title, stops = sketch_map.load_spec(SPEC)
    lay = sketch_map.build_layout(title, stops)
    st = lay["stats"]
    assert st["nodes"] == len(stops)
    # 连续路线：n 个节点应有 n-1 段路径（含跨日转场）
    assert st["legs"] == len(stops) - 1, "路径段数应为节点数-1，实际 %d" % st["legs"]
    assert st["transfers"] >= 1, "示例含跨日行程，应存在转场段"
    assert st["arrows"] > 0, "应有方向箭头"
    # 每段路径首尾相接（不按天断开）
    for a, b in zip(lay["legs"], lay["legs"][1:]):
        assert (a["x1"], a["y1"]) == (b["x0"], b["y0"]), "路径在 %s 处断开" % a


def test_svg_output(tmp_path=None):
    title, stops = sketch_map.load_spec(SPEC)
    lay = sketch_map.build_layout(title, stops)
    svg = sketch_map.to_svg(lay)
    root = ET.fromstring(svg.split("\n", 1)[1])  # 去掉 XML 声明
    tags = [e.tag.split("}")[-1] for e in root.iter()]
    assert tags.count("line") > 0 and tags.count("polygon") > 0 and tags.count("text") > 0
    # 箭头数 = SVG 中的 polygon 数（箭头全为三角形，图例里另有 1 个）
    assert tags.count("polygon") >= lay["stats"]["arrows"], "SVG 中箭头数量不足"


def test_label_placement():
    title, stops = sketch_map.load_spec(SPEC)
    lay = sketch_map.build_layout(title, stops)
    assert lay["stats"]["labels_placed"] == len(stops), "示例行程的标签应全部放下"


def test_png_backend_optional():
    if sketch_map.Image is None:
        print("跳过：未安装 Pillow，不做位图检查")
        return
    title, stops = sketch_map.load_spec(SPEC)
    lay = sketch_map.build_layout(title, stops)
    img = sketch_map.to_png(lay, dpi=1)
    assert img.size == (lay["w"], lay["h"])


def test_duplicate_and_single_node():
    single = [{"name": "A", "lng": 104.05, "lat": 30.66, "day": 1, "lodging": False}]
    lay = sketch_map.build_layout("单节点", single)
    assert lay["stats"]["legs"] == 0
    dup = single + [{"name": "B", "lng": 104.05, "lat": 30.66, "day": 2, "lodging": False}]
    lay = sketch_map.build_layout("重合坐标", dup)
    assert lay["stats"]["legs"] == 0, "坐标重合时不应画出零长度路径"


if __name__ == "__main__":
    failed = 0
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        try:
            fn()
            print("[PASS] %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("[FAIL] %s -> %s" % (fn.__name__, e))
    print("\n%s" % ("全部通过" if not failed else "%d 项失败" % failed))
    sys.exit(1 if failed else 0)
