# -*- coding: utf-8 -*-
"""自绘示意图 + 干货类封面（第 9、10 篇用）。

不依赖图库、不调用图像生成模型：全部用 PIL 画线、矩形、箭头和文字。
输出直接落到对应 draft 目录，供 build_publish_package.py 打包。

用法：
    python scripts/draw_diagrams.py

画布与配色（与发布包规范一致）：
    1200x800 白底 / 线条 #0d273e / 尺寸箭头 #f5a623 / 辅助文字 #6b7a89
    标题字 msyhbd.ttc（微软雅黑 Bold），标注字 msyh.ttc
"""
from PIL import Image, ImageDraw, ImageFont
import math, os

W, H = 1200, 800
CW, CH, LEFT = 1200, 630, 596          # 封面尺寸与右栏起点
NAVY = (13, 39, 62)
ACCENT = (245, 166, 35)
CREAM = (245, 199, 126)
GREY = (107, 122, 137)
LIGHT = (232, 238, 243)
WHITE = (255, 255, 255)
BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
REG = r"C:\Windows\Fonts\msyh.ttc"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D9 = os.path.join(ROOT, "drafts", "08-wheelchair-measurements")
D10 = os.path.join(ROOT, "drafts", "09-home-modification")
for d in (D9, D10):
    os.makedirs(d, exist_ok=True)


def canvas():
    im = Image.new("RGB", (W, H), WHITE)
    return im, ImageDraw.Draw(im)


def label(d, xy, text, size=26, color=NAVY, bold=True, anchor="lm"):
    d.text(xy, text, font=ImageFont.truetype(BOLD if bold else REG, size), fill=color, anchor=anchor)


def arrow(d, p1, p2, color=ACCENT, width=4, head=12):
    d.line([p1, p2], fill=color, width=width)
    ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    for a in (ang + math.radians(150), ang - math.radians(150)):
        d.line([p2, (p2[0] + head * math.cos(a), p2[1] + head * math.sin(a))], fill=color, width=width)


def dim_h(d, x1, x2, y, text):
    arrow(d, (x1, y), (x2, y))
    arrow(d, (x2, y), (x1, y))
    label(d, ((x1 + x2) // 2, y - 26), text, 24, ACCENT, True, "mm")


def dim_v(d, x, y1, y2, text):
    arrow(d, (x, y1), (x, y2))
    arrow(d, (x, y2), (x, y1))
    label(d, (x + 14, (y1 + y2) // 2), text, 24, ACCENT, True, "lm")


# ---------------------------------------------------------------- 第 9 篇

def d_seat():
    im, d = canvas()
    label(d, (60, 70), "俯视：座宽与座深", 34)
    d.rounded_rectangle([300, 200, 900, 640], 18, fill=LIGHT, outline=NAVY, width=5)
    d.rectangle([300, 200, 900, 260], fill=(214, 224, 232))
    label(d, (600, 230), "靠背", 22, GREY, False, "mm")
    dim_h(d, 300, 900, 690, "座宽 = 臀宽 + 2cm")
    dim_v(d, 940, 260, 640, "座深")
    label(d, (60, 300), "座宽：臀部最宽处", 22, GREY, False)
    label(d, (60, 336), "再加 2cm 余量", 22, GREY, False)
    label(d, (60, 420), "座深：臀后到膝弯", 22, GREY, False)
    label(d, (60, 456), "少 2cm，膝弯不受压", 22, GREY, False)
    im.save(os.path.join(D9, "02-seat-size.jpg"), "JPEG", quality=92)


def d_armrest():
    im, d = canvas()
    label(d, (60, 70), "侧视：扶手高与靠背高", 34)
    d.line([(300, 600), (820, 600)], fill=NAVY, width=6)      # 座面
    d.line([(300, 600), (300, 220)], fill=NAVY, width=6)      # 靠背
    d.line([(300, 420), (640, 420)], fill=NAVY, width=6)      # 扶手
    d.line([(640, 420), (640, 600)], fill=NAVY, width=6)      # 扶手立柱
    d.line([(800, 600), (800, 700)], fill=NAVY, width=6)      # 前腿
    d.line([(300, 600), (300, 700)], fill=NAVY, width=6)      # 后腿
    for y, txt in ((420, "坐姿肘部高度"), (260, "肩胛下角")):
        x = 300
        while x < 980:
            d.line([(x, y), (x + 14, y)], fill=GREY, width=2)
            x += 26
        label(d, (990, y), txt, 20, GREY, False)
    dim_v(d, 210, 420, 600, "扶手高")
    dim_v(d, 900, 220, 600, "靠背高")
    label(d, (60, 300), "扶手高：坐直后肘部自然", 22, GREY, False)
    label(d, (60, 336), "下垂的高度，不是扶手顶面", 22, GREY, False)
    label(d, (60, 400), "靠背高：量到肩胛下角，", 22, GREY, False)
    label(d, (60, 436), "不是量到肩膀", 22, GREY, False)
    label(d, (60, 730), "量的时候穿日常衣服，坐直，别挺胸", 22, GREY, False)
    im.save(os.path.join(D9, "03-armrest-height.jpg"), "JPEG", quality=92)


def d_doors():
    im, d = canvas()
    label(d, (60, 70), "出门前量的三个数", 34)
    d.rectangle([140, 470, 420, 500], fill=(200, 212, 222), outline=NAVY, width=4)
    d.rectangle([140, 300, 180, 470], fill=(214, 224, 232), outline=NAVY, width=4)
    d.rectangle([380, 300, 420, 470], fill=(214, 224, 232), outline=NAVY, width=4)
    dim_v(d, 100, 470, 500, "h")
    label(d, (280, 570), "门槛高度", 24, NAVY, True, "mm")
    d.rectangle([520, 250, 820, 500], outline=NAVY, width=6)
    d.line([(670, 250), (670, 500)], fill=NAVY, width=4)
    dim_h(d, 520, 820, 540, "净宽")
    label(d, (670, 590), "电梯门净宽", 24, NAVY, True, "mm")
    d.rounded_rectangle([900, 300, 1120, 500], 24, outline=NAVY, width=6)
    dim_v(d, 1160, 300, 500, "开口")
    label(d, (1010, 570), "车门开口高度", 24, NAVY, True, "mm")
    label(d, (60, 700), "三个数只要有一个不够，车的型号就得换", 22, GREY, False)
    im.save(os.path.join(D9, "04-door-sizes.jpg"), "JPEG", quality=92)


# ---------------------------------------------------------------- 第 10 篇

def d_ramp():
    im, d = canvas()
    label(d, (60, 70), "门槛斜坡：坡度 1:8", 34)
    d.rectangle([200, 620, 900, 660], fill=(200, 212, 222), outline=NAVY, width=4)
    d.polygon([(200, 620), (200, 460), (760, 620)], fill=(238, 244, 249), outline=NAVY)
    d.line([(200, 620), (200, 460)], fill=NAVY, width=6)
    d.line([(200, 460), (760, 620)], fill=ACCENT, width=6)
    dim_v(d, 160, 460, 620, "h")
    dim_h(d, 200, 760, 690, "长度 = 8 × h")
    label(d, (60, 300), "3cm 门槛 → 斜坡长 24cm", 22, GREY, False)
    label(d, (60, 336), "5cm 门槛 → 斜坡长 40cm", 22, GREY, False)
    label(d, (60, 400), "坡太陡会翻，别图短", 22, GREY, False)
    im.save(os.path.join(D10, "02-ramp.jpg"), "JPEG", quality=92)


def d_bathroom():
    im, d = canvas()
    label(d, (60, 70), "卫生间：扶手装在哪", 34)
    d.rounded_rectangle([240, 380, 520, 620], 30, fill=LIGHT, outline=NAVY, width=5)
    d.rounded_rectangle([300, 300, 460, 400], 20, fill=(214, 224, 232), outline=NAVY, width=4)
    d.rectangle([520, 430, 560, 470], fill=ACCENT, outline=NAVY, width=3)
    d.rectangle([560, 470, 640, 490], fill=ACCENT, outline=NAVY, width=3)
    dim_v(d, 200, 300, 620, "70cm")
    label(d, (700, 380), "马桶侧扶手", 26, NAVY)
    label(d, (700, 420), "高 70cm 左右", 22, GREY, False)
    label(d, (700, 460), "离马桶前沿 20-25cm", 22, GREY, False)
    label(d, (700, 540), "门净宽 ≥ 80cm", 22, GREY, False)
    label(d, (60, 700), "扶手不是装饰，是起身时唯一能借力的地方", 22, GREY, False)
    im.save(os.path.join(D10, "03-bathroom.jpg"), "JPEG", quality=92)


def d_bed():
    im, d = canvas()
    label(d, (60, 70), "床边：高度与护栏", 34)
    d.rectangle([220, 420, 860, 620], fill=LIGHT, outline=NAVY, width=5)
    d.rectangle([220, 360, 260, 620], fill=(214, 224, 232), outline=NAVY, width=4)
    d.line([(700, 420), (700, 300)], fill=NAVY, width=6)
    d.line([(700, 300), (860, 300)], fill=NAVY, width=6)
    dim_v(d, 180, 420, 620, "45-50cm")
    label(d, (500, 500), "床面高度", 26, NAVY, True, "mm")
    label(d, (900, 290), "护栏", 26, NAVY)
    label(d, (900, 330), "高于床面 30cm", 22, GREY, False)
    label(d, (60, 690), "床太高下地会崴脚，太低起身费腰", 22, GREY, False)
    im.save(os.path.join(D10, "04-bedside.jpg"), "JPEG", quality=92)


# ---------------------------------------------------------------- 封面

def cover(src, out, kicker, lines, subs):
    c = Image.new("RGB", (CW, CH), NAVY)
    d = ImageDraw.Draw(c)
    d.rectangle([LEFT, 0, CW, CH], fill=(244, 247, 250))
    im = Image.open(src).convert("RGB")
    im.thumbnail((480, 360), Image.LANCZOS)
    x = LEFT + (CW - LEFT - im.width) // 2
    y = (CH - im.height) // 2
    d.rounded_rectangle([x - 18, y - 18, x + im.width + 18, y + im.height + 18], 16,
                        fill=WHITE, outline=(214, 224, 232), width=2)
    c.paste(im, (x, y))
    xx = 62
    d.rectangle([xx, 92, xx + 54, 100], fill=ACCENT)
    d.text((xx, 124), kicker, font=ImageFont.truetype(BOLD, 27), fill=CREAM)
    yy = 182
    for line in lines:
        d.text((xx, yy), line, font=ImageFont.truetype(BOLD, 48), fill=WHITE)
        yy += 62
    for i, s in enumerate(subs):
        d.text((xx, yy + 22 + i * 34), s, font=ImageFont.truetype(REG, 23), fill=(196, 209, 221))
    c.save(out, "JPEG", quality=92, optimize=True)


if __name__ == "__main__":
    d_seat(); d_armrest(); d_doors()
    d_ramp(); d_bathroom(); d_bed()
    cover(os.path.join(D9, "02-seat-size.jpg"), os.path.join(D9, "hero.jpg"),
          "选购干货 · 尺寸篇", ["买轮椅前", "先量四个数", "再挑型号"], ["座宽 / 座深", "扶手高 / 靠背高"])
    cover(os.path.join(D10, "02-ramp.jpg"), os.path.join(D10, "hero.jpg"),
          "适老化改造 · 防跌倒", ["家里这三处", "改一下", "少摔一半跤"], ["门槛 / 卫生间 / 床边", "附优先级表"])
    print("diagrams + covers done")
