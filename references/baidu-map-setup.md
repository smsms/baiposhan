# 百度地图 AK 与 MCP 接入指南

本文件说明如何拿到一个「真的能用」的百度地图 AK、如何把它接到 WorkBuddy 的 MCP 上、以及报错时怎么排。所有出图与校验动作都由 `scripts/baidu_map.py` 执行，本文件只提供判断依据。

## 一、先明确需要几个 AK

| 用途 | 建议 AK 类型 | 勾选的服务 | 白名单 |
|---|---|---|---|
| MCP Server（地理编码、地点检索、路线规划） | 服务端 | 地理编码、逆地理编码、地点检索、路线规划、天气（按需） | IP 白名单留空（不限制）或填本机公网出口 IP |
| 静态图出图（`staticimage/v2`） | 浏览器端 | 静态图 API | Referer 白名单留空（不限制）或填来源域名 |

结论：**最省事的组合是两个 AK**（服务端给 MCP、浏览器端给静态图），互不牵连，排查时不会互相误导。只申请一个也行，但要用「浏览器端」类型并保证 Referer 白名单留空，否则 MCP 侧调用会因为缺少 Referer 被拦。

关键差异（都来自官方文档口径）：

- 静态图 `staticimage/v2` 明确支持浏览器端 AK；服务端 AK **不支持 sn 校验方式**，如果该 AK 开了 SN 校验，出图会直接失败。
- MCP Server 侧（含 `map_geocode`）走的是纯服务端 HTTP 调用，浏览器端 AK 若配了 Referer 白名单会因「无 Referer」被拒。
- 静态图返回的不是图片而是 JSON 报错，就说明 AK 侧有问题，不要怀疑坐标。

## 二、申请步骤

1. 打开百度地图开放平台：`https://lbsyun.baidu.com/`，用百度账号登录。
2. 完成开发者认证（个人需实名，企业需营业执照）。未认证的账号配额极低，且部分服务不可勾选。
3. 进入控制台「应用管理 → 我的应用 → 创建应用」：
   - 应用名称：如 `travel-planner-mcp`
   - 应用类型：按上表选择 `服务端` 或 `浏览器端`
   - 启用服务：勾选实际要用的服务（**静态图、地理编码、地点检索、路线规划**是本技能的必需项）
   - 白名单：IP（服务端）或 Referer（浏览器端），不确定就**留空表示不限制**
4. 保存后复制「访问应用（AK）」那串 32 位字符。**注意首尾空格**——这是最高频的低级错误。
5. 白名单修改后需要 **5–15 分钟** 生效，刚改完立刻测会得到误导性结论。

> 配额（QPS、日调用量）以控制台「应用管理 → 配额」显示的为准，不同账号等级差异很大。本技能一次出图只消耗 1 次静态图调用。

## 三、把 AK 落地到本技能

**先说硬边界**：AK 无法凭空生成，也无法由行程中的地点"推导"出来——它绑定百度账号、应用类型与服务白名单，必须由本人在控制台实名创建。技能能做的是**自动发现与复用**：配置一次，之后所有子命令零参数。

`scripts/baidu_map.py ak` 按以下优先级自动扫描，命中即用：

1. 命令行 `--ak`
2. 环境变量 `BAIDU_MAP_AK`（或 `BAIDU_MAP_API_KEY`、`BAIDU_MAP_SERVER_AK`）
3. 技能目录 `config.local.json`
4. `~/.workbuddy/baidu_map.json`（同格式，跨技能复用）
5. MCP 配置中百度 server 的凭据：`~/.workbuddy/mcp.json`、`~/.workbuddy/.mcp.json`、当前目录 `.mcp.json`、`<cwd>/.workbuddy/mcp.json`——会从百度 server 的 URL（`?ak=`）、`env`、`headers` 里提取；server 识别只看名称与 URL 主机名，别的服务的 key 不会被误认。

落地第 3 种最省事，不用手写 JSON：

```bash
python scripts/baidu_map.py set-ak --ak 你的AK
# 静态图 AK 与 MCP AK 分开时：加 --static-ak 你的静态图AK
```

脚本会校验是否带空白字符、长度是否异常，写入后自动收紧文件权限。`config.local.json` 跟技能放一起，换机器时一起带走；若该目录纳入版本控制，记得把它加进忽略列表。

**安全约束**：AK 会出现在静态图 URL 里。对外分享时用脚本默认的打码输出（`--print-url` 才打印完整 AK）；不要把 AK 写进公开文档、聊天记录或截图。

## 四、校验 AK 是否可用

```bash
python scripts/baidu_map.py check
```

脚本做两项探测并给出结论：

1. **静态图探测**：请求一张 160×160 天安门小图。返回 `image/*` 或 PNG 头 → 通过；返回 JSON → 打印 `status/message` 并给排查建议。
2. **地理编码探测**：解析「北京天安门」。`status=0` → 通过。

两项都通过才算「可出图」。只有地理编码通过、静态图失败时，通常是 AK 类型或「静态图」服务未勾选。

## 五、接入百度地图 MCP Server

### 5.1 用脚本自动写配置

```bash
# 预演：只打印将要写入的片段
python scripts/baidu_map.py mcp-config --ak 你的AK

# 落盘：合并进 ~/.workbuddy/mcp.json（原有 server 保留，自动备份）
python scripts/baidu_map.py mcp-config --ak 你的AK --write
```

三种传输方式用 `--transport` 指定：

| 方式 | 参数 | 说明 |
|---|---|---|
| Streamable HTTP（推荐） | `--transport http` | 远程直连 `https://mcp.map.baidu.com/mcp?ak=AK`，无需装 Node/Python |
| SSE | `--transport sse` | 远程 `https://mcp.map.baidu.com/sse?ak=AK`，兼容旧客户端 |
| stdio | `--transport stdio` | 本地进程 `npx -y @baidumap/mcp-server-baidu-map`，AK 走 `BAIDU_MAP_API_KEY` |

**写入后不会自动生效**：到 WorkBuddy 连接器管理页右上角的「自定义连接器」入口，找到 `baidu-maps` 并点「信任」，然后在新会话里验证。

### 5.2 验证 MCP 是否真的通了

先做纯协议层验证，不依赖 WorkBuddy 是否已加载工具：

```bash
python scripts/baidu_map.py mcp-check --ak 你的AK --test-geocode 成都宽窄巷子 --city 成都
```

它会分三步报结果：initialize 握手 → tools/list（应为 14 个工具）→ 真实调用 `map_geocode`。**只有三步全通过才算通**，因为百度地图 MCP 在鉴权失败时不会抛 JSON-RPC 错误，而是返回 `result.isError=true` 和一句 `Authentication failed: ...`，只看握手成功会得到"能用"的假象。

WorkBuddy 侧再确认一次：让它调用 `map_geocode` 解析任意确定地址，能返回经纬度即连通。若工具列表为空，说明本地配置未生效——检查 `~/.workbuddy/mcp.json` 里的 `baidu-maps` 条目，并到「自定义连接器」点「信任」后开新会话。

### 5.3 MCP 工具与用途

完整 14 个工具的入参、返回形态与踩坑点见 `references/mcp-tools.md`。高频的五个：

| 工具 | 本技能里的用途 |
|---|---|
| `map_geocode` | 把景点/住宿地址批量转成坐标（出图的输入） |
| `map_search_places` | 按关键词在城市内找 POI，补全不确切的景点名、找餐厅 |
| `map_directions` / `map_directions_matrix` | 查相邻节点通行方式与耗时，验证行程是否顺路 |
| `map_mark` | 行程定稿后生成在线分享链接与二维码 |
| `map_geoconv` | 把高德/腾讯/GPS 坐标统一转成 bd09ll |

另外 `map_weather` 影响户外/室内排序，`map_reverse_geocode` 判断住宿是否与景点同区。

## 六、坐标系与经纬度顺序：最容易出错的一环

- MCP 与百度地理编码 REST 返回的都是 **bd09ll（百度经纬度）**。`map_reverse_geocode` 的参数说明里直接写了 `纬度 (bd09ll)`，这是最硬的证据。
- 静态图 `coordtype` 默认也是 `bd09ll`，两边天然一致，**本技能出图默认传 `--coordtype bd09ll`**，不需要额外转换。
- 只有坐标来自高德/腾讯（gcj02ll）或 GPS（wgs84）时才有问题：这种坐标直接出图会**偏移约 100–600 米**，看起来就像「景点跑到隔壁街」。正确做法不是改 `coordtype` 硬凑，而是用 `map_geoconv(model=1 高德/腾讯 或 2 GPS)` 转成 bd09ll 再出图。
- **经纬度顺序有两套**：`map_directions`、`map_directions_matrix`、`map_search_places.location` 都是 `纬度,经度`；静态图 `markers`/`paths` 与 `map_geoconv.coords` 是 `经度,纬度`。写反的典型表现是「检索无结果」或「路线跑到几百公里外」。
- 判断是否偏移：若标注点系统性偏向同一方向，先查坐标来源与顺序，再调 `coordtype`，最后才动 zoom。

## 七、报错速查

| 现象 | 大概原因 | 处理 |
|---|---|---|
| `status=200 APP不存在，AK有误请检查再重试` | AK 复制不完整/含空格，或应用被删 | 重新复制 AK，跑 `check` |
| `status=240 APP 服务被禁用` | 该 AK 没勾选对应服务 | 控制台勾选「静态图」/「地理编码」 |
| `status=302 / 303 / 402` | 配额超限或并发过高 | 看控制台配额，降频或换 AK |
| 地理编码通过、静态图失败 | AK 类型不对或未勾静态图 | 换成浏览器端 AK |
| 图片为空白底图、无标注 | URL 超长被截断，或坐标越界 | 降级重试（脚本自动做），或按天分图 |
| 打开 URL 是 JSON | 服务端返回错误而非图片 | 按 status 查上表 |
| 白名单刚改就报错 | 未到生效时间 | 等 15 分钟再测 |

## 八、没有 AK 时的交付方式

不要伪造图片链接。改为输出：

1. 每个节点的 `名称 + 坐标（bd09ll）+ 当日顺序`；
2. 文字版路线描述（「Day1：宽窄巷子 → 人民公园 → 春熙路，直线距离约 2.4km」）；
3. 一句提示：配置 AK 后可运行 `python scripts/baidu_map.py staticmap --spec trip.json --out trip.png` 补出图片。
