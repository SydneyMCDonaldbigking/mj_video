from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
STRATEGIES = {"provided_reference", "auto_keyframe", "text_only_h3"}
ORIENTATIONS = {"portrait", "landscape"}
AUDIO_MODES = {"inherit", "on", "off"}
# 竖版 (宽, 高)，横版自动对调；都是 32 的倍数。
KEYFRAME_RESOLUTIONS = {
    "1mp": (768, 1344),
    "1080p": (1088, 1920),
    "2k": (1440, 2560),
}


@dataclass(frozen=True)
class ClipSpec:
    clip_id: str
    input_strategy: str
    place: str
    keyframe_prompt: str
    motion_prompt: str
    seed: int
    reference: Path | None = None
    duration_seconds: float = 5.0


@dataclass(frozen=True)
class TravelProject:
    project_id: str
    destination: str
    audience: str
    season: str
    time_of_day: str
    orientation: str
    style: str
    audio_mode: str
    clips: tuple[ClipSpec, ...]
    source_path: Path
    keyframe_resolution: str = "1080p"

    @property
    def keyframe_size(self) -> tuple[int, int]:
        width, height = KEYFRAME_RESOLUTIONS[self.keyframe_resolution]
        return (width, height) if self.orientation == "portrait" else (height, width)

    @property
    def h3_size(self) -> tuple[int, int]:
        return (480, 832) if self.orientation == "portrait" else (832, 480)

    def keyframe_prompt_for(self, clip: ClipSpec) -> str:
        direction = "vertical portrait composition" if self.orientation == "portrait" else "horizontal landscape composition"
        parts = [
            f"Single photorealistic editorial travel keyframe of {self.destination}",
            f"location beat: {clip.place}",
            f"season: {self.season}",
            f"time and light: {self.time_of_day}",
            f"visual treatment: {self.style}",
            clip.keyframe_prompt.strip(),
            direction,
            "one coherent camera viewpoint, believable geography and architecture, natural depth, clean edges",
            "no captions, no title card, no watermark, no fake logo, no prominent readable signage, no collage, no split screen",
        ]
        return ". ".join(part.rstrip(". ") for part in parts if part) + "."

    def motion_prompt_for(self, clip: ClipSpec) -> str:
        # H3 通用规律：参考图负责"画面里有什么"，文字只写"什么在动"；
        # 不逐条点名禁止项（点名会激活对应运动先验），状态锁用正面陈述。
        opener = (
            "Follow the reference image exactly. Everything in frame is reproduced as shown "
            "and does not move unless described below."
        )
        closer = "Photoreal cinematography. No text, no watermarks, no cuts, no scene change."
        return f"{opener}\n\n{clip.motion_prompt.strip()}\n\n{closer}"


def _required_text(payload: dict[str, Any], key: str, context: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"{context}.{key} 不能为空")
    return value


def load_project(path: str | Path) -> TravelProject:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("旅游项目必须是 JSON 对象")

    project_id = _required_text(payload, "project_id", "project")
    if not SAFE_ID.fullmatch(project_id):
        raise ValueError("project_id 仅允许 1-80 位字母、数字、点、下划线和短横线")
    orientation = str(payload.get("orientation", "portrait")).strip()
    if orientation not in ORIENTATIONS:
        raise ValueError(f"orientation 只能是 {sorted(ORIENTATIONS)}")
    audio_mode = str(payload.get("audio_mode", "inherit")).strip()
    if audio_mode not in AUDIO_MODES:
        raise ValueError(f"audio_mode 只能是 {sorted(AUDIO_MODES)}")

    keyframe_resolution = str(payload.get("keyframe_resolution", "1080p")).strip().lower()
    if keyframe_resolution not in KEYFRAME_RESOLUTIONS:
        raise ValueError(f"keyframe_resolution 只能是 {sorted(KEYFRAME_RESOLUTIONS)}")

    raw_clips = payload.get("clips")
    if not isinstance(raw_clips, list) or not 1 <= len(raw_clips) <= 20:
        raise ValueError("clips 必须是包含 1-20 段的数组")
    clips: list[ClipSpec] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_clips, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"clips[{index}] 必须是 JSON 对象")
        context = f"clips[{index}]"
        clip_id = _required_text(raw, "clip_id", context)
        if not SAFE_ID.fullmatch(clip_id) or clip_id in seen:
            raise ValueError(f"{context}.clip_id 非法或重复: {clip_id}")
        seen.add(clip_id)
        strategy = str(raw.get("input_strategy", "auto_keyframe")).strip()
        if strategy not in STRATEGIES:
            raise ValueError(f"{context}.input_strategy 只能是 {sorted(STRATEGIES)}")
        keyframe_prompt = str(raw.get("keyframe_prompt", "")).strip()
        if strategy == "auto_keyframe" and not keyframe_prompt:
            raise ValueError(f"{context}.keyframe_prompt 在 auto_keyframe 模式下不能为空")
        motion_prompt = _required_text(raw, "motion_prompt", context)
        reference_value = raw.get("reference")
        reference = None
        if reference_value:
            reference = Path(str(reference_value)).expanduser()
            if not reference.is_absolute():
                reference = (source.parent / reference).resolve()
            else:
                reference = reference.resolve()
        if strategy == "provided_reference" and reference is None:
            raise ValueError(f"{context}.reference 在 provided_reference 模式下不能为空")
        if strategy == "text_only_h3" and reference is not None:
            raise ValueError(f"{context} 为 text_only_h3，不能同时提供 reference")
        duration = float(raw.get("duration_seconds", 5.0))
        if not 2.0 <= duration <= 15.0:
            raise ValueError(f"{context}.duration_seconds 必须在 2-15 秒之间")
        clips.append(ClipSpec(
            clip_id=clip_id,
            input_strategy=strategy,
            place=_required_text(raw, "place", context),
            keyframe_prompt=keyframe_prompt,
            motion_prompt=motion_prompt,
            seed=int(raw.get("seed", 26092600 + index)),
            reference=reference,
            duration_seconds=duration,
        ))

    return TravelProject(
        project_id=project_id,
        destination=_required_text(payload, "destination", "project"),
        audience=str(payload.get("audience", "")).strip(),
        season=str(payload.get("season", "")).strip(),
        time_of_day=str(payload.get("time_of_day", "")).strip(),
        orientation=orientation,
        style=_required_text(payload, "style", "project"),
        audio_mode=audio_mode,
        clips=tuple(clips),
        source_path=source,
        keyframe_resolution=keyframe_resolution,
    )
