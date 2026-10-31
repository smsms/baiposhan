---
name: baiposhan-travel-planner
display_name: 百坡山智慧规划旅行家
display_name_en: Baiposhan Smart Travel Planner
description: 旅行规划技能。根据目的地与四项偏好（节奏：轻松/紧凑/常规；景点喜好：自然风光/人文/现代都市；饮食口味：清淡/重口味/低脂；预算：奢华/轻奢/节约）生成可执行的分日行程计划与人均预算明细（住宿/餐饮/门票/交通分项），并通过百度地图 MCP 解析坐标、校验通勤耗时、生成在线分享；输出按天配色、全程连续、带方向箭头的矢量路线图，未配置地图 AK 时也可零凭据出图。触发词：旅行规划、行程安排、旅游路线制定、几日游、行程花费测算、路线图、行程地图、配置百度地图 AK。不回答与旅行规划无关的问题，不虚构未经验证的景点营业状态或交通信息。
description_zh: 按目的地与四项偏好生成分日行程与人均预算明细，输出连续路线的方向箭头矢量图；可选接入百度地图 MCP 做坐标解析与通勤校验。
description_en: Plans day-by-day itineraries from destination plus four preferences (pace, sight type, taste, budget) with per-person cost breakdowns; renders vector route maps with direction arrows; optionally uses Baidu Maps MCP for geocoding and travel-time checks.
version: 1.5.0
author: 百坡山
examples_zh:
  - "帮我规划成都 3 天游：轻松节奏，偏人文，口味清淡，轻奢预算"
  - "从西安出发去杭州 4 天，紧凑节奏想看自然风光，人均 3000 以内"
  - "没有百度地图 AK，帮我出一张带方向箭头的矢量行程路线图"
examples_en:
  - "Plan a relaxed 3-day Chengdu trip, culture-focused, light meals, mid-range budget"
  - "4 days in Hangzhou from Xi'an, packed pace, nature sights, under 3000 CNY per person"
  - "No Baidu Map AK - give me a vector route map with direction arrows"
---
# 百坡山智慧规划旅行家

## Overview

本技能负责根据用户给出的目的地和四项偏好生成可执行的分日旅行计划，同步输出每日与全程的预算明细，并通过百度地图输出带路线标注的地图图片与分享链接。地图链路自带可执行工具：校验 AK、接入百度地图 MCP Server、探测 MCP 端点、批量地理编码、渲染本地 PNG 与说明页。只处理旅行规划任务；不回答与旅行规划无关的问题；不虚构未经验证的景点营业状态或交通信息。

## 核心能力

1. **偏好解析与路线编排**：将节奏（轻松/紧凑/常规）、景点喜好（自然风光/人文/现代都市）、饮食口味（清淡/重口味/低脂）、预算（奢华/轻奢/节约）转化为每日景点组合、住宿区位与餐饮筛选条件。
2. **MCP 接入与探测**：`mcp-config` 生成或合并 `~/.workbuddy/mcp.json`（http/sse/stdio 三种传输、写前备份）；`mcp-check` 直接对端点做 initialize + tools/list + `map_geocode` 实测，把「端点通」和「AK 能用」分开验证。工具参数见 `references/mcp-tools.md`。
3. **坐标解析与路线校验**：优先用 MCP 的 `map_geocode` 取坐标、`map_search_places` 查准 POI、`map_directions(_matrix)` 校验相邻节点通勤耗时；MCP 不可用时用 `scripts/baidu_map.py geocode` 走 REST 兜底；`map_geoconv` 负责把高德/腾讯/GPS 坐标统一到 bd09ll。
4. **静态路线图与分享**：`staticmap` 按天配色、按当日顺序编号、bbox 自动取景、四级降级，落成本地 PNG（`--html` 另出带图例的说明页）；行程定稿后再用 `map_mark` 生成在线分享链接与二维码。
5. **零 AK 出图**：`scripts/sketch_map.py` 直接渲染 **SVG 真矢量**路线示意图（全程连续连线、方向箭头、按天配色、节点编号、起点/终点环、比例尺、指北针、右侧图例面板，可选同名 PNG 位图），完全不需要任何地图凭据；AK 路径可用时仍优先 `staticmap`（有真实底图，但为位图）。
6. **预算测算**：按预算档位基准把住宿、餐饮、门票、市内交通逐项折算为人均区间，输出每日小计与全程合计，规则见 `references/budget.md`。

## 工作流程

按以下六步顺序执行，不得跳步或合并。

### 第 0 步：确保地图能力可用（AK 与 MCP）

出图前先自检，不通过就走降级路径，不要带着缺陷往下做。

1. 执行 `python scripts/baidu_map.py ak` 自动发现本机 AK：
   - 脚本按优先级扫描 `--ak` 参数 → 环境变量 → 技能目录 `config.local.json` → `~/.workbuddy/baidu_map.json` → MCP 配置里百度 server 的 URL/env（4 处路径），命中即自动复用，无需用户再提供。
   - 有候选 → 直接进第 2 小步的 `check`。
   - 无候选 → 先说清一个硬边界：**AK 不能凭空生成或从地点推导**，它绑定百度账号且需实名认证，只能由用户本人在 lbsyun.baidu.com 控制台创建。然后按 `references/baidu-map-setup.md` 第二节引导申请，拿到后执行 `python scripts/baidu_map.py set-ak --ak <AK> --static-ak <静态图AK>`，再重跑 `check`。
   - 用户暂时不想申请 AK → 图片环节改走 `scripts/sketch_map.py` 零 AK 示意图（见 5.4），不要空手跳过图片。
2. 执行 `python scripts/baidu_map.py check` 校验发现的 AK：
   - 输出「AK 可用」→ 进第 1 步。
   - 输出「AK 尚不可用」→ 按 HINTS 提示定位（未开通服务 / 白名单拦截 / 类型不匹配），修复后复测。
3. 看 MCP 三态，分别处理：
   - **已接入**（会话里能看到 `map_geocode`、`map_directions` 等工具）→ 直接用，坐标解析走第 5 步 5.2 路径 A。
   - **已配置但本会话看不到**（`~/.workbuddy/mcp.json` 里有 `baidu-maps`）→ 说明未点「信任」或未开新会话；本会话改用路径 B，并提示用户去「自定义连接器」点信任。
   - **未配置** → 执行 `python scripts/baidu_map.py mcp-config --ak <AK> --write`，再跑 `python scripts/baidu_map.py mcp-check --test-geocode 成都宽窄巷子 --city 成都` 确认服务端与 AK 都通，最后提示用户去点「信任」。
4. 记牢两条硬约束：坐标一律 **bd09ll**；`map_directions`/`map_search_places` 的坐标是**纬度在前**，静态图 `markers` 是**经度在前**，两处相反。

### 第 1 步：解析偏好，生成景点路线骨架

从用户输入中提取目的地、节奏、景点喜好、饮食口味、预算。

- 按节奏决定每日景点数量：轻松 2–3 个主景点，常规 3–4 个，紧凑 4–6 个。
- 用景点喜好决定主类型配比：自然风光优先户外/公园/山野；人文优先博物馆/古迹/街区；现代都市优先商圈/地标/夜景。
- 预算决定住宿档次和餐饮价格带，具体档位基准见 `references/budget.md` 第一节；饮食口味作为每餐推荐的第一筛选条件。
- 若用户未明确行程天数，默认按 3 天规划，并在开头注明"默认为 3 天，可调整"。

### 第 2 步：逐日填充游览顺序、住宿与美食

对每一天，按"上午 → 午餐 → 下午 → 晚餐"的时间轴安排景点顺序：

- 同一天内的景点应在地理上顺路，避免跨区折返。MCP 可用时用 `map_directions_matrix` 算一次耗时矩阵，把最顺路的组合排进当天。
- 住宿地点优先选在当日最后一个景点附近，或次日第一个景点通勤 20 分钟以内的区域；可用 `map_reverse_geocode` 确认住宿与景点是否同区。
- 美食推荐必须匹配用户的饮食口味：MCP 可用时用 `map_search_places(query="餐厅", tag="美食", region=城市名)` 找具体店名，每餐给 1–2 个并标注预算档位。
- 户外占比高的日子，用 `map_weather` 看未来 5 天预报，雨天把室内项目前置。

### 第 3 步：输出 Day 1 / Day 2 / Day 3 结构化计划

每天以 `### Day N：主题` 开头，下面固定四栏：

- `**游览顺序**`：时间 + 景点名 + 停留时长（有 MCP 时补一句相邻节点通勤耗时，如「步行 12 分钟 / 2.1km」）
- `**住宿**`：区域 + 类型，不编造具体酒店名
- `**美食**`：早/午/晚分开，标注口味与预算匹配情况
- `**当日预算**`：人均区间 + 四项分项，例如 `人均 ¥420–780（住宿 ¥200–400 / 餐饮 ¥100–200 / 门票 ¥80–120 / 市内交通 ¥40–60）`

当日预算的分项必须与当日行程严格对应：门票只能来自当日已排的景点，免费景点计 ¥0 并注明"免费"，住宿按当日实际住宿区域所在档位取值。测算口径见 `references/budget.md` 第二节。

### 第 4 步：汇总全程预算

在所有 Day 计划之后输出 `### 全程预算汇总`，用一张表格给出四项分项与合计：

| 项目 | 单价区间 | 数量 | 人均小计 |
|---|---|---|---|
| 住宿 | ¥A–B / 间 / 晚 | N 晚 ÷ 同住人数 | ¥X–Y |
| 餐饮 | ¥A–B / 人 / 天 | N 天 | ¥X–Y |
| 门票与体验 | ¥A–B / 人 / 天 | N 天 | ¥X–Y |
| 市内交通 | ¥A–B / 人 / 天 | N 天 | ¥X–Y |
| 弹性杂项 | 上四项 × 10% | — | ¥X–Y |
| **人均合计** | | | **¥X–Y** |

- 同住人数默认按 2 人一间折算；单人出行在表下补一行"含单房差"说明。
- 多人同行时在合计行下方补一行"团队总计（N 人）= 人均 × N"。
- 用户给了出发地且需要跨城往返时，在表格上方单列一行"大交通：¥X–Y / 人（未套用档位）"；未给出发地则注明"未含大交通，补充出发地后可测算"。
- 表下固定加一句：`以上为区间估算，实际以预订时的实时价格为准。`

### 第 5 步：解析坐标、出图、生成分享链接

**5.1 写节点清单**（`trip.json`，放当前工作目录）：

```json
{
  "title": "成都 3 日游",
  "coordtype": "bd09ll",
  "stops": [
    { "name": "宽窄巷子", "address": "成都市青羊区长顺上街宽窄巷子", "city": "成都", "day": 1 },
    { "name": "春熙路", "address": "成都市锦江区春熙路", "city": "成都", "day": 1 },
    { "name": "东郊记忆", "address": "成都市成华区建设南支路4号", "city": "成都", "day": 2, "lodging": true }
  ]
}
```

已拿到坐标的节点直接写 `{"name","lng","lat","day"}`，并在顶层写 `"coordtype": "bd09ll"`。

**5.2 解析坐标**（三选一，按可用性降级）：

- **路径 A：MCP 可用。** 名称不确定的先 `map_search_places(query=名称, region=城市)` 查准，再 `map_geocode(address=完整地址, city=城市)` 取 `result.location.lng/lat`（bd09ll），写进 `trip.json`。地址越完整（省市区 + 门牌）精度越高，`address` 上限 84 字节。
- **路径 B：MCP 不可用但有 AK。** `python scripts/baidu_map.py geocode --spec trip.json --out trip-coord.json`，脚本会写入 `"coordtype": "bd09ll"` 供出图自动沿用。
- **路径 C：都没有。** 让用户手填每个节点的大致经纬度（从任意地图 App 长按地点即可复制），或直接跳过坐标环节走 5.4 的零 AK 示意图（用相对位置排版，有坐标才画）。两者都不行再走 5.6 降级交付。

坐标不是 bd09ll 时（高德/腾讯 gcj02ll、GPS wgs84），先用 `map_geoconv(model=1/2, coords="经度,纬度;经度,纬度")` 转换，不要靠改 `coordtype` 硬凑。

**5.3 校验路线顺路度**（MCP 可用时做，推荐）：对当天相邻节点调用 `map_directions(model="driving|riding|walking|transit", origin="纬度,经度", destination="纬度,经度")`，把耗时写进第 3 步的「游览顺序」，明显绕路的调整顺序。参数方向见 `references/mcp-tools.md` 第二节。

**5.4 出图**（按凭据可用性二选一）：

```bash
# 有可用 AK（有真实地图底图，优先；底图为位图 PNG）
python scripts/baidu_map.py staticmap --spec trip-coord.json --out trip.png --html

# 无任何 AK（零凭据 SVG 矢量示意图：全程连续路线 + 方向箭头；--png 可另出位图）
python scripts/sketch_map.py --spec trip.json --out trip.svg --html
```

- `staticmap`：默认 1024×1024 高清、按天配色、按当日顺序编号、自动取景。节点分散全城或跨日跨区时加 `--split-day`；单日点位过密加 `--split-day`；图面宽松时才加 `--labels`。验收：脚本打印 `[成功]` 并标注降级档位即通过；文件小于 5 KB 视为空白底图，换 `--fit zoom` 复测。
- `sketch_map`：默认 1280×860，`--out` 扩展名决定格式（`.svg` 矢量优先、无需任何依赖；`.png` 位图需 Pillow），`--png` 在 SVG 之外额外导出同名位图。路线按「日期 → 当日顺序」把全部节点串成**一条连续折线**（跨日转场段同样相连，用出发日配色），每段路径按等距放置**方向箭头**指向下一站，并画起点环/终点双环；右侧图例面板含行进方向与起点/终点说明。节点名称标签自动避让，放不下的自动省略（信息仍在图例与 HTML 说明页）；`--html` 把 SVG 内联进说明页，缩放不糊、可直接打印。
- 两条路径都不修改坐标系语义：`sketch_map` 只做相对位置排版，任何 coordtype 都能画，但**不要**把它当导航底图用，图内已注明"非实际道路底图"。用户明确要"矢量图/可编辑图/放大不糊"时，即使有 AK 也改用 `sketch_map` 的 SVG 输出。

**5.5 生成在线分享**（MCP 可用且用户需要转发时）：把整份行程文本传给 `map_mark(text_content=...)`，得到分享 URL + 二维码。注意文本里不要带 `\` 等特殊字符；它只返回链接，不是图片，不要拿它替代 5.4 的 PNG。

**5.6 呈现与降级**：

- 用 `present_files` 展示路线图（SVG 矢量或 PNG）与（若生成）HTML 说明页；正文给文字图例：`Day 1 红 · Day 2 蓝 · Day 3 绿（住宿为深灰大号点，标注字符为当日顺序；箭头指向下一站，全程连续连线）`，并注明"路线为节点直线连线，非道路级导航轨迹"。有 `map_mark` 链接时单列一行「在线版：<链接>」。
- 无 AK 且连零 AK 示意图也不适用（用户没给也没法提供任何坐标）时：给「名称 + 当日顺序」清单 + 文字路线描述（如 `Day1：宽窄巷子 → 人民公园 → 春熙路，直线距离约 2.4km`），不贴任何伪造 URL，只补一句「申请 AK 后运行 `staticmap` 可出带真实底图的路线图」。

## 输出规范

- 最终交付结构固定为：先用 2–3 句话说明本次规划的偏好适配逻辑，然后依次给出 Day 1、Day 2、Day 3 的结构化内容，接着输出 `### 全程预算汇总` 表格，最后输出地图图片（或降级交付）、图例与可选分享链接。
- 每天必须包含游览顺序、住宿、美食、当日预算四个字段，字段名加粗，不得省略任何一项。
- 美食推荐必须标注"口味匹配：是/部分匹配"和"预算档位"；无法确认的店不写具体人均，只写区间。
- 预算一律输出区间（`¥X–Y`），不得给单点精确值；分项之和必须等于当日/全程合计，不得出现对不上的数字。
- 地图相关：坐标一律 bd09ll 并写入节点文件；标注字符是"当日顺序"而非全行程序号；正文图例必须写清配色与编号规则。
- 外发链接一律用脚本输出的打码 URL（`ak=abcd***wxyz`），禁止把含完整 AK 的链接贴进对话或文档；`map_mark` 的分享链接不含 AK，可安全外发。

## 注意事项

- **MCP 不返回图片。** 图片由 `staticmap` 拼接静态图 API URL 后下载到本地；`map_mark` 只给分享链接与二维码。三者别混用。
- **坐标系只有一套：bd09ll。** MCP 的 `map_geocode` 与百度地理编码 REST 返回的都是 bd09ll，静态图默认也是 bd09ll。手头若是 gcj02ll/wgs84，先 `map_geoconv` 转换（model 1/2），再出图。
- **经纬度顺序两套：** `map_directions`/`map_directions_matrix`/`map_search_places.location` 用 `纬度,经度`；静态图 `markers`/`paths`、`map_geoconv.coords` 用 `经度,纬度`。写反的表现是「无结果」或「路线跑到几百公里外」。
- **MCP 鉴权失败不抛 error：** 返回 `result.isError=true` + `content[0].text="Authentication failed: ..."`。只看握手成功会误判，必须用 `mcp-check --test-geocode` 实测一次。
- 静态图对 AK 有类型要求：优先「浏览器端」AK 并勾选静态图服务；服务端 AK 不支持 sn 校验。详见 `references/baidu-map-setup.md`。
- `markerStyles` 格式是 `size,label,color`，`size` 只能 `l`/`m`/`s`，`label` 只能是单个 `[0-9]`/`[A-Z]` 字符；传错不报错，只是样式不生效。
- URL 上限 2048 字符、标注点上限 50 个；超长表现为"部分标注消失"，靠 `--split-day` 拆图比硬塞可靠。
- 景点营业时间、门票价格、餐厅是否营业以用户确认或实时检索为准；`map_place_details` 只能作参考，不得断言"确认营业中"。
- 预算只给区间，不得写成确定数值，也不得写"价格已核实"；住宿只推荐"区域 + 类型"，不编造酒店名称和房态。
- AK 属于用户凭据：写入 `config.local.json` 或环境变量，不写进 SKILL.md、references、示例与对外文档；`--print-url` 仅本机调试用。
- `mcp-config --write` 只做合并并自动备份原 `~/.workbuddy/mcp.json`；写入后不会自动生效，必须提示用户去点「信任」。
- 如果用户只给了目的地而没有给全四项偏好，在开头用一句话追问缺失项，但不阻塞输出：先按"常规节奏 / 人文+自然 / 清淡 / 轻奢"的默认组合给出可调整的草案。
- 如果用户给出硬性预算上限（如"人均 2000 以内"），先按节约/轻奢档位铺排再核算，超限时替换高价项目并说明替换逻辑，同时把实际估算区间与上限的差额在汇总表下方点明。
- 不要在本文件或专家包 frontmatter 中写入 `tools:` 字段；MCP 工具由 WorkBuddy 系统分配，流程中按名称调用 `map_geocode`、`map_directions`、`map_search_places`、`map_mark` 等，脚本按路径调用。

## 资源文件

| 文件 | 用途 | 何时读取 / 执行 |
|---|---|---|
| `scripts/baidu_map.py` | 七个子命令：`ak` 自动发现本机 AK、`set-ak` 落地 AK、`check` 校验 AK、`mcp-config` 写配置、`mcp-check` 探测 MCP 端点、`geocode` 批量解析坐标、`staticmap` 出图 | 第 0 步与第 5 步直接执行，无需通读源码 |
| `scripts/sketch_map.py` | 零 AK 示意图渲染（SVG 真矢量优先）：全程连续路线 + 方向箭头、按天配色、节点编号、起点/终点环、标签避让、比例尺/指北针/图例面板、可选同名 PNG 与内联矢量 HTML 说明页 | 无 AK、或用户要矢量图/放大不糊时执行；PNG 后端依赖 Pillow |
| `references/mcp-tools.md` | 14 个 MCP 工具的参数、坐标系与经纬度顺序约定、典型调用序列、故障对照 | 调用 MCP 前必读，尤其 `map_directions`/`map_mark`/`map_geoconv` |
| `references/baidu-map-setup.md` | AK 类型选择与申请步骤、白名单/SN 规则、MCP 三种接入方式、报错速查 | AK 缺失、校验失败、MCP 未接入时 |
| `references/static-map-spec.md` | 静态图 API 参数与样式格式、配色契约、四级降级、出图验收 | 调样式、排查"标注缺失/偏移/空白图"时 |
| `references/budget.md` | 预算档位基准、人均房费折算规则、档位与偏好联动、示例演算、预算禁忌 | 核定价格区间口径或做硬预算上限校验时 |
| `assets/icon.png`、`assets/icon.jpg` | 技能图标 | 由 WorkBuddy 加载，无需在流程中引用 |

## 版本记录

变更历史见仓库根目录 `CHANGELOG.md`（当前版本 1.5.0）。
