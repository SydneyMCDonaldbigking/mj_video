# 服务器部署记录（RTX 5090 / 64GB，2026-09-26 跑通）

本目录记录把 Krea2 MJ 生图接到已有 H3 生视频环境上时，在服务器上做过的改动。
权重和上游节点代码不入库，只记录位置和来源。

## 目录约定

| 路径 | 内容 |
|---|---|
| `/opt/H3_MJ` | 本仓库 |
| `/opt/h3` | MINIMAXH3_2PASS_Autoworkflow（H3 队列、worker、配置 `config/production.5090.cn.yaml`） |
| `/opt/ComfyUI` | ComfyUI 0.34.0 |
| `/usr/local/miniconda3/envs/h3director` | Python 3.12 环境，自带 ffmpeg/ffprobe |

## 1. Krea2 基础模型（软链自平台只读模型库 `/model`）

```bash
K=/model/ModelScope/Comfy-Org/Krea-2; M=/opt/ComfyUI/models
ln -sfn $K/diffusion_models/krea2_turbo_fp8_scaled.safetensors $M/diffusion_models/
ln -sfn $K/text_encoders/qwen3vl_4b_fp8_scaled.safetensors     $M/text_encoders/
ln -sfn $K/vae/qwen_image_vae.safetensors                      $M/vae/
```

## 2. MJ LoRA（手动上传，不在模型库里）

放到 `/opt/ComfyUI/models/loras/krea2_mj/`：

- `krea2小志_xm_k20_texture.safetensors`
- `krea2-Cc-MJ-风格滤镜.safetensors`
- `K2-MJ美学增强Afterlight_v1.safetensors`
- `krea2-Cc-MJ-影视柔光.safetensors`

原工作流里的第 5 个 `krea2_10000.safetensors` 未找到，当前不使用。

## 3. ConditioningKrea2Rebalance 节点

来源 `https://github.com/nova452/Rebalance-Pack`（commit `4553b14`，国内可用 ghfast 镜像）。
只装 Krea2 需要的两个文件，不装 OmniNode（运行时 exec 粘贴代码 + OpenRouter 联网）：

```bash
T=/opt/ComfyUI/custom_nodes/Rebalance-Pack-Krea2; mkdir -p $T
cp Rebalance-Pack/{krea2.py,conditioning_rebalance.py,LICENSE} $T/
cp server/rebalance_krea2_init.py $T/__init__.py
```

新版节点只有 `multiplier` + `per_layer_weights`，没有旧版的 `preset` / `renormalize`；
代码会只提交服务器节点实际声明的输入。同 seed 对比后选 `multiplier = 0.58`
（旧版 renormalize 的近似：1 / 12 层权重均值 1.72）。

## 4. 启动

两个进程都必须带上 conda 环境的 PATH，否则 worker 找不到 ffmpeg，
视频生成完会在 QA 阶段报 `FileNotFoundError: 'ffmpeg'`。

```bash
E=/usr/local/miniconda3/envs/h3director/bin
cd /opt/ComfyUI && PATH=$E:$PATH PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  setsid nohup $E/python main.py --listen 127.0.0.1 --port 8188 > /opt/h3/logs/comfyui.log 2>&1 &
cd /opt/h3 && PATH=$E:$PATH setsid nohup $E/python -m src.worker > /opt/h3/logs/worker.log 2>&1 &
```

生图和生视频共用一个 ComfyUI；提交 H3 前先 `POST /free`（`unload_models`、`free_memory`）
把 Krea2 卸掉，64GB 内存不够两套模型同时驻留。

## 5. 跑一个项目

```bash
P=$E/python; cd /opt/H3_MJ
$P scripts/cc_travel_submit.py --project japan_food_project.json --stage keyframes \
  --krea-model-map server/server_model_map.json --h3-repo /opt/h3
# 人工看 runs/<id>/keyframes/approved_inputs/ 后：
$P scripts/cc_travel_submit.py --project japan_food_project.json --stage h3 --approve-keyframes --h3-repo /opt/h3
# 单段重跑：--stage h3 --rerun-clip clip-03-wagyu
$P scripts/cc_travel_submit.py --project japan_food_project.json --stage report --h3-repo /opt/h3
```

单张关键帧不满意时只重抽那一张：`--stage keyframes --reroll-clip clip-02-yokocho`
（`--force` 会把整个项目全部重抽）。

## 冷机部署（2026-10-01，4090 24GB 空机，约 35 分钟）

H3 工程按 `MINIMAXH3_2PASS_Autoworkflow/docs/DEPLOY_CLOUD_5090.md`「第二台实测」一节部署，差异：

- `/model/.../MiniMax-H3/` 仍然**没有 `loras/`**，4-step LoRA 从魔搭拉（6 MB/s，5 分钟）。
- CUDA 12.8 apt 装 3.7 GB；bootstrap 副本在 nvcc 检查前加了等待循环，两者并行省约 15 分钟。
- GitHub 直连随机 TLS 断：ComfyUI 先用 `git clone --depth 1 --branch v0.34.0` 走
  `https://gh-proxy.com/https://github.com/...` 手动克隆好，KJNodes 同理，bootstrap 见到 `.git` 会跳过克隆。
  `--filter=blob:none` 走代理会按需拉 blob，极慢且容易 checkout 失败，不要用。ghfast.top 这次直接挂起。
- 4 个 MJ LoRA 只能从本地上传（约 400 KB/s，785 MB 用了约 25 分钟），**最先开始传**。
- 这类平台的 SSH 端口转发会随机断连接，命令都要带重试；长任务一律 `setsid --fork` 放服务器上跑。

## 实测

| 项目 | 数值 |
|---|---|
| 1080p 关键帧（4 LoRA + Rebalance） | 约 10 秒/张，首张 13 秒 |
| 5 秒 H3 成片（二采 + RealESRGAN，1440×2560） | 约 4.7–4.8 分钟 |
| 日本美食 3×5 秒（含一次重跑） | 19.5 GPU 分钟，0.97 元（3 元/小时） |
| 4090：1080p 关键帧 | 约 16 秒/张，首张含加载约 45 秒 |
| 4090：5 秒 H3 成片 | 约 6.8 分钟（405–420 秒），QA 全过 |
| 4090：4 个主题各 3×5 秒（2026-10-01） | 每条 21–22 GPU 分钟、1.06–1.09 元；12 段零失败 |
| 同 seed 可复现性 | fp8 + LoRA 不保证逐位复现；选中的图直接用文件，不靠 seed 重出 |
