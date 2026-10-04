#!/usr/bin/env python
"""本地一键编排 Reels：分镜板 → 服务器关键帧 → 审核 → H3 → 下载 → 后期包装。

    python scripts/reels_make.py japan-hakone-01               # 跑到关键帧审核为止，出总览图
    python scripts/reels_make.py japan-hakone-01 --reroll clip-02-onsen   # 改完分镜板后只重抽这张
    python scripts/reels_make.py japan-hakone-01 --approve     # 审核通过：H3 → 下载 → 后期
    python scripts/reels_make.py japan-hakone-01 --auto        # 不停在审核，一口气跑完
    python scripts/reels_make.py a b c                          # 一批（--approve / --auto 同理）
    python scripts/reels_make.py japan-hakone-01 --post-only   # 只重做后期（改了文案、位置、贴纸）
    python scripts/reels_make.py japan-hakone-01 --check       # 只校验分镜板、生成 projects/<id>.json

服务器地址在 server/reels_server.json。SSH 随机断线自动重试；长任务在服务器上后台跑，
本地定时查状态，只在状态变化时打印一行。服务器连不上（关机）时退出码为 2。
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import reels_post as post                                   # noqa: E402
from travel_workflow.project import load_project            # noqa: E402

BOARDS = ROOT / "reels" / "boards"
SERVER = json.loads((ROOT / "server" / "reels_server.json").read_text(encoding="utf-8"))
HOUSE_STYLE = ("premium cinematic travel film still, photorealistic, restrained MJ-inspired soft "
               "cinematic light, natural rich colour, shallow depth of field")
DEFAULT_AUDIENCE = "English-speaking Instagram travellers, mostly in Australia"
MINUTES_PER_CLIP, CNY_PER_CLIP = 7.1, 0.355                 # 4090 实测：一张关键帧 + 一段 5 秒 H3
PUSH_FILES = ["scripts/cc_travel_submit.py", "scripts/reels_remote.py",
              "server/server_model_map.json", "mj风格工作流.json"]
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]


class ServerDown(RuntimeError):
    pass


def _retry(cmd: list[str], *, retries: int = 6, timeout: int = 900) -> subprocess.CompletedProcess:
    """这类平台的 SSH 端口转发会随机断开（退出码 255），断了就重试；一直拒绝连接说明关机了。"""
    last = ""
    for attempt in range(retries):
        try:
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            last = "超时"
            continue
        if result.returncode != 255 and not (cmd[0] == "scp" and "Connection" in result.stderr):
            return result
        last = result.stderr.strip()
        if "refused" in last and attempt >= 2:
            break
        time.sleep(min(30, 4 * (attempt + 1)))
    raise ServerDown(last)


def ssh(command: str, **kw) -> str:
    result = _retry(["ssh", "-p", str(SERVER["port"]), *SSH_OPTS, SERVER["host"], command], **kw)
    if result.returncode:
        raise RuntimeError(f"远程命令失败（{result.returncode}）：{result.stderr.strip()[-400:]}")
    return result.stdout


def scp(src: str, dst: str) -> None:
    """本地路径用相对 ROOT 的写法：Git 自带的 scp 会把 C:/ 当成主机名。"""
    result = _retry(["scp", "-P", str(SERVER["port"]), *SSH_OPTS, src, dst])
    if result.returncode:
        raise RuntimeError(f"scp 失败：{result.stderr.strip()[-300:]}")


def remote(path: str) -> str:
    return f"{SERVER['host']}:{path}"


def board_to_project(board: dict) -> dict:
    """分镜板里的生产字段 → 现有 cc_travel_submit 的项目 JSON。"""
    clips = []
    for shot in board["shots"]:
        ref = shot.get("reference")
        clips.append({
            "clip_id": shot["clip_id"],
            "input_strategy": "provided_reference" if ref else "auto_keyframe",
            "place": shot["place"],
            "reference": f"../{ref}" if ref else None,      # 项目文件在 projects/ 下，参考图路径相对它解析
            "keyframe_prompt": shot.get("keyframe_prompt", ""),
            "motion_prompt": shot["motion_prompt"],
            "duration_seconds": shot.get("duration_seconds", 5),
            "seed": shot["seed"],
        })
    return {
        "project_id": board["id"],
        "generated_from": f"reels/boards/{board['id']}.json",
        "destination": board["destination"],
        "audience": board.get("audience", DEFAULT_AUDIENCE),
        "season": board["season"],
        "time_of_day": board["time_of_day"],
        "orientation": "portrait",
        "keyframe_resolution": board.get("keyframe_resolution", "1080p"),
        "style": board.get("style", HOUSE_STYLE),
        "audio_mode": "inherit",
        "clips": clips,
    }


def prepare(pid: str) -> dict:
    path = BOARDS / f"{pid}.json"
    board = post.load_board(path)
    if board.get("id") != pid:
        raise ValueError(f"{path.name}: id 必须和文件名一致")
    for key in ("destination", "season", "time_of_day", "pin", "ig_caption", "hashtags"):
        if not board.get(key):
            raise ValueError(f"{pid}: 缺少 {key}")
    for shot in board["shots"]:
        for key in ("clip_id", "place", "motion_prompt", "seed"):
            if not shot.get(key):
                raise ValueError(f"{pid}: shot 缺少 {key}")
    if board.get("source"):
        return board                                         # 重剪旧成片：不走生产，不生成项目文件
    out = ROOT / "projects" / f"{pid}.json"
    out.write_text(json.dumps(board_to_project(board), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_project(out)                                        # 复用生产端的校验
    return board


def push(boards: dict[str, dict]) -> None:
    """把服务器跑生产需要的代码和项目文件打包推上去（每次都推，保证两边一致）。"""
    files = [*sorted((ROOT / "travel_workflow").glob("*.py")), *(ROOT / f for f in PUSH_FILES)]
    files += [ROOT / "projects" / f"{pid}.json" for pid in boards]
    files += [ROOT / s["reference"] for b in boards.values() for s in b["shots"] if s.get("reference")]
    bundle = ROOT / "runs" / "_push.tar"
    bundle.parent.mkdir(exist_ok=True)
    with tarfile.open(bundle, "w") as tar:
        for path in files:
            tar.add(path, arcname=path.relative_to(ROOT).as_posix())
    scp("runs/_push.tar", remote("/tmp/reels_push.tar"))
    ssh(f"tar -xf /tmp/reels_push.tar -C {SERVER['repo']} && rm -f /tmp/reels_push.tar")
    bundle.unlink()


def start(pid: str, action: str, *extra: str) -> str:
    """在服务器上后台跑 reels_remote.py，返回这一轮的 token。"""
    token = str(time.time_ns())
    repo, py = SERVER["repo"], SERVER["python"]
    inner = " ".join(shlex.quote(a) for a in [py, "scripts/reels_remote.py", action, pid, "--token", token, *extra])
    ssh(f"mkdir -p {repo}/runs/{pid} && cd {repo} && setsid --fork bash -c "
        f"{shlex.quote(inner + f' >> runs/{pid}/reels_remote.log 2>&1')} < /dev/null > /dev/null 2>&1")
    return token


def wait(pid: str, token: str, targets: set[str], every: int) -> dict:
    state_file = f"{SERVER['repo']}/runs/{pid}/reels_state.json"
    started, last = time.time(), None
    while True:
        state = json.loads(ssh(f"cat {state_file} 2>/dev/null || echo '{{}}'") or "{}")
        if state.get("token") == token:
            line = f"{state.get('stage')} · {state.get('detail') or ''}"
            if line != last:
                print(f"[{time.strftime('%H:%M')}] {pid}: {line}", flush=True)
                last = line
            if state.get("stage") == "error":
                raise RuntimeError(f"{pid}: {state.get('error')}")
            if state.get("stage") in targets:
                return state
        elif time.time() - started > 180:
            tail = ssh(f"tail -n 15 {SERVER['repo']}/runs/{pid}/reels_remote.log 2>/dev/null || true")
            raise RuntimeError(f"{pid}: 服务器端 3 分钟没起来：\n{tail}")
        time.sleep(every)


def keyframe_sheet(pid: str, board: dict, refs: dict[str, str]) -> Path:
    """关键帧总览图：每张画四条文字位置参考线（T/U/M/L），绿线是自动建议，白框是分镜板里写死的。"""
    folder = ROOT / "runs" / pid / "keyframes"
    folder.mkdir(parents=True, exist_ok=True)
    tile_w, tile_h, head = 360, 624, 40
    sheet = Image.new("RGB", (len(board["shots"]) * (tile_w + 8) + 8, tile_h + head + 8), "white")
    font = post.Fonts(Path("C:/Windows/Fonts"))("msyhbd.ttc", 18)
    small = post.Fonts(Path("C:/Windows/Fonts"))("segoeuib.ttf", 16)
    wanted = [(board.get("hook") or {}).get("anchor")] + [s.get("anchor") for s in board["shots"][1:]]
    for k, shot in enumerate(board["shots"]):
        local = folder / f"{shot['clip_id']}.png"
        scp(remote(refs[shot["clip_id"]]), local.relative_to(ROOT).as_posix())
        img = Image.open(local).convert("RGB")
        gray = np.asarray(img.convert("L").resize((108, 192)), np.float32) / 255
        suggestion = post.auto_anchor([gray])
        tile = img.resize((tile_w, tile_h), Image.LANCZOS)
        d = ImageDraw.Draw(tile)
        for name, y in post.ANCHOR_Y.items():
            yy = round(y * tile_h / post.H)
            color = (90, 220, 120) if name == suggestion else (255, 255, 255)
            d.line((0, yy, tile_w, yy), fill=color, width=3 if name == suggestion else 1)
            d.text((6, yy - 20), name[0].upper(), font=small, fill=color)
            if wanted[k] == name:
                d.rectangle((2, yy - 46, tile_w - 3, yy + 46), outline=(255, 255, 255), width=2)
        x = 8 + k * (tile_w + 8)
        sheet.paste(tile, (x, head))
        ImageDraw.Draw(sheet).text((x, 10), f"{k + 1}. {shot['clip_id']}  建议 {suggestion}", font=font, fill="black")
    out = ROOT / "runs" / pid / "keyframes_sheet.jpg"
    sheet.save(out, quality=88)
    return out


def fetch_clips(pid: str, state: dict) -> None:
    folder = ROOT / "runs" / pid / "clips"
    folder.mkdir(parents=True, exist_ok=True)
    for clip_id, path in state["clips"].items():
        scp(remote(path), f"runs/{pid}/clips/{clip_id}.mp4")
    scp(remote(f"{SERVER['repo']}/runs/{pid}/cost_report.json"), f"runs/{pid}/cost_report.json")


def main() -> int:
    ap = argparse.ArgumentParser(description="Reels 一键编排：分镜板 → 关键帧 → 审核 → H3 → 后期")
    ap.add_argument("ids", nargs="+", help="reels/boards/<id>.json 的 id")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--approve", action="store_true", help="关键帧已审核：提交 H3，下载后做后期")
    mode.add_argument("--auto", action="store_true", help="不停在审核，一口气跑完")
    mode.add_argument("--post-only", action="store_true", help="只重做后期")
    mode.add_argument("--check", action="store_true", help="只校验分镜板并生成项目文件")
    ap.add_argument("--reroll", action="append", default=[], metavar="CLIP_ID", help="只重抽这张关键帧")
    args = ap.parse_args()

    boards = {pid: prepare(pid) for pid in args.ids}
    recut = [pid for pid, board in boards.items() if board.get("source")]
    if recut and not (args.post_only or args.check):
        print(f"{', '.join(recut)} 是重剪旧成片（分镜板里有 source），只能 --post-only")
        return 1
    if args.check:
        for pid, board in boards.items():
            n = len(board["shots"])
            if board.get("source"):
                print(f"{pid}: {board['format']} · {n} 镜 · 重剪 {board['source']}，不花 GPU")
                continue
            print(f"{pid}: {board['format']} · {n} 镜 · 预计 {n * MINUTES_PER_CLIP:.0f} GPU 分钟 / "
                  f"{n * CNY_PER_CLIP:.2f} 元 · projects/{pid}.json 已生成")
        return 0
    fonts = post.Fonts(Path("C:/Windows/Fonts"))
    out_dir = ROOT / "deliveries" / "reels"
    if args.post_only:
        for pid in boards:
            post.render(BOARDS / f"{pid}.json", out_dir, fonts)
        return 0
    try:
        push(boards)
        print(ssh(f"cd {SERVER['repo']} && {SERVER['python']} scripts/reels_remote.py services").strip())
        if not args.approve:
            tokens = {}
            for pid, board in boards.items():
                mine = [c for c in args.reroll if c in {s["clip_id"] for s in board["shots"]}]
                tokens[pid] = start(pid, "keyframes", *[a for c in mine for a in ("--reroll", c)])
            for pid, board in boards.items():
                state = wait(pid, tokens[pid], {"review"}, every=20)
                print(f"{pid}: 关键帧总览 → {keyframe_sheet(pid, board, state['references'])}")
            if not args.auto:
                print("看完总览图：有问题改分镜板后 --reroll <clip_id>；没问题 --approve 继续。")
                return 0
        tokens = {pid: start(pid, "produce") for pid in boards}
        for pid in boards:
            state = wait(pid, tokens[pid], {"done"}, every=60)
            fetch_clips(pid, state)
            post.render(BOARDS / f"{pid}.json", out_dir, fonts)
            cost = state.get("cost") or {}
            print(f"{pid}: 完成 · {cost.get('total_gpu_minutes')} GPU 分钟 · {cost.get('total_cost_cny')} 元 · "
                  f"检查条 deliveries/reels/{pid}-qa.jpg")
    except ServerDown as exc:
        print(f"服务器连不上（可能关机了）：先在平台上开机，再重跑同一条命令。\n{exc}")
        return 2
    except RuntimeError as exc:
        print(f"失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
