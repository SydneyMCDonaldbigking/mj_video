# 格式模板

时长由 `scripts/reels_post.py` 的 `FORMATS` 决定（H3 每段生成 5 秒 / 124 帧，剪辑时只取其中一段）。
成本按 4090 实测：每镜约 7.1 GPU 分钟、0.355 元。

| 格式 | 镜数 | 每镜时长 | 总长 | 成本 | 适合 |
|---|---|---|---|---|---|
| `mood` 氛围 | 3 | 4.6 秒 | 约 14 秒 | 约 1.1 元 / 21 分钟 | 拉新、循环播放、建立账号调性 |
| `list` 清单 | 3–5 | 首镜 3.8 秒，中间 2.8 秒，末镜 4.3 秒 | 11–17 秒 | 1.1–1.8 元 | 收藏率通常最高 |
| `itinerary` 行程 | 3–4 | 首镜 3.8 秒，中间 3.5 秒，末镜 4.6 秒 | 12–16 秒 | 1.1–1.4 元 | 收藏 + 分享（「send to your travel buddy」） |
| `asmr` 慢旅行 | 3 | 4.8 秒 | 约 14 秒 | 约 1.1 元 | 完播率高；靠 H3 自带环境声做差异化 |

## 各格式要写的字段

**mood**

- `hook`：显示在第 1 镜，整镜都在。
- `shots[1..].text`：每镜一句。
- `cta`

**list**

- `hook`：只显示前 1.8 秒，形如「3 things you can't skip in X 👇」。
- `shots[*].text`：每一项的名字，包括第 1 镜（钩子消失后出现）。
- 「1/N」编号标签会自动加上，不用写。

**itinerary**

- `hook`：显示在第 1 镜。
- `shots[*].label`：每镜一个时间，如 `"7:00 AM"`，自动生成时间贴纸，表盘 emoji 跟时间对应。
- `shots[1..].text`：这个时间在做什么。

**asmr**

- `hook` 可以不写 `lines`，只写 `anchor`。
- 地点和「🔊 SOUND ON」贴纸会自动加。
- `shots[*].text` 一般不写。
- `cta` 要短，如「Save this for a {slow day}」。

## 节奏规则

- 第一帧就要出钩子文字，最抓眼的镜头放第一个，不要用最平淡的大全景开场。
- 动静交替：固定机位和运镜穿插，不连用两个推镜。
- 每句字幕最多 7 个词、2 行。
- 收藏引导固定 2.1 秒，脚本会把末镜压暗。

## 钩子写法（挑一种，别每条都用同一种）

| 类型 | 例子 |
|---|---|
| POV | `POV: your first night in {Tokyo}` |
| 时机 | `Book Kyoto for {November.}` + 副行 `Thank me later` |
| 反差 / 疑问 | `Wait… this is {Japan?}` |
| 本地梗 | `Swap a 40° Aussie Christmas for {this}` |
| 名号 | `They call Osaka {Japan’s kitchen}` |
| 趣味事实 | `In Nara, the locals are {deer}` |
| 清单 | `3 things you can’t skip in {Osaka} 👇` |
| 行程 | `One perfect night in {Tokyo}` |
| 想象 | `Imagine waking up to {Mt Fuji}` |

`{}` 里放全句最想让人记住的那个词。
