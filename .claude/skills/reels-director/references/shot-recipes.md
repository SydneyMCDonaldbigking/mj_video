# 镜头配方（Krea2 关键帧 + H3 Ref2VA）

H3 的通用规律在 `~/.claude/skills/h3-motion-transfer/references/prompt-template.md` 第 0 节，写 motion_prompt 前读一次。
本页只记旅游片里验证过的写法，来源是 2026-09/10 跑过的 7 条日本片，21 段一次性全部通过。

## 关键帧提示词（`keyframe_prompt`）

管线会自动在前后加上目的地、季节、光线、风格、竖构图，以及
「no captions, no watermark, no fake logo, no prominent readable signage」。
所以这里只写**这一镜的画面**，按这个顺序：

1. 主体，带具体材质和颜色
2. 前景 / 背景，以及景深
3. 光线和天气
4. 机位：low angle / eye-level / high three-quarter / close-up
5. 排除项：`no people`，有招牌风险的再加 `no signboards, no lettering`

## 按题材

| 题材 | 写法 | 坑 |
|---|---|---|
| 地标远景（城堡、塔、寺庙） | 隔水或隔树看，前景放倒影或红叶，`low angle from across the moat` | 地标细节会画错，点名必须配真实参考图；否则写成 `an old castle keep` 一类泛称 |
| 自然风景（山、湖、海） | 镜面倒影 + 薄雾，主体占画面上 1/3 | 「5 cm push-in」常推得过头，宁可写固定机位 |
| 美食 | `tight close-up`，器皿和桌面质感写清楚，背景 `completely melted into soft bokeh` | 背景里的招牌会出伪文字，必须写虚化；不要写画面里没有的筷子、勺子 |
| 街头夜景 | 改拍水面霓虹倒影，或隔着雨窗看虚化的灯 | 正面拍商店街，必出乱码招牌（大阪道顿堀、东京横丁都翻过车） |
| 室内带窗景 | 低桌、茶具放前景，窗框里放远景 | 窗外景要在提示词里写明，否则会被随机替换 |
| 动物 | 单只、侧身或半侧、`head slightly turned toward the camera` | 不要写多只；动作只给一个（低头吃草再抬头） |
| 海滩、海岛 | 高位 3/4 俯看海岸线，写清浅水到深水的颜色渐变 | 不写船、不写人，否则会多出东西 |

## 运动提示词（`motion_prompt`）

模板（三段，总长不超过 600 字符）：

```
<机位>：Locked-off camera, no movement. / Slow 5 cm push-in …, then the camera holds still for the final second.

<唯一的主运动 + 环境里本来就在动的东西>。<要保持不动的主体，用正面陈述>。

Sound: <2–3 种环境声>.
```

- 主运动只写一个：浪拍岸、蒸汽升、霓虹倒影晃、鹿低头。
- 固定机位最稳；要动就只用一种运镜：推、横移、上摇。
- `Sound:` 写环境声，H3 会生成对应音轨，ASMR 格式全靠它。不要写人声和音乐。

## 自检表（写完逐条数，不要凭印象）

| # | 检查 | 不过的标准 |
|---|---|---|
| 1 | 数进行时动词（rise / drift / sway / ripple / flicker…） | 超过 1 个主运动 |
| 2 | 提示词里的每样东西，关键帧里都有吗 | 有图里没有的东西 |
| 3 | 关键帧里有字吗 | 有字却写了 `no text` |
| 4 | 长度 | motion_prompt 超过 800 字符 |
| 5 | 有 `try to` / `as much as possible` | 有 |

## seed

- 每镜一个，按 `<日期>` + `<序号>` 编，比如 26100301、26100302…
- 重抽时换 seed 并改 prompt：fp8 + LoRA 不保证同 seed 逐位复现，单改 seed 有时出来几乎一样。
