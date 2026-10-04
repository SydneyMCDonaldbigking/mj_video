#!/usr/bin/env python
"""英文 Instagram Reels 后期：按分镜板 reels/boards/<id>.json 把 H3 片段剪成成片。

只剪辑包装，不生成新画面。四种格式（每镜默认保留的帧区间见 FORMATS）：
  mood       钩子 + 每镜一句字幕 + 收藏引导
  list       编号清单，每镜一个「1/5」标签
  itinerary  一日行程，每镜一个时间贴纸
  asmr       几乎无字：地点 + 「sound on」，靠原环境声
文字支持彩色 emoji（Windows 自带 Segoe UI Emoji）；贴纸有地点、标签、时间、emoji、手绘箭头。
文字位置默认自动：分析每镜画面，挑细节少、不过亮的横带，避开主体。
不配旁白，保留原环境声；音乐在 Instagram 里用平台曲库加。

    python scripts/reels_post.py reels/boards/japan-nara-01.json
    python scripts/reels_post.py --all

输出到 deliveries/reels/：<id>-reels.mp4（1080×1920）、-cover.jpg、-caption.txt、
-qa.jpg（每镜一帧 + 结尾一帧的检查条）。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BOARDS = ROOT / "reels" / "boards"
SRC_W, SRC_H = 1440, 2560
W, H, FPS = 1080, 1920, 24
CLIP_FRAMES = 124                   # H3 每段 5 秒 = 124 帧
CENTER_X = 520                      # 略偏左，避开右侧点赞/评论按钮
MAX_TEXT_W = 860
SAFE_TOP, SAFE_BOTTOM = 250, 1480   # 顶部 Reels 标题栏、底部账号与文案区
ANCHOR_Y = {"top": 500, "upper": 740, "middle": 900, "lower": 1250}
ANCHOR_PRIOR = {"top": 0.0, "upper": 0.02, "middle": 0.05, "lower": 0.0}
CTA_SECONDS = 2.1
GRADE = "eq=contrast=1.03:saturation=1.07"
SS = 4                              # 圆角、图标的超采样倍数
GAP = 16                            # 贴纸与文字块的间距
EMOJI_FONT, EMOJI_SCALE = "seguiemj.ttf", 0.92

# 格式：首镜 / 中间镜 / 末镜默认保留的段内帧区间；钩子停留秒数（None = 整个首镜）。
# 末镜要留够 2.1 秒收藏引导 + 至少 2 秒字幕，所以清单和行程的末镜更长。
FORMATS = {
    "mood": {"first": (3, 113), "rest": (3, 113), "last": (3, 113), "hook_seconds": None, "caption": "caption"},
    "list": {"first": (8, 100), "rest": (20, 87), "last": (14, 118), "hook_seconds": 1.8, "caption": "item"},
    "itinerary": {"first": (8, 100), "rest": (12, 96), "last": (8, 118), "hook_seconds": None,
                  "caption": "caption"},
    "asmr": {"first": (4, 119), "rest": (4, 119), "last": (4, 119), "hook_seconds": None, "caption": "caption"},
}

# 样式名 → (字体文件, 基准字号, 行距倍数)
STYLES = {
    "hook": ("seguibl.ttf", 96, 1.10),
    "cta": ("seguibl.ttf", 84, 1.12),
    "item": ("seguibl.ttf", 84, 1.10),
    "caption": ("segoeuib.ttf", 72, 1.14),
    "sub": ("seguisb.ttf", 48, 1.25),
    "sticker": ("seguibl.ttf", 46, 1.0),
    "tag": ("seguibl.ttf", 44, 1.0),
    "number": ("seguibl.ttf", 58, 1.0),     # 清单的「1/5」编号
}

# 单码位 emoji（可带变体选择符 / 肤色）。本机 Pillow 没有 raqm，组合 emoji 和国旗拼不起来。
EMOJI_RE = re.compile(
    "[\u231a\u231b\u23e9-\u23fa\u2600-\u27bf\u2934\u2935\u2b05-\u2b55\u3030\u303d\u3297\u3299"
    "\U0001f000-\U0001faff][\ufe0f\U0001f3fb-\U0001f3ff]*")

ARROW_DIRS = {"down": (0, 1), "up": (0, -1), "left": (-1, 0), "right": (1, 0),
              "down-left": (-1, 1), "down-right": (1, 1), "up-left": (-1, -1), "up-right": (1, -1)}


class Fonts:
    def __init__(self, folder: Path):
        self.folder, self.cache = folder, {}

    def __call__(self, file: str, size: int) -> ImageFont.FreeTypeFont:
        if (file, size) not in self.cache:
            self.cache[file, size] = ImageFont.truetype(str(self.folder / file), size)
        return self.cache[file, size]


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


def _ease_out_back(x: float, k: float = 1.70158) -> float:
    x -= 1
    return 1 + (k + 1) * x ** 3 + k * x ** 2


def _color(c) -> tuple[int, int, int, int]:
    if isinstance(c, (tuple, list)):
        return tuple(c) if len(c) == 4 else (*c, 255)
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255


def _segments(line: str) -> list[tuple[str, bool]]:
    """'are {deer} 🦌' → [('are ', False), ('deer', True), (' 🦌', False)]"""
    return [(p[1:-1], True) if p.startswith("{") else (p, False)
            for p in re.split(r"(\{[^}]*\})", line) if p]


def _clean(emoji: str) -> str:
    """去掉变体选择符和肤色修饰：没有 raqm 时它们会多出空位或色块。"""
    return "".join(ch for ch in emoji if ch != "\ufe0f" and not "\U0001f3fb" <= ch <= "\U0001f3ff")


def _split_emoji(text: str) -> list[tuple[str, bool]]:
    out, pos = [], 0
    for m in EMOJI_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((_clean(m.group()), True))
        pos = m.end()
    out.append((text[pos:], False))
    return [(t, e) for t, e in out if t]


def _aa_mask(size: tuple[int, int], draw_fn) -> Image.Image:
    """在 SS 倍画布上画遮罩再缩小，得到抗锯齿的 L 遮罩。"""
    big = Image.new("L", (size[0] * SS, size[1] * SS), 0)
    draw_fn(ImageDraw.Draw(big), SS)
    return big.resize(size, Image.LANCZOS)


def _layer(size: tuple[int, int], color, mask: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", size, _color(color))
    layer.putalpha(mask)
    return layer


def _unpremultiply(img: Image.Image) -> Image.Image:
    """ImageDraw 往透明画布上画彩色字形，边缘颜色会被 alpha 预乘变暗，这里还原。"""
    a = np.asarray(img, dtype=np.float32)
    alpha = a[..., 3:4]
    rgb = np.where(alpha > 0, a[..., :3] * 255.0 / np.maximum(alpha, 1), 0)
    return Image.fromarray(np.concatenate([np.clip(rgb, 0, 255), alpha], -1).astype(np.uint8))


def _emoji_layer(size, placements, efont) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if not placements:
        return layer
    d = ImageDraw.Draw(layer)
    for x, y, emoji in placements:
        d.text((x, y), emoji, font=efont, anchor="ls", embedded_color=True)
    return _unpremultiply(layer)


def _glow(size, ink: Image.Image, scale: float) -> Image.Image:
    """文字用：柔光 + 贴身两层黑影，亮天空、白沙滩上也读得清。"""
    glow = ink.filter(ImageFilter.GaussianBlur(scale * 0.16)).point(lambda v: round(v * 0.62))
    tight = ImageChops.offset(ink, 0, max(1, round(scale * 0.03)))
    tight = tight.filter(ImageFilter.GaussianBlur(scale * 0.04)).point(lambda v: round(v * 0.5))
    return _layer(size, (0, 0, 0), ImageChops.lighter(glow, tight))


def _drop(size, mask: Image.Image, scale: float) -> Image.Image:
    """贴纸用：往下偏的柔和投影，像贴在画面上。"""
    soft = mask.filter(ImageFilter.GaussianBlur(scale * 0.3)).point(lambda v: round(v * 0.42))
    return _layer(size, (0, 0, 0), ImageChops.offset(soft, 0, round(scale * 0.14)))


class Typeset:
    """一行富文本：普通字、`{高亮}` 色块、彩色 emoji。x 坐标从行首算，y 相对基线。"""

    def __init__(self, fonts: Fonts, line: str, font: ImageFont.FreeTypeFont):
        self.font = font
        self.efont = fonts(EMOJI_FONT, max(8, round(font.size * EMOJI_SCALE)))
        size = font.size
        self.cap = -font.getbbox("H", anchor="ls")[1]
        top, bottom = self.efont.getbbox("\U0001f600", anchor="ls")[1::2]
        self.emoji_dy = -self.cap / 2 - (top + bottom) / 2      # emoji 中心对齐大写字母中线
        self.emoji_half = (bottom - top) / 2
        pad = round(size * 0.2)
        self.runs: list[tuple[float, str, str]] = []            # (x, 文本, text/mark/emoji)
        self.boxes: list[tuple[float, float]] = []
        x = 0.0
        for seg, marked in _segments(line):
            x0 = x
            if marked:
                x += pad
            for chunk, is_emoji in _split_emoji(seg):
                self.runs.append((x, chunk, "emoji" if is_emoji else "mark" if marked else "text"))
                x += (self.efont if is_emoji else font).getlength(chunk)
            if marked:
                x += pad
                self.boxes.append((x0, x))
        self.width = x
        self.has_emoji = any(kind == "emoji" for _, _, kind in self.runs)
        self.box_top, self.box_bottom = -self.cap - round(size * 0.2), round(size * 0.28)

    def render(self, size, ox: float, base: float):
        """返回 (白字遮罩, 高亮字遮罩, 高亮块遮罩, emoji 图层)，(ox, base) 是行首基线位置。"""
        plain, marked = Image.new("L", size, 0), Image.new("L", size, 0)
        draws = {"text": ImageDraw.Draw(plain), "mark": ImageDraw.Draw(marked)}
        emoji = []
        for x, chunk, kind in self.runs:
            if kind == "emoji":
                emoji.append((ox + x, base + self.emoji_dy, chunk))
            else:
                draws[kind].text((ox + x, base), chunk, font=self.font, anchor="ls", fill=255)
        boxes = Image.new("L", size, 0)
        if self.boxes:
            r = self.font.size * 0.18

            def draw_boxes(d, k):
                for x0, x1 in self.boxes:
                    d.rounded_rectangle((round((ox + x0) * k), round((base + self.box_top) * k),
                                         round((ox + x1) * k), round((base + self.box_bottom) * k)),
                                        radius=round(r * k), fill=255)
            boxes = _aa_mask(size, draw_boxes)
        return plain, marked, boxes, _emoji_layer(size, emoji, self.efont)


def text_line(fonts: Fonts, line: str, font, accent, accent_text) -> tuple[Image.Image, float]:
    """一行字幕的精灵图：白字 + 主题色高亮块 + 彩色 emoji，带柔光阴影。"""
    ts = Typeset(fonts, line, font)
    m = round(font.size * 0.5)
    w, h = math.ceil(ts.width) + 2 * m, ts.box_bottom - ts.box_top + 2 * m
    plain, marked, boxes, emoji = ts.render((w, h), m, m - ts.box_top)
    ink = ImageChops.lighter(ImageChops.lighter(plain, boxes), emoji.getchannel("A"))
    sprite = _glow((w, h), ink, font.size)
    if ts.boxes:
        sprite.alpha_composite(_layer((w, h), accent, boxes))
        sprite.alpha_composite(_layer((w, h), accent_text, marked))
    sprite.alpha_composite(_layer((w, h), (255, 255, 255), plain))
    sprite.alpha_composite(emoji)
    return sprite, ts.width


def pill(fonts: Fonts, text: str, *, style: str = "sticker", bg="#FFFFFF", fg="#141414",
         roundness: float = 1.0, angle: float = -4.0) -> Image.Image:
    """胶囊贴纸：白底黑字（或主题色底），可带 emoji，略微倾斜。"""
    font = fonts(*STYLES[style][:2])
    ts = Typeset(fonts, text, font)
    size = font.size
    pad_x, pad_y = round(size * 0.6), round(size * 0.42)
    half = max(ts.cap / 2, ts.emoji_half if ts.has_emoji else 0.0) + pad_y
    m = round(size * 0.8)
    bw, bh = math.ceil(ts.width) + 2 * pad_x, math.ceil(2 * half)
    w, h = bw + 2 * m, bh + 2 * m
    radius = bh / 2 * roundness

    def shape(d, k):
        d.rounded_rectangle((m * k, m * k, (m + bw) * k, (m + bh) * k), radius=round(radius * k), fill=255)

    mask = _aa_mask((w, h), shape)
    plain, marked, _, emoji = ts.render((w, h), m + pad_x, m + half + ts.cap / 2)
    sprite = _drop((w, h), mask, size)
    sprite.alpha_composite(_layer((w, h), bg, mask))
    sprite.alpha_composite(_layer((w, h), fg, ImageChops.lighter(plain, marked)))
    sprite.alpha_composite(emoji)
    return sprite.rotate(angle, resample=Image.BICUBIC, expand=True) if angle else sprite


def emoji_sprite(fonts: Fonts, emoji: str, size: int) -> Image.Image:
    efont = fonts(EMOJI_FONT, size)
    emoji = _clean(emoji)
    left, top, right, bottom = efont.getbbox(emoji, anchor="ls")
    m = round(size * 0.35)
    w, h = right - left + 2 * m, bottom - top + 2 * m
    layer = _emoji_layer((w, h), [(m - left, m - top, emoji)], efont)
    sprite = _drop((w, h), layer.getchannel("A"), size * 0.6)
    sprite.alpha_composite(layer)
    return sprite


def bookmark(size: int, filled: bool) -> Image.Image:
    """Instagram 收藏图标：先出空心，再变实心。"""
    bw, bh = size * 0.78, float(size)
    m = round(size * 0.35)
    w, h = math.ceil(bw) + 2 * m, math.ceil(bh) + 2 * m

    def draw(d, k):
        pts = [(m, m), (m + bw, m), (m + bw, m + bh), (m + bw / 2, m + bh * 0.72), (m, m + bh)]
        pts = [(x * k, y * k) for x, y in pts]
        if filled:
            d.polygon(pts, fill=255)
        d.line(pts + pts[:2], fill=255, width=round(size * 0.085 * k), joint="curve")

    mask = _aa_mask((w, h), draw)
    sprite = _glow((w, h), mask, size * 0.6)
    sprite.alpha_composite(_layer((w, h), (255, 255, 255), mask))
    return sprite


def arrow_frames(direction: str = "down", length: int = 200, steps: int = 8) -> list[Image.Image]:
    """手绘感白色箭头，逐帧画出来（draw-on）。"""
    dx, dy = ARROW_DIRS[direction]
    norm = math.hypot(dx, dy)
    dx, dy = dx / norm, dy / norm
    px, py = -dy, dx
    pts = [(dx * length * t + px * math.sin(math.pi * t) * length * 0.18,
            dy * length * t + py * math.sin(math.pi * t) * length * 0.18)
           for t in (i / 40 for i in range(41))]
    ang = math.atan2(pts[-1][1] - pts[-4][1], pts[-1][0] - pts[-4][0])
    head = length * 0.2
    wings = [(pts[-1][0] - head * math.cos(ang + s), pts[-1][1] - head * math.sin(ang + s))
             for s in (0.5, -0.5)]
    xs, ys = [p[0] for p in pts + wings], [p[1] for p in pts + wings]
    m, stroke = 40, 11
    w, h = math.ceil(max(xs) - min(xs)) + 2 * m, math.ceil(max(ys) - min(ys)) + 2 * m
    ox, oy = m - min(xs), m - min(ys)
    frames = []
    for step in range(1, steps + 1):
        upto = max(2, round(len(pts) * step / steps))

        def draw(d, k, upto=upto, final=step == steps):
            line = [((x + ox) * k, (y + oy) * k) for x, y in pts[:upto]]
            r = stroke * k / 2
            d.line(line, fill=255, width=round(stroke * k), joint="curve")
            ends = [line[0], line[-1]]
            if final:
                for wx, wy in wings:
                    end = ((wx + ox) * k, (wy + oy) * k)
                    d.line([line[-1], end], fill=255, width=round(stroke * k))
                    ends.append(end)
            for x, y in ends:
                d.ellipse((x - r, y - r, x + r, y + r), fill=255)

        mask = _aa_mask((w, h), draw)
        sprite = _glow((w, h), mask, 50)
        sprite.alpha_composite(_layer((w, h), (255, 255, 255), mask))
        frames.append(sprite)
    return frames


def _clock(text: str) -> str:
    """'7:00 AM' → 🕖，半点用半点表盘。"""
    m = re.match(r"\s*(\d{1,2})(?::(\d{2}))?", text)
    if not m:
        return "\U0001f552"
    hour = int(m.group(1)) % 12 or 12
    half = m.group(2) is not None and 15 <= int(m.group(2)) < 45
    return chr((0x1F55C if half else 0x1F550) + hour - 1)


def sticker_sprite(fonts: Fonts, spec: dict, accent: str, accent_text: str):
    """返回 (精灵图或逐帧列表, 动效名)。"""
    kind = spec["type"]
    angle = spec.get("angle")
    if kind == "location":
        return pill(fonts, "\U0001f4cd " + spec["text"].upper(), angle=-4 if angle is None else angle), "sticker"
    if kind == "time":
        return pill(fonts, f"{_clock(spec['text'])} {spec['text'].upper()}",
                    angle=-3 if angle is None else angle), "sticker"
    if kind == "sound":
        return pill(fonts, "\U0001f50a SOUND ON", angle=3 if angle is None else angle), "sticker"
    if kind == "tag":
        return pill(fonts, spec["text"].upper(), style=spec.get("style", "tag"), bg=accent, fg=accent_text,
                    roundness=0.35, angle=5 if angle is None else angle), "sticker"
    if kind == "emoji":
        return emoji_sprite(fonts, spec["emoji"], int(spec.get("size", 150))), "float"
    if kind == "arrow":
        return arrow_frames(spec.get("dir", "down"), int(spec.get("length", 200))), "draw"
    raise ValueError(f"未知贴纸类型: {kind}")


def _solid_box(sprite) -> tuple[float, float, float, float]:
    """精灵图里实心部分（不算阴影）相对中心的包围盒：(dx, dy, 宽, 高)。"""
    img = sprite[-1] if isinstance(sprite, list) else sprite
    x0, y0, x1, y1 = img.getchannel("A").point(lambda v: 255 if v > 160 else 0).getbbox() or (0, 0, *img.size)
    return (x0 + x1) / 2 - img.width / 2, (y0 + y1) / 2 - img.height / 2, x1 - x0, y1 - y0


@dataclass
class Item:
    sprite: object          # Image；逐帧动画（箭头）是 Image 列表
    cx: float
    cy: float
    t_in: float
    t_out: float
    motion: str = "pop"     # pop 弹出 / slide 上滑 / bounce 回弹 / sticker 贴纸弹入 / float 漂浮 / draw 画出
    phase: float = 0.0


@dataclass
class Block:
    items: list
    left: float
    top: float
    right: float
    bottom: float


def _fit(fonts: Fonts, style: str, lines: list[str]) -> ImageFont.FreeTypeFont:
    file, size, _ = STYLES[style]
    font = fonts(file, size)
    widest = max(Typeset(fonts, line, font).width for line in lines)
    return fonts(file, int(size * MAX_TEXT_W / widest)) if widest > MAX_TEXT_W else font


def text_block(fonts: Fonts, style: str, spec: dict | None, anchor_y: float, t_in: float, t_out: float,
               accent: str, accent_text: str, reserve_top: float = 0, reserve_bottom: float = 0) -> Block:
    """主行逐行弹出，可选小字副行；整块垂直居中于锚点并夹在安全区内。"""
    if not spec or not spec.get("lines"):
        return Block([], CENTER_X, anchor_y, CENTER_X, anchor_y)
    lines, sub = spec["lines"], spec.get("sub")
    font = _fit(fonts, style, lines)
    pitch = font.size * STYLES[style][2]
    rows, widths = [], []
    for i, line in enumerate(lines):
        sprite, width = text_line(fonts, line, font, accent, accent_text)
        rows.append((sprite, i * pitch, t_in + i * 0.10))
        widths.append(width)
    bottom = (len(lines) - 1) * pitch + font.size * 0.55
    if sub:
        sfont = _fit(fonts, "sub", [sub])
        offset = (len(lines) - 1) * pitch + (pitch + sfont.size * STYLES["sub"][2]) * 0.5
        sprite, width = text_line(fonts, sub, sfont, accent, accent_text)
        rows.append((sprite, offset, t_in + len(lines) * 0.10 + 0.25))
        widths.append(width)
        bottom = offset + sfont.size * 0.55
    top = -font.size * 0.55
    first = anchor_y - (top + bottom) / 2
    first = max(first, SAFE_TOP + reserve_top - top)
    first = min(first, SAFE_BOTTOM - reserve_bottom - bottom)
    half = max(widths) / 2
    return Block([Item(sprite, CENTER_X, first + off, start, t_out) for sprite, off, start in rows],
                 CENTER_X - half, first + top, CENTER_X + half, first + bottom)


def add_block(fonts: Fonts, style: str, spec: dict | None, anchor: str, t_in: float, t_out: float,
              stickers: list[dict], accent: str, accent_text: str) -> list[Item]:
    """文字块 + 贴在它周围的贴纸。贴纸 pos：above / below / tl / tr / bl / br / [x, y]。"""
    made = []
    for spec_ in stickers:
        sprite, motion = sticker_sprite(fonts, spec_, accent, accent_text)
        made.append((spec_, sprite, motion, _solid_box(sprite)))
    reserve = {"above": 0.0, "below": 0.0}
    for spec_, _, _, (_, _, _, sh) in made:
        pos = spec_.get("pos", "above")
        if pos in reserve:
            reserve[pos] += sh + GAP
    block = text_block(fonts, style, spec, ANCHOR_Y[anchor], t_in, t_out, accent, accent_text,
                       reserve_top=reserve["above"], reserve_bottom=reserve["below"])
    items = list(block.items)
    up, down = block.top - (GAP if block.items else 0), block.bottom + (GAP if block.items else 0)
    for k, (spec_, sprite, motion, (dx, dy, sw, sh)) in enumerate(made):
        pos = spec_.get("pos", "above")
        if isinstance(pos, (list, tuple)):
            tx, ty = pos
        elif pos == "above":
            tx, ty = CENTER_X, up - sh / 2
            up -= sh + GAP
        elif pos == "below":
            tx, ty = CENTER_X, down + sh / 2
            down += sh + GAP
        else:
            tx = block.right + sw * 0.15 if pos[1] == "r" else block.left - sw * 0.15
            ty = block.top - sh * 0.05 if pos[0] == "t" else block.bottom + sh * 0.05
        tx = min(max(tx, sw / 2 + 24), W - sw / 2 - 24)
        ty = min(max(ty, SAFE_TOP - 60 + sh / 2), SAFE_BOTTOM + 80 - sh / 2)
        delay = spec_.get("delay", 0.22 + 0.12 * k)
        items.append(Item(sprite, tx - dx, ty - dy, t_in + delay, t_out, motion, phase=1.3 * k))
    return items


def build_items(board: dict, segs: list[tuple[float, float]], anchors: list[str],
                fonts: Fonts) -> tuple[list[Item], float]:
    fmt_name = board["format"]
    fmt = FORMATS[fmt_name]
    accent, accent_text = board["accent"], board.get("accent_text", "#FFFFFF")
    shots, total = board["shots"], segs[-1][1]
    n, t_cta = len(shots), total - CTA_SECONDS
    cap_style = fmt["caption"]
    hook = board.get("hook") or {}
    location = {"type": "location", "text": board.get("pin") or board["destination"]}
    items: list[Item] = []

    def block(style, spec, k, t_in, t_out, stickers):
        items.extend(add_block(fonts, style, spec, anchors[k], t_in, t_out, stickers, accent, accent_text))

    def shot_end(k):
        return min(segs[k][1], t_cta) if k == n - 1 else segs[k][1]

    first_stickers = [location] + hook.get("stickers", []) + shots[0].get("stickers", [])
    if fmt_name == "list":
        hook_end = min(segs[0][1], fmt["hook_seconds"])
        block("hook", hook, 0, -0.06, hook_end, [location] + hook.get("stickers", []))
        for k, shot in enumerate(shots):
            start = hook_end + 0.05 if k == 0 else segs[k][0] + 0.10
            tag = {"type": "tag", "text": f"{k + 1}/{n}", "style": "number", "delay": 0.0}
            block(cap_style, shot.get("text"), k, start, shot_end(k), [tag] + shot.get("stickers", []))
    elif fmt_name == "itinerary":
        time0 = [{"type": "time", "text": shots[0]["label"], "pos": "below"}] if shots[0].get("label") else []
        block("hook", hook, 0, -0.06, shot_end(0), first_stickers + time0)
        for k in range(1, n):
            label = [{"type": "time", "text": shots[k]["label"], "delay": 0.0}] if shots[k].get("label") else []
            block(cap_style, shots[k].get("text"), k, segs[k][0] + 0.10, shot_end(k),
                  label + shots[k].get("stickers", []))
    else:
        if fmt_name == "asmr":
            first_stickers = [{"type": "sound", "delay": 0.0}] + first_stickers
        block("hook", hook, 0, -0.06, shot_end(0), first_stickers)
        for k in range(1, n):
            block(cap_style, shots[k].get("text"), k, segs[k][0] + 0.10, shot_end(k),
                  shots[k].get("stickers", []))

    cta = add_block(fonts, "cta", board["cta"], "middle", t_cta + 0.20, total + 1, [],
                    accent, accent_text)
    items += cta
    icon_y = min(it.cy for it in cta) - 150
    items.append(Item(bookmark(96, False), CENTER_X, icon_y, t_cta + 0.05, t_cta + 0.90))
    items.append(Item(bookmark(96, True), CENTER_X, icon_y, t_cta + 0.80, total + 1, "bounce"))
    return items, t_cta


def draw_item(frame: Image.Image, it: Item, t: float) -> None:
    if not it.t_in <= t < it.t_out:
        return
    p = t - it.t_in
    alpha, scale, dy, angle = _clamp(p / 0.12), 1.0, 0.0, 0.0
    sprite = it.sprite
    if it.motion == "pop":
        scale = 0.82 + 0.18 * _ease_out_back(_clamp(p / 0.28))
    elif it.motion == "bounce":
        alpha, scale = 1.0, 1.35 - 0.35 * _ease_out_back(_clamp(p / 0.30))
    elif it.motion == "slide":
        alpha, dy = _clamp(p / 0.25), 22 * (1 - _ease_out_cubic(_clamp(p / 0.35)))
    elif it.motion in ("sticker", "float"):
        alpha, scale = _clamp(p / 0.08), 0.3 + 0.7 * _ease_out_back(_clamp(p / 0.32), 2.4)
        if it.motion == "float":
            dy = 7 * math.sin(2 * math.pi * p / 1.9 + it.phase)
            angle = 4 * math.sin(2 * math.pi * p / 2.7 + it.phase)
    elif it.motion == "draw":
        alpha = 1.0
        sprite = sprite[min(len(sprite) - 1, int(p / 0.42 * len(sprite)))]
    leave = _clamp((it.t_out - t) / 0.16)                   # 离场：0.16 秒淡出微缩
    alpha *= leave
    scale *= 0.96 + 0.04 * leave
    if alpha <= 0.01 or scale <= 0.02:
        return
    if abs(scale - 1) > 1e-3:
        sprite = sprite.resize((max(1, round(sprite.width * scale)),
                                max(1, round(sprite.height * scale))), Image.BICUBIC)
    if abs(angle) > 0.05:
        sprite = sprite.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 0.999:
        sprite = sprite.copy()
        sprite.putalpha(sprite.getchannel("A").point(lambda v: round(v * alpha)))
    frame.paste(sprite, (round(it.cx - sprite.width / 2), round(it.cy + dy - sprite.height / 2)), sprite)


def _zoom_factor(k: int, j: int, n: int, last: int) -> float:
    """每镜开头从放大状态回弹（首镜轻一点），切点前 3 帧往里冲，接成推拉转场。"""
    punch, settle = (0.06, 10) if k == 0 else (0.10, 8)
    z = 1 + punch * (1 - _ease_out_cubic(_clamp(j / settle)))
    if k < last and j >= n - 3:
        z *= 1 + 0.06 * ((j - n + 4) / 3) ** 2
    return z


def _zoomed(src: Image.Image, z: float) -> Image.Image:
    cw, ch = SRC_W / z, SRC_H / z
    box = ((SRC_W - cw) / 2, (SRC_H - ch) / 2, (SRC_W + cw) / 2, (SRC_H + ch) / 2)
    return src.resize((W, H), Image.LANCZOS, box=box)


def shot_sources(board: dict) -> list[tuple[Path, int, int]]:
    """每镜的 (源文件, 起始帧, 结束帧)。默认读 reels_make 下载的 runs/<id>/clips/<clip_id>.mp4；
    旧成片只有拼好的 3×124 帧整片时，用 board["source"] 按段偏移。"""
    fmt = FORMATS[board["format"]]
    out = []
    for k, shot in enumerate(board["shots"]):
        default = fmt["first"] if k == 0 else fmt["last"] if k == len(board["shots"]) - 1 else fmt["rest"]
        a, b = shot.get("trim") or default
        if board.get("source"):
            src, offset = ROOT / board["source"], k * CLIP_FRAMES
        else:
            src, offset = ROOT / "runs" / board["id"] / "clips" / f"{shot['clip_id']}.mp4", 0
        if not src.is_file():
            raise FileNotFoundError(f"{board['id']}: 找不到 {src}")
        out.append((src, a + offset, b + offset))
    return out


def analysis_frames(sources: list[tuple[Path, int, int]]) -> list[list[np.ndarray]]:
    """每镜取首、中、尾 3 帧的 108×192 灰度图，给自动选文字位置用。"""
    wanted: dict[Path, set[int]] = {}
    for src, a, b in sources:
        wanted.setdefault(src, set()).update((a, (a + b) // 2, b - 1))
    frames: dict[tuple[Path, int], np.ndarray] = {}
    for src, idx in wanted.items():
        order = sorted(idx)
        expr = "+".join(f"eq(n\\,{i})" for i in order)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf",
                              f"select='{expr}',scale=108:192,format=gray", "-vsync", "0",
                              "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        arr = np.frombuffer(raw, np.uint8).reshape(-1, 192, 108).astype(np.float32) / 255
        frames.update({(src, i): arr[j] for j, i in enumerate(order[:len(arr)])})
    return [[frames[src, i] for i in (a, (a + b) // 2, b - 1) if (src, i) in frames]
            for src, a, b in sources]


def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    r = int(3 * sigma)
    k = np.exp(-np.arange(-r, r + 1) ** 2 / (2 * sigma * sigma))
    k /= k.sum()
    for axis in (0, 1):
        a = np.apply_along_axis(lambda v: np.convolve(np.pad(v, r, mode="reflect"), k, "valid"), axis, a)
    return a


def _saliency(gray: np.ndarray) -> np.ndarray:
    """谱残差显著性（Hou & Zhang 2007），192×108 灰度 → 96×54 显著图。"""
    small = gray.reshape(96, 2, 54, 2).mean((1, 3))
    spec = np.fft.fft2(small)
    log_amp = np.log(np.abs(spec) + 1e-6)
    sal = np.abs(np.fft.ifft2(np.exp(log_amp - _blur(log_amp, 1.0) + 1j * np.angle(spec)))) ** 2
    sal = _blur(sal, 2.5)
    return sal / sal.max()


def auto_anchor(frames: list[np.ndarray]) -> str:
    """后备方案：挑显著性最低、不过亮的横带放字。
    在 7 条日本片上 21 镜对了 14 镜，大块主体（富士山、塔、鹿）常认不出来，
    所以分镜板里由导演在关键帧审核时写死 anchor，这里只兜底。"""
    sal = np.max([_saliency(f) for f in frames], axis=0)

    def score(name: str) -> float:
        y = ANCHOR_Y[name]
        bright = max(float(np.percentile(f[(y - 190) // 10:(y + 190) // 10], 80)) for f in frames)
        return float(sal[(y - 190) // 20:(y + 190) // 20].mean()) + 0.3 * max(0.0, bright - 0.75) \
            + ANCHOR_PRIOR[name]
    return min(ANCHOR_Y, key=score)


def _audio_graph(sources: list[tuple[Path, int, int]], inputs: list[Path],
                 has_audio: dict[Path, bool], total: float) -> str:
    uses = Counter(src for src, _, _ in sources if has_audio[src])
    parts, pads = [], {}
    for src, count in uses.items():
        idx = inputs.index(src) + 1
        if count == 1:
            pads[src] = [f"[{idx}:a]"]
        else:
            pads[src] = [f"[a{idx}_{j}]" for j in range(count)]
            parts.append(f"[{idx}:a]asplit={count}" + "".join(pads[src]))
    fmt = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    for k, (src, a, b) in enumerate(sources):
        d = (b - a) / FPS
        if has_audio[src]:
            parts.append(f"{pads[src].pop(0)}atrim=start={a / FPS:.5f}:end={b / FPS:.5f},"
                         f"asetpts=PTS-STARTPTS,{fmt},afade=t=in:d=0.03,"
                         f"afade=t=out:st={d - 0.03:.5f}:d=0.03[c{k}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.5f},{fmt}[c{k}]")
    parts.append("".join(f"[c{k}]" for k in range(len(sources))) + f"concat=n={len(sources)}:v=0:a=1,"
                 "loudnorm=I=-18:TP=-1.5:LRA=11,aresample=48000,"
                 "aformat=sample_rates=48000:channel_layouts=stereo,"       # 4.3 的 loudnorm 不带声道布局
                 f"afade=t=out:st={total - 0.8:.3f}:d=0.8[aout]")
    return ";".join(parts)


def _has_audio(src: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", str(src)], capture_output=True, text=True)
    return bool(out.stdout.strip())


def load_board(path: Path) -> dict:
    board = json.loads(Path(path).read_text(encoding="utf-8"))
    name = board.get("id", Path(path).stem)
    if board.get("format") not in FORMATS:
        raise ValueError(f"{name}: format 只能是 {sorted(FORMATS)}")
    if not 2 <= len(board.get("shots", [])) <= 8:
        raise ValueError(f"{name}: shots 需要 2–8 个")
    if board["format"] != "asmr" and not (board.get("hook") or {}).get("lines"):
        raise ValueError(f"{name}: 缺少 hook.lines")
    if not (board.get("cta") or {}).get("lines"):
        raise ValueError(f"{name}: 缺少 cta.lines")
    texts = json.dumps([board.get("hook"), board.get("cta"), board["shots"]], ensure_ascii=False)
    if "\u200d" in texts or re.search("[\U0001f1e6-\U0001f1ff]", texts):
        raise ValueError(f"{name}: 不支持组合 emoji 和国旗（本机 Pillow 拼不起来），换单个 emoji")
    for anchor in [(board.get("hook") or {}).get("anchor")] + [s.get("anchor") for s in board["shots"]]:
        if anchor not in (None, "auto", *ANCHOR_Y):
            raise ValueError(f"{name}: anchor 只能是 auto / {' / '.join(ANCHOR_Y)}")
    return board


def render(board_path: Path, out_dir: Path, fonts: Fonts) -> Path:
    started = time.monotonic()
    board = load_board(board_path)
    name = board["id"]
    sources = shot_sources(board)
    segs, t = [], 0.0
    for _, a, b in sources:
        segs.append((t, t + (b - a) / FPS))
        t += (b - a) / FPS
    total = segs[-1][1]

    looks = analysis_frames(sources)
    wanted = [(board.get("hook") or {}).get("anchor")] + [s.get("anchor") for s in board["shots"][1:]]
    anchors = [a if a in ANCHOR_Y else auto_anchor(look) for a, look in zip(wanted, looks)]
    items, t_cta = build_items(board, segs, anchors, fonts)

    inputs = list(dict.fromkeys(src for src, _, _ in sources))
    has_audio = {src: _has_audio(src) for src in inputs}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}-reels.mp4"
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         *[arg for src in inputs for arg in ("-i", str(src))],
         "-filter_complex", f"[0:v]scale={W}:{H}:out_color_matrix=bt709:out_range=tv,"
                            f"format=yuv420p[v];{_audio_graph(sources, inputs, has_audio, total)}",
         "-map", "[v]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-level", "4.2",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)],
        stdin=subprocess.PIPE)

    frame_bytes, written, last = SRC_W * SRC_H * 3, 0, len(sources) - 1
    for k, (src, a, b) in enumerate(sources):
        dec = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-i", str(src), "-vf",
             f"trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,{GRADE},"
             f"scale={SRC_W}:{SRC_H}:in_color_matrix=bt709:in_range=tv",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        for j in range(b - a):
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                raise RuntimeError(f"{name}: {src.name} 只读到 {a + j} 帧，trim 越界")
            t = written / FPS
            frame = _zoomed(Image.frombuffer("RGB", (SRC_W, SRC_H), buf, "raw", "RGB", 0, 1),
                            _zoom_factor(k, j, b - a, last))
            if t >= t_cta:                                    # 结尾压暗给收藏引导让位
                level = 1 - 0.45 * _ease_out_cubic(_clamp((t - t_cta) / 0.4))
                frame = frame.point([round(v * level) for v in range(256)] * 3)
            for it in items:
                draw_item(frame, it, t)
            enc.stdin.write(frame.tobytes())
            written += 1
        dec.stdout.close()
        dec.wait()
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"{name}: ffmpeg 编码失败")

    cover = out_dir / f"{name}-cover.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.4", "-i", str(out),
                    "-frames:v", "1", "-q:v", "2", str(cover)], check=True)
    picks = [round(1.2 * FPS)] + [round((s + min(1.4, (e - s) * 0.6)) * FPS) for s, e in segs[1:]]
    if board["format"] == "list":
        picks.insert(1, round((FORMATS["list"]["hook_seconds"] + 0.6) * FPS))
    picks.append(written - 6)
    expr = "+".join(f"eq(n\\,{p})" for p in picks)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-vf",
                    f"select='{expr}',scale=240:-1,tile={len(picks)}x1:padding=4:color=white",
                    "-frames:v", "1", "-vsync", "0", "-q:v", "3", str(out_dir / f"{name}-qa.jpg")], check=True)
    caption = out_dir / f"{name}-caption.txt"
    caption.write_text(board["ig_caption"].rstrip() + "\n\n" + " ".join(board["hashtags"]) + "\n",
                       encoding="utf-8")
    print(f"{name}: {board['format']} · {written} 帧 / {total:.2f} 秒 · 文字位置 {'/'.join(anchors)}"
          f" → {out.name}（{time.monotonic() - started:.0f} 秒）")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="按分镜板把 H3 片段剪成英文 Instagram Reels")
    ap.add_argument("boards", nargs="*", type=Path, help="reels/boards/<id>.json")
    ap.add_argument("--all", action="store_true", help="渲染 reels/boards/ 下全部")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "deliveries" / "reels")
    ap.add_argument("--fonts-dir", type=Path, default=Path("C:/Windows/Fonts"))
    args = ap.parse_args()
    boards = sorted(BOARDS.glob("*.json")) if args.all else args.boards
    if not boards:
        ap.error("指定分镜板文件，或用 --all")
    fonts = Fonts(args.fonts_dir)
    for board in boards:
        render(board, args.out_dir, fonts)


if __name__ == "__main__":
    main()
