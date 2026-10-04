#!/usr/bin/env python3
"""服务器端：Reels 流水线的生图 / 生视频段，由本地 scripts/reels_make.py 通过 SSH 调用。

    python scripts/reels_remote.py services                       # 确保 ComfyUI 和 H3 worker 在跑
    python scripts/reels_remote.py keyframes <id> --token T [--reroll CLIP_ID ...]
    python scripts/reels_remote.py produce <id> --token T          # 批准 → H3 → 失败自动重跑一次 → 成本报告
    python scripts/reels_remote.py status <id>

keyframes / produce 由本地用 setsid 放后台跑，进度写 runs/<id>/reels_state.json，
本地带 token 轮询 status，避免把上一轮的结束状态当成这一轮。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENV_BIN = Path(os.environ.get("H3_ENV_BIN", "/usr/local/miniconda3/envs/h3director/bin"))
H3_REPO = Path(os.environ.get("H3_REPO", "/opt/h3"))
H3_CONFIG = "config/production.5090.cn.yaml"
COMFY_DIR = Path(os.environ.get("COMFY_DIR", "/opt/ComfyUI"))
COMFY = "http://127.0.0.1:8188"
POLL_SECONDS = 30
H3_TIMEOUT = 4 * 3600


def _env() -> dict[str, str]:
    # 两个进程都要带 conda 环境的 PATH，否则 worker 的 QA 找不到 ffmpeg（见 server/DEPLOY.md）
    return {**os.environ, "PATH": f"{ENV_BIN}:{os.environ.get('PATH', '')}"}


def _state_path(pid: str) -> Path:
    return ROOT / "runs" / pid / "reels_state.json"


def _write_state(pid: str, **fields) -> None:
    path = _state_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    state.update(fields, updated=time.strftime("%Y-%m-%d %H:%M:%S"))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _submit(pid: str, stage: str, *extra: str) -> None:
    log = ROOT / "runs" / pid / "submit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as out:
        out.write(f"\n=== {time.strftime('%H:%M:%S')} {stage} {' '.join(extra)}\n")
        out.flush()
        code = subprocess.run([sys.executable, "scripts/cc_travel_submit.py", "--project", f"projects/{pid}.json",
                               "--stage", stage, "--h3-repo", str(H3_REPO), "--h3-config", H3_CONFIG, *extra],
                              cwd=ROOT, env=_env(), stdout=out, stderr=subprocess.STDOUT).returncode
    if code:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
        raise RuntimeError(f"cc_travel_submit {stage} 失败：" + " | ".join(tail))


def _comfy_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{COMFY}/system_stats", timeout=3):
            return True
    except OSError:
        return False


def _worker_alive() -> bool:
    return subprocess.run(["pgrep", "-f", "python -m src.worker"], capture_output=True).returncode == 0


def ensure_services() -> dict[str, str]:
    started = {}
    if not _comfy_alive():
        log = open(H3_REPO / "logs" / "comfyui.log", "a")
        subprocess.Popen([str(ENV_BIN / "python"), "main.py", "--listen", "127.0.0.1", "--port", "8188"],
                         cwd=COMFY_DIR, env={**_env(), "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"},
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        started["comfyui"] = "started"
        deadline = time.time() + 300
        while not _comfy_alive():
            if time.time() > deadline:
                raise RuntimeError("ComfyUI 5 分钟没起来，看 /opt/h3/logs/comfyui.log")
            time.sleep(5)
    if not _worker_alive():
        log = open(H3_REPO / "logs" / "worker.log", "a")
        subprocess.Popen([str(ENV_BIN / "python"), "-m", "src.worker"], cwd=H3_REPO, env=_env(),
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        started["worker"] = "started"
    return started or {"services": "already running"}


def _h3_paths() -> dict:
    from travel_workflow.pipeline import H3QueueAdapter
    return H3QueueAdapter(H3_REPO, H3_CONFIG).config["paths"]


def _h3_busy(paths: dict) -> int:
    jobs = Path(paths["jobs"])
    return sum(len(list((jobs / name).iterdir())) for name in ("inbox", "running")
               if (jobs / name).is_dir())


def _manifest(pid: str) -> dict:
    return json.loads((ROOT / "runs" / pid / "manifest.json").read_text(encoding="utf-8"))


def keyframes(pid: str, token: str, reroll: list[str]) -> None:
    _write_state(pid, token=token, stage="keyframes", detail="生成关键帧", error=None)
    ensure_services()
    paths = _h3_paths()
    while _h3_busy(paths):          # H3 和 Krea2 共用一张卡和一个 ComfyUI，别和视频任务抢显存
        _write_state(pid, detail=f"等 H3 队列清空（还有 {_h3_busy(paths)} 个）")
        time.sleep(POLL_SECONDS)
    _submit(pid, "keyframes", "--krea-model-map", "server/server_model_map.json",
            *[arg for clip in reroll for arg in ("--reroll-clip", clip)])
    clips = _manifest(pid).get("clips", {})
    refs = {cid: state.get("reference") for cid, state in clips.items()}
    _write_state(pid, stage="review", detail="关键帧待审核", references=refs)


def _clip_outcome(paths: dict, job_id: str) -> tuple[str, str | None]:
    ready = Path(paths["review_ready"]) / job_id
    if (ready / "report.json").is_file() and (ready / f"{job_id}.mp4").is_file():
        return "done", str(ready / f"{job_id}.mp4")
    if (Path(paths["rejected"]) / job_id / "report.json").is_file():
        return "rejected", None
    if (Path(paths["jobs"]) / "failed" / f"{job_id}.json").is_file():
        return "failed", None
    return "waiting", None


def produce(pid: str, token: str) -> None:
    _write_state(pid, token=token, stage="h3", detail="提交 H3", error=None)
    ensure_services()
    request = urllib.request.Request(f"{COMFY}/free", method="POST",
                                     data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
                                     headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=30).close()     # 先卸掉 Krea2，内存不够两套模型同时驻留
    _submit(pid, "h3", "--approve-keyframes")
    paths, retried, deadline = _h3_paths(), set(), time.time() + H3_TIMEOUT
    while True:
        clips = _manifest(pid)["clips"]
        outcome = {cid: _clip_outcome(paths, state["h3"]["job_id"]) for cid, state in clips.items()}
        bad = [cid for cid, (status, _) in outcome.items() if status in ("failed", "rejected")]
        for cid in bad:
            if cid in retried:
                raise RuntimeError(f"{cid} 重跑后仍然 {outcome[cid][0]}")
            retried.add(cid)
            _submit(pid, "h3", "--rerun-clip", cid)
        done = {cid: path for cid, (status, path) in outcome.items() if status == "done"}
        _write_state(pid, detail=f"H3 {len(done)}/{len(clips)} 段完成，队列 {_h3_busy(paths)} 个",
                     retried=sorted(retried))
        if len(done) == len(clips) and not bad:
            break
        if time.time() > deadline:
            raise RuntimeError("H3 超过 4 小时没跑完")
        time.sleep(POLL_SECONDS)
    _submit(pid, "report")
    report = json.loads((ROOT / "runs" / pid / "cost_report.json").read_text(encoding="utf-8"))
    _write_state(pid, stage="done", detail="H3 全部完成", clips=done,
                 cost=report["totals"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Reels 流水线服务器端")
    ap.add_argument("action", choices=("services", "keyframes", "produce", "status"))
    ap.add_argument("pid", nargs="?")
    ap.add_argument("--token", default="")
    ap.add_argument("--reroll", action="append", default=[])
    args = ap.parse_args()
    if args.action == "services":
        print(json.dumps(ensure_services(), ensure_ascii=False))
        return 0
    if not args.pid:
        ap.error("需要项目 id")
    if args.action == "status":
        path = _state_path(args.pid)
        print(path.read_text(encoding="utf-8") if path.is_file() else "{}")
        return 0
    try:
        if args.action == "keyframes":
            keyframes(args.pid, args.token, args.reroll)
        else:
            produce(args.pid, args.token)
    except Exception as exc:    # 写进状态文件，本地轮询能看到原因
        _write_state(args.pid, stage="error", error=f"{type(exc).__name__}: {exc}")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
