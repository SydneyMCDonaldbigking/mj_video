---
name: reels-director
description: 英文区（澳洲为主）Instagram Reels 旅游短视频的导演 + 一键生产流水线。用户说「做一条 X 的 Reels」「来几条某地的旅游视频」「批量做 Reels」「出一条清单 / 行程 / ASMR」「再做几条」「换个城市」，或给出目的地、季节想要成片时使用。负责定格式和角度、写分镜板 reels/boards/<id>.json、跑 scripts/reels_make.py（关键帧 → 审核 → H3 → 后期），交付 mp4 + 封面 + IG 文案。
---

# Reels 导演

每条片子只做三件事：**写分镜板、看一张关键帧总览图、看一张成片检查条**。其余交给脚本。

## 流程

1. **定选题**：按 [formats.md](references/formats.md) 选格式，按 [au-calendar.md](references/au-calendar.md) 选角度和季节钩子。brief 里有目的地就直接做，不要追问。
2. **写分镜板** `reels/boards/<id>.json`（id 形如 `japan-hakone-01`）。照抄一个现有板子改最快，字段见 [board-schema.md](references/board-schema.md)。
   - 提示词按 [shot-recipes.md](references/shot-recipes.md) 写，**写完逐条过一遍那里的自检表**。
   - 文案、emoji、贴纸按 [style-bible.md](references/style-bible.md) 写。
3. `python scripts/reels_make.py <id> --check`：校验，不连服务器，同时打印预计 GPU 分钟和成本。
4. `python scripts/reels_make.py <id>`（用 run_in_background 跑）：跑到关键帧审核为止，出 `runs/<id>/keyframes_sheet.jpg`。
5. **看总览图**，这是唯一要看的生成图：
   - 有伪文字招牌、地标画错、构图不对：改该镜的 `keyframe_prompt` 或 `seed`，再 `--reroll <clip_id>`。
   - **给每镜定文字位置**：图上有 T/U/M/L 四条线，选不压主体的那条，写进 `hook.anchor` / `shots[k].anchor`。绿线是自动建议，大块主体（山、塔、动物）常判错，不能照单全收。
6. `python scripts/reels_make.py <id> --approve`（run_in_background，3 镜约 25 分钟）：完成后看 `deliveries/reels/<id>-qa.jpg`。
   - 只是文字或贴纸有问题：改板子后跑 `--post-only`（30 秒，不用 GPU）。
7. **交付**：`deliveries/reels/<id>-reels.mp4`、`-cover.jpg`、`-caption.txt`。提醒用户发帖时打开 AI 标签，并在 IG 里加音乐。

批量：多个 id 写在一条命令里（`reels_make.py a b c`），关键帧全部出完再一起审。

同一批素材可以剪出不同格式：新建一个板子，带 `"source": "deliveries/<原片>.mp4"`，再跑 `--post-only`，不花 GPU。

## 省 token 的规矩

- 长步骤一律用 `run_in_background`，等完成通知。不要轮询，不要 sleep。
- 只看两张图：关键帧总览图、成片检查条。不额外抽帧，不读日志，除非报错。
- 出错先看脚本打印的那一行原因；SSH 断线脚本会自己重试。
- 退出码 2 表示服务器关机：告诉用户去平台开机，不要排查。
- 已经写在这里的规则不要重新推导，拿不准的再去读 references。

## 硬规则（违反就重写）

- **提示词**：
  - 每镜只写一个进行中的动作动词。
  - 只写参考图里有的东西（H3 通用规律：`~/.claude/skills/h3-motion-transfer/references/prompt-template.md` 第 0 节）。
- **画面**：
  - 招牌多的场景要么改成特写加背景虚化，要么改拍水面倒影，并写上 `no signboards, no lettering`。
  - 默认不出现人脸。
  - 点名具体地标必须配真实参考图（`reference`）；没有参考图，就只写体验和氛围。
- **文案**：
  - 澳式英语拼写，第二人称。
  - 钩子不超过 7 个词。
  - 不写价格、排名、营业时间这类无法核实的说法。
  - 画面不冒充实拍。
- **emoji**：
  - 只用单个码位的常见 emoji，不用组合 emoji、国旗、肤色。
  - 每行最多 1 个。
- **贴纸**：每条最多 3 个。地点、编号、时间、sound on 这些自动加的不算在内。
