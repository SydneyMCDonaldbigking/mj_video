#!/usr/bin/env python
"""把 3×5 秒旅游成片剪成英文 Instagram Reels 版（只剪辑包装，不生成新画面）。

每段掐头去尾提节奏，切点做推拉转场；第 1 段叠开场钩子和地点图钉，第 2、3 段各一句字幕，
最后约 2 秒压暗出收藏引导。保留原环境声，不配旁白；音乐在 Instagram 里用平台曲库加。

    python scripts/reels_post.py post/reels/japan-nara-01.json
    python scripts/reels_post.py --all

输出到 deliveries/reels/：<id>-reels.mp4（1080×1920）、<id>-cover.jpg、<id>-caption.txt。
文案写在 post/reels/<id>.json，`{词}` 表示用主题色高亮。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SRC_W, SRC_H = 1440, 2560
W, H, FPS = 1080, 1920, 24
CLIP_FRAMES = 124                   # 成片每段 5 秒 = 124 帧
TRIM = (3, 113)                     # 每段默认保留的段内帧区间，约 4.6 秒
CENTER_X = 520                      # 略偏左，避开右侧点赞/评论按钮
MAX_TEXT_W = 860
SAFE_TOP, SAFE_BOTTOM = 250, 1480   # 顶部 Reels 标题栏、底部账号与文案区
ANCHOR_Y = {"top": 500, "upper": 740, "middle": 900, "lower": 1250}
CTA_SECONDS = 2.1
GRADE = "eq=contrast=1.03:saturation=1.07"
SS = 4                              # 圆角和图标的超采样倍数

# 样式名 → (字体文件, 基准字号, 行距倍数)
STYLES = {
    "hook": ("seguibl.ttf", 96, 1.10),
    "cta": ("seguibl.ttf", 84, 1.12),
    "caption": ("segoeuib.ttf", 72, 1.14),
    "sub": ("seguisb.ttf", 48, 1.25),
    "pin": ("seguisb.ttf", 46, 1.0),
}


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


def _rgba(color: str) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255


def _segments(line: str) -> list[tuple[str, bool]]:
    """'are {deer}' → [('are ', False), ('deer', True)]"""
    return [(p[1:-1], True) if p.startswith("{") else (p, False)
            for p in re.split(r"(\{[^}]*\})", line) if p]


def _aa_mask(size: tuple[int, int], draw_fn) -> Image.Image:
    """在 SS 倍画布上画遮罩再缩小，得到抗锯齿的 L 遮罩。"""
    big = Image.new("L", (size[0] * SS, size[1] * SS), 0)
    draw_fn(ImageDraw.Draw(big), SS)
    return big.resize(size, Image.LANCZOS)


def _layer(size: tuple[int, int], color, mask: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", size, color)
    layer.putalpha(mask)
    return layer


def _shadow(size: tuple[int, int], ink: Image.Image, scale: float) -> Image.Image:
    """柔光 + 贴身两层黑影，亮天空、白沙滩上也读得清。"""
    glow = ink.filter(ImageFilter.GaussianBlur(scale * 0.16)).point(lambda v: round(v * 0.62))
    tight = ImageChops.offset(ink, 0, max(1, round(scale * 0.03)))
    tight = tight.filter(ImageFilter.GaussianBlur(scale * 0.04)).point(lambda v: round(v * 0.5))
    return _layer(size, (0, 0, 0, 255), ImageChops.lighter(glow, tight))


def _line_width(line: str, font: ImageFont.FreeTypeFont) -> float:
    pad = round(font.size * 0.2)
    return sum(font.getlength(t) + (2 * pad if hl else 0) for t, hl in _segments(line))


def text_line(line: str, font: ImageFont.FreeTypeFont, accent: str, accent_text: str) -> Image.Image:
    """一行文字的精灵图：白字，`{高亮}` 部分是主题色圆角底块。"""
    size = font.size
    pad = round(size * 0.2)
    cap = -font.getbbox("H", anchor="ls")[1]
    top, bottom = -cap - round(size * 0.2), round(size * 0.28)   # 高亮块相对基线的上下沿
    segs = _segments(line)
    widths = [font.getlength(t) + (2 * pad if hl else 0) for t, hl in segs]
    m = round(size * 0.5)
    w, h = math.ceil(sum(widths)) + 2 * m, bottom - top + 2 * m
    base = m - top
    boxes, runs, x = [], [], float(m)
    for (text, hl), sw in zip(segs, widths):
        if hl:
            boxes.append((x, base + top, x + sw, base + bottom))
        runs.append((x + (pad if hl else 0), text, hl))
        x += sw

    def draw_boxes(d, k):
        for x0, y0, x1, y1 in boxes:
            d.rounded_rectangle((round(x0 * k), round(y0 * k), round(x1 * k), round(y1 * k)),
                                radius=round(size * 0.18 * k), fill=255)

    def ink(highlighted: bool) -> Image.Image:
        mask = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(mask)
        for rx, text, hl in runs:
            if hl == highlighted:
                d.text((rx, base), text, font=font, anchor="ls", fill=255)
        return mask

    box_mask, plain, marked = _aa_mask((w, h), draw_boxes), ink(False), ink(True)
    sprite = _shadow((w, h), ImageChops.lighter(plain, box_mask), size)
    if boxes:
        sprite.alpha_composite(_layer((w, h), _rgba(accent), box_mask))
        sprite.alpha_composite(_layer((w, h), _rgba(accent_text), marked))
    sprite.alpha_composite(_layer((w, h), (255, 255, 255, 255), plain))
    return sprite


def pin_label(text: str, font: ImageFont.FreeTypeFont, color: str) -> Image.Image:
    """主题色图钉 + 白字地点名。"""
    size = font.size
    r = size * 0.36
    icon_w, icon_h = math.ceil(2 * r), math.ceil(r * 2.75)
    gap, m = round(size * 0.3), round(size * 0.5)
    cap = -font.getbbox("H", anchor="ls")[1]
    w = 2 * m + icon_w + gap + math.ceil(font.getlength(text))
    h = 2 * m + max(icon_h, cap + round(size * 0.3))
    iy = (h - icon_h) / 2

    def draw_pin(d, k):
        cx, cy, rr = (m + r) * k, (iy + r) * k, r * k
        # 圆头上与尖端相切的两点（尖端在圆心下方 1.75r）
        side = (rr * math.cos(math.radians(35)), rr * math.sin(math.radians(35)))
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=255)
        d.polygon([(cx - side[0], cy + side[1]), (cx + side[0], cy + side[1]),
                   (cx, (iy + icon_h) * k)], fill=255)
        hole = rr * 0.42
        d.ellipse((cx - hole, cy - hole, cx + hole, cy + hole), fill=0)

    icon = _aa_mask((w, h), draw_pin)
    words = Image.new("L", (w, h), 0)
    ImageDraw.Draw(words).text((m + icon_w + gap, h / 2 + cap / 2), text,
                               font=font, anchor="ls", fill=255)
    sprite = _shadow((w, h), ImageChops.lighter(icon, words), size)
    sprite.alpha_composite(_layer((w, h), _rgba(color), icon))
    sprite.alpha_composite(_layer((w, h), (255, 255, 255, 255), words))
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
    sprite = _shadow((w, h), mask, size * 0.6)
    sprite.alpha_composite(_layer((w, h), (255, 255, 255, 255), mask))
    return sprite


@dataclass
class Item:
    sprite: Image.Image
    cx: float
    cy: float
    t_in: float
    t_out: float
    motion: str = "pop"     # pop：弹出；slide：上滑淡入；bounce：放大回弹


def _fit(fonts: Fonts, style: str, lines: list[str]) -> ImageFont.FreeTypeFont:
    file, size, _ = STYLES[style]
    font = fonts(file, size)
    widest = max(_line_width(line, font) for line in lines)
    return fonts(file, int(size * MAX_TEXT_W / widest)) if widest > MAX_TEXT_W else font


def text_block(fonts: Fonts, style: str, block: dict, anchor_y: float, t_in: float,
               t_out: float, accent: str, accent_text: str,
               reserve_top: float = 0) -> tuple[list[Item], float]:
    """主行逐行弹出，可选小字副行；返回 Item 列表和整块顶边（用来往上摆图钉/图标）。"""
    lines, sub = block["lines"], block.get("sub")
    font = _fit(fonts, style, lines)
    pitch = font.size * STYLES[style][2]
    rows = [(text_line(line, font, accent, accent_text), i * pitch, t_in + i * 0.10)
            for i, line in enumerate(lines)]
    bottom = (len(lines) - 1) * pitch + font.size * 0.55
    if sub:
        sfont = _fit(fonts, "sub", [sub])
        offset = (len(lines) - 1) * pitch + (pitch + sfont.size * STYLES["sub"][2]) * 0.5
        rows.append((text_line(sub, sfont, accent, accent_text), offset,
                     t_in + len(lines) * 0.10 + 0.25))
        bottom = offset + sfont.size * 0.55
    top = -font.size * 0.55
    first = anchor_y - (top + bottom) / 2                         # 整块垂直居中于锚点
    first = max(first, SAFE_TOP + reserve_top - top)
    first = min(first, SAFE_BOTTOM - bottom)
    items = [Item(sprite, CENTER_X, first + off, start, t_out) for sprite, off, start in rows]
    return items, first + top


def build_items(spec: dict, segs: list[tuple[float, float]], fonts: Fonts) -> tuple[list[Item], float]:
    accent, accent_text = spec["accent"], spec.get("accent_text", "#FFFFFF")
    total = segs[-1][1]
    t_cta = total - CTA_SECONDS

    hook = spec["hook"]
    items, top = text_block(fonts, "hook", hook, ANCHOR_Y[hook.get("anchor", "top")],
                            -0.06, segs[0][1], accent, accent_text, reserve_top=70)
    pin = pin_label(spec["pin"], fonts(STYLES["pin"][0], STYLES["pin"][1]), accent)
    items.append(Item(pin, CENTER_X, top - 40, 0.30, segs[0][1], "slide"))

    for k, cap in enumerate(spec["captions"], start=1):
        start, end = segs[k]
        if k == len(segs) - 1:
            end = min(end, t_cta)
        block, _ = text_block(fonts, "caption", cap, ANCHOR_Y[cap.get("anchor", "lower")],
                              start + 0.10, end, accent, accent_text)
        items += block

    block, top = text_block(fonts, "cta", spec["cta"], ANCHOR_Y["middle"], t_cta + 0.20,
                            total + 1, accent, accent_text, reserve_top=150)
    icon_y = top - 70
    items += block
    items.append(Item(bookmark(96, False), CENTER_X, icon_y, t_cta + 0.05, t_cta + 0.90))
    items.append(Item(bookmark(96, True), CENTER_X, icon_y, t_cta + 0.80, total + 1, "bounce"))
    return items, t_cta


def draw_item(frame: Image.Image, it: Item, t: float) -> None:
    if not it.t_in <= t < it.t_out:
        return
    p = t - it.t_in
    alpha, scale, dy = _clamp(p / 0.12), 1.0, 0.0
    if it.motion == "pop":
        scale = 0.82 + 0.18 * _ease_out_back(_clamp(p / 0.28))
    elif it.motion == "bounce":
        alpha, scale = 1.0, 1.35 - 0.35 * _ease_out_back(_clamp(p / 0.30))
    elif it.motion == "slide":
        alpha, dy = _clamp(p / 0.25), 22 * (1 - _ease_out_cubic(_clamp(p / 0.35)))
    leave = _clamp((it.t_out - t) / 0.16)                         # 离场：0.16 秒淡出微缩
    alpha *= leave
    scale *= 0.96 + 0.04 * leave
    if alpha <= 0.01:
        return
    sprite = it.sprite
    if abs(scale - 1) > 1e-3:
        sprite = sprite.resize((max(1, round(sprite.width * scale)),
                                max(1, round(sprite.height * scale))), Image.BICUBIC)
    if alpha < 0.999:
        sprite = sprite.copy()
        sprite.putalpha(sprite.getchannel("A").point(lambda v: round(v * alpha)))
    frame.paste(sprite, (round(it.cx - sprite.width / 2), round(it.cy + dy - sprite.height / 2)), sprite)


def _zoom_factor(k: int, j: int, n: int, last: int) -> float:
    """每段开头从放大状态回弹（第一段轻一点），切点前 3 帧往里冲，接成推拉转场。"""
    punch, settle = (0.06, 10) if k == 0 else (0.10, 8)
    z = 1 + punch * (1 - _ease_out_cubic(_clamp(j / settle)))
    if k < last and j >= n - 3:
        z *= 1 + 0.06 * ((j - n + 4) / 3) ** 2
    return z


def _zoomed(src: Image.Image, z: float) -> Image.Image:
    cw, ch = SRC_W / z, SRC_H / z
    box = ((SRC_W - cw) / 2, (SRC_H - ch) / 2, (SRC_W + cw) / 2, (SRC_H + ch) / 2)
    return src.resize((W, H), Image.LANCZOS, box=box)


def _audio_graph(trims: list[tuple[int, int]], total: float) -> str:
    n = len(trims)
    parts = [f"[1:a]asplit={n}" + "".join(f"[s{k}]" for k in range(n))]
    for k, (a, b) in enumerate(trims):
        d = (b - a) / FPS
        parts.append(f"[s{k}]atrim=start={a / FPS:.5f}:end={b / FPS:.5f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.03,afade=t=out:st={d - 0.03:.5f}:d=0.03[c{k}]")
    parts.append("".join(f"[c{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1,"
                 "loudnorm=I=-18:TP=-1.5:LRA=11,aresample=48000,"
                 "aformat=sample_rates=48000:channel_layouts=stereo,"    # 4.3 的 loudnorm 不带声道布局
                 f"afade=t=out:st={total - 0.8:.3f}:d=0.8[aout]")
    return ";".join(parts)


def render(spec_path: Path, out_dir: Path, fonts: Fonts) -> Path:
    started = time.monotonic()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    name = spec_path.stem
    src = ROOT / spec["source"]
    trims = [tuple(t) for t in spec.get("trim") or
             [(k * CLIP_FRAMES + TRIM[0], k * CLIP_FRAMES + TRIM[1]) for k in range(3)]]
    if any(b <= a for a, b in trims) or any(trims[i][1] > trims[i + 1][0] for i in range(len(trims) - 1)):
        raise ValueError(f"{spec_path}: trim 必须递增且不重叠")
    if len(spec["captions"]) != len(trims) - 1:
        raise ValueError(f"{spec_path}: captions 数量应为片段数 - 1（第一段用 hook）")

    segs, t = [], 0.0
    for a, b in trims:
        segs.append((t, t + (b - a) / FPS))
        t += (b - a) / FPS
    total = segs[-1][1]
    items, t_cta = build_items(spec, segs, fonts)
    keep = {f: (k, j, b - a) for k, (a, b) in enumerate(trims) for j, f in enumerate(range(a, b))}

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}-reels.mp4"
    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(src),
         "-vf", f"{GRADE},scale={SRC_W}:{SRC_H}:in_color_matrix=bt709:in_range=tv",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", str(src),
         "-filter_complex", f"[0:v]scale={W}:{H}:out_color_matrix=bt709:out_range=tv,"
                            f"format=yuv420p[v];{_audio_graph(trims, total)}",
         "-map", "[v]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-level", "4.2",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)],
        stdin=subprocess.PIPE)

    frame_bytes, f, written = SRC_W * SRC_H * 3, 0, 0
    while True:
        buf = dec.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        if f in keep:
            k, j, n = keep[f]
            t = written / FPS
            frame = _zoomed(Image.frombuffer("RGB", (SRC_W, SRC_H), buf, "raw", "RGB", 0, 1),
                            _zoom_factor(k, j, n, len(trims) - 1))
            if t >= t_cta:                                        # 结尾压暗给 CTA 让位
                b = 1 - 0.45 * _ease_out_cubic(_clamp((t - t_cta) / 0.4))
                frame = frame.point([round(v * b) for v in range(256)] * 3)
            for it in items:
                draw_item(frame, it, t)
            enc.stdin.write(frame.tobytes())
            written += 1
        f += 1
    dec.wait()
    enc.stdin.close()
    if enc.wait() != 0 or dec.returncode != 0:
        raise RuntimeError(f"{name}: ffmpeg 失败")
    if written != len(keep):
        raise RuntimeError(f"{name}: 只读到 {written}/{len(keep)} 帧，源片比 trim 短")

    cover = out_dir / f"{name}-cover.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.5", "-i", str(out),
                    "-frames:v", "1", "-q:v", "2", str(cover)], check=True)
    caption = out_dir / f"{name}-caption.txt"
    caption.write_text(spec["ig_caption"].rstrip() + "\n\n" + " ".join(spec["hashtags"]) + "\n",
                       encoding="utf-8")
    print(f"{name}: {written} 帧 / {total:.2f} 秒 → {out.name}（{time.monotonic() - started:.0f} 秒）")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="把 3×5 秒成片剪成英文 Instagram Reels 版")
    ap.add_argument("specs", nargs="*", type=Path, help="post/reels/<id>.json")
    ap.add_argument("--all", action="store_true", help="渲染 post/reels/ 下全部")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "deliveries" / "reels")
    ap.add_argument("--fonts-dir", type=Path, default=Path("C:/Windows/Fonts"))
    args = ap.parse_args()
    specs = sorted((ROOT / "post" / "reels").glob("*.json")) if args.all else args.specs
    if not specs:
        ap.error("指定 spec 文件，或用 --all")
    fonts = Fonts(args.fonts_dir)
    for spec in specs:
        render(spec, args.out_dir, fonts)


if __name__ == "__main__":
    main()
