# 贡献指南

## 开发环境

```bash
git clone https://github.com/<your-name>/baiposhan-travel-planner.git
cd baiposhan-travel-planner
python tests/test_render.py          # 冒烟测试：不需要网络、不需要 AK
```

可选：`pip install pillow`，仅用于验证 PNG 位图后端。SVG 输出是零依赖路径。

## 目录约定

| 目录 | 用途 | 是否会被打进技能包 |
|---|---|---|
| `SKILL.md` | 技能定义，AI 读取的指令 | 是 |
| `references/` | 按需加载的参考文档 | 是 |
| `scripts/` | 可执行脚本 | 是 |
| `assets/` | 图标等输出素材 | 是 |
| `examples/` `tests/` `tools/` `docs/` | 仓库配套，供人看 | 否 |

## 改动原则

1. **布局与渲染分离**：几何计算一律放在 `build_layout()`，SVG 与 PNG 两个后端只做「照着画」。
   不要在某个后端里直接算坐标，否则两种格式的版式会漂移。
2. **路线必须连续**：全部节点按「日期升序 → 数组顺序」串成一条折线，跨日转场段也要连线。
   改动后请确认 `stats["legs"] == 节点数 - 1`。
3. **不引入重依赖**：除可选的 Pillow 外只用标准库；新增依赖需在 PR 说明理由。
4. **不写入本地路径与凭据**：代码、文档、示例中不得出现个人路径、AK、token。
   `config.local.json` 已在 `.gitignore` 中。
5. **文档同步**：改参数改行为时，同步更新 `SKILL.md`、`README.md` 与 `CHANGELOG.md`。

## 提交前自检

```bash
python tests/test_render.py
python tools/build_package.py     # 会校验 frontmatter 必填字段与 zip 结构
```

## 提交规范

- 分支命名：`feat/xxx`、`fix/xxx`、`docs/xxx`
- 提交信息用祈使句描述做了什么，例如 `fix: 跨日转场段缺失导致路线断裂`
- PR 描述里写清：改了什么、为什么、如何验证（附测试输出为佳）
