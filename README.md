# 旅游视频专用工作流：Krea2 MJ → MiniMax H3

这套新工作流把当前目录的 `mj风格工作流.json` 与已经跑通的
`double_flow/MINIMAXH3_2PASS_Autoworkflow` 串成两阶段生产线：

```text
旅游项目 JSON
  → Krea2 + 5 个 MJ/纹理/柔光 LoRA 生成旅行关键帧
  → 解码、方向检查、无拉伸中心裁切为 H3 reference
  → 人工审核闸门
  → 已成功的 H3 Ref2VA 队列
  → latent 二采 / RealESRGAN / QA / review_ready
```

Krea2 和 MiniMax H3 的 UNet/LoRA 不兼容，因此不能把两条 `MODEL` 线硬连。真正的交接对象是已经落盘并通过审核的 PNG。这样也避免关键帧有问题时直接烧 H3 视频算力。

## 已生成的文件

- `workflows/Krea2-MJ-旅游关键帧-竖版.json`：可直接导入 ComfyUI 的阶段 1 画布。
- `travel_project.example.json`：三段式旅游项目样板（抵达 / 体验 / 收束）。
- `scripts/cc_travel_submit.py`：实际串联两个工作流的入口。
- `runs/<project_id>/manifest.json`：关键帧、seed、模型清单、哈希、H3 job 和队列路径的证据链。

## 使用顺序

先检查计划；此命令不访问 GPU，也不写 H3 队列：

```powershell
python scripts/cc_travel_submit.py `
  --project travel_project.example.json `
  --stage plan
```

只生成关键帧：

```powershell
python scripts/cc_travel_submit.py `
  --project travel_project.example.json `
  --stage keyframes `
  --comfy-url http://127.0.0.1:8188
```

某一张不满意时，改该片段的 `keyframe_prompt`（或 seed）后只重抽它：`--stage keyframes --reroll-clip <clip_id>`；`--force` 则整组重抽。

在 `runs/<project_id>/keyframes/approved_inputs/` 查看三张图。确认无错误地标、乱码招牌、建筑变形、人物漂移风险后，再显式批准并提交 H3：

```powershell
python scripts/cc_travel_submit.py `
  --project travel_project.example.json `
  --stage h3 `
  --approve-keyframes `
  --h3-repo /opt/h3
```

每个 clip 作为独立 H3 job 入队，便于单独重跑和手工剪辑。默认不自动拼接。

H3 跑完后统计本项目的生图 + 生视频耗时与机时成本：

```powershell
python scripts/cc_travel_submit.py `
  --project travel_project.example.json `
  --stage report
```

结果写入 `runs/<project_id>/cost_report.json`。成本 = GPU 占用秒数 × H3 配置里的 `cost.gpu_hourly_cny` / 3600：

- 生图：每张关键帧从提交到落盘的墙钟时间（另记 ComfyUI 纯执行时长），`--force` 重抽也计入。
- 生视频：直接读 H3 每个任务 `report.json` 的 `elapsed_seconds` 和 `estimated_gpu_cost_cny`；失败任务没有耗时记录，不计入。
- 另给出成片总秒数和每秒成片成本。单独建的 seed 试验项目不在本项目 manifest 里，不计入。

## 英文 Instagram Reels 流水线

面向英文区（澳洲为主）的旅游 Reels，一条分镜板走到底：

```text
reels/boards/<id>.json（分镜板：镜头、提示词、文案、emoji、贴纸、IG 文案）
  → scripts/reels_make.py：生成 projects/<id>.json，推到服务器
  → 服务器 scripts/reels_remote.py：关键帧 → 下载总览图（人工审核，可 --auto 跳过）
  → H3（失败自动重跑一次）→ 成本报告 → 下载单镜头片段
  → scripts/reels_post.py：剪辑包装 → deliveries/reels/
```

```powershell
python scripts/reels_make.py japan-hakone-01 --check     # 校验分镜板，打印预计 GPU 分钟和成本
python scripts/reels_make.py japan-hakone-01             # 跑到关键帧审核，出 runs/<id>/keyframes_sheet.jpg
python scripts/reels_make.py japan-hakone-01 --reroll clip-02-onsen   # 改了提示词后只重抽这张
python scripts/reels_make.py japan-hakone-01 --approve   # H3 → 下载 → 后期
python scripts/reels_make.py japan-hakone-01 --post-only # 只改了文案、位置、贴纸时重做后期（30 秒）
```

- 服务器地址在 `server/reels_server.json`。SSH 断线会自动重试；长任务在服务器后台跑，本地只在状态变化时打印一行。
  服务器关机时退出码为 2。
- 四种格式：`mood`（氛围）、`list`（编号清单）、`itinerary`（带时间贴纸的行程）、`asmr`（几乎无字，靠环境声）。
  每种格式的镜头时长写在 `reels_post.py` 的 `FORMATS` 里。
- 文字支持彩色 emoji（Windows 的 Segoe UI Emoji）。贴纸有地点、标签、时间、浮动 emoji、手绘箭头、sound on。
- 文字位置在关键帧审核时定（总览图上画了 T/U/M/L 四条线）。自动建议只用来兜底，大块主体常判错。
- 不配旁白，保留 H3 原环境声，响度归一到 -18 LUFS；音乐在 Instagram 里用平台曲库加。
- 输出 `-reels.mp4`（1080×1920）、`-cover.jpg`、`-caption.txt` 和 `-qa.jpg`（每镜一帧的检查条），都在 `deliveries/reels/`，不入库。
- 旧成片只有拼好的整片时，在分镜板里写 `"source"`，就能用 `--post-only` 重剪成别的格式，不花 GPU。

导演规则（格式、节奏、提示词配方、文案和贴纸规范、澳洲选题日历）写在 Claude Code 技能
`.claude/skills/reels-director/` 里。需要本机 ffmpeg、numpy、Pillow 和 Windows 自带字体（`--fonts-dir` 可改）。

## 输入策略

- `auto_keyframe`：调用 Krea2 生成关键帧，审核后走 H3 Ref2VA。
- `provided_reference`：复制并验证用户图片，不重新绘制；直接视为已批准。
- `text_only_h3`：明确走 H3 FL2VA，不生成关键帧，且不允许同时附带 reference。

项目默认竖版。Krea2 关键帧尺寸由项目 JSON 的 `keyframe_resolution` 决定：`1080p`（默认，`1088×1920`）、`2k`（`1440×2560`）或 `1mp`（`768×1344`），横版自动对调。高清原图保留在 `keyframes/raw/`，随后无拉伸中心裁切为 H3 的 `480×832` 输入。最终尺寸、二采、超分和 QA 继续服从成功工程的配置。

## 注意

- 工作流只引用原 JSON 中的模型与 LoRA 名，不包含权重；目标 ComfyUI 必须已有这些文件与对应自定义节点。
- 真实目的地如果没有真实参考图，输出属于合成旅行视觉，不能宣称为纪录镜头。
- 画面内地点名、日期、路线与价格请后期叠加，不交给生成模型写字。
- `audio_mode` 默认为 `inherit`，不会偷偷改变成功工程当前的音频 QA 行为。
