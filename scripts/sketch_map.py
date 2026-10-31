#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""零 AK 路线示意图渲染器：默认输出 SVG 真矢量图，可选 PNG 位图。

为什么是矢量：原先用 Pillow 出 PNG，放大即糊、打印发虚，且「矢量示意图」名不副实。
现在几何布局只算一次，SVG 后端把折线/箭头/文字直接写成 <path>/<line>/<text>，
可无损缩放、可进 Illustrator/Figma 二次编辑；PNG 后端退为可选（需要 Pillow）。

路线为什么改成连续：按天分别画折线时，Day N 的终点与 Day N+1 的起点之间没有线，
读图会以为行程断成几段。现在按「日期 → 当日顺序」把全部节点串成一条连续折线，
跨日转场段同样相连（用出发日配色），并在每段路径上按等距加方向箭头，
箭头一律指向下一站，图例中标注「行进方向」。

它不是地图瓦片底图，而是矢量示意图：只有相对位置关系，
没有真实道路与建筑。页面底部会明确标注这一点，避免误导。

用法：
  python sketch_map.py --spec trip.json --out trip.svg              # 矢量（推荐）
  python sketch_map.py --spec trip.json --out trip.svg --png --html  # 同时出位图 + 说明页
  python sketch_map.py --spec trip.json --out trip.png              # 位图（需 Pillow）
"""

import argparse
import html
import json
import math
import os
import sys
import time
from xml.sax.saxutils import escape as xml_escape

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与 baidu_map.py 保持一致的多日配色
DAY_COLORS = ["D93025", "1A73E8", "0F9D58", "7B1FA2", "F29900", "00ACC1", "C2185B", "6D4C41", "546E7A"]
LODGING_COLOR = "37474F"
INK = "1F2933"
MUTED = "6B7785"
GRID = "E8ECF0"
PANEL_BG = "F7F9FB"
RULE = "DDE3EA"

FONT_STACK = "Microsoft YaHei, PingFang SC, Hiragino Sans GB, Noto Sans CJK SC, Source Han Sans SC, sans-serif"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

# 箭头几何参数（单位与画布一致，默认画布宽 1280）
ARROW_LEN = 13.0
ARROW_HALF = 5.6
ARROW_SPACING = 118.0
ARROW_MIN_LEG = 46.0


# --------------------------------------------------------------------------- 准备


def hex_rgb(h):
    h = h.strip()
    if h.lower().startswith("0x"):
        h = h[2:]
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def day_rgb(day):
    return hex_rgb(DAY_COLORS[(max(1, int(day or 1)) - 1) % len(DAY_COLORS)])


def day_hex(day):
    return DAY_COLORS[(max(1, int(day or 1)) - 1) % len(DAY_COLORS)]


def load_font(size, bold=False):
    paths = FONT_CANDIDATES
    if bold:
        paths = [p for p in FONT_CANDIDATES if "bd" in p or "hei" in p] + FONT_CANDIDATES
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def check_coord(lng, lat, label="节点"):
    """范围校验，拦截经纬度顺序写反（中国境内纬度不会超过 54）。"""
    if not (-180.0 <= lng <= 180.0):
        raise SystemExit("%s 经度越界：%.6f。常见原因是经纬度顺序写反。" % (label, lng))
    if not (-90.0 <= lat <= 90.0):
        raise SystemExit("%s 纬度越界：%.6f。要求 lng 在前、lat 在后。" % (label, lat))


def load_spec(path):
    """读取行程文件，返回 (title, stops)。字段与 baidu_map.py 的 staticmap 一致。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("stops") or data.get("nodes") or []
    stops = []
    for i, s in enumerate(raw, start=1):
        lng, lat = s.get("lng", s.get("lon")), s.get("lat")
        if lng is None or lat is None:
            raise SystemExit("第 %d 个节点缺少 lng/lat：%s" % (i, s.get("name") or ""))
        lng, lat = float(lng), float(lat)
        check_coord(lng, lat, "第 %d 个节点「%s」" % (i, s.get("name") or ""))
        stops.append(
            {
                "name": str(s.get("name") or "节点%d" % i),
                "lng": float(lng),
                "lat": float(lat),
                "day": int(s.get("day") or 1),
                "lodging": bool(s.get("lodging")),
            }
        )
    if not stops:
        raise SystemExit("stops 为空，无法出图")
    return data.get("title") or "旅行路线图", stops


def mercator(lng, lat):
    """Web 墨卡托投影，只用于平面排版，与坐标类型无关。"""
    x = lng
    lat = max(-85.0, min(85.0, lat))
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 180.0 / math.pi
    return x, y


def nice_step(span, target_lines=5):
    """把跨度切成好看的刻度间隔（1/2/5 × 10^n）。"""
    if span <= 0:
        return 0.001
    raw = span / max(1, target_lines)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def text_w(text, size, bold=False):
    """无字体度量时的文字宽度估算：全角按 1em，半角按 0.55em。

    SVG 端不需要真实度量（浏览器自己排），这里只用于标签避让的排版预留。
    """
    w = 0.0
    for ch in text:
        w += 0.55 if ord(ch) < 128 else 1.0
    return w * size * (1.04 if bold else 1.0)


def label_baseline(y_top, size):
    """SVG 用基线定位文字，把左上角坐标换算成基线 y。"""
    return y_top + size * 0.86


def arrow_points(x, y, ang_deg, length=ARROW_LEN, half=ARROW_HALF):
    """以 (x, y) 为中心、朝向 ang_deg 的实心三角形三个顶点。"""
    a = math.radians(ang_deg)
    ca, sa = math.cos(a), math.sin(a)
    tip = (x + ca * length * 0.58, y + sa * length * 0.58)
    back_c = (x - ca * length * 0.42, y - sa * length * 0.42)
    left = (back_c[0] - sa * half, back_c[1] + ca * half)
    right = (back_c[0] + sa * half, back_c[1] - ca * half)
    return [tip, left, right]


def _hit(a, b):
    return not (a["x1"] <= b["x0"] or b["x1"] <= a["x0"] or a["y1"] <= b["y0"] or b["y1"] <= a["y0"])


# --------------------------------------------------------------------------- 布局


def build_layout(title, stops, width=1280, height=860, panel=True):
    """一次性算完所有几何：投影取景、节点、连续路径、箭头、标签避让、图例。

    返回纯数据字典，SVG 与 PNG 两个后端都只负责「照着画」，
    保证两种输出格式的版式完全一致。
    """
    W, H = float(width), float(height)
    panel_w = min(max(W * 0.30, 300.0), 460.0) if panel else 0.0
    map_w = W - panel_w
    pad = 58.0
    top_reserved = 78.0
    bottom_reserved = 76.0

    n = len(stops)
    r = 15.0 if n <= 26 else 12.5

    # 时间顺序：先按天，再按文件中的书写顺序（书写顺序即当日游览顺序）
    ordered = sorted(range(n), key=lambda i: (stops[i]["day"], i))
    seq = {}
    counter = {}
    for i in ordered:
        d = stops[i]["day"]
        counter[d] = counter.get(d, 0) + 1
        seq[i] = counter[d]

    # ---- 投影与取景
    mx, my = [], []
    for s in stops:
        a, b = mercator(s["lng"], s["lat"])
        mx.append(a)
        my.append(b)
    minx, maxx = min(mx), max(mx)
    miny, maxy = min(my), max(my)

    avail_w = map_w - 2 * pad
    avail_h = H - top_reserved - bottom_reserved - pad
    spanx = maxx - minx
    spany = maxy - miny
    degenerate = spanx <= 1e-6 and spany <= 1e-6
    if degenerate:
        scale = 1.0
        offx = pad + avail_w / 2
        offy = top_reserved + pad + avail_h / 2
    else:
        spanx = max(spanx, 1e-6)
        spany = max(spany, 1e-6)
        scale = min(avail_w / spanx, avail_h / spany)
        if not (scale > 0) or scale == float("inf"):
            scale = 1.0
        offx = pad + (avail_w - spanx * scale) / 2
        offy = top_reserved + pad + (avail_h - spany * scale) / 2

    pos = [(offx + (mx[i] - minx) * scale, offy + (maxy - my[i]) * scale) for i in range(n)]

    # ---- 节点
    nodes = []
    for i, s in enumerate(stops):
        x, y = pos[i]
        nodes.append(
            {
                "name": s["name"],
                "x": x,
                "y": y,
                "r": r,
                "color": LODGING_COLOR if s["lodging"] else day_hex(s["day"]),
                "label": "住" if s["lodging"] else str(seq[i]),
                "day": s["day"],
                "order": seq[i],
                "lodging": s["lodging"],
                "start": False,
                "end": False,
            }
        )
    nodes[ordered[0]]["start"] = True
    nodes[ordered[-1]]["end"] = True

    # ---- 连续路径：按时间顺序把相邻节点逐段相连（含跨日转场），不按天断开
    legs = []
    arrows = []
    for a, b in zip(ordered, ordered[1:]):
        x0, y0 = pos[a]
        x1, y1 = pos[b]
        dx, dy = x1 - x0, y1 - y0
        leg_len = math.hypot(dx, dy)
        if leg_len < 1.0:
            continue
        color = day_hex(stops[a]["day"])
        legs.append(
            {
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "color": color,
                "transfer": stops[a]["day"] != stops[b]["day"],
            }
        )
        if leg_len < ARROW_MIN_LEG:
            continue
        inset = min(r + 13.0, leg_len * 0.34)
        s0, s1 = inset, leg_len - inset
        usable = s1 - s0
        if usable <= 8:
            continue
        k = max(1, min(4, int(round(usable / ARROW_SPACING)) or 1))
        ang = math.degrees(math.atan2(dy, dx))
        for j in range(k):
            t = (s0 + usable * (j + 0.5) / k) / leg_len
            arrows.append(
                {
                    "x": x0 + dx * t,
                    "y": y0 + dy * t,
                    "ang": ang,
                    "color": color,
                }
            )

    # ---- 标签：四方向候选 + 碰撞避让
    occupied = [
        {"x0": nd["x"] - r - 2, "y0": nd["y"] - r - 2, "x1": nd["x"] + r + 2, "y1": nd["y"] + r + 2}
        for nd in nodes
    ]
    labels = []
    f_size = 14.0
    gap = 10.0
    bottom_limit = H - 86.0
    placed_n = 0
    clipped = 0
    for i in ordered:
        nd = nodes[i]
        tw = text_w(nd["name"], f_size, bold=True)
        th = f_size * 1.3
        x, y = nd["x"], nd["y"]
        cands = [
            (x + r + gap, y - th / 2),  # 右
            (x - r - gap - tw, y - th / 2),  # 左
            (x - tw / 2, y - r - gap - th),  # 上
            (x - tw / 2, y + r + gap),  # 下
        ]
        in_bounds = False
        for cx, cy in cands:
            rect = {"x0": cx, "y0": cy, "x1": cx + tw, "y1": cy + th}
            if rect["x0"] < 4 or rect["x1"] > map_w - 4 or rect["y0"] < top_reserved - 24 or rect["y1"] > bottom_limit:
                continue
            in_bounds = True
            if any(_hit(rect, o) for o in occupied):
                continue
            occupied.append(rect)
            labels.append({"x": cx, "y": cy, "text": nd["name"], "size": f_size})
            placed_n += 1
            break
        else:
            if not in_bounds:
                clipped += 1

    # ---- 经纬网
    total_span = max(maxx - minx, maxy - miny)
    step = nice_step(total_span if total_span > 0 else 0.01, 5)
    grid_v, grid_h = [], []
    gx = math.floor(minx / step) * step
    guard = 0
    while gx <= maxx + step and guard < 200:
        x = offx + (gx - minx) * scale
        if 0 <= x <= map_w:
            grid_v.append(x)
        gx += step
        guard += 1
    guard = 0
    gy = math.floor(miny / step) * step
    while gy <= maxy + step and guard < 200:
        y = offy + (maxy - gy) * scale
        if top_reserved <= y <= H:
            grid_h.append(y)
        gy += step
        guard += 1

    # ---- 比例尺
    mean_lat = sum(s["lat"] for s in stops) / n
    m_per_deg = max(111320.0 * math.cos(math.radians(mean_lat)), 1.0)
    if degenerate:
        bar_px, bar_text = 120.0, "比例尺不适用"
    else:
        bar_m = nice_step(120.0 / max(scale, 1e-9) * m_per_deg, 1)
        bar_px = bar_m / m_per_deg * scale
        bar_text = ("%.0f 米" % bar_m) if bar_m < 1000 else ("%.1f 公里" % (bar_m / 1000.0))
    scale_bar = {
        "x": pad,
        "y": H - 46.0,
        "px": bar_px,
        "text": bar_text,
        "note": "示意图：仅表示相对位置与行进顺序，非实际道路底图",
    }

    north = {"cx": map_w - 48.0, "cy": H - 56.0, "r": 16.0}

    # ---- 右侧图例
    legend = None
    days = sorted({s["day"] for s in stops})
    if panel_w:
        x0 = W - panel_w + 26.0
        y = 30.0
        items = [{"kind": "title", "x": x0, "y": y, "text": "行程图例", "size": 26.0}]
        y += 46.0
        for day in days:
            items.append({"kind": "swatch", "x": x0, "y": y, "color": day_hex(day), "text": "Day %d" % day, "size": 14.0})
            y += 30.0
        if any(s["lodging"] for s in stops):
            items.append({"kind": "swatch", "x": x0, "y": y, "color": LODGING_COLOR, "text": "住宿点", "size": 14.0})
            y += 30.0
        items.append({"kind": "arrow", "x": x0, "y": y, "color": INK, "text": "行进方向（全程连续）", "size": 14.0})
        y += 30.0
        items.append({"kind": "ring", "x": x0, "y": y, "color": INK, "text": "起点 / 终点", "size": 14.0})
        y += 30.0
        y += 10.0
        items.append({"kind": "rule", "x": x0, "x1": W - 26.0, "y": y})
        y += 18.0
        limit = H - 96.0
        shown = 0
        for day in days:
            if y > limit:
                break
            items.append({"kind": "group", "x": x0, "y": y, "color": day_hex(day), "text": "Day %d" % day, "size": 14.0})
            y += 26.0
            for i in ordered:
                nd = nodes[i]
                if nd["day"] != day:
                    continue
                if y > limit:
                    break
                items.append(
                    {"kind": "node", "x": x0, "y": y, "text": "%s  %s" % (nd["label"], nd["name"]), "size": 14.0}
                )
                y += 24.0
                shown += 1
            y += 8.0
        if shown < n:
            items.append({"kind": "note", "x": x0, "y": y, "text": "（其余 %d 个节点未列出）" % (n - shown), "size": 11.0})
        legend = {"x0": x0, "items": items}

    return {
        "w": int(width),
        "h": int(height),
        "panel_w": panel_w,
        "map_w": map_w,
        "pad": pad,
        "top": top_reserved,
        "title": title,
        "subtitle": "共 %d 天 · %d 个节点 · 数字为当日游览顺序 · 箭头为行进方向" % (len(days), n),
        "grid_v": grid_v,
        "grid_h": grid_h,
        "nodes": nodes,
        "legs": legs,
        "arrows": arrows,
        "labels": labels,
        "scale": scale_bar,
        "north": north,
        "legend": legend,
        "stats": {
            "nodes": n,
            "days": len(days),
            "legs": len(legs),
            "transfers": sum(1 for lg in legs if lg["transfer"]),
            "arrows": len(arrows),
            "labels_total": n,
            "labels_placed": placed_n,
            "labels_clipped": clipped,
            "labels_dropped": n - placed_n,
            "panel": bool(panel_w),
            "size": [int(width), int(height)],
        },
    }


# --------------------------------------------------------------------------- SVG 后端


def _svg_text(x, y, s, size, fill, anchor="start", bold=False, halo=0.0):
    halo_attr = ""
    if halo:
        halo_attr = (
            ' stroke="#FFFFFF" stroke-width="%.2f" stroke-linejoin="round" paint-order="stroke"' % halo
        )
    return (
        '<text x="%.2f" y="%.2f" font-size="%.1f" font-weight="%s" fill="#%s" text-anchor="%s"%s>%s</text>'
        % (x, y, size, "bold" if bold else "normal", fill, anchor, halo_attr, xml_escape(s))
    )


def to_svg(lay):
    """把布局数据写成 SVG 字符串（真矢量：文字仍是文字，可搜索、可编辑）。"""
    W, H = lay["w"], lay["h"]
    p = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
        'font-family="%s">' % (W, H, W, H, FONT_STACK),
        "<title>%s</title>" % xml_escape(lay["title"]),
        "<desc>分日行程路线示意图：路线按时间顺序连续连线并以箭头标注行进方向，仅表示节点相对位置，不含真实道路底图。</desc>",
        '<rect x="0" y="0" width="%d" height="%d" fill="#FFFFFF"/>' % (W, H),
    ]
    if lay["panel_w"]:
        p.append(
            '<rect x="%.2f" y="0" width="%.2f" height="%d" fill="#%s"/>'
            % (W - lay["panel_w"], lay["panel_w"], H, PANEL_BG)
        )

    for x in lay["grid_v"]:
        p.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#%s" stroke-width="1"/>' % (x, lay["top"], x, H, GRID))
    for y in lay["grid_h"]:
        p.append('<line x1="0" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#%s" stroke-width="1"/>' % (y, lay["map_w"], y, GRID))

    # 连续路径：逐段相连，round linecap 保证拐点无缝
    for lg in lay["legs"]:
        p.append(
            '<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#%s" stroke-width="5" stroke-linecap="round"/>'
            % (lg["x0"], lg["y0"], lg["x1"], lg["y1"], lg["color"])
        )

    # 方向箭头（白描边把箭头从同色线条上「抠」出来）
    for ar in lay["arrows"]:
        pts = " ".join("%.2f,%.2f" % (pt[0], pt[1]) for pt in arrow_points(ar["x"], ar["y"], ar["ang"]))
        p.append(
            '<polygon points="%s" fill="#%s" stroke="#FFFFFF" stroke-width="1.6" stroke-linejoin="round"/>'
            % (pts, ar["color"])
        )

    # 节点名称（白描边保证压在网格/连线上仍可读）
    for lb in lay["labels"]:
        p.append(_svg_text(lb["x"], label_baseline(lb["y"], lb["size"]), lb["text"], lb["size"], INK, bold=True, halo=3.2))

    # 起点/终点环 → 节点圆 → 序号
    for nd in lay["nodes"]:
        if nd["start"]:
            p.append(
                '<circle cx="%.2f" cy="%.2f" r="%.2f" fill="none" stroke="#%s" stroke-width="2.4"/>'
                % (nd["x"], nd["y"], nd["r"] + 6, nd["color"])
            )
        if nd["end"]:
            p.append(
                '<circle cx="%.2f" cy="%.2f" r="%.2f" fill="none" stroke="#%s" stroke-width="2.4"/>'
                % (nd["x"], nd["y"], nd["r"] + 6, nd["color"])
            )
            p.append(
                '<circle cx="%.2f" cy="%.2f" r="%.2f" fill="none" stroke="#%s" stroke-width="1.4" opacity="0.75"/>'
                % (nd["x"], nd["y"], nd["r"] + 10, nd["color"])
            )
    for nd in lay["nodes"]:
        p.append(
            '<circle cx="%.2f" cy="%.2f" r="%.2f" fill="#%s" stroke="#FFFFFF" stroke-width="2.5"/>'
            % (nd["x"], nd["y"], nd["r"], nd["color"])
        )
        p.append(_svg_text(nd["x"], nd["y"] + 15 * 0.34, nd["label"], 15.0, "FFFFFF", anchor="middle", bold=True))

    # 标题
    p.append(_svg_text(lay["pad"], label_baseline(26.0, 26.0), lay["title"], 26.0, INK, bold=True))
    p.append(_svg_text(lay["pad"], label_baseline(58.0, 13.0), lay["subtitle"], 13.0, MUTED))

    # 比例尺
    sb = lay["scale"]
    note_w = text_w(sb["note"], 11.0)
    pill_w = max(sb["px"], note_w) + 24.0
    p.append(
        '<rect x="%.2f" y="%.2f" width="%.2f" height="50" fill="#FFFFFF"/>' % (sb["x"] - 12, sb["y"] - 20, pill_w)
    )
    p.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#%s" stroke-width="2"/>' % (sb["x"], sb["y"], sb["x"] + sb["px"], sb["y"], INK))
    for bx in (sb["x"], sb["x"] + sb["px"]):
        p.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#%s" stroke-width="2"/>' % (bx, sb["y"] - 5, bx, sb["y"] + 5, INK))
    p.append(_svg_text(sb["x"] + sb["px"] + 8, sb["y"] + 4, sb["text"], 11.0, MUTED))
    p.append(_svg_text(sb["x"], sb["y"] + 22, sb["note"], 11.0, MUTED))

    # 指北针
    nb = lay["north"]
    cx, cy, cr = nb["cx"], nb["cy"], nb["r"]
    p.append('<circle cx="%.2f" cy="%.2f" r="%.2f" fill="#FFFFFF"/>' % (cx, cy + 2, cr + 16))
    p.append(
        '<polygon points="%.2f,%.2f %.2f,%.2f %.2f,%.2f %.2f,%.2f" fill="#%s"/>'
        % (cx, cy - cr, cx - cr * 0.55, cy + cr * 0.7, cx, cy + cr * 0.35, cx + cr * 0.55, cy + cr * 0.7, INK)
    )
    p.append(_svg_text(cx, cy - cr - 6, "N", 13.0, INK, anchor="middle"))

    # 图例
    if lay["legend"]:
        for it in lay["legend"]["items"]:
            k = it["kind"]
            x, y = it["x"], it["y"]
            if k == "title":
                p.append(_svg_text(x, label_baseline(y, it["size"]), it["text"], it["size"], INK, bold=True))
            elif k == "swatch":
                p.append('<rect x="%.2f" y="%.2f" width="22" height="14" rx="4" fill="#%s"/>' % (x, y + 4, it["color"]))
                p.append(_svg_text(x + 34, label_baseline(y, it["size"]), it["text"], it["size"], INK))
            elif k == "arrow":
                ymid = y + 11
                p.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#%s" stroke-width="3" stroke-linecap="round"/>' % (x, ymid, x + 24, ymid, it["color"]))
                pts = " ".join("%.2f,%.2f" % (pt[0], pt[1]) for pt in arrow_points(x + 30, ymid, 0.0, 11.0, 4.6))
                p.append('<polygon points="%s" fill="#%s"/>' % (pts, it["color"]))
                p.append(_svg_text(x + 44, label_baseline(y, it["size"]), it["text"], it["size"], INK))
            elif k == "ring":
                ymid = y + 11
                p.append('<circle cx="%.2f" cy="%.2f" r="6.5" fill="none" stroke="#%s" stroke-width="2"/>' % (x + 7, ymid, it["color"]))
                p.append('<circle cx="%.2f" cy="%.2f" r="6.5" fill="none" stroke="#%s" stroke-width="1.2" opacity="0.7"/>' % (x + 20, ymid, it["color"]))
                p.append(_svg_text(x + 34, label_baseline(y, it["size"]), it["text"], it["size"], INK))
            elif k == "rule":
                p.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#%s" stroke-width="1"/>' % (x, y, it["x1"], y, RULE))
            elif k == "group":
                p.append(_svg_text(x, label_baseline(y, it["size"]), it["text"], it["size"], it["color"], bold=True))
            elif k == "node":
                p.append(_svg_text(x, label_baseline(y, it["size"]), it["text"], it["size"], INK))
            elif k == "note":
                p.append(_svg_text(x, label_baseline(y, it["size"]), it["text"], it["size"], MUTED))

    p.append("</svg>")
    return "\n".join(p) + "\n"


# --------------------------------------------------------------------------- PNG 后端（可选）


class _PngCanvas:
    """把单位坐标（1280×860 逻辑画布）按 dpi 缩放成像素坐标的薄封装。"""

    def __init__(self, w, h, dpi):
        self.k = float(dpi)
        self.img = Image.new("RGB", (int(w * dpi), int(h * dpi)), (255, 255, 255))
        self.d = ImageDraw.Draw(self.img)

    def _p(self, x, y):
        return (x * self.k, y * self.k)

    def _wid(self, w):
        return max(1, int(round(w * self.k)))

    def rect(self, x, y, w, h, fill):
        self.d.rectangle([x * self.k, y * self.k, (x + w) * self.k, (y + h) * self.k], fill=hex_rgb(fill))

    def round_rect(self, x, y, w, h, radius, fill):
        self.d.rounded_rectangle(
            [x * self.k, y * self.k, (x + w) * self.k, (y + h) * self.k],
            radius=radius * self.k,
            fill=hex_rgb(fill),
        )

    def line(self, x0, y0, x1, y1, color, width):
        rgb = hex_rgb(color)
        self.d.line([self._p(x0, y0), self._p(x1, y1)], fill=rgb, width=self._wid(width))
        rr = width / 2.0
        for x, y in ((x0, y0), (x1, y1)):
            self.d.ellipse(
                [(x - rr) * self.k, (y - rr) * self.k, (x + rr) * self.k, (y + rr) * self.k], fill=rgb
            )

    def polygon(self, pts, fill, outline=None, width=0.0):
        self.d.polygon([self._p(x, y) for x, y in pts], fill=hex_rgb(fill))
        if outline and width:
            seq = [self._p(x, y) for x, y in pts] + [self._p(pts[0][0], pts[0][1])]
            self.d.line(seq, fill=hex_rgb(outline), width=self._wid(width), joint="curve")

    def circle(self, cx, cy, r, fill=None, outline=None, width=0.0):
        self.d.ellipse(
            [(cx - r) * self.k, (cy - r) * self.k, (cx + r) * self.k, (cy + r) * self.k],
            fill=hex_rgb(fill) if fill else None,
            outline=hex_rgb(outline) if outline else None,
            width=self._wid(width) if outline else 0,
        )

    def text(self, x, y, s, size, color, anchor="start", bold=False, halo=0.0):
        font = load_font(int(round(size * self.k)), bold=bold)
        rgb = hex_rgb(color)
        if anchor == "middle":
            self.d.text(self._p(x, y), s, font=font, fill=rgb, anchor="mm")
            return
        if halo:
            pad = halo * 0.8
            for dx in (-pad, 0, pad):
                for dy in (-pad, 0, pad):
                    if dx or dy:
                        self.d.text(((x + dx) * self.k, (y + dy) * self.k), s, font=font, fill=(255, 255, 255))
        self.d.text(self._p(x, y), s, font=font, fill=rgb)


def to_png(lay, dpi=2):
    """同一份布局数据的位图后端；仅用于需要 PNG 的场景（Pillow 缺失时不可用）。"""
    if Image is None:
        raise SystemExit("缺少 Pillow，无法输出 PNG。请改用矢量输出：--out trip.svg（不需要 Pillow）。")
    W, H = lay["w"], lay["h"]
    c = _PngCanvas(W, H, dpi)

    if lay["panel_w"]:
        c.rect(W - lay["panel_w"], 0, lay["panel_w"], H, PANEL_BG)
    for x in lay["grid_v"]:
        c.line(x, lay["top"], x, H, GRID, 1.0)
    for y in lay["grid_h"]:
        c.line(0, y, lay["map_w"], y, GRID, 1.0)

    for lg in lay["legs"]:
        c.line(lg["x0"], lg["y0"], lg["x1"], lg["y1"], lg["color"], 5.0)
    for ar in lay["arrows"]:
        c.polygon(arrow_points(ar["x"], ar["y"], ar["ang"]), ar["color"], outline="FFFFFF", width=1.6)

    for lb in lay["labels"]:
        c.text(lb["x"], lb["y"], lb["text"], lb["size"], INK, bold=True, halo=3.2)

    for nd in lay["nodes"]:
        if nd["start"]:
            c.circle(nd["x"], nd["y"], nd["r"] + 6, outline=nd["color"], width=2.4)
        if nd["end"]:
            c.circle(nd["x"], nd["y"], nd["r"] + 6, outline=nd["color"], width=2.4)
            c.circle(nd["x"], nd["y"], nd["r"] + 10, outline=nd["color"], width=1.4)
    for nd in lay["nodes"]:
        c.circle(nd["x"], nd["y"], nd["r"], fill=nd["color"], outline="FFFFFF", width=2.5)
        c.text(nd["x"], nd["y"] + 0.5, nd["label"], 15.0, "FFFFFF", anchor="middle", bold=True)

    c.text(lay["pad"], 26.0, lay["title"], 26.0, INK, bold=True)
    c.text(lay["pad"], 58.0, lay["subtitle"], 13.0, MUTED)

    sb = lay["scale"]
    pill_w = max(sb["px"], text_w(sb["note"], 11.0)) + 24.0
    c.rect(sb["x"] - 12, sb["y"] - 20, pill_w, 50, "FFFFFF")
    c.line(sb["x"], sb["y"], sb["x"] + sb["px"], sb["y"], INK, 2.0)
    for bx in (sb["x"], sb["x"] + sb["px"]):
        c.line(bx, sb["y"] - 5, bx, sb["y"] + 5, INK, 2.0)
    c.text(sb["x"] + sb["px"] + 8, sb["y"] - 7, sb["text"], 11.0, MUTED)
    c.text(sb["x"], sb["y"] + 9, sb["note"], 11.0, MUTED)

    nb = lay["north"]
    c.circle(nb["cx"], nb["cy"] + 2, nb["r"] + 16, fill="FFFFFF")
    c.polygon(
        [
            (nb["cx"], nb["cy"] - nb["r"]),
            (nb["cx"] - nb["r"] * 0.55, nb["cy"] + nb["r"] * 0.7),
            (nb["cx"], nb["cy"] + nb["r"] * 0.35),
            (nb["cx"] + nb["r"] * 0.55, nb["cy"] + nb["r"] * 0.7),
        ],
        INK,
    )
    c.text(nb["cx"], nb["cy"] - nb["r"] - 20, "N", 13.0, INK)

    if lay["legend"]:
        for it in lay["legend"]["items"]:
            k = it["kind"]
            x, y = it["x"], it["y"]
            if k == "title":
                c.text(x, y, it["text"], it["size"], INK, bold=True)
            elif k == "swatch":
                c.round_rect(x, y + 4, 22, 14, 4, it["color"])
                c.text(x + 34, y, it["text"], it["size"], INK)
            elif k == "arrow":
                ymid = y + 11
                c.line(x, ymid, x + 24, ymid, it["color"], 3.0)
                c.polygon(arrow_points(x + 30, ymid, 0.0, 11.0, 4.6), it["color"])
                c.text(x + 44, y, it["text"], it["size"], INK)
            elif k == "ring":
                ymid = y + 11
                c.circle(x + 7, ymid, 6.5, outline=it["color"], width=2.0)
                c.circle(x + 20, ymid, 6.5, outline=it["color"], width=1.2)
                c.text(x + 34, y, it["text"], it["size"], INK)
            elif k == "rule":
                c.line(x, y, it["x1"], y, RULE, 1.0)
            elif k == "group":
                c.text(x, y, it["text"], it["size"], it["color"], bold=True)
            elif k == "node":
                c.text(x, y, it["text"], it["size"], INK)
            elif k == "note":
                c.text(x, y, it["text"], it["size"], MUTED)

    return c.img


# --------------------------------------------------------------------------- 输出


def write_html(title, stops, out_path, svg_markup=None, img_name=None):
    """说明页：优先内联 SVG（缩放不糊、可直接打印），退化时才用 <img> 引位图。"""
    days = sorted({s["day"] for s in stops})
    rows = []
    for day in days:
        items = [s for s in stops if s["day"] == day]
        lis = "".join(
            "<li><span class='dot' style='background:#%s'></span>%s%s</li>"
            % (
                LODGING_COLOR if s["lodging"] else day_hex(day),
                html.escape(s["name"]),
                "<em>住宿</em>" if s["lodging"] else "",
            )
            for s in items
        )
        rows.append("<section><h3>Day %d</h3><ul>%s</ul></section>" % (day, lis))

    if svg_markup:
        media = '<figure class="vec">%s</figure>' % svg_markup
        caption = "本图为 SVG 矢量示意图，路线按时间顺序连续连线、箭头指向下一站；仅表示节点相对位置与顺序，不含真实道路底图，可无损放大与打印。"
    else:
        media = '<figure><img src="%s" alt="路线示意图"></figure>' % img_name
        caption = "本图仅表示节点相对位置与顺序，不含真实道路底图。"

    doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>%(title)s · 行程路线图</title>
<style>
:root{--ink:#1f2933;--muted:#6b7785;--line:#e3e8ee;--bg:#ffffff}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px;background:#f4f6f8;color:var(--ink);
 font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB",system-ui,sans-serif;line-height:1.6}
.wrap{max-width:1080px;margin:0 auto;background:var(--bg);border-radius:14px;
 box-shadow:0 2px 16px rgba(16,24,40,.08);overflow:hidden}
header{padding:26px 30px 18px;border-bottom:1px solid var(--line)}
h1{margin:0 0 6px;font-size:22px;letter-spacing:.3px}
.meta{color:var(--muted);font-size:13px}
figure{margin:0;padding:20px 30px 6px}
figure img{width:100%%;height:auto;border:1px solid var(--line);border-radius:10px;display:block}
figure.vec svg{width:100%%;height:auto;display:block;border:1px solid var(--line);border-radius:10px}
figcaption{color:var(--muted);font-size:12px;margin-top:10px}
.days{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:18px;padding:16px 30px 30px}
h3{margin:0 0 8px;font-size:15px}
ul{margin:0;padding:0;list-style:none;font-size:14px}
li{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px dashed #eef1f4}
li:last-child{border-bottom:0}
.dot{width:9px;height:9px;border-radius:50%%;flex:0 0 auto}
em{margin-left:auto;color:var(--muted);font-size:12px;font-style:normal}
footer{padding:14px 30px 24px;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}
</style></head><body><div class="wrap">
<header><h1>%(title)s</h1>
<div class="meta">共 %(ndays)d 天 · %(nstops)d 个节点 · 数字为当日游览顺序 · 箭头为行进方向</div></header>
%(media)s
<figcaption>%(caption)s</figcaption>
<div class="days">%(rows)s</div>
<footer>由「百坡山智慧规划旅行家」生成 · 未使用任何地图 AK（AK-free 矢量渲染）</footer>
</div></body></html>
""" % {
        "title": html.escape(title),
        "ndays": len(days),
        "nstops": len(stops),
        "rows": "".join(rows),
        "media": media,
        "caption": caption,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="零 AK 行程路线示意图（SVG 矢量优先）")
    ap.add_argument("--spec", required=True, help="行程节点 JSON：{title, stops:[{name,lng,lat,day,lodging}]}")
    ap.add_argument("--out", required=True, help="输出路径：.svg 为矢量（推荐），.png 为位图")
    ap.add_argument("--png", action="store_true", help="--out 为 .svg 时，额外再导出一张同名 PNG")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=860)
    ap.add_argument("--dpi", type=int, default=2, help="PNG 超采样倍数，2 表示 2x 抗锯齿")
    ap.add_argument("--no-panel", action="store_true", help="不画右侧图例面板")
    ap.add_argument("--html", action="store_true", help="同时生成 HTML 说明页（内联矢量）")
    ap.add_argument("--stats", action="store_true", help="输出渲染统计（标签放置率、箭头数、耗时）")
    args = ap.parse_args()

    title, stops = load_spec(args.spec)
    t0 = time.time()
    lay = build_layout(title, stops, args.width, args.height, panel=not args.no_panel)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    ext = os.path.splitext(args.out)[1].lower()
    want_png = ext == ".png"
    if want_png and Image is None:
        raise SystemExit("缺少 Pillow，无法输出 PNG。请改用矢量输出：--out trip.svg（不需要 Pillow）。")

    svg_text = None
    if not want_png:
        svg_text = to_svg(lay)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(svg_text)
        size_kb = os.path.getsize(args.out) / 1024.0
        print("行程：%s，节点 %d 个" % (title, len(stops)))
        print(
            "[成功] 矢量示意图 → %s（%d×%d，%.1f KB，SVG，无 AK）"
            % (args.out, args.width, args.height, size_kb)
        )
    if want_png or args.png:
        png_path = args.out if want_png else os.path.splitext(args.out)[0] + ".png"
        img = to_png(lay, args.dpi)
        img.save(png_path, "PNG", optimize=True)
        print(
            "[成功] 位图 → %s（%d×%d，%.1f KB）"
            % (png_path, args.width, args.height, os.path.getsize(png_path) / 1024.0)
        )

    elapsed = time.time() - t0
    st = lay["stats"]
    if args.stats:
        print(
            "统计：路径 %d 段（跨日转场 %d 段）· 箭头 %d 个 · 标签 %d/%d 已放置（越界裁剪 %d）· 渲染 %.0f ms"
            % (st["legs"], st["transfers"], st["arrows"], st["labels_placed"], st["labels_total"], st["labels_clipped"], elapsed * 1000)
        )
        if st["labels_dropped"]:
            print("说明：未放置的标签仍完整保留在右侧图例与 HTML 说明页中。")
    if args.html:
        html_svg = None
        if svg_text:
            html_svg = "\n".join(l for l in svg_text.splitlines() if not l.startswith("<?xml"))
            p = write_html(title, stops, os.path.splitext(args.out)[0] + ".html", svg_markup=html_svg)
        else:
            p = write_html(
                title,
                stops,
                os.path.splitext(args.out)[0] + ".html",
                img_name=os.path.basename(args.out),
            )
        print("[成功] 说明页 → %s" % p)
    print("说明：路线按时间顺序连续绘制并以箭头标示方向；图为矢量示意图，只有相对位置与顺序，不含真实道路底图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
