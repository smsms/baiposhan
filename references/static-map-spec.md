# 静态路线图出图规范

本文件是 `scripts/baidu_map.py staticmap` 的参数依据与出图验收标准。改脚本前先读这里。

## 一、服务约束（超出即失败或静默截断）

| 项 | 限制 | 说明 |
|---|---|---|
| 服务地址 | `https://api.map.baidu.com/staticimage/v2` | GET，仅此版本可用 |
| URL 总长 | ≤ 2048 字符 | 超长会截断参数，表现为「部分标注丢失」而不是报错 |
| 标注点数量 | ≤ 50 | 超过需分图 |
| width / height | (0, 1024] | `scale=2` 时上限 512 |
| zoom | 3–18（高清）/ 3–19（普通） | 与 `bbox` 二选一 |
| 返回格式 | PNG | 失败时返回 JSON |

## 二、参数

| 参数 | 必选 | 本技能取值 | 说明 |
|---|---|---|---|
| `ak` | 是 | 浏览器端 AK | 服务端 AK 不支持 sn 校验 |
| `width` `height` | 否 | 1024×1024 | 方图便于竖版页面嵌入 |
| `coordtype` | 否 | **bd09ll** | 与 MCP / 百度地理编码保持一致；仅当坐标另有来源时才改 |
| `bbox` | 否 | 由节点外扩 18% 自动计算 | 格式 `minX,minY;maxX,maxY`，与 `center` 二选一 |
| `center` `zoom` | 否 | 降级时才用 | `zoom` 由经纬跨度查表推算 |
| `dpiType` | 否 | `ph` | 高清底图 |
| `scale` | 否 | `1` | 与 `dpiType=ph` 搭配输出高清尺寸 |
| `markers` | 否 | 全部节点 | `lng,lat|lng,lat|...` |
| `markerStyles` | 否 | 按天配色 | `size,label,color` 逐项对应，见第三节 |
| `paths` | 否 | 按天分段 | 每天一条折线：点用 `;`，多条线用 `|` |
| `pathStyles` | 否 | 与当天同色 | `color,weight,opacity,fillColor` |
| `labels` `labelStyles` | 否 | 仅 `--labels` 时 | 地名标签，点位密集时会互相压盖 |

## 三、样式格式（官方口径，写错不报错、只是不生效）

**markerStyles**：`size,label,color`

- `size`：`l` / `m` / `s`（注意不是 `large`/`mid`/`tiny`，那是腾讯/Google 的取值）
- `label`：**单个** `[0-9]` 或 `[A-Z]` 字符，超出一个字符不渲染
- `color`：`0xRRGGBB`
- 样式数少于点数时，多余点沿用最后一种样式

**pathStyles**：`color,weight,opacity,fillColor`，例 `0xD93025,6,0.85,0xFFFFFF`

**labelStyles**：`content,border,fontSize,fontColor,bgColor,fontWeight`，例 `宽窄巷子,1,12,0xFFFFFF,0xD93025,1`

## 四、本技能的视觉契约（改配色需同步 SKILL.md 图例说明）

| Day | 色值 | 中文 |
|---|---|---|
| 1 | `0xD93025` | 红 |
| 2 | `0x1A73E8` | 蓝 |
| 3 | `0x0F9D58` | 绿 |
| 4 | `0x7B1FA2` | 紫 |
| 5 | `0xF29900` | 橙 |
| 6 | `0x00ACC1` | 青 |
| 住宿 | `0x333333` + size `l` | 深灰大号 |

- 标注字符为「当日游览顺序」，每天从 1 重新编号（同日同色，跨天靠颜色区分）；字符池 `123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ`，单日超过 35 个节点时需 `--split-day` 或拆半天。
- 静态图无法在图上写标题和指北针，标题与图例由外部文字或 `--html` 侧边页承担。

## 五、四级降级策略（脚本内置，无需手动干预）

| 档位 | 保留内容 | 触发时机 |
|---|---|---|
| 0 全要素 | 标注+样式+路线+标签 | 首选 |
| 1 去标签 | 标注+样式+路线 | `--labels` 时 URL 超长或请求失败 |
| 2 去标注样式 | 标注+路线 | 上一档仍失败 |
| 3 仅标注点 | 标注 + `center/zoom` | 兜底，`bbox` 可能不合法时改走中心点 |

每一档都会把实际使用的档位打印出来（`降级档位：xxx`）。若最终仍失败，脚本返回非 0 且打印 `status/message`，此时不要重试，按 `baidu-map-setup.md` 第七节排查。

## 六、出图验收

1. **Content-Type 为 `image/*` 或响应体以 PNG 魔数 `89 50 4E 47` 开头**；否则视为失败（服务端错误是 JSON）。
2. 文件大于 5 KB：小于该值多为纯色空白底图。
3. 肉眼检查三件事：标注数量等于节点数；同一天的点被同色折线串起；标注没有挤成一团（挤则改 `--split-day`）。
4. 多日行程且节点分散在全城时，优先「总览图 + 每日分图」两张：总览看格局，分图看顺序。

## 七、常见显示问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 底图正常但景点整体偏一条街 | 坐标不是 bd09ll（多为高德/腾讯 gcj02ll 混入） | 先用 `map_geoconv model=1` 转换；确认来源后再改 `--coordtype` |
| 只显示前几个标注 | URL 超 2048 被截断 | 减少节点或 `--split-day` |
| 标注字符变成统一的 A | `markerStyles` 的 label 传了多字符 | 用单字符（脚本已处理） |
| 折线横穿水面/建筑 | 直线连线不沿路网 | 属预期；如需沿路轨迹，用 `map_directions` 的步骤坐标替换 path 点 |
| 中文地名显示为方块 | 字体不支持 | 改用数字/字母标注，地名放到侧边图例 |
| `--html` 页面里图片不显示 | 相对路径跨目录 | 用同目录输出，脚本默认同目录 |
