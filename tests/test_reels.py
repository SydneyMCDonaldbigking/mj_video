from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import reels_make  # noqa: E402
import reels_post  # noqa: E402
from travel_workflow.project import load_project  # noqa: E402


def _board(**overrides) -> dict:
    board = {
        "id": "test-reel-01", "format": "mood", "destination": "Hakone, Japan", "pin": "Hakone, Japan",
        "season": "late autumn", "time_of_day": "misty morning", "accent": "#FF8A5B",
        "hook": {"lines": ["Hot spring", "with a {view} ♨️"]},
        "shots": [
            {"clip_id": "clip-01-a", "place": "lake", "keyframe_prompt": "a lake", "motion_prompt": "mist drifts",
             "seed": 1},
            {"clip_id": "clip-02-b", "place": "onsen", "keyframe_prompt": "an onsen", "motion_prompt": "steam rises",
             "seed": 2, "text": {"lines": ["Then this"]}},
        ],
        "cta": {"lines": ["Save this for your", "{Japan trip}"]},
        "ig_caption": "caption", "hashtags": ["#hakone"],
    }
    board.update(overrides)
    return board


class ReelsTextTests(unittest.TestCase):
    def test_segments_split_highlight_and_emoji(self):
        self.assertEqual(reels_post._segments("are {deer} 🦌"),
                         [("are ", False), ("deer", True), (" 🦌", False)])
        self.assertEqual(reels_post._split_emoji(" ❄️ snow"), [(" ", False), ("❄", True), (" snow", False)])

    def test_clock_face_matches_time(self):
        self.assertEqual(reels_post._clock("7:00 AM"), "\U0001f556")
        self.assertEqual(reels_post._clock("7:30 pm"), "\U0001f562")
        self.assertEqual(reels_post._clock("12 noon"), "\U0001f55b")

    def test_board_rejects_zwj_emoji_and_bad_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            path.write_text(json.dumps(_board(hook={"lines": ["hi 👨‍👩‍👧"]})), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "组合 emoji"):
                reels_post.load_board(path)
            path.write_text(json.dumps(_board(hook={"lines": ["hi"], "anchor": "bottom"})), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "anchor"):
                reels_post.load_board(path)

    def test_auto_anchor_avoids_busy_band(self):
        rng = np.random.default_rng(0)
        frame = np.full((192, 108), 0.3, np.float32)
        frame[30:70, 20:90] = rng.random((40, 70))          # 顶部一块高显著性的"主体"
        self.assertNotEqual(reels_post.auto_anchor([frame]), "top")


class ReelsBoardTests(unittest.TestCase):
    def test_board_converts_to_valid_project(self):
        board = _board()
        board["shots"][1]["reference"] = "reels/refs/onsen.jpg"
        project = reels_make.board_to_project(board)
        self.assertEqual(project["clips"][1]["input_strategy"], "provided_reference")
        self.assertEqual(project["clips"][1]["reference"], "../reels/refs/onsen.jpg")
        self.assertEqual(project["audience"], reels_make.DEFAULT_AUDIENCE)
        project["clips"][1].update(input_strategy="auto_keyframe", reference=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps(project), encoding="utf-8")
            loaded = load_project(path)
        self.assertEqual([c.clip_id for c in loaded.clips], ["clip-01-a", "clip-02-b"])

    def test_list_format_trims_first_shot_longer(self):
        board = _board(format="list", source="deliveries/x.mp4")
        first, rest = reels_post.FORMATS["list"]["first"], reels_post.FORMATS["list"]["rest"]
        self.assertGreater(first[1] - first[0], rest[1] - rest[0])
        with self.assertRaises(FileNotFoundError):
            reels_post.shot_sources(board)


if __name__ == "__main__":
    unittest.main()
