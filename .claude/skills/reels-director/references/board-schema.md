# 分镜板 `reels/boards/<id>.json`

一个文件同时管生产和后期。`reels_make.py` 会把生产字段转成 `projects/<id>.json` 交给现有的 `cc_travel_submit.py`；
`reels_post.py` 读后期字段剪成片。照抄一个现有板子改最快：

- mood：`japan-nara-01.json`
- list：`japan-osaka-list-01.json`
- itinerary：`japan-tokyo-itinerary-01.json`
- asmr：`japan-hokkaido-asmr-01.json`

## 顶层

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✓ | 跟文件名一致，如 `japan-hakone-01` |
| `format` | ✓ | `mood` / `list` / `itinerary` / `asmr` |
| `template` | | 视觉模板，见 style-bible.md。不写或写 `"random"` 时，第一次渲染会均衡随机抽一套并写回 |
| `template_seed` | | 整数，换它能让随机抽到别的模板 |
| `destination` | ✓ | 生图用，如 `"Hakone, Japan"` |
| `pin` | ✓ | 地点贴纸上的字 |
| `season`、`time_of_day` | ✓ | 生图用，写英文 |
| `style` | | 不写就用 `HOUSE_STYLE` |
| `audience` | | 不写就用默认的澳洲受众 |
| `accent`、`accent_text` | ✓ / | 主题色，见 style-bible.md |
| `hook` | 除 asmr 外必填 | `{"lines": [...], "sub": "...", "anchor": "top", "stickers": [...]}` |
| `shots` | ✓ | 2–8 个，见下 |
| `cta` | ✓ | `{"lines": ["Save this for your", "{Japan trip}"], "sub": "..."}` |
| `ig_caption`、`hashtags` | ✓ | 写进 `-caption.txt` |
| `source` | | 只有重剪旧成片时才写：拼好的 3×124 帧整片路径，有它就不需要 H3 |

## shots[*]

| 字段 | 必填 | 说明 |
|---|---|---|
| `clip_id` | ✓ | `clip-01-<短名>`，H3 job id 会用到 |
| `place` | ✓ | 一句话说明这一镜拍什么 |
| `keyframe_prompt` | ✓（有 reference 时可省） | 见 shot-recipes.md |
| `motion_prompt` | ✓ | 见 shot-recipes.md |
| `seed` | ✓ | 整数 |
| `reference` | | 真实参考图，相对仓库根目录，如 `reels/refs/kinkakuji.jpg`；有它就不生图 |
| `duration_seconds` | | H3 时长，默认 5。格式的裁剪区间按 5 秒设计，别乱改 |
| `text` | | 字幕 `{"lines": [...], "sub": "..."}`；mood 和 itinerary 第 1 镜用 hook，这里不写 |
| `anchor` | | 文字位置 `top` / `upper` / `middle` / `lower` / `auto`。第 1 镜的位置写在 `hook.anchor` |
| `label` | | 行程格式的时间，如 `"7:00 AM"` |
| `stickers` | | 见 style-bible.md |
| `trim` | | `[起, 止)` 帧，覆盖格式默认值 |

## 例子（mood，节选）

```json
{
  "id": "japan-hakone-01",
  "format": "mood",
  "destination": "Hakone, Japan",
  "pin": "Hakone, Japan",
  "season": "late autumn, crisp clear air",
  "time_of_day": "misty dawn into golden afternoon",
  "accent": "#FF8A5B",
  "accent_text": "#1A0E08",
  "hook": {"lines": ["Your next long", "weekend: {Hakone} ♨️"], "anchor": "lower"},
  "shots": [
    {"clip_id": "clip-01-lake-torii", "place": "...", "keyframe_prompt": "...",
     "motion_prompt": "Locked-off camera, no movement.\n\n...\n\nSound: ...", "seed": 26100301},
    {"clip_id": "clip-02-onsen", "...": "...", "text": {"lines": ["Private onsen", "with {this view}"]},
     "anchor": "upper", "stickers": [{"type": "arrow", "dir": "up"}]},
    {"clip_id": "clip-03-kaiseki", "...": "...", "text": {"lines": ["Then a 10-course", "dinner 🍣"]},
     "anchor": "top", "stickers": [{"type": "tag", "text": "MUST TRY 🔥"}]}
  ],
  "cta": {"lines": ["Save this for your", "{Japan trip}"], "sub": "Send it to who you’re going with"},
  "ig_caption": "...",
  "hashtags": ["#hakone", "#hakoneonsen", "#japantravel", "#japantrip", "#aussietravellers"]
}
```
