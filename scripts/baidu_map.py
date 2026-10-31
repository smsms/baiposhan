#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""百度地图能力工具箱（仅标准库，无需安装依赖）。

七个子命令：
  ak          自动发现本机可用的百度 AK（含 MCP 配置里百度 server 的凭据）
  set-ak      把 AK 写入技能目录 config.local.json
  check       校验 AK 是否真的可用（静态图 + 地理编码双探测）
  mcp-config  生成 / 写入百度地图 MCP Server 配置到 ~/.workbuddy/mcp.json
  mcp-check   直接对 MCP 端点做握手、拉工具列表、实调 map_geocode
  geocode     批量把地址解析成坐标，产出 staticmap 可用的节点文件
  staticmap   把行程节点渲染成带标注与路线的本地 PNG 地图

AK 查找顺序（ak / check / staticmap 共用）：
  --ak 参数 > 环境变量 BAIDU_MAP_AK 等 > 技能目录 config.local.json
  > ~/.workbuddy/baidu_map.json > MCP 配置中百度 server 的 URL/env
注意：AK 无法凭空生成，只能由本人在 lbsyun.baidu.com 控制台创建。

零 AK 出图见同目录 sketch_map.py（SVG 矢量示意图：全程连续路线 + 方向箭头）。

示例：
  python baidu_map.py ak
  python baidu_map.py check
  python baidu_map.py mcp-config --ak xxx --write
  python baidu_map.py staticmap --spec trip.json --out trip.png
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_API = "https://api.map.baidu.com/staticimage/v2"
GEOCODE_API = "https://api.map.baidu.com/geocoding/v3/"
MCP_HTTP = "https://mcp.map.baidu.com/mcp"
MCP_SSE = "https://mcp.map.baidu.com/sse"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 每日配色：0xRRGGBB。红/蓝/绿/紫/橙，两两之间在灰度与色相上都可区分。
DAY_COLORS = [
    ("0xD93025", "红"),
    ("0x1A73E8", "蓝"),
    ("0x0F9D58", "绿"),
    ("0x7B1FA2", "紫"),
    ("0xF29900", "橙"),
    ("0x00ACC1", "青"),
]

HINTS = {
    0: "AK 可用",
    200: "AK 有误或应用不存在：核对 AK 是否复制完整（常见于末尾多空格/少字符），或该 AK 已被删除",
    240: "该 AK 未开通此服务：到控制台「应用管理 - 我的应用」勾选对应服务（静态图 / 地理编码 / 地点检索 / 路线规划）",
    201: "AK 被用户禁用：控制台重新启用该应用",
    202: "应用已被删除：重新创建应用获取新 AK",
    203: "AK 类型与接口不匹配：静态图请用「浏览器端」AK，服务端接口请用「服务端」AK",
    302: "配额已超限：控制台查看当日配额，或更换 AK",
    303: "并发超限：降低请求频率后重试",
    401: "并发/权限异常：稍后重试，仍失败则检查白名单",
    402: "并发超限：稍后重试",
}


def eprint(*args):
    print(*args, file=sys.stderr)


def mask_ak(ak):
    if not ak:
        return "(空)"
    if len(ak) <= 8:
        return ak[:2] + "***"
    return ak[:4] + "***" + ak[-4:]


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


AK_URL_RE = re.compile(r"[?&]ak=([0-9A-Za-z]{8,64})")


def _mcp_config_paths():
    """可能存在 MCP 配置的文件（存在才扫描）。"""
    home = os.path.expanduser("~")
    cwd = os.getcwd()
    return [
        os.path.join(home, ".workbuddy", "mcp.json"),
        os.path.join(home, ".workbuddy", ".mcp.json"),
        os.path.join(cwd, ".mcp.json"),
        os.path.join(cwd, ".workbuddy", "mcp.json"),
    ]


def _aks_in_mcp_config(path):
    """从 MCP 配置里提取百度地图 AK，返回 [(ak, 来源描述)]。

    只在 server 与百度相关（名称或 URL 含 baidu）时提取，避免把别的
    服务的 key 误认成百度 AK。加密存储的连接器凭据不做解密——那条路
    走不通也不会去尝试。
    """
    data = read_json(path)
    if not isinstance(data, dict):
        return []
    servers = data.get("mcpServers") or data.get("servers") or {}
    out, seen = [], set()
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        # 只按 server 名称与 URL 主机名判断是否百度服务，
        # 避免参数值里碰巧含 "baidu" 字样导致别的服务被误提取
        url_str = str(cfg.get("url") or "")
        try:
            host = urllib.parse.urlparse(url_str).netloc.lower()
        except Exception:
            host = ""
        if "baidu" not in (str(name) + " " + host).lower():
            continue
        blob_parts = [url_str]
        blob_parts += [str(a) for a in (cfg.get("args") or [])]
        for section in ("env", "headers"):
            val = cfg.get(section)
            if isinstance(val, dict):
                blob_parts += ["%s=%s" % (k, v) for k, v in val.items()]
        joined = " ".join(blob_parts)
        src = "%s → %s" % (path, name)
        for m in AK_URL_RE.finditer(joined):
            if m.group(1) not in seen:
                seen.add(m.group(1))
                out.append((m.group(1), "%s（URL/参数里的 ak）" % src))
        for section in ("env", "headers"):
            val = cfg.get(section)
            if not isinstance(val, dict):
                continue
            for k, v in val.items():
                sv = str(v).strip()
                if ("ak" in k.lower() or "key" in k.lower()) and re.fullmatch(
                    r"[0-9A-Za-z]{16,64}", sv
                ):
                    if sv not in seen:
                        seen.add(sv)
                        out.append((sv, "%s（%s.%s）" % (src, section, k)))
    return out


def iter_ak_sources(cli_ak=None, kind="default"):
    """按优先级产出 (ak, 来源描述)。resolve_ak 取第一个即可。"""
    if cli_ak and cli_ak.strip():
        yield cli_ak.strip(), "--ak 参数"
    for key in ("BAIDU_MAP_AK", "BAIDU_MAP_API_KEY", "BAIDU_MAP_SERVER_AK"):
        val = (os.environ.get(key) or "").strip()
        if val:
            yield val, "环境变量 %s" % key
    for path in (
        os.path.join(SKILL_DIR, "config.local.json"),
        os.path.join(os.path.expanduser("~"), ".workbuddy", "baidu_map.json"),
    ):
        data = read_json(path)
        if isinstance(data, dict):
            order = ["baidu_static_ak", "baidu_ak"] if kind == "static" else ["baidu_ak", "baidu_static_ak"]
            for field in order:
                raw = data.get(field)
                if raw is None and field == "baidu_ak":
                    raw = data.get("ak")
                val = str(raw or "").strip()
                if val:
                    yield val, "%s 的 %s" % (path, field)
    for path in _mcp_config_paths():
        for ak, src in _aks_in_mcp_config(path):
            yield ak, src


def resolve_ak(cli_ak=None, kind="default"):
    """返回 (ak, 来源描述)。kind="static" 时优先取静态图专用 AK。"""
    for ak, src in iter_ak_sources(cli_ak, kind):
        return ak, src
    return "", "未找到"


def cmd_ak(args):
    """展示本机所有能发现的百度 AK 候选及最终将采用哪个。"""
    cands = list(iter_ak_sources(args.ak))
    if not cands:
        print("未在任何位置发现百度地图 AK。已扫描：")
        print("  1) --ak 参数 / 环境变量 BAIDU_MAP_AK 等")
        print("  2) 技能目录 config.local.json")
        print("  3) ~/.workbuddy/baidu_map.json")
        print("  4) MCP 配置（~/.workbuddy/mcp.json 等 4 处）")
        print()
        print("说明：AK 无法凭空生成或从地点推导——它绑定百度账号，")
        print("必须由你本人在 lbsyun.baidu.com 控制台创建（需实名认证）。")
        print("申请步骤见 references/baidu-map-setup.md；")
        print("拿到后运行：python scripts/baidu_map.py set-ak --ak 你的AK")
        print("没有 AK 也可以先出矢量示意图：python scripts/sketch_map.py --spec trip.json --out trip.svg")
        return 2
    print("发现 %d 个 AK 候选（按优先级）：" % len(cands))
    for i, (ak, src) in enumerate(cands, start=1):
        mark = "← 采用" if i == 1 else ""
        print("  %d. %s  来自 %s %s" % (i, mask_ak(ak), src, mark))
    dup = len({ak for ak, _ in cands}) < len(cands)
    if dup:
        print("（存在重复候选，已按优先级去重采用）")
    print()
    print("校验可用性：python scripts/baidu_map.py check")
    return 0


def http_get(url, timeout=20, referer=None):
    headers = {"User-Agent": UA, "Accept": "image/png,image/*,*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.headers.get("Content-Type", ""), resp.read()


def parse_error_body(body):
    """把非图片响应解析成 (status, message)。注：百度返回的 status 有时是字符串，统一转 int。"""
    text = body.decode("utf-8", "ignore").strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            st = data.get("status")
            try:
                st = int(st)
            except (TypeError, ValueError):
                pass
            return st, data.get("message") or data.get("msg") or text[:200]
    except Exception:
        pass
    return None, text[:200]


def is_image(ctype, body):
    if "image" in (ctype or "").lower():
        return True
    return body[:8].startswith(b"\x89PNG") or body[:3] == b"\xff\xd8\xff"


# --------------------------------------------------------------------------- check


def cmd_check(args):
    ak, source = resolve_ak(args.ak)
    ak_static, src_static = resolve_ak(args.ak, kind="static")
    if not ak:
        print("AK：未配置")
        print("下一步：按 references/baidu-map-setup.md 申请 AK，然后二选一落地：")
        print('  1) 一条命令写入：python scripts/baidu_map.py set-ak --ak 你的AK')
        print("  2) 设置环境变量 BAIDU_MAP_AK")
        return 2
    print("通用 AK：%s（%s，长度 %d）" % (mask_ak(ak), source, len(ak)))
    if ak_static and ak_static != ak:
        print("静态图 AK：%s（%s，长度 %d）" % (mask_ak(ak_static), src_static, len(ak_static)))
    else:
        print("静态图 AK：与通用 AK 相同（两个 AK 分开申请时可用 set-ak --static-ak 指定）")

    ok_all = True

    # 探测 1：静态图（出图能力，决定地图图片能否生成）
    probe_url = "%s?ak=%s&width=160&height=160&center=116.404000,39.915000&zoom=12&markers=116.404000,39.915000" % (
        STATIC_API,
        urllib.parse.quote(ak_static or ak),
    )
    try:
        ctype, body = http_get(probe_url, referer=args.referer)
        if is_image(ctype, body):
            print("[通过] 静态图 API：返回 %d 字节图片" % len(body))
        else:
            status, msg = parse_error_body(body)
            ok_all = False
            print("[失败] 静态图 API：status=%s message=%s" % (status, msg))
            if status in HINTS:
                print("       排查：%s" % HINTS[status])
            else:
                print("       排查：确认是「浏览器端」AK、已勾选静态图服务、Referer 白名单留空或已包含来源域名")
    except Exception as exc:
        ok_all = False
        print("[失败] 静态图 API：请求异常 %s" % exc)

    # 探测 2：地理编码（MCP 之外的兜底：把地址解析成坐标）
    geo_url = "%s?address=%s&output=json&ak=%s" % (
        GEOCODE_API,
        urllib.parse.quote("北京天安门"),
        urllib.parse.quote(ak),
    )
    try:
        ctype, body = http_get(geo_url)
        data = json.loads(body.decode("utf-8", "ignore"))
        status = data.get("status")
        try:
            status = int(status)
        except (TypeError, ValueError):
            pass
        if status == 0:
            loc = (data.get("result") or {}).get("location") or {}
            print("[通过] 地理编码：天安门 → %s,%s" % (loc.get("lng"), loc.get("lat")))
        else:
            ok_all = False
            print("[失败] 地理编码：status=%s message=%s" % (status, data.get("message")))
            if status in HINTS:
                print("       排查：%s" % HINTS[status])
    except Exception as exc:
        ok_all = False
        print("[失败] 地理编码：请求异常 %s" % exc)

    print("")
    print("结论：%s" % ("AK 可用，可以出图" if ok_all else "AK 尚不可用，按上面提示修复后重跑 check"))
    return 0 if ok_all else 1


# ---------------------------------------------------------------------- mcp-config


def cmd_mcp_config(args):
    ak, source = resolve_ak(args.ak)
    if not ak:
        eprint("未找到 AK。先按 references/baidu-map-setup.md 申请，或传 --ak。")
        return 2
    print("AK 来源：%s（%s）" % (source, mask_ak(ak)))

    server_name = args.name or "baidu-maps"
    if args.transport == "http":
        entry = {"url": "%s?ak=%s" % (MCP_HTTP, ak)}
    elif args.transport == "sse":
        entry = {"url": "%s?ak=%s" % (MCP_SSE, ak)}
    else:  # stdio
        entry = {
            "command": "npx",
            "args": ["-y", "@baidumap/mcp-server-baidu-map"],
            "env": {"BAIDU_MAP_API_KEY": ak},
        }

    target = args.config or os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")
    existing = read_json(target) or {}
    if not isinstance(existing, dict):
        existing = {}
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    merged = dict(servers)
    merged[server_name] = entry
    out = dict(existing)
    out["mcpServers"] = merged

    snippet = json.dumps({"mcpServers": {server_name: entry}}, ensure_ascii=False, indent=2)
    print("将要写入：%s" % target)
    print(snippet)

    if not args.write:
        print("")
        print("当前为预演模式，未写入文件。确认无误后加 --write 落盘。")
        return 0

    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    if os.path.isfile(target):
        backup = "%s.bak-%s" % (target, time.strftime("%Y%m%d-%H%M%S"))
        with open(target, "rb") as src, open(backup, "wb") as dst:
            dst.write(src.read())
        print("已备份原配置：%s" % backup)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("")
    print("已写入。后续动作：")
    print("  1) 在 WorkBuddy 连接器管理页右上角的「自定义连接器」入口找到 %s 并点「信任」使其生效" % server_name)
    print("  2) 用 `python scripts/baidu_map.py mcp-check --test-geocode 成都宽窄巷子` 确认服务端真的可用")
    print("  3) MCP 返回坐标是 bd09ll，与静态图默认一致；但 map_directions 的入参是「纬度,经度」，别写反")
    return 0


# ----------------------------------------------------------------------- mcp-check


def _mcp_post(url, payload, session=None, timeout=25):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": UA,
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.headers.get("Content-Type", ""), resp.read().decode("utf-8", "ignore"), resp.headers.get("Mcp-Session-Id")


def _mcp_parse(ctype, raw):
    """兼容 application/json 与 text/event-stream 两种响应形态。"""
    if "text/event-stream" in (ctype or ""):
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except Exception:
                    continue
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def cmd_mcp_check(args):
    ak, source = resolve_ak(args.ak)
    if args.url:
        url = args.url if "?" in args.url else args.url + "?ak=" + urllib.parse.quote(ak)
    else:
        if not ak:
            eprint("未配置 AK 且未给 --url。先跑 set-ak，或直接 --url 指定完整 MCP 地址。")
            return 2
        url = "%s?ak=%s" % (MCP_HTTP, urllib.parse.quote(ak))
    shown = re.sub(r"(ak=)([^&]+)", lambda m: m.group(1) + mask_ak(m.group(2)), url)
    print("MCP 端点：%s" % shown)
    if not args.url:
        print("AK 来源：%s" % source)

    session = None
    try:
        ctype, raw, session = _mcp_post(
            url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "baidu_map.py", "version": "1.3"},
                },
            },
        )
    except Exception as exc:
        print("[失败] 握手请求异常：%s" % exc)
        print("       排查：网络是否可达 mcp.map.baidu.com；公司代理是否拦截 POST")
        return 1

    data = _mcp_parse(ctype, raw) or {}
    result = data.get("result") or {}
    info = result.get("serverInfo") or {}
    if not info:
        print("[失败] 握手未返回 serverInfo：%s" % raw[:300])
        print("       排查：URL 是否指向 /mcp（Streamable HTTP）而非 /sse；AK 是否被网关拦下")
        return 1
    print("[通过] 握手成功：%s v%s，协议 %s" % (info.get("name"), info.get("version"), result.get("protocolVersion")))
    if result.get("instructions"):
        print("       服务说明：%s" % str(result["instructions"])[:120])
    if session:
        print("       会话 ID：%s" % session[:8] + "…")

    # 补一条 initialized 通知，部分实现不做严格校验，失败也不影响后续
    try:
        _mcp_post(url, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, session=session)
    except Exception:
        pass

    tools = []
    if not args.no_tools:
        try:
            ctype, raw, session = _mcp_post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session=session)
            data = _mcp_parse(ctype, raw) or {}
            tools = (data.get("result") or {}).get("tools") or []
        except Exception as exc:
            print("[失败] 拉取工具列表异常：%s" % exc)
            return 1
        if not tools:
            print("[失败] 工具列表为空：%s" % raw[:300])
            return 1
        names = [t.get("name") for t in tools]
        print("[通过] 工具 %d 个：%s" % (len(names), ", ".join(names)))

    if args.test_geocode:
        try:
            ctype, raw, session = _mcp_post(
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "map_geocode", "arguments": {"address": args.test_geocode, "city": args.city or ""}},
                },
                session=session,
            )
        except Exception as exc:
            print("[失败] 调用 map_geocode 异常：%s" % exc)
            return 1
        data = _mcp_parse(ctype, raw) or {}
        if data.get("error"):
            print("[失败] map_geocode 返回错误：%s" % json.dumps(data["error"], ensure_ascii=False)[:300])
            print("       排查：AK 是否开通「地理编码」服务、是否被白名单拦下")
            return 1
        content = (data.get("result") or {}).get("content") or []
        text = ""
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text += part.get("text") or ""
        is_err = bool((data.get("result") or {}).get("isError")) or "Authentication failed" in text
        if is_err:
            print("[失败] map_geocode(%s) 返回错误：%s" % (args.test_geocode, (text or raw)[:300]))
            print("       排查：AK 是否复制完整、是否开通「地理编码」服务、是否被 IP/Referer 白名单拦下")
            return 1
        print("[通过] map_geocode(%s) 返回：%s" % (args.test_geocode, (text or raw)[:400]))

    print("")
    print("结论：MCP 服务端可用。若 WorkBuddy 里仍看不到这些工具，是本地配置未生效——检查 ~/.workbuddy/mcp.json 并在「自定义连接器」点「信任」。")
    return 0


# ------------------------------------------------------------------------- set-ak


def cmd_set_ak(args):
    ak = (args.ak or "").strip()
    if not ak:
        eprint("AK 为空。用法：set-ak --ak 你的AK")
        return 2
    if re.search(r"\s", ak):
        eprint("AK 里含空白字符，八成是复制时带上了空格/换行，清掉再试。")
        return 2
    if len(ak) < 16:
        eprint("警告：AK 通常为 32 位，当前只有 %d 位，可能复制不完整。仍会写入。" % len(ak))

    path = os.path.join(SKILL_DIR, "config.local.json")
    data = read_json(path)
    if not isinstance(data, dict):
        data = {}
    data["baidu_ak"] = ak
    if args.static_ak:
        data["baidu_static_ak"] = args.static_ak.strip()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    print("已写入 %s（AK：%s）" % (path, mask_ak(ak)))
    print("下一步：python scripts/baidu_map.py check")
    return 0


# ------------------------------------------------------------------------- geocode


def geocode_one(address, city, ak):
    q = [("address", address), ("output", "json"), ("ak", ak)]
    if city:
        q.append(("city", city))
    url = "%s?%s" % (GEOCODE_API, urllib.parse.urlencode(q))
    try:
        ctype, body = http_get(url)
        data = json.loads(body.decode("utf-8", "ignore"))
    except Exception as exc:
        return None, "请求异常 %s" % exc
    st = data.get("status")
    try:
        st = int(st)
    except (TypeError, ValueError):
        pass
    if st != 0:
        return None, "status=%s %s" % (st, data.get("message"))
    result = data.get("result") or {}
    loc = result.get("location") or {}
    if not loc.get("lng"):
        return None, "无匹配结果"
    return (
        {
            "lng": float(loc["lng"]),
            "lat": float(loc["lat"]),
            "level": result.get("level"),
            "confidence": result.get("confidence"),
            "precise": result.get("precise"),
        },
        "",
    )


def cmd_geocode(args):
    ak, source = resolve_ak(args.ak)
    if not ak:
        eprint("未找到 AK，无法做地理编码。见 references/baidu-map-setup.md。")
        return 2
    print("AK 来源：%s（%s）" % (source, mask_ak(ak)))

    if args.address:
        items = [{"name": args.address, "address": args.address, "city": args.city, "day": args.day}]
        title = args.address
    else:
        data = read_json(args.spec)
        if data is None:
            eprint("无法读取节点清单：%s" % args.spec)
            return 2
        if isinstance(data, list):
            data = {"stops": data}
        title = data.get("title") or "自定义行程"
        items = data.get("stops") or []
    if not items:
        eprint("没有待解析的地址")
        return 2

    stops, failed = [], []
    for i, it in enumerate(items, start=1):
        addr = str(it.get("address") or it.get("name") or "").strip()
        # 已有坐标的条目直接沿用，避免重复调用（也便于在已解析文件上补点）
        if it.get("lng") is not None and it.get("lat") is not None:
            stop = {
                "name": it.get("name") or addr or "P%d" % i,
                "lng": float(it["lng"]),
                "lat": float(it["lat"]),
                "day": int(it.get("day") or 1),
            }
            if it.get("lodging"):
                stop["lodging"] = True
            stops.append(stop)
            print("[沿用] %d %s → %s,%s（文件内已有坐标）" % (i, stop["name"], fmt(stop["lng"]), fmt(stop["lat"])))
            continue
        if not addr:
            failed.append({"index": i, "address": "", "error": "地址为空"})
            continue
        hit, err = geocode_one(addr, it.get("city") or args.city, ak)
        if not hit:
            print("[失败] %d %s：%s" % (i, addr, err))
            failed.append({"index": i, "address": addr, "error": err})
            continue
        stop = {
            "name": it.get("name") or addr,
            "lng": round(hit["lng"], 6),
            "lat": round(hit["lat"], 6),
            "day": int(it.get("day") or 1),
        }
        if it.get("lodging"):
            stop["lodging"] = True
        stops.append(stop)
        acc = hit.get("level") or "-"
        print("[通过] %d %s → %s,%s（精度 %s，确信度 %s）" % (i, addr, stop["lng"], stop["lat"], acc, hit.get("confidence")))
        time.sleep(args.interval)

    payload = {"title": title, "coordtype": "bd09ll", "stops": stops, "failed": failed}
    if args.out:
        parent = os.path.dirname(os.path.abspath(args.out))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("")
        print("已写入 %s（%d 个节点，%d 个失败）" % (args.out, len(stops), len(failed)))
        print("坐标系 bd09ll 已记录在文件里，staticmap 会自动沿用，无需再传 --coordtype。")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if failed:
        print("")
        print("失败项处理：换更完整的地址（加省市区与地标，如「成都市青羊区长顺上街宽窄巷子」），")
        print("或用 MCP 的 map_search_places 先查准确 POI 名称后重试；仍失败就手动填经纬度。")
    return 0 if stops else 1


# ----------------------------------------------------------------------- staticmap


def check_coord(lng, lat, label="节点"):
    """范围校验。经纬度顺序写反时（中国境内经纬度都在 73–135 / 3–54），
    纬度会落进 90 以上直接越界，因此这里能拦住绝大多数互换错误。"""
    if not (-180.0 <= lng <= 180.0):
        raise SystemExit("%s 经度越界：%.6f（应在 -180~180）。常见原因是经纬度顺序写反。" % (label, lng))
    if not (-90.0 <= lat <= 90.0):
        raise SystemExit(
            "%s 纬度越界：%.6f（应在 -90~90）。经纬度顺序写反了——"
            "本技能要求 lng 在前、lat 在后。" % (label, lat)
        )


def load_stops(spec_path):
    data = read_json(spec_path)
    if data is None:
        raise SystemExit("无法读取行程节点文件：%s（应为 JSON）" % spec_path)
    if isinstance(data, list):
        data = {"stops": data}
    stops = data.get("stops") or []
    cleaned = []
    for i, s in enumerate(stops):
        try:
            lng = float(s.get("lng"))
            lat = float(s.get("lat"))
        except (TypeError, ValueError):
            raise SystemExit("第 %d 个节点缺少合法 lng/lat：%s" % (i + 1, s))
        check_coord(lng, lat, "第 %d 个节点「%s」" % (i + 1, s.get("name") or ""))
        cleaned.append(
            {
                "name": str(s.get("name") or "P%d" % (i + 1))[:12],
                "lng": lng,
                "lat": lat,
                "day": int(s.get("day") or 1),
                "lodging": bool(s.get("lodging")),
            }
        )
    if not cleaned:
        raise SystemExit("stops 为空，无法出图")
    return data.get("title") or "旅行路线图", cleaned, (data.get("coordtype") or "").strip()


def day_color(day):
    return DAY_COLORS[(max(1, day) - 1) % len(DAY_COLORS)][0]


def day_color_name(day):
    return DAY_COLORS[(max(1, day) - 1) % len(DAY_COLORS)][1]


MARKER_LABELS = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def marker_label(i):
    """静态图 markerStyles 的 label 只接受单个 [0-9]/[A-Z] 字符。"""
    if i < len(MARKER_LABELS):
        return MARKER_LABELS[i]
    return MARKER_LABELS[i % len(MARKER_LABELS)]


def fmt(n):
    return ("%.6f" % n).rstrip("0").rstrip(".")


def compute_bbox(stops, pad=0.18):
    xs = [s["lng"] for s in stops]
    ys = [s["lat"] for s in stops]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    dx = max(maxx - minx, 0.004) * pad
    dy = max(maxy - miny, 0.004) * pad
    return (minx - dx, miny - dy, maxx + dx, maxy + dy)


def zoom_for_span(stops):
    span = max(
        max(s["lng"] for s in stops) - min(s["lng"] for s in stops),
        max(s["lat"] for s in stops) - min(s["lat"] for s in stops),
    )
    table = [(0.02, 15), (0.05, 14), (0.12, 13), (0.25, 12), (0.5, 11), (1.0, 10), (2.0, 9), (4.0, 8)]
    for limit, z in table:
        if span <= limit:
            return z
    return 7


def build_static_url(stops, ak, args, level=0, day_filter=None):
    """level: 0 全要素 / 1 去标签 / 2 去路线 / 3 仅标注点（center+zoom）。"""
    pts = [s for s in stops if day_filter is None or s["day"] == day_filter]
    if not pts:
        return None
    q = [("ak", ak), ("width", str(args.width)), ("height", str(args.height))]
    if args.dpi == 2:
        q.append(("dpiType", "ph"))
        q.append(("scale", "1"))
    q.append(("coordtype", args.coordtype))

    if level >= 3 or args.fit == "zoom":
        cx = sum(s["lng"] for s in pts) / len(pts)
        cy = sum(s["lat"] for s in pts) / len(pts)
        q.append(("center", "%s,%s" % (fmt(cx), fmt(cy))))
        q.append(("zoom", str(min(args.zoom or zoom_for_span(pts), 18))))
    else:
        minx, miny, maxx, maxy = compute_bbox(pts)
        q.append(("bbox", "%s,%s;%s,%s" % (fmt(minx), fmt(miny), fmt(maxx), fmt(maxy))))

    q.append(("markers", "|".join("%s,%s" % (fmt(s["lng"]), fmt(s["lat"])) for s in pts)))
    if level <= 1 and not args.no_styles:
        # 官方格式：size,label,color；size 取 l/m/s，label 取单个 [0-9]/[A-Z]，color 取 0xRRGGBB
        # 编号按「当日顺序」重新从 1 开始，与侧边说明页保持一致，同日同色便于区分
        styles = []
        counters = {}
        for s in pts:
            counters[s["day"]] = counters.get(s["day"], 0) + 1
            color = "0x333333" if s.get("lodging") else day_color(s["day"])
            size = "l" if s.get("lodging") else "m"
            styles.append("%s,%s,%s" % (size, marker_label(counters[s["day"]] - 1), color))
        q.append(("markerStyles", "|".join(styles)))

    if level <= 2 and not args.no_paths:
        lines, pstyles = [], []
        days = sorted({s["day"] for s in pts})
        for d in days:
            seq = [s for s in pts if s["day"] == d]
            if len(seq) < 2:
                continue
            lines.append(";".join("%s,%s" % (fmt(s["lng"]), fmt(s["lat"])) for s in seq))
            pstyles.append("%s,%d,0.85,0xFFFFFF" % (day_color(d), args.weight))
        if lines:
            q.append(("paths", "|".join(lines)))
            if not args.no_styles:
                q.append(("pathStyles", "|".join(pstyles)))

    if level == 0 and args.labels:
        q.append(("labels", "|".join("%s,%s" % (fmt(s["lng"]), fmt(s["lat"])) for s in pts)))
        q.append(
            (
                "labelStyles",
                "|".join("%s,1,12,0xFFFFFF,%s,1" % (s["name"], day_color(s["day"])) for s in pts),
            )
        )

    return "%s?%s" % (STATIC_API, urllib.parse.urlencode(q, safe="|;,:"))


def save_image(url, out_path, referer):
    try:
        ctype, body = http_get(url, referer=referer)
    except Exception as exc:  # 网络不可达、DNS、超时等
        return False, "请求异常 %s" % exc, (None, str(exc), None)
    if not is_image(ctype, body):
        status, msg = parse_error_body(body)
        return False, "status=%s message=%s" % (status, msg), (status, msg, None)
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(body)
    return True, "%d 字节" % len(body), (0, "", body)


def write_html(title, stops, images, out_path, coordtype="bd09ll"):
    """生成与图片同目录的说明页：标题 + 图例 + 分日节点顺序，图片以 base64 内联。"""
    blocks = []
    for img in images:
        try:
            with open(img["path"], "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception:
            continue
        cap = "总览" if img["day"] is None else "Day %d" % img["day"]
        blocks.append(
            '<figure><img src="data:image/png;base64,%s" alt="%s"><figcaption>%s</figcaption></figure>'
            % (b64, title, cap)
        )

    legend = []
    for idx, (color, cname) in enumerate(DAY_COLORS, start=1):
        if any(s["day"] == idx for s in stops):
            legend.append(
                '<span class="lg"><i style="background:%s"></i>Day %d · %s</span>'
                % ("#" + color.replace("0x", ""), idx, cname)
            )
    if any(s["lodging"] for s in stops):
        legend.append('<span class="lg"><i style="background:#333333"></i>住宿点</span>')

    day_rows = []
    for d in sorted({s["day"] for s in stops}):
        seq = [s for s in stops if s["day"] == d]
        items = "".join(
            '<li><b>%s</b> %s<span class="coord">%s, %s</span></li>'
            % (marker_label(i), s["name"] + ("（住宿）" if s.get("lodging") else ""), fmt(s["lng"]), fmt(s["lat"]))
            for i, s in enumerate(seq)
        )
        day_rows.append('<div class="day"><h3>Day %d</h3><ol class="plain">%s</ol></div>' % (d, items))

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>%s</title>
<style>
:root{--ink:#1b1f24;--muted:#5b6572;--line:#e3e7ee;--bg:#ffffff;--soft:#f6f8fb}
*{box-sizing:border-box}
body{margin:0;padding:28px 32px 40px;background:var(--bg);color:var(--ink);
font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.65}
h1{font-size:22px;margin:0 0 4px;letter-spacing:.02em}
.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 18px;font-size:13px;color:var(--muted)}
.lg{display:inline-flex;align-items:center;gap:6px}
.lg i{width:12px;height:12px;border-radius:3px;display:inline-block}
figure{margin:0 0 18px;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--soft)}
figure img{display:block;width:100%%;height:auto}
figcaption{padding:8px 12px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);background:#fff}
.days{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:6px}
.day{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--soft)}
.day h3{margin:0 0 8px;font-size:13px;letter-spacing:.06em;color:var(--muted);text-transform:uppercase}
.plain{list-style:none;margin:0;padding:0;font-size:14px}
.plain li{display:flex;justify-content:space-between;gap:8px;padding:3px 0;border-bottom:1px dashed var(--line)}
.plain li:last-child{border-bottom:0}
.plain b{color:#0b62c4;min-width:14px}
.coord{color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
footer{margin-top:16px;font-size:12px;color:var(--muted)}
</style></head><body>
<h1>%s</h1>
<div class="sub">百度地图静态图 · 标注字符为当日游览顺序 · 路线为节点直线连线（非道路级轨迹）</div>
<div class="legend">%s</div>
%s
<div class="days">%s</div>
<footer>坐标类型 %s；底图与路网版权归百度地图所有。图片为静态渲染，实际位置以导航结果为准。</footer>
</body></html>""" % (
        title,
        title,
        "".join(legend),
        "\n".join(blocks),
        "\n".join(day_rows),
        coordtype,
    )

    parent = os.path.dirname(os.path.abspath(out_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def cmd_staticmap(args):
    ak, source = resolve_ak(args.ak, kind="static")
    title, stops, file_coord = load_stops(args.spec)
    # 优先级：命令行 > 节点文件记录的坐标系 > bd09ll（MCP 与百度地理编码同为 bd09ll）
    args.coordtype = args.coordtype or file_coord or "bd09ll"
    print("行程：%s，节点 %d 个，AK 来源：%s，坐标类型：%s" % (title, len(stops), source, args.coordtype))
    if not ak:
        eprint("未找到 AK，无法出图。先跑 check 子命令确认或申请 AK（见 references/baidu-map-setup.md）。")
        return 2

    if args.only_day:
        days = [args.only_day]
    elif args.split_day:
        days = sorted({s["day"] for s in stops})
    else:
        days = [None]

    outputs = []
    failed = []
    # 凭据/配额类错误不再逐档重试，否则白烧配额与时间
    FATAL_STATUS = {200, 240, 201, 202, 203, 302, 303, 3, 5}
    fatal = False
    # 降级阶梯：0 全要素 → 1 去标签（仅在开标签时才有意义）→ 2 去标注样式 → 3 仅标注点
    levels = [0]
    if args.labels:
        levels.append(1)
    if not args.no_paths:
        levels.append(2)
    levels.append(3)
    for d in days:
        suffix = "" if d is None else "-day%d" % d
        out_path = args.out if d is None else re.sub(r"\.png$", "", args.out) + "%s.png" % suffix
        success = False
        last_err = ""
        for level in levels:
            url = build_static_url(stops, ak, args, level=level, day_filter=d)
            if not url:
                break
            if len(url) > 2048:
                last_err = "URL 长度 %d 超过 2048 上限" % len(url)
                continue  # 降级到下一档，通常会去掉样式/标签
            ok, info, meta = save_image(url, out_path, args.referer)
            if ok:
                level_name = {0: "全要素", 1: "去标签", 2: "去路线", 3: "仅标注点"}[level]
                print("[成功] %s → %s（%s，降级档位：%s）" % (os.path.basename(out_path), out_path, info, level_name))
                outputs.append(
                    {"path": out_path, "url": url, "level": level, "day": d, "bytes": len(meta[2] or b"")}
                )
                success = True
                break
            last_err = info
            status = meta[0]
            if status is None or status in FATAL_STATUS:
                fatal = True  # AK 无效 / 服务未开通 / 配额耗尽 / 网络不通，换个档位也一样失败
                break
        if not success:
            print("[失败] %s：%s" % (os.path.basename(out_path), last_err))
            failed.append({"path": out_path, "error": last_err})
            if fatal:
                status_hint = HINTS.get(meta[0]) if meta[0] in HINTS else "按 references/baidu-map-setup.md 第七节排查"
                print("已中止后续出图：%s" % status_hint)
                break

    if args.print_url:
        for o in outputs:
            print("URL：%s" % o["url"])
    else:
        for o in outputs:
            masked = re.sub(r"(ak=)([^&]+)", lambda m: m.group(1) + mask_ak(m.group(2)), o["url"])
            print("URL（AK 已打码，可直接分享）：%s" % masked)

    if outputs:
        print("")
        print("图例：")
        for idx, (color, cname) in enumerate(DAY_COLORS, start=1):
            if any(s["day"] == idx for s in stops):
                print("  Day %d  %s（%s）" % (idx, cname, color))
        if any(s["lodging"] for s in stops):
            print("  住宿点  大号深灰标注（0x333333）")
        print("提示：标注字符为当日游览顺序（每天从 1 重新编号、同日同色）；路线为节点直线连线，非道路级导航轨迹。")

    if outputs and args.html:
        html_path = re.sub(r"\.png$", "", args.out) + ".html"
        write_html(title, stops, outputs, html_path, args.coordtype)
        print("说明页：%s（可整页分享，图片已内联）" % html_path)

    if args.json:
        print("")
        print(json.dumps({"title": title, "images": outputs, "failed": failed}, ensure_ascii=False, indent=2))
    return 0 if outputs and not failed else (1 if failed else 0)


# ---------------------------------------------------------------------------- main


def main(argv=None):
    p = argparse.ArgumentParser(description="百度地图 AK 校验 / MCP 配置 / 静态路线图生成")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("ak", help="列出本机能自动发现的所有百度 AK 候选")
    a.add_argument("--ak", help="额外把该值加入候选（不写入任何文件）")
    a.set_defaults(func=cmd_ak)

    c = sub.add_parser("check", help="校验 AK 是否可用于静态图与地理编码")
    c.add_argument("--ak")
    c.add_argument("--referer", help="若 AK 配了 Referer 白名单，填入白名单中的域名")
    c.set_defaults(func=cmd_check)

    m = sub.add_parser("mcp-config", help="生成或写入百度地图 MCP Server 配置")
    m.add_argument("--ak")
    m.add_argument("--name", default="baidu-maps")
    m.add_argument("--transport", choices=["http", "sse", "stdio"], default="http")
    m.add_argument("--config", help="默认 ~/.workbuddy/mcp.json")
    m.add_argument("--write", action="store_true", help="真正落盘（默认只预演打印）")
    m.set_defaults(func=cmd_mcp_config)

    mc = sub.add_parser("mcp-check", help="直接对百度地图 MCP 端点做握手与工具探测")
    mc.add_argument("--ak")
    mc.add_argument("--url", help="完整 MCP 地址，默认 https://mcp.map.baidu.com/mcp?ak=…")
    mc.add_argument("--no-tools", action="store_true", help="只握手不拉工具列表")
    mc.add_argument("--test-geocode", metavar="地址", help="额外调用一次 map_geocode 验证鉴权")
    mc.add_argument("--city", help="配合 --test-geocode 限定城市")
    mc.set_defaults(func=cmd_mcp_check)

    k = sub.add_parser("set-ak", help="把 AK 写入技能目录 config.local.json")
    k.add_argument("--ak", required=True)
    k.add_argument("--static-ak", help="静态图专用 AK（可选，与 MCP 用 AK 分开时填）")
    k.set_defaults(func=cmd_set_ak)

    g = sub.add_parser("geocode", help="批量把地址解析成坐标，产出 staticmap 可用的节点文件")
    g.add_argument("--spec", help="节点清单 JSON：{\"title\":..,\"stops\":[{\"name\",\"address\",\"city\",\"day\"}]}")
    g.add_argument("--address", help="单个地址（与 --spec 二选一）")
    g.add_argument("--city", help="限定城市，提升命中率")
    g.add_argument("--day", type=int, default=1)
    g.add_argument("--out", help="输出节点文件路径；不填则直接打印")
    g.add_argument("--ak")
    g.add_argument("--interval", type=float, default=0.15, help="每个地址之间的间隔秒数")
    g.set_defaults(func=cmd_geocode)

    s = sub.add_parser("staticmap", help="生成带标注与路线的本地地图 PNG")
    s.add_argument("--spec", required=True, help="行程节点 JSON 文件")
    s.add_argument("--out", required=True, help="输出 PNG 路径")
    s.add_argument("--ak")
    s.add_argument("--coordtype", choices=["gcj02ll", "bd09ll", "wgs84ll", "bd09mc"], help="默认沿用节点文件里的记录，否则 bd09ll")
    s.add_argument("--width", type=int, default=1024)
    s.add_argument("--height", type=int, default=1024)
    s.add_argument("--dpi", type=int, default=2, choices=[1, 2], help="2=高清图")
    s.add_argument("--zoom", type=int, default=0, help="仅 fit=zoom 时生效，0 表示按跨度自动推算")
    s.add_argument("--fit", choices=["bbox", "zoom"], default="bbox")
    s.add_argument("--weight", type=int, default=6, help="路线线宽")
    s.add_argument("--labels", action="store_true", help="额外画地名标签（易拥挤，默认关闭）")
    s.add_argument("--no-styles", action="store_true")
    s.add_argument("--no-paths", action="store_true")
    s.add_argument("--split-day", action="store_true", help="按天分张出图")
    s.add_argument("--only-day", type=int, help="只出某一天")
    s.add_argument("--referer")
    s.add_argument("--print-url", action="store_true", help="打印含完整 AK 的 URL 用于调试")
    s.add_argument("--html", action="store_true", help="额外生成同名 HTML 说明页（标题+图例+节点顺序）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_staticmap)

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        p.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
