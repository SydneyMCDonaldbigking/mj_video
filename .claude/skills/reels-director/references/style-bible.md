# 风格手册

## 画面

- 统一用 MJ 电影感：柔光、浅景深、暖冷对比，写实。不用插画风，不用过饱和的明信片色。
- 分镜板里不写 `style` 时，用 `reels_make.py` 的 `HOUSE_STYLE`。单条需要不同光线时，在 `style` 里补充，不要推翻整体调性。
- 竖版 1080p 关键帧，后期统一调成稍高对比、稍高饱和（`GRADE`）。

## 主题色（`accent` / `accent_text`）

每条一个主题色，用在高亮字块、标签和图钉上。按画面主色挑：

| 场景 | accent | accent_text |
|---|---|---|
| 红叶、神社 | `#E0393E` | `#FFFFFF` |
| 霓虹夜景 | `#FF2D87` | `#FFFFFF` |
| 日出、温泉 | `#FF8A5B` | `#1A0E08` |
| 雪、冬季 | `#A9DBFF` | `#0A1A2B` |
| 美食、暖光 | `#FFB21A` | `#1A1206` |
| 海岛、海水 | `#27D3C3` | `#04201E` |
| 森林、苔藓 | `#9BD06A` | `#0F1A08` |

浅色主题色配深色字，深色主题色配白字。

## 文字

- 字体和字号由脚本固定，分镜板里不用管：钩子用 Segoe UI Black，字幕用 Segoe UI Bold；白字带柔光阴影。
- 每句最多 7 个词、2 行，句子尽量短。每块文字只用一个 `{高亮}`。
- 用澳式拼写：colour、favourite、travelling、centre、jewellery。
- 口吻是口语化的第二人称（you），像朋友推荐，不像广告。
- 不写价格、排名（「best in the world」）、营业时间、航班时长这类无法核实的说法。

## emoji

- 每行最多 1 个，放在行尾。钩子最好带 1 个。
- 只用单个码位的常见 emoji，比如 🍁 🍂 🏮 🌃 🍻 🍢 🗻 ♨️ ❄️ ☃️ 🍜 🐙 🔥 ✨ 🏝️ 🌊 🌅 🦌 🚆 🏯 ⛩️ 🌸 🍵 🍣 🎌 🌧️ 👇 🔖。
- 本机 Pillow 没有 raqm，不能用组合 emoji（👨‍👩‍👧）、国旗、肤色修饰；`load_board` 遇到会报错。
- 渲染用 Windows 的 Fluent 风格彩色 emoji，跟 iPhone 上的样子不一样，这是正常的。

## 贴纸（`stickers`，每条最多 3 个，自动加的不算）

| type | 用法 | 字段 |
|---|---|---|
| `location` | 首镜自动加（取 `pin`），不用写 | `text` |
| `tag` | 主题色倾斜标签：「MUST TRY 🔥」「LOCAL FAVE 🍢」「EASY DAY TRIP 🚆」「HIDDEN GEM 💎」 | `text` |
| `emoji` | 浮动大 emoji，会轻轻晃，放在文字块角上 | `emoji`、`size`（默认 150） |
| `arrow` | 手绘箭头，逐帧画出来，指向主体（如「with this view」指向富士山） | `dir`：up/down/left/right/up-left… |
| `time` | 行程格式里由 `label` 自动生成 | `text` |
| `sound` | ASMR 格式自动加 | — |

位置用 `pos`：`above`（默认）、`below`、`tl`、`tr`、`bl`、`br`，也可以写 `[x, y]`（1080×1920 坐标）。

## 收藏引导（cta）

- 固定格式：`["Save this for your", "{Japan trip}"]`，副行写 `"Send it to who you’re going with"`。
- 高亮词按主题换：Kyoto trip / winter escape / island escape / Osaka trip。
- ASMR 用短句：`["Save this for", "a {slow day} ❄️"]`。

## IG 文案（`ig_caption` + `hashtags`）

```
<一句钩子，带 1 个 emoji>
<1–2 句实用信息：什么时候去、怎么去、去了做什么>

📍 <地点>
🔖 Save this for your <…>

Visuals created with AI.
```

话题标签放 5 个：2 个地点 + 2 个泛旅行（`#japantravel #japantrip`）+ `#aussietravellers`。

## AI 披露（每条都要）

- 文案末尾固定写一句「Visuals created with AI.」。
- 提醒用户发帖时打开 Instagram 的 AI 标签。
- 不点名具体地标，除非用了真实参考图。不说「I went」「I visited」这类暗示实拍的话。
