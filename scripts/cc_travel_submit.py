#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from travel_workflow.krea_provider import ComfyKrea2MJProvider
from travel_workflow.pipeline import H3QueueAdapter, TravelRun
from travel_workflow.project import load_project


def _default_h3_repo() -> Path:
    configured = os.environ.get("H3_REPO")
    if configured:
        return Path(configured).expanduser()
    candidate = ROOT.parent / "double_flow" / "MINIMAXH3_2PASS_Autoworkflow"
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="旅游视频两阶段编排：Krea2 MJ 关键帧 -> 审核 -> MiniMax H3 Ref2VA")
    parser.add_argument("--project", required=True, help="旅游项目 JSON")
    parser.add_argument("--stage", choices=("plan", "keyframes", "approve", "h3", "report"),
                        default="plan")
    parser.add_argument("--run-root", default=str(ROOT / "runs"))
    parser.add_argument("--krea-workflow", default=str(ROOT / "mj风格工作流.json"))
    parser.add_argument("--krea-model-map", default=None,
                        help="目标服务器模型映射 JSON（unet/clip/vae/loras/rebalance），偏差会写入 manifest")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--comfy-timeout", type=float, default=900)
    parser.add_argument("--comfy-poll", type=float, default=2)
    parser.add_argument("--h3-repo", default=str(_default_h3_repo()))
    parser.add_argument("--h3-config", default="config/production.5090.cn.yaml")
    parser.add_argument("--approve-keyframes", action="store_true",
                        help="明确批准当前 manifest 中全部待审关键帧后再提交 H3")
    parser.add_argument("--force", action="store_true", help="重新生成已存在的关键帧")
    parser.add_argument("--reroll-clip", action="append", default=[], metavar="CLIP_ID",
                        help="keyframes 阶段：只重抽指定片段的关键帧（可重复），旧计时并入历史")
    parser.add_argument("--rerun-clip", action="append", default=[], metavar="CLIP_ID",
                        help="h3 阶段：用当前 motion_prompt 重跑指定片段（可重复），旧成片留档并计入成本")
    args = parser.parse_args()

    project = load_project(args.project)
    run = TravelRun(project, args.run_root)
    if args.stage == "plan":
        result = run.plan(workflow_path=args.krea_workflow, h3_repo=args.h3_repo)
    elif args.stage == "keyframes":
        needs_provider = any(clip.input_strategy == "auto_keyframe" for clip in project.clips)
        provider = None
        if needs_provider:
            model_map = None
            if args.krea_model_map:
                model_map = json.loads(Path(args.krea_model_map).read_text(encoding="utf-8-sig"))
            provider = ComfyKrea2MJProvider(
                args.comfy_url, args.krea_workflow,
                timeout=args.comfy_timeout, poll_seconds=args.comfy_poll,
                model_map=model_map)
        result = run.generate_keyframes(provider, force=args.force,
                                         reroll=tuple(args.reroll_clip))
    elif args.stage == "approve":
        result = run.approve_keyframes()
    elif args.stage == "report":
        result = run.cost_report(H3QueueAdapter(args.h3_repo, args.h3_config))
    else:
        adapter = H3QueueAdapter(args.h3_repo, args.h3_config)
        result = run.submit_h3(adapter, approve_keyframes=args.approve_keyframes,
                               rerun=tuple(args.rerun_clip))

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
