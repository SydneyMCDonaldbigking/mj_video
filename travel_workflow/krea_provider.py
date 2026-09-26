from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KeyframeRequest:
    project_id: str
    clip_id: str
    prompt: str
    width: int
    height: int
    seed: int


@dataclass(frozen=True)
class KreaWorkflowSpec:
    source_path: Path
    source_sha256: str
    unet_name: str
    unet_dtype: str
    clip_name: str
    clip_type: str
    clip_device: str
    vae_name: str
    loras: tuple[tuple[str, float], ...]
    steps: int
    cfg: float
    sampler: str
    scheduler: str
    denoise: float
    rebalance_preset: str
    rebalance_weights: str
    rebalance_multiplier: float
    rebalance_renormalize: bool
    use_rebalance: bool = True
    overrides: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_ui_workflow(cls, path: str | Path) -> "KreaWorkflowSpec":
        source = Path(path).expanduser().resolve()
        raw_bytes = source.read_bytes()
        workflow = json.loads(raw_bytes.decode("utf-8-sig"))
        nodes = {int(node["id"]): node for node in workflow.get("nodes", [])}
        links = workflow.get("links", [])

        def one(node_type: str) -> dict[str, Any]:
            matches = [node for node in nodes.values() if node.get("type") == node_type]
            if len(matches) != 1:
                raise ValueError(f"工作流必须恰好包含一个 {node_type}，实际 {len(matches)}")
            return matches[0]

        unet = one("UNETLoader")
        clip = one("CLIPLoader")
        vae = one("VAELoader")
        sampler = one("KSampler")
        rebalance = one("ConditioningKrea2Rebalance")

        # 按 MODEL 边从 UNET 一路追到 KSampler，保留用户成功工作流中的 LoRA 顺序。
        outgoing: dict[int, list[int]] = {}
        for link in links:
            if len(link) >= 6 and link[5] == "MODEL":
                outgoing.setdefault(int(link[1]), []).append(int(link[3]))
        chain: list[tuple[str, float]] = []
        current = int(unet["id"])
        sampler_id = int(sampler["id"])
        visited: set[int] = set()
        while current != sampler_id:
            if current in visited:
                raise ValueError("MODEL 链存在循环")
            visited.add(current)
            targets = outgoing.get(current, [])
            if len(targets) != 1:
                raise ValueError(f"节点 {current} 的 MODEL 输出不是唯一链路")
            current = targets[0]
            if current == sampler_id:
                break
            node = nodes.get(current)
            if not node or node.get("type") != "LoraLoaderModelOnly":
                raise ValueError(f"MODEL 链中出现不支持的节点: {current}")
            values = node.get("widgets_values") or []
            chain.append((str(values[0]), float(values[1])))
        if not chain:
            raise ValueError("没有从原工作流解析到 LoRA 链")

        uv = unet.get("widgets_values") or []
        cv = clip.get("widgets_values") or []
        vv = vae.get("widgets_values")
        sv = sampler.get("widgets_values") or []
        rv = rebalance.get("widgets_values") or []
        if not isinstance(vv, str):
            vv = (vv or [""])[0]
        return cls(
            source_path=source,
            source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            unet_name=str(uv[0]), unet_dtype=str(uv[1]),
            clip_name=str(cv[0]), clip_type=str(cv[1]), clip_device=str(cv[2]),
            vae_name=str(vv), loras=tuple(chain),
            steps=int(sv[2]), cfg=float(sv[3]), sampler=str(sv[4]),
            scheduler=str(sv[5]), denoise=float(sv[6]),
            rebalance_preset=str(rv[0]), rebalance_weights=str(rv[1]),
            rebalance_multiplier=float(rv[2]), rebalance_renormalize=bool(rv[3]),
        )

    def with_overrides(self, mapping: dict[str, Any]) -> "KreaWorkflowSpec":
        """Adapt model filenames to a target server; every deviation is kept in the manifest."""
        allowed = {"unet", "clip", "vae", "loras", "rebalance"}
        unknown = sorted(set(mapping).difference(allowed))
        if unknown:
            raise ValueError("模型映射包含未知字段: " + ", ".join(unknown))
        changes: dict[str, Any] = {}
        if "unet" in mapping:
            changes["unet_name"] = str(mapping["unet"])
        if "clip" in mapping:
            changes["clip_name"] = str(mapping["clip"])
        if "vae" in mapping:
            changes["vae_name"] = str(mapping["vae"])
        if "loras" in mapping:
            changes["loras"] = tuple((str(name), float(strength))
                                     for name, strength in mapping["loras"])
        if "rebalance" in mapping:
            rebalance = mapping["rebalance"]
            if isinstance(rebalance, dict):
                # 新版 Rebalance-Pack 去掉了 preset/renormalize，只剩 multiplier + per_layer_weights。
                changes["use_rebalance"] = True
                if "multiplier" in rebalance:
                    changes["rebalance_multiplier"] = float(rebalance["multiplier"])
                if "per_layer_weights" in rebalance:
                    changes["rebalance_weights"] = str(rebalance["per_layer_weights"])
            else:
                changes["use_rebalance"] = bool(rebalance)
        return replace(self, overrides=dict(mapping), **changes)

    def model_manifest(self) -> dict[str, Any]:
        return {
            "provider": "comfy_krea2_mj",
            "source_workflow": str(self.source_path),
            "source_sha256": self.source_sha256,
            "unet": self.unet_name,
            "clip": self.clip_name,
            "vae": self.vae_name,
            "loras": [{"name": name, "strength": strength} for name, strength in self.loras],
            "sampling": {
                "steps": self.steps, "cfg": self.cfg, "sampler": self.sampler,
                "scheduler": self.scheduler, "denoise": self.denoise,
            },
            "rebalance": ({"multiplier": self.rebalance_multiplier,
                           "per_layer_weights": self.rebalance_weights}
                          if self.use_rebalance else False),
            "server_overrides": self.overrides,
        }

    def build_api_prompt(self, request: KeyframeRequest, filename_prefix: str) -> dict[str, Any]:
        prompt: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": self.unet_name, "weight_dtype": self.unet_dtype}},
        }
        model_source: list[Any] = ["1", 0]
        for index, (name, strength) in enumerate(self.loras, start=2):
            node_id = str(index)
            prompt[node_id] = {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": model_source, "lora_name": name, "strength_model": strength}}
            model_source = [node_id, 0]
        next_id = len(self.loras) + 2
        clip_id, encode_id, rebalance_id, zero_id = (str(next_id + i) for i in range(4))
        latent_id, sampler_id, vae_id, decode_id, save_id = (str(next_id + i) for i in range(4, 9))
        positive: list[Any] = [encode_id, 0]
        if self.use_rebalance:
            prompt[rebalance_id] = {"class_type": "ConditioningKrea2Rebalance", "inputs": {
                "conditioning": [encode_id, 0], "preset": self.rebalance_preset,
                "per_layer_weights": self.rebalance_weights,
                "multiplier": self.rebalance_multiplier,
                "renormalize": self.rebalance_renormalize}}
            positive = [rebalance_id, 0]
        prompt.update({
            clip_id: {"class_type": "CLIPLoader", "inputs": {
                "clip_name": self.clip_name, "type": self.clip_type, "device": self.clip_device}},
            encode_id: {"class_type": "CLIPTextEncode", "inputs": {
                "clip": [clip_id, 0], "text": request.prompt}},
            zero_id: {"class_type": "ConditioningZeroOut", "inputs": {
                "conditioning": [encode_id, 0]}},
            latent_id: {"class_type": "EmptyLatentImage", "inputs": {
                "width": request.width, "height": request.height, "batch_size": 1}},
            sampler_id: {"class_type": "KSampler", "inputs": {
                "model": model_source, "positive": positive, "negative": [zero_id, 0],
                "latent_image": [latent_id, 0], "seed": request.seed,
                "steps": self.steps, "cfg": self.cfg, "sampler_name": self.sampler,
                "scheduler": self.scheduler, "denoise": self.denoise}},
            vae_id: {"class_type": "VAELoader", "inputs": {"vae_name": self.vae_name}},
            decode_id: {"class_type": "VAEDecode", "inputs": {
                "samples": [sampler_id, 0], "vae": [vae_id, 0]}},
            save_id: {"class_type": "SaveImage", "inputs": {
                "images": [decode_id, 0], "filename_prefix": filename_prefix}},
        })
        return prompt


class ComfyKrea2MJProvider:
    def __init__(self, base_url: str, workflow_path: str | Path, *, timeout: float = 900,
                 poll_seconds: float = 2.0, model_map: dict[str, Any] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.spec = KreaWorkflowSpec.from_ui_workflow(workflow_path)
        if model_map:
            self.spec = self.spec.with_overrides(model_map)
        self.timeout = timeout
        self.poll_seconds = poll_seconds
        self.client_id = f"travel-{uuid.uuid4().hex}"
        self._preflight_done = False
        self._rebalance_inputs: set[str] | None = None

    @property
    def model_manifest(self) -> dict[str, Any]:
        return self.spec.model_manifest()

    def _json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ComfyUI 请求失败 {path}: {exc}") from exc

    def preflight(self) -> dict[str, Any]:
        """Fail before queueing when nodes or model filenames from the supplied UI graph are absent."""
        info = self._json("/object_info")
        required_classes = {
            "UNETLoader", "CLIPLoader", "CLIPTextEncode",
            "ConditioningZeroOut", "EmptyLatentImage",
            "KSampler", "VAELoader", "VAEDecode", "SaveImage",
        }
        if self.spec.loras:
            required_classes.add("LoraLoaderModelOnly")
        if self.spec.use_rebalance:
            required_classes.add("ConditioningKrea2Rebalance")
        missing_classes = sorted(required_classes.difference(info))
        if missing_classes:
            raise RuntimeError("ComfyUI 缺少节点: " + ", ".join(missing_classes))

        def choices(node_type: str, input_name: str) -> set[str]:
            try:
                raw = info[node_type]["input"]["required"][input_name][0]
                return {str(value) for value in raw} if isinstance(raw, list) else set()
            except (KeyError, IndexError, TypeError):
                return set()

        missing_models: list[str] = []
        checks = [
            ("UNETLoader", "unet_name", self.spec.unet_name),
            ("CLIPLoader", "clip_name", self.spec.clip_name),
            ("VAELoader", "vae_name", self.spec.vae_name),
        ]
        checks.extend(("LoraLoaderModelOnly", "lora_name", name)
                      for name, _strength in self.spec.loras)
        for node_type, input_name, expected in checks:
            available = choices(node_type, input_name)
            if available and expected not in available:
                missing_models.append(expected)
        if missing_models:
            raise RuntimeError("ComfyUI 缺少原工作流所需模型/LoRA: " + ", ".join(missing_models))
        if self.spec.use_rebalance:
            spec = info["ConditioningKrea2Rebalance"].get("input", {})
            self._rebalance_inputs = set(spec.get("required", {})) | set(spec.get("optional", {}))
        self._preflight_done = True
        return {"nodes": "ok", "models": "ok", "source_sha256": self.spec.source_sha256}

    def generate(self, request: KeyframeRequest, output_path: str | Path) -> dict[str, Any]:
        if not self._preflight_done:
            self.preflight()
        started = time.monotonic()
        filename_prefix = f"travel_keyframes/{request.project_id}/{request.clip_id}"
        graph = self.spec.build_api_prompt(request, filename_prefix)
        if self._rebalance_inputs is not None:
            # 只提交服务器节点版本实际声明的输入，旧版 UI 工作流里的多余参数会被 ComfyUI 拒绝。
            for node in graph.values():
                if node["class_type"] == "ConditioningKrea2Rebalance":
                    node["inputs"] = {k: v for k, v in node["inputs"].items()
                                      if k in self._rebalance_inputs}
        queued = self._json("/prompt", {"prompt": graph, "client_id": self.client_id})
        prompt_id = str(queued.get("prompt_id", ""))
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 未返回 prompt_id: {queued}")
        deadline = time.monotonic() + self.timeout
        record: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            history = self._json(f"/history/{urllib.parse.quote(prompt_id)}")
            record = history.get(prompt_id) if isinstance(history, dict) else None
            if record and record.get("outputs"):
                break
            status = (record or {}).get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"Krea2 关键帧生成失败: {status}")
            time.sleep(self.poll_seconds)
        else:
            raise TimeoutError(f"等待 Krea2 关键帧超时: {prompt_id}")

        image_info = None
        for output in (record or {}).get("outputs", {}).values():
            images = output.get("images") if isinstance(output, dict) else None
            if images:
                image_info = images[0]
                break
        if not image_info:
            raise RuntimeError(f"ComfyUI 历史记录里没有图片输出: {prompt_id}")
        query = urllib.parse.urlencode({
            "filename": image_info["filename"],
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        })
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".partial")
        try:
            with urllib.request.urlopen(self.base_url + "/view?" + query, timeout=120) as response:
                temporary.write_bytes(response.read())
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
        timing = {"wall_seconds": round(time.monotonic() - started, 3)}
        execution = comfy_execution_seconds(record or {})
        if execution is not None:
            timing["comfy_execution_seconds"] = execution
        return {"prompt_id": prompt_id, "provider": "comfy_krea2_mj",
                "model_manifest": self.model_manifest, "raw_path": str(target.resolve()),
                "width": request.width, "height": request.height, "timing": timing}


def comfy_execution_seconds(record: dict[str, Any]) -> float | None:
    """ComfyUI history 里 execution_start -> execution_success 的执行时长（不含排队和下载）。"""
    stamps: dict[str, float] = {}
    for message in (record.get("status") or {}).get("messages") or []:
        if (isinstance(message, (list, tuple)) and len(message) == 2
                and isinstance(message[1], dict) and "timestamp" in message[1]):
            stamps[str(message[0])] = float(message[1]["timestamp"])
    if "execution_start" in stamps and "execution_success" in stamps:
        return round((stamps["execution_success"] - stamps["execution_start"]) / 1000, 3)
    return None
