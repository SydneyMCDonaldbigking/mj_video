from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from travel_workflow.krea_provider import KeyframeRequest, KreaWorkflowSpec
from travel_workflow.pipeline import TravelRun, inspect_image
from travel_workflow.project import load_project


ROOT = Path(__file__).resolve().parents[1]


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def model_manifest(self):
        return {"provider": "fake"}

    def generate(self, request: KeyframeRequest, output_path: str | Path):
        self.calls += 1
        Image.new("RGB", (request.width, request.height), (40, 80, 120)).save(output_path)
        return {"provider": "fake", "prompt_id": f"p-{request.clip_id}"}


class FakeH3:
    def __init__(self) -> None:
        self.calls = []

    def submit_clip(self, project, clip, reference, metadata, attempt=1):
        self.calls.append((clip.clip_id, reference, metadata))
        suffix = f"-r{attempt}" if attempt > 1 else ""
        return {"job_id": f"{project.project_id}-{clip.clip_id}{suffix}",
                "queue_file": "fake.json", "profile": "ref2va_4step", "state": "queued",
                "attempt": attempt}


class TravelWorkflowTests(unittest.TestCase):
    def test_extracts_the_original_model_and_lora_chain(self):
        spec = KreaWorkflowSpec.from_ui_workflow(ROOT / "mj风格工作流.json")
        self.assertEqual(spec.unet_name, "krea2_turbo_fp8.safetensors")
        self.assertEqual(len(spec.loras), 5)
        self.assertEqual(spec.loras[-1][0], "krea2-Cc-MJ-影视柔光.safetensors")
        self.assertEqual((spec.steps, spec.cfg, spec.sampler, spec.scheduler),
                         (8, 1.0, "euler", "simple"))

    def test_api_prompt_is_vertical_and_uses_the_parsed_chain(self):
        spec = KreaWorkflowSpec.from_ui_workflow(ROOT / "mj风格工作流.json")
        graph = spec.build_api_prompt(KeyframeRequest(
            "p", "c", "travel", 768, 1344, 7), "travel/p/c")
        latent = next(node for node in graph.values() if node["class_type"] == "EmptyLatentImage")
        sampler = next(node for node in graph.values() if node["class_type"] == "KSampler")
        self.assertEqual((latent["inputs"]["width"], latent["inputs"]["height"]), (768, 1344))
        self.assertEqual(sampler["inputs"]["steps"], 8)
        self.assertEqual(sum(node["class_type"] == "LoraLoaderModelOnly"
                             for node in graph.values()), 5)

    def test_server_model_map_can_drop_loras_and_rebalance(self):
        spec = KreaWorkflowSpec.from_ui_workflow(ROOT / "mj风格工作流.json").with_overrides(
            {"unet": "krea2_turbo_fp8_scaled.safetensors", "loras": [], "rebalance": False})
        graph = spec.build_api_prompt(KeyframeRequest(
            "p", "c", "travel", 768, 1344, 7), "travel/p/c")
        types = [node["class_type"] for node in graph.values()]
        self.assertNotIn("LoraLoaderModelOnly", types)
        self.assertNotIn("ConditioningKrea2Rebalance", types)
        sampler = next(node for node in graph.values() if node["class_type"] == "KSampler")
        encode_id = next(k for k, node in graph.items() if node["class_type"] == "CLIPTextEncode")
        self.assertEqual(sampler["inputs"]["positive"], [encode_id, 0])
        self.assertEqual(sampler["inputs"]["model"], ["1", 0])
        self.assertEqual(spec.model_manifest()["server_overrides"]["loras"], [])
        with self.assertRaises(ValueError):
            spec.with_overrides({"lora": []})

    def test_rebalance_multiplier_can_be_overridden(self):
        spec = KreaWorkflowSpec.from_ui_workflow(ROOT / "mj风格工作流.json").with_overrides(
            {"rebalance": {"multiplier": 0.58}})
        graph = spec.build_api_prompt(KeyframeRequest(
            "p", "c", "travel", 768, 1344, 7), "travel/p/c")
        node = next(n for n in graph.values() if n["class_type"] == "ConditioningKrea2Rebalance")
        self.assertEqual(node["inputs"]["multiplier"], 0.58)
        self.assertEqual(spec.model_manifest()["rebalance"]["multiplier"], 0.58)

    def test_keyframe_resolution_presets(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = json.loads((ROOT / "travel_project.example.json").read_text(encoding="utf-8"))
            project_file = Path(folder) / "project.json"
            for value, size in (("1080p", (1088, 1920)), ("2k", (1440, 2560)), ("1mp", (768, 1344))):
                payload["keyframe_resolution"] = value
                project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(load_project(project_file).keyframe_size, size)
            payload["keyframe_resolution"] = "4k"
            project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_project(project_file)

    def test_wrong_orientation_is_rejected_instead_of_stretched(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "landscape.png"
            Image.new("RGB", (200, 100)).save(path)
            with self.assertRaisesRegex(ValueError, "拒绝自动拉伸"):
                inspect_image(path, "portrait")

    def test_review_gate_prevents_h3_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            payload = json.loads((ROOT / "travel_project.example.json").read_text(encoding="utf-8"))
            payload["clips"] = payload["clips"][:1]
            project_file = temp / "project.json"
            project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            project = load_project(project_file)
            run = TravelRun(project, temp / "runs")
            provider = FakeProvider()
            run.generate_keyframes(provider)
            h3 = FakeH3()
            with self.assertRaises(PermissionError):
                run.submit_h3(h3)
            self.assertEqual(h3.calls, [])
            run.submit_h3(h3, approve_keyframes=True)
            self.assertEqual(len(h3.calls), 1)

    def test_approved_keyframe_is_not_regenerated(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            payload = json.loads((ROOT / "travel_project.example.json").read_text(encoding="utf-8"))
            payload["clips"] = payload["clips"][:1]
            project_file = temp / "project.json"
            project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            project = load_project(project_file)
            run = TravelRun(project, temp / "runs")
            provider = FakeProvider()
            run.generate_keyframes(provider)
            run.approve_keyframes()
            run.generate_keyframes(provider)
            self.assertEqual(provider.calls, 1)

    def test_reroll_regenerates_only_the_named_clip(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            payload = json.loads((ROOT / "travel_project.example.json").read_text(encoding="utf-8"))
            payload["clips"] = payload["clips"][:2]
            project_file = temp / "project.json"
            project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            run = TravelRun(load_project(project_file), temp / "runs")
            provider = TimedProvider()
            run.generate_keyframes(provider)
            target = payload["clips"][1]["clip_id"]
            run.generate_keyframes(provider, reroll=(target,))
            self.assertEqual(provider.calls, 3)
            state = json.loads(run.manifest_path.read_text(encoding="utf-8"))["clips"]
            self.assertEqual(len(state[target]["keyframe_history"]), 1)
            self.assertEqual(state[payload["clips"][0]["clip_id"]]["keyframe_history"], [])
            with self.assertRaises(ValueError):
                run.generate_keyframes(provider, reroll=("no-such-clip",))

    def test_pending_keyframe_is_also_idempotent_without_force(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            payload = json.loads((ROOT / "travel_project.example.json").read_text(encoding="utf-8"))
            payload["clips"] = payload["clips"][:1]
            project_file = temp / "project.json"
            project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            project = load_project(project_file)
            run = TravelRun(project, temp / "runs")
            provider = FakeProvider()
            run.generate_keyframes(provider)
            run.generate_keyframes(provider)
            self.assertEqual(provider.calls, 1)


class TimedProvider(FakeProvider):
    def generate(self, request: KeyframeRequest, output_path: str | Path):
        result = super().generate(request, output_path)
        result.update({"width": request.width, "height": request.height,
                       "timing": {"wall_seconds": 10.0, "comfy_execution_seconds": 9.0}})
        return result


class FakeCostAdapter:
    def __init__(self, root: Path) -> None:
        self.config = {"cost": {"gpu_hourly_cny": 3.6},
                       "paths": {key: str(root / key)
                                 for key in ("jobs", "review_ready", "rejected")}}


class CostReportTests(unittest.TestCase):
    def test_comfy_execution_seconds_from_history(self):
        from travel_workflow.krea_provider import comfy_execution_seconds
        record = {"status": {"messages": [
            ["execution_start", {"prompt_id": "x", "timestamp": 1000}],
            ["execution_cached", {"nodes": [], "timestamp": 1100}],
            ["execution_success", {"prompt_id": "x", "timestamp": 10900}],
        ]}}
        self.assertEqual(comfy_execution_seconds(record), 9.9)
        self.assertIsNone(comfy_execution_seconds({}))

    def test_report_counts_rerolls_and_video_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            payload = json.loads((ROOT / "travel_project.example.json").read_text(encoding="utf-8"))
            payload["clips"] = payload["clips"][:2]
            project_file = temp / "project.json"
            project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            run = TravelRun(load_project(project_file), temp / "runs")
            provider = TimedProvider()
            run.generate_keyframes(provider)
            run.generate_keyframes(provider, force=True)
            run.submit_h3(FakeH3(), approve_keyframes=True)

            adapter = FakeCostAdapter(temp)
            first = "sydney-spring-01-clip-01-arrival"
            (temp / "review_ready" / first).mkdir(parents=True)
            (temp / "review_ready" / first / "report.json").write_text(json.dumps({
                "job_id": first, "status": "awaiting_human_review",
                "elapsed_seconds": 300.0, "estimated_gpu_cost_cny": 0.3,
                "qa": {"technical": {"duration": 5.0, "width": 1440, "height": 2560}},
            }), encoding="utf-8")

            report = run.cost_report(adapter)
            totals = report["totals"]
            self.assertEqual(totals["keyframe_images"], 4)          # 2 段 x (首抽 + force 重抽)
            self.assertEqual(totals["keyframe_gpu_seconds"], 40.0)
            self.assertEqual(totals["keyframe_cost_cny"], 0.04)     # 40s x 3.6/3600
            self.assertEqual(totals["videos_completed"], 1)
            self.assertEqual(totals["videos_pending_or_failed"], 1)
            self.assertEqual(totals["total_cost_cny"], 0.34)
            self.assertEqual(totals["cost_cny_per_delivered_second"], 0.068)
            self.assertEqual(report["clips"][1]["video"]["status"], "pending")
            self.assertTrue((run.run_dir / "cost_report.json").is_file())

            # 重跑第一段：新 job_id，旧成片成本仍然计入，交付秒数不重复计
            h3 = FakeH3()
            run.submit_h3(h3, rerun=("clip-01-arrival",))
            self.assertEqual([c[0] for c in h3.calls], ["clip-01-arrival"])
            state = json.loads(run.manifest_path.read_text(encoding="utf-8"))["clips"]["clip-01-arrival"]
            self.assertEqual(state["h3"]["job_id"], first + "-r2")
            self.assertEqual(state["h3_history"][0]["job_id"], first)
            again = run.cost_report(adapter)["totals"]
            self.assertEqual(again["videos_rerun_superseded"], 1)
            self.assertEqual(again["video_cost_cny"], 0.3)
            self.assertEqual(again["delivered_video_seconds"], 0)
            with self.assertRaises(ValueError):
                run.submit_h3(h3, rerun=("no-such-clip",))


if __name__ == "__main__":
    unittest.main()
