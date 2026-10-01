from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageOps

from .krea_provider import KeyframeRequest
from .project import ClipSpec, TravelProject


class KeyframeProvider(Protocol):
    @property
    def model_manifest(self) -> dict[str, Any]: ...

    def generate(self, request: KeyframeRequest, output_path: str | Path) -> dict[str, Any]: ...


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_image(path: str | Path, orientation: str) -> tuple[int, int, str]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        with Image.open(source) as image:
            image.verify()
        with Image.open(source) as image:
            width, height = image.size
            fmt = str(image.format or "unknown")
    except Exception as exc:
        raise ValueError(f"图片无法解码: {source}: {exc}") from exc
    matches = height > width if orientation == "portrait" else width > height
    if not matches:
        raise ValueError(f"图片方向与项目 {orientation} 不一致，拒绝自动拉伸: {source} ({width}x{height})")
    return width, height, fmt


def normalize_reference(source: str | Path, target: str | Path,
                        size: tuple[int, int], orientation: str) -> dict[str, Any]:
    original_width, original_height, fmt = inspect_image(source, orientation)
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        normalized = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        normalized.save(destination, format="PNG", optimize=True)
    return {
        "source_width": original_width, "source_height": original_height,
        "source_format": fmt, "width": size[0], "height": size[1],
        "transform": "center_crop_then_resize_no_stretch",
    }


class H3QueueAdapter:
    """Thin adapter over the already proven MINIMAXH3_2PASS queue implementation."""

    def __init__(self, repo: str | Path, config_path: str | Path) -> None:
        self.repo = Path(repo).expanduser().resolve()
        if not (self.repo / "src" / "cc_queue.py").is_file():
            raise FileNotFoundError(f"不是有效的 H3 工作流仓库: {self.repo}")
        config = Path(config_path)
        if not config.is_absolute():
            config = self.repo / config
        self.config_path = config.resolve()
        sys.path.insert(0, str(self.repo))
        try:
            try:
                self.cc_queue = importlib.import_module("src.cc_queue")
                self.config_module = importlib.import_module("src.config")
            except ModuleNotFoundError as exc:
                if exc.name == "yaml":
                    raise RuntimeError(
                        "当前 Python 缺少 PyYAML；请在已安装 H3 requirements.txt 的环境中运行"
                    ) from exc
                raise
        finally:
            try:
                sys.path.remove(str(self.repo))
            except ValueError:
                pass
        self.config = self.config_module.load_config(self.config_path)

    def submit_clip(self, project: TravelProject, clip: ClipSpec,
                    reference: Path | None, metadata: dict[str, Any],
                    attempt: int = 1) -> dict[str, Any]:
        profile = "fl2va_8step" if clip.input_strategy == "text_only_h3" else "ref2va_4step"
        qa = None
        if project.audio_mode in {"on", "off"}:
            qa = {"require_audio": project.audio_mode == "on"}
        width, height = project.h3_size
        job_id = f"{project.project_id}-{clip.clip_id}"
        if attempt > 1:
            job_id += f"-r{attempt}"
        job, queue_path = self.cc_queue.submit(
            self.config,
            job_id=job_id,
            project_id=project.project_id,
            prompt=project.motion_prompt_for(clip),
            references=[] if reference is None else [str(reference.resolve())],
            profile=profile,
            duration=clip.duration_seconds,
            width=width,
            height=height,
            seed=clip.seed,
            metadata=metadata,
            qa=qa,
        )
        return {"job_id": job["job_id"], "queue_file": str(queue_path.resolve()),
                "profile": profile, "state": "queued", "attempt": attempt,
                "motion_prompt": project.motion_prompt_for(clip)}


def _find_h3_record(paths: dict[str, Any], job_id: str | None
                    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not job_id:
        return None, None
    for key in ("review_ready", "rejected"):
        candidate = Path(paths[key]) / job_id / "report.json"
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8")), None
    failed_path = Path(paths["jobs"]) / "failed" / f"{job_id}.json"
    if failed_path.is_file():
        return None, json.loads(failed_path.read_text(encoding="utf-8"))
    return None, None


def _clip_cost_row(clip_id: str, state: dict[str, Any], h3_report: dict[str, Any] | None,
                   h3_failed: dict[str, Any] | None, hourly: float) -> dict[str, Any]:
    generation = state.get("generation") or {}
    attempts = [item.get("timing") or {} for item in state.get("keyframe_history", [])]
    if generation.get("timing"):
        attempts.append(generation["timing"])
    image_seconds = round(sum(float(t.get("wall_seconds", 0)) for t in attempts), 3)
    row: dict[str, Any] = {
        "clip_id": clip_id,
        "keyframe": {
            "attempts": len(attempts),
            "size": [generation.get("width"), generation.get("height")],
            "wall_seconds": image_seconds,
            "comfy_execution_seconds": round(sum(
                float(t.get("comfy_execution_seconds", 0)) for t in attempts), 3),
            "estimated_gpu_cost_cny": round(image_seconds / 3600 * hourly, 4),
        },
        "video": None,
    }
    if h3_report:
        technical = (h3_report.get("qa") or {}).get("technical") or {}
        row["video"] = {
            "job_id": h3_report.get("job_id"),
            "status": h3_report.get("status"),
            "duration_seconds": technical.get("duration") or h3_report.get("expected_duration"),
            "resolution": [technical.get("width"), technical.get("height")],
            "elapsed_seconds": h3_report.get("elapsed_seconds"),
            "estimated_gpu_cost_cny": h3_report.get("estimated_gpu_cost_cny"),
            "result": h3_report.get("result"),
        }
    elif h3_failed:
        row["video"] = {"job_id": h3_failed.get("job_id"), "status": "failed",
                        "error": h3_failed.get("error"),
                        "note": "失败任务没有耗时记录，未计入成本"}
    elif state.get("h3"):
        row["video"] = {"job_id": state["h3"].get("job_id"), "status": "pending"}
    return row


class TravelRun:
    def __init__(self, project: TravelProject, run_root: str | Path) -> None:
        self.project = project
        self.run_dir = Path(run_root).expanduser().resolve() / project.project_id
        self.manifest_path = self.run_dir / "manifest.json"

    def plan(self, *, workflow_path: str | Path | None = None,
             h3_repo: str | Path | None = None) -> dict[str, Any]:
        return {
            "project_id": self.project.project_id,
            "destination": self.project.destination,
            "orientation": self.project.orientation,
            "keyframe_size": list(self.project.keyframe_size),
            "h3_generation_size": list(self.project.h3_size),
            "run_directory": str(self.run_dir),
            "krea_workflow": str(Path(workflow_path).resolve()) if workflow_path else None,
            "h3_repo": str(Path(h3_repo).resolve()) if h3_repo else None,
            "review_gate": "auto_keyframe clips must be approved before H3 queue submission",
            "clips": [{
                "clip_id": clip.clip_id,
                "place": clip.place,
                "input_strategy": clip.input_strategy,
                "seed": clip.seed,
                "duration_seconds": clip.duration_seconds,
                "h3_profile": "fl2va_8step" if clip.input_strategy == "text_only_h3" else "ref2va_4step",
                "reference": str(clip.reference) if clip.reference else None,
            } for clip in self.project.clips],
        }

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.is_file():
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {
            "schema_version": 1,
            "project_id": self.project.project_id,
            "project_file": str(self.project.source_path),
            "destination": self.project.destination,
            "orientation": self.project.orientation,
            "audio_mode": self.project.audio_mode,
            "clips": {},
        }

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        _atomic_json(self.manifest_path, manifest)

    def generate_keyframes(self, provider: KeyframeProvider | None, *, force: bool = False,
                           reroll: tuple[str, ...] = ()) -> dict[str, Any]:
        unknown = set(reroll).difference(clip.clip_id for clip in self.project.clips)
        if unknown:
            raise ValueError("--reroll-clip 指定了不存在的片段: " + ", ".join(sorted(unknown)))
        manifest = self._load_manifest()
        clips_state = manifest.setdefault("clips", {})
        raw_dir = self.run_dir / "keyframes" / "raw"
        normalized_dir = self.run_dir / "keyframes" / "approved_inputs"
        for clip in self.project.clips:
            state = clips_state.get(clip.clip_id, {})
            normalized_path = normalized_dir / f"{clip.clip_id}.png"
            if (not force and clip.clip_id not in reroll
                    and state.get("keyframe_status") in {"pending_review", "approved"}
                    and normalized_path.is_file()):
                if _sha256(normalized_path) != state.get("reference_sha256"):
                    raise ValueError(
                        f"{clip.clip_id} 关键帧已在生成后被改动；请改用 provided_reference "
                        "接收修图，或显式传 --force 重新生成"
                    )
                continue
            # --force 重抽也是真实花掉的机时，旧的计时并入历史而不是被覆盖。
            history = list(state.get("keyframe_history", []))
            if (state.get("generation") or {}).get("timing"):
                history.append({"prompt_id": state["generation"].get("prompt_id"),
                                "seed": state.get("seed"),
                                "timing": state["generation"]["timing"]})
            base = {
                "keyframe_history": history,
                "clip_id": clip.clip_id,
                "place": clip.place,
                "input_strategy": clip.input_strategy,
                "seed": clip.seed,
                "duration_seconds": clip.duration_seconds,
                "motion_prompt": self.project.motion_prompt_for(clip),
            }
            if clip.input_strategy == "text_only_h3":
                base.update({"keyframe_status": "not_required", "reference": None})
                clips_state[clip.clip_id] = base
                self._save_manifest(manifest)
                continue
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_path = raw_dir / f"{clip.clip_id}.png"
            try:
                if clip.input_strategy == "provided_reference":
                    if clip.reference is None or not clip.reference.is_file():
                        raise FileNotFoundError(clip.reference)
                    shutil.copy2(clip.reference, raw_path)
                    generation = {"provider": "provided_reference",
                                  "source_path": str(clip.reference)}
                    status = "approved"
                    keyframe_prompt = None
                else:
                    if provider is None:
                        raise ValueError("auto_keyframe 片段需要 KeyframeProvider")
                    keyframe_prompt = self.project.keyframe_prompt_for(clip)
                    width, height = self.project.keyframe_size
                    generation = provider.generate(KeyframeRequest(
                        project_id=self.project.project_id,
                        clip_id=clip.clip_id,
                        prompt=keyframe_prompt,
                        width=width,
                        height=height,
                        seed=clip.seed,
                    ), raw_path)
                    status = "pending_review"
                image_info = normalize_reference(
                    raw_path, normalized_path, self.project.h3_size, self.project.orientation)
                base.update({
                    "keyframe_status": status,
                    "keyframe_prompt": keyframe_prompt,
                    "raw_keyframe": str(raw_path.resolve()),
                    "reference": str(normalized_path.resolve()),
                    "reference_sha256": _sha256(normalized_path),
                    "image": image_info,
                    "generation": generation,
                })
                clips_state[clip.clip_id] = base
                self._save_manifest(manifest)
            except Exception as exc:
                base.update({"keyframe_status": "keyframe_failed", "error": str(exc)})
                clips_state[clip.clip_id] = base
                self._save_manifest(manifest)
                raise
        manifest["stage"] = "keyframes_ready_for_review"
        self._save_manifest(manifest)
        return manifest

    def approve_keyframes(self) -> dict[str, Any]:
        manifest = self._load_manifest()
        states = manifest.get("clips", {})
        for clip in self.project.clips:
            if clip.input_strategy == "text_only_h3":
                continue
            state = states.get(clip.clip_id)
            if not state or state.get("keyframe_status") not in {"pending_review", "approved"}:
                raise ValueError(f"{clip.clip_id} 没有可批准的关键帧")
            reference = Path(state["reference"])
            inspect_image(reference, self.project.orientation)
            if _sha256(reference) != state.get("reference_sha256"):
                raise ValueError(f"{clip.clip_id} 关键帧已在生成后被改动，请重新执行 keyframes")
            state["keyframe_status"] = "approved"
        manifest["stage"] = "keyframes_approved"
        self._save_manifest(manifest)
        return manifest

    def cost_report(self, adapter: H3QueueAdapter) -> dict[str, Any]:
        """汇总生图 + 生视频的耗时和机时成本，写入 runs/<project>/cost_report.json。"""
        manifest = self._load_manifest()
        hourly = float(adapter.config.get("cost", {}).get("gpu_hourly_cny", 0))
        paths = adapter.config["paths"]
        rows = []
        for clip in self.project.clips:
            state = manifest.get("clips", {}).get(clip.clip_id, {})
            report, failed = _find_h3_record(paths, (state.get("h3") or {}).get("job_id"))
            row = _clip_cost_row(clip.clip_id, state, report, failed, hourly)
            # 被重跑替换掉的旧成片也真实占用了 GPU，计入成本但不计入交付秒数。
            superseded = []
            for old in state.get("h3_history", []):
                old_report, _ = _find_h3_record(paths, old.get("job_id"))
                superseded.append({
                    "job_id": old.get("job_id"),
                    "elapsed_seconds": (old_report or {}).get("elapsed_seconds"),
                    "estimated_gpu_cost_cny": (old_report or {}).get("estimated_gpu_cost_cny"),
                    "result": (old_report or {}).get("result"),
                })
            row["superseded_videos"] = superseded
            rows.append(row)

        image_seconds = round(sum(r["keyframe"]["wall_seconds"] for r in rows), 3)
        done_videos = [r["video"] for r in rows
                       if r["video"] and r["video"].get("elapsed_seconds") is not None]
        old_videos = [v for r in rows for v in r["superseded_videos"]
                      if v.get("elapsed_seconds") is not None]
        video_seconds = round(sum(float(v["elapsed_seconds"])
                                  for v in done_videos + old_videos), 3)
        delivered = round(sum(float(v.get("duration_seconds") or 0) for v in done_videos
                              if v.get("status") == "awaiting_human_review"), 3)
        image_cost = round(image_seconds / 3600 * hourly, 4)
        video_cost = round(sum(float(v.get("estimated_gpu_cost_cny") or 0)
                               for v in done_videos + old_videos), 4)
        total_cost = round(image_cost + video_cost, 4)
        report = {
            "project_id": self.project.project_id,
            "gpu_hourly_cny": hourly,
            "clips": rows,
            "totals": {
                "keyframe_images": sum(r["keyframe"]["attempts"] for r in rows),
                "keyframe_gpu_seconds": image_seconds,
                "keyframe_cost_cny": image_cost,
                "videos_completed": len(done_videos),
                "videos_pending_or_failed": len(rows) - len(done_videos),
                "videos_rerun_superseded": len(old_videos),
                "video_gpu_seconds": video_seconds,
                "video_cost_cny": video_cost,
                "total_gpu_seconds": round(image_seconds + video_seconds, 3),
                "total_gpu_minutes": round((image_seconds + video_seconds) / 60, 2),
                "total_cost_cny": total_cost,
                "delivered_video_seconds": delivered,
                "cost_cny_per_delivered_second": (round(total_cost / delivered, 4)
                                                  if delivered else None),
            },
            "notes": [
                "成本 = GPU 占用秒数 x gpu_hourly_cny / 3600；生图按提交到落盘的墙钟时间计，含 --force 重抽。",
                "不在本项目 manifest 里的试图（如单独的 seed 试验项目）不计入。",
            ],
        }
        _atomic_json(self.run_dir / "cost_report.json", report)
        return report

    def submit_h3(self, adapter: H3QueueAdapter, *, approve_keyframes: bool = False,
                  rerun: tuple[str, ...] = ()) -> dict[str, Any]:
        if approve_keyframes:
            self.approve_keyframes()
        manifest = self._load_manifest()
        states = manifest.get("clips", {})
        unknown = set(rerun).difference(clip.clip_id for clip in self.project.clips)
        if unknown:
            raise ValueError("--rerun-clip 指定了不存在的片段: " + ", ".join(sorted(unknown)))
        for clip in self.project.clips:
            state = states.get(clip.clip_id)
            if not state:
                raise ValueError(f"{clip.clip_id} 尚未准备；先执行 keyframes 阶段")
            attempt = 1
            if clip.clip_id in rerun and state.get("h3"):
                # 旧任务留档：成本报告要算它，成片也保留在 review_ready 里可对比。
                history = state.setdefault("h3_history", [])
                history.append(state.pop("h3"))
                attempt = max(int(item.get("attempt", 1)) for item in history) + 1
            if state.get("h3", {}).get("state") == "queued":
                continue
            reference = None
            if clip.input_strategy != "text_only_h3":
                if state.get("keyframe_status") != "approved":
                    raise PermissionError(f"{clip.clip_id} 关键帧尚未批准，拒绝提交 H3")
                reference = Path(state["reference"])
                inspect_image(reference, self.project.orientation)
            state["h3"] = adapter.submit_clip(
                self.project, clip, reference,
                metadata={
                    "workflow": "travel-krea2-mj-to-h3",
                    "manifest": str(self.manifest_path),
                    "destination": self.project.destination,
                    "place": clip.place,
                    "keyframe_sha256": state.get("reference_sha256"),
                },
                attempt=attempt,
            )
            self._save_manifest(manifest)
        manifest["stage"] = "h3_queued"
        self._save_manifest(manifest)
        return manifest
