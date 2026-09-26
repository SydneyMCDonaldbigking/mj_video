#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path


PROMPT = (
    "Single photorealistic editorial travel keyframe, one coherent viewpoint, believable geography "
    "and architecture, natural depth and clean colour separation, premium MJ-inspired soft cinematic "
    "lighting, vertical composition with safe margins, no captions, no title card, no watermark, "
    "no fake logo, no prominent readable signage, no collage, no split screen"
)


def build(source: Path, target: Path) -> None:
    workflow = json.loads(source.read_text(encoding="utf-8-sig"))
    workflow = deepcopy(workflow)
    # 去掉原作者的宣传标签；模型、LoRA、采样参数与连接不动。
    removed = {node["id"] for node in workflow["nodes"] if node.get("type") == "Label (rgthree)"}
    workflow["nodes"] = [node for node in workflow["nodes"] if node["id"] not in removed]
    workflow["links"] = [link for link in workflow["links"]
                         if link[1] not in removed and link[3] not in removed]
    for node in workflow["nodes"]:
        if node.get("type") == "TTResolutionSelector":
            values = list(node.get("widgets_values") or [])
            values[0] = True
            values[2] = 768
            values[3] = 1344
            node["widgets_values"] = values
            node["title"] = "旅游关键帧尺寸｜768×1344 竖版"
        elif node.get("type") == "CR Prompt Text":
            node["widgets_values"] = PROMPT
            node["title"] = "填写单镜旅行关键帧提示词"
        elif node.get("type") == "SaveImage":
            node["widgets_values"] = "travel_keyframes/preview"
            node["title"] = "保存待审核关键帧"
        elif node.get("type") == "MarkdownNote":
            node["title"] = "旅游工作流使用说明"
            node["widgets_values"] = (
                "## 阶段 1：Krea2 MJ 旅行关键帧\n\n"
                "本画布只负责生成竖版关键帧。模型与 5 个 LoRA 顺序继承自原成功图像工作流。\n\n"
                "1. 在左侧填写单镜场景，避免可读招牌与画面内文字。\n"
                "2. 输出后先检查地标、建筑、人物、方向和乱码。\n"
                "3. 通过 `scripts/cc_travel_submit.py --stage h3 --approve-keyframes` 把审核图交给 "
                "MiniMax H3 Ref2VA。两个模型体系不共享 MODEL 线，交接媒介是已落盘 PNG。\n\n"
                "默认 768×1344 是约 1MP 的保守起点；正式批量前请在目标显卡上实测。"
            )
    workflow["groups"] = [{
        "id": 1,
        "title": "阶段 1｜旅游关键帧（审核后再进入 H3）",
        "bounding": [-570, 1715, 2050, 1025],
        "color": "#2f6f5f",
        "font_size": 28,
        "flags": {},
    }]
    workflow["last_node_id"] = max(node["id"] for node in workflow["nodes"])
    workflow["last_link_id"] = max(link[0] for link in workflow["links"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="从用户提供的 Krea2 MJ 图像流生成旅游关键帧 UI 工作流")
    parser.add_argument("--source", default="mj风格工作流.json")
    parser.add_argument("--output", default="workflows/Krea2-MJ-旅游关键帧-竖版.json")
    args = parser.parse_args()
    build(Path(args.source).resolve(), Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
