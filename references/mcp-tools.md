# 百度地图 MCP 工具速查（mcp-server-baidu-maps）

本文件是技能调用 MCP 时的参数依据，内容取自 `tools/list` 实测返回（服务端 `mcp-server-baidu-maps v1.28.0`，协议 `2025-06-18`，共 14 个工具）。

## 一、端点

| 传输 | 地址 / 命令 |
|---|---|
| Streamable HTTP（推荐） | `https://mcp.map.baidu.com/mcp?ak=你的AK` |
| SSE | `https://mcp.map.baidu.com/sse?ak=你的AK` |
| stdio | `npx -y @baidumap/mcp-server-baidu-map`，AK 走 `BAIDU_MAP_API_KEY` |

本地自检（不经过 WorkBuddy，直接验证服务端与 AK）：

```bash
python scripts/baidu_map.py mcp-check --ak 你的AK --test-geocode 成都宽窄巷子 --city 成都
```

握手成功只说明端点通，`--test-geocode` 才验证 AK 鉴权。**注意**：鉴权失败时 MCP 不会抛 JSON-RPC error，而是返回 `result.isError = true` + `content[0].text = "Authentication failed: ..."`，所以只看握手成功会误判。

## 二、全局约定（最容易踩的三条）

1. **坐标系是 bd09ll**（百度经纬度）。`map_reverse_geocode` 的参数说明里明确写了 `纬度 (bd09ll)`。静态图默认也是 `bd09ll`，两边一致，不要自作聪明改成 gcj02ll。
2. **纬度在前、经度在后**：`map_directions` 的 `origin`/`destination`、`map_directions_matrix` 的 `origins`/`destinations`、`map_search_places` 的 `location`，格式都是 `纬度,经度`（如 `30.6634,104.0555`）。静态图的 `markers`/`paths` 反过来是 `经度,纬度`。写反的典型表现是「检索无结果」或「路线跑到几百公里外」。
3. **坐标来源不是 bd09ll 时必须转换**：手头是高德/腾讯坐标（gcj02ll）用 `map_geoconv model=1`，是 GPS 原始坐标（wgs84）用 `model=2`，转完再出图。

## 三、工具表

| 工具 | 作用 | 关键入参 | 在本技能里怎么用 |
|---|---|---|---|
| `map_geocode` | 地址 → 坐标 | `address`（≤84 字节）、`city` | **主路径**：把每天的景点/住宿转成 `lng/lat` 写入 `trip.json` |
| `map_reverse_geocode` | 坐标 → 地址/行政区 | `latitude`/`longitude`（bd09ll，可用 `lat`/`lng`） | 校验住宿是否落在当日景点同区；给「区域 + 类型」的住宿建议提供依据 |
| `map_search_places` | 城市内检索 / 周边检索 | `query`、`region`、`tag`、`location`(lat,lng)、`radius` | 景点名不确定时先查准 POI；按 `tag=美食` 找餐厅，产出具体店名 |
| `map_place_details` | POI 详情 | `uid` | 取营业时间、电话、评分，用于「营业状态」类判断（仍以官方/现场为准） |
| `map_directions` | 路线规划 | `model`=driving/riding/walking/transit、`origin`、`destination`（名称或 `lat,lng`） | 验证相邻景点通勤耗时，决定上午/下午排几个点 |
| `map_directions_matrix` | 批量算路 | `origins`、`destinations`（`|` 分隔，`lat,lng`）、`model` | 一次算出多个候选景点之间的耗时矩阵，用于选「最顺路」的组合 |
| `map_weather` | 实时 + 5 天预报 | `location`（城市名或坐标）、`district_id` | 户外/室内行程排序，雨天把室内项目前置 |
| `map_ip_location` | IP 定位 | `ip` | 用户没说城市时猜当前城市（仅作追问用，不写进行程） |
| `map_road_traffic` | 实时路况 | `model`、`road_name`/`city`/`bounds`/`center`+`radius` | 判断晚高峰是否该改地铁，影响市内交通预算 |
| `map_search_pro` | 自然语言多维检索 | `query`、`region`、`type`、`center` | 「适合带孩子的室内博物馆」这类模糊需求，比 `map_search_places` 更合适 |
| `map_district_search` | 行政区划检索 | `keyword`、`boundary` | 拿 adcode 与边界，判断「跨区折返」 |
| `map_uri` | 地图调起链接 | `service`=route/search、`origin`、`destination`、`mode` | 给用户可直接点开导航的链接 |
| `map_mark` | 旅游规划分享图 | `text_content`（整份行程文本） | **行程定稿后**生成分享 URL + 二维码，和本地 PNG 一起交付 |
| `map_geoconv` | 坐标转换 | `coords`（`经度,纬度`，多组用 `;`）、`model` 1–6 | 高德/腾讯/GPS 坐标转 bd09ll（model 1/2），或 bd09ll 与 bd09mc 互转（3/4） |

`map_geoconv` 的 `model` 取值：1 高德/腾讯→bd09ll，2 GPS→bd09ll，3 bd09ll→bd09mc，4 bd09mc→bd09ll，5 bd09ll→高德/腾讯，6 bd09mc→高德/腾讯。

## 四、典型调用序列

```
1. map_search_places(query="宽窄巷子", region="成都市")      # 名称不确定时先查准
2. map_geocode(address="成都市青羊区长顺上街宽窄巷子", city="成都市")
   → result.location.lng / lat（bd09ll）
3. 写入 trip.json：{"coordtype":"bd09ll","stops":[{"name","lng","lat","day"}]}
4. map_directions_matrix(origins="30.6634,104.0555", destinations="30.6570,104.0810", model="driving")
   → 得到耗时，回填到当日行程的「游览顺序」说明里
5. python scripts/baidu_map.py staticmap --spec trip.json --out trip.png --html
6. map_mark(text_content=<整份行程文本>)  → 分享 URL + 二维码，作为补充交付
```

`map_mark` 只要一段行程文本即可，注意**不要传反斜杠等特殊字符**，否则工具会报错。它返回的是分享链接和二维码，不是可直接嵌入的图片，别拿它替代静态图。

## 五、MCP 不可用时的等价能力

| 能力 | MCP | 无 MCP 时的替代 |
|---|---|---|
| 地址 → 坐标 | `map_geocode` | `python scripts/baidu_map.py geocode --spec trip.json --out trip-coord.json`（走 REST 地理编码，结果同为 bd09ll） |
| 通勤耗时 | `map_directions` | 无替代，改为按直线距离 × 道路系数粗估（并在文中标明是估算） |
| 天气 | `map_weather` | 提示用户自行查看，不编造天气 |
| 分享图 | `map_mark` | 只交付本地 PNG + HTML 说明页 |

## 六、故障对照

| 现象 | 原因 | 处理 |
|---|---|---|
| 工具列表为空 | 配置未生效 | `~/.workbuddy/mcp.json` 检查后到「自定义连接器」点「信任」，新会话重试 |
| `Authentication failed: APP不存在` | AK 错/未开通服务 | `set-ak` 重新落地，控制台勾服务 |
| 检索/算路无结果 | 经纬度顺序写反 | 改回 `纬度,经度` |
| 路线距离离谱 | 坐标混了 gcj02ll | `map_geoconv model=1` 转 bd09ll |
| `text_content` 报错 | 含 `\` 等特殊字符 | 清洗文本后重试 |
