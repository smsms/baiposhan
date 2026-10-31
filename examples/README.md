# 行程节点文件格式

`scripts/sketch_map.py` 与 `scripts/baidu_map.py staticmap` 共用同一份节点文件。

## 字段

| 字段 | 必须 | 说明 |
|---|---|---|
| `title` | 否 | 图标题，缺省为「旅行路线图」 |
| `coordtype` | 建议 | 坐标系。支持 `bd09ll`（默认）、`gcj02ll`、`wgs84ll`、`bd09mc` |
| `stops[].name` | 是 | 节点名称，会作为标签与图例文字 |
| `stops[].lng` | 是 | 经度，**在前**（`lon` 也接受） |
| `stops[].lat` | 是 | 纬度，**在后** |
| `stops[].day` | 建议 | 第几天，缺省 1；决定配色与编号分段 |
| `stops[].lodging` | 否 | `true` 表示住宿点，用深灰大圆显示 |
| `stops[].address` / `city` | 否 | 仅 `baidu_map.py geocode` 批量解析坐标时使用 |

## 顺序约定

数组书写顺序 = 当日游览顺序。出图时按「日期升序 → 数组顺序」串联成一条连续路线，
跨日转场段也会连线（用出发日配色），箭头指向下一站。

## 最小示例

```json
{
  "title": "成都 3 日游",
  "coordtype": "bd09ll",
  "stops": [
    { "name": "宽窄巷子", "lng": 104.0551, "lat": 30.6699, "day": 1 },
    { "name": "春熙路", "lng": 104.0805, "lat": 30.6571, "day": 1 },
    { "name": "东郊记忆", "lng": 104.1247, "lat": 30.67, "day": 2, "lodging": true }
  ]
}
```

## 只有地址、没有坐标时

```bash
python scripts/baidu_map.py geocode --spec trip.json --out trip-coord.json
```

需要百度地图 AK。脚本会写入 `coordtype`，出图时自动沿用。
