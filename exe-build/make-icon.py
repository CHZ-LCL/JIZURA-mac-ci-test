#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 exe 图标（icon.ico）、启动画面（splash.png），可选 1024x1024 PNG（macOS .icns 用）。
由 make-build.py / make-build-mac.py 调用（需要 Pillow）。"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ACCENT = (255, 178, 32)
DARK = (14, 17, 23)
LIGHT = (245, 245, 245)
GREY = (170, 176, 186)
GREY2 = (128, 134, 144)

BASE = 256.0


def font(size, bold=False):
    cands = [
        r'C:\Windows\Fonts\msyhbd.ttc' if bold else r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\simhei.ttf',
        r'C:\Windows\Fonts\arialbd.ttf',
        r'C:\Windows\Fonts\arial.ttf',
        # macOS（Apple Silicon / Intel）
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/Helvetica.ttc',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/Library/Fonts/Arial.ttf',
    ]
    for c in cands:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(size):
    """按 256 的设计稿等比绘制任意尺寸的图标。"""
    s = size / BASE
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8 * s, 8 * s, 248 * s, 248 * s], radius=56 * s,
                        fill=DARK, outline=ACCENT, width=max(2, int(10 * s)))
    f = font(int(116 * s), bold=True)
    text = 'JZ'
    box = d.textbbox((0, 0), text, font=f)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text((size / 2 - w / 2 - box[0], 118 * s - h / 2 - box[1]), text, font=f, fill=ACCENT)
    for i, hh in enumerate((14, 26, 18)):  # 底部三条小竖线（声波）
        x = (98 + i * 24) * s
        d.rounded_rectangle([x, 196 * s, x + 10 * s, (196 + hh) * s], radius=5 * s, fill=LIGHT)
    return img


def make_icon(path):
    render(256).save(path, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


def make_splash(path):
    W, H = 560, 300
    img = Image.new('RGB', (W, H), DARK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 8, W, H], fill=ACCENT)
    d.rounded_rectangle([28, 28, 108, 108], radius=20, outline=ACCENT, width=6)
    d.text((44, 42), 'JZ', font=font(44, bold=True), fill=ACCENT)
    d.text((140, 38), 'JIZURA 离线版', font=font(34, bold=True), fill=LIGHT)
    d.text((140, 92), '正在准备文件，请稍候…', font=font(19), fill=GREY)
    d.text((30, 196), '首次打开需要解包（约 20~40 秒），属正常现象。', font=font(17), fill=GREY)
    d.text((30, 226), 'Keep this window open · 识曲与在线歌词需联网', font=font(14), fill=GREY2)
    img.save(path, 'PNG')


def main():
    out_icon = sys.argv[1] if len(sys.argv) > 1 else 'icon.ico'
    out_splash = sys.argv[2] if len(sys.argv) > 2 else 'splash.png'
    out_png = sys.argv[3] if len(sys.argv) > 3 else ''
    Path(out_icon).parent.mkdir(parents=True, exist_ok=True)
    make_icon(out_icon)
    make_splash(out_splash)
    if out_png:
        Path(out_png).parent.mkdir(parents=True, exist_ok=True)
        render(1024).save(out_png, 'PNG')
    print('assets ok:', out_icon, out_splash, out_png or '-')


if __name__ == '__main__':
    main()
