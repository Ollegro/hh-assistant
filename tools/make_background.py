# -*- coding: utf-8 -*-
"""Генератор фона для веб-интерфейса.

Два режима:

    python tools/make_background.py                     # процедурные розово-сиреневые облака
    python tools/make_background.py --from photo.jpg    # фон из фотографии (cover-кроп 1600x1000)

Нужен Pillow (в рантайме приложения он не используется — готовый файл
`hhassist/static/bg.jpg` лежит в репозитории).
"""
import argparse
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

W, H = 1600, 1000          # итоговый размер (2x для экранов с ретиной)
SEED = 20261024

SKY_TOP = (255, 200, 228)      # розовый зенит
SKY_MID = (223, 185, 246)      # сиреневая середина
SKY_BOTTOM = (150, 126, 224)   # насыщенный сиреневый у горизонта

CLOUD_TOP = (255, 250, 253)    # освещённая макушка облака
CLOUD_MID = (247, 231, 250)
CLOUD_LOW = (214, 194, 242)    # теневая кромка
SHADOW = (196, 173, 234)       # тень под облаком


def gradient(size, stops):
    """Вертикальный градиент: stops = [(позиция 0..1, (r, g, b)), ...]."""
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / float(h - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                px[0, y] = tuple(int(round(c0[j] + (c1[j] - c0[j]) * k)) for j in range(3))
                break
        else:
            px[0, y] = stops[-1][1]
    return img.resize((w, h), Image.BILINEAR)


def octave(rnd, cols, rows, blur):
    """Мягкое облачное пятно: редкая сетка случайных значений, растянутая до WxH."""
    small = Image.new("L", (cols, rows))
    small.putdata([rnd.randint(0, 255) for _ in range(cols * rows)])
    big = small.resize((W, H), Image.BICUBIC)
    return big.filter(ImageFilter.GaussianBlur(blur))


def _stretch(mask, low=0.03, high=0.995, gamma=0.85):
    """Растяжка по перцентилям: mask -> 0..255 без бинаризации (мягкие края)."""
    hist = mask.histogram()
    total = float(sum(hist))
    acc, lo, hi = 0, 0, 255
    for value, count in enumerate(hist):
        acc += count
        if acc >= total * low and not lo:
            lo = value
        if acc >= total * high:
            hi = value
            break
    span = float(max(1, hi - lo))
    return mask.point(lambda v: 0 if v <= lo else int(
        255.0 * min(1.0, (v - lo) / span) ** gamma))


def _cut(mask, lo=70, hi=170):
    """Почти бинарная кромка с мягким переходом между lo и hi."""
    span = float(max(1, hi - lo))
    return mask.point(lambda v: 0 if v <= lo else (255 if v >= hi
                                                    else int((v - lo) / span * 255)))


def diag_gradient(size, stops, tilt=0.38):
    """Диагональный градиент: t = tilt*x + (1-tilt)*y (0 в левом верхнем углу)."""
    w, h = size
    sw, sh = 240, max(2, int(240 * h / float(w)))
    small = Image.new("RGB", (sw, sh))
    px = small.load()
    for y in range(sh):
        ty = y / float(sh - 1)
        for x in range(sw):
            t = tilt * (x / float(sw - 1)) + (1.0 - tilt) * ty
            for i in range(len(stops) - 1):
                p0, c0 = stops[i]
                p1, c1 = stops[i + 1]
                if p0 <= t <= p1:
                    k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                    px[x, y] = tuple(int(round(c0[j] + (c1[j] - c0[j]) * k))
                                     for j in range(3))
                    break
            else:
                px[x, y] = stops[-1][1] if t > stops[-1][0] else stops[0][1]
    return small.resize((w, h), Image.BICUBIC)


def build():
    rnd = random.Random(SEED)
    size = (W, H)

    # небо: розовое свечение сверху-слева, сиреневый горизонт справа-внизу
    sky = diag_gradient(size, [(0.0, (255, 209, 232)), (0.38, (233, 198, 246)),
                               (0.72, (198, 168, 243)), (1.0, (166, 140, 231))])

    # два слоя: дальний (выше и полупрозрачнее) и ближний (крупный, плотный)
    far = _cut(_stretch(_clouds(rnd, size, [(0.22, 0.05, 0.30, 4),
                                            (0.42, 0.06, 0.46, 5)]),
                        0.05, 0.99, 0.9), 90, 165).point(lambda v: int(v * 0.68))
    near = _cut(_stretch(_clouds(rnd, size, [(0.64, 0.07, 0.70, 4),
                                             (0.92, 0.06, 0.98, 3)]),
                         0.05, 0.99, 0.9), 80, 175)

    # тонкая модуляция плотности внутри облаков — без серых пятен, только лёгкая дымка
    mod = _fbmp(rnd, size, ((18, 12, 0.5, 10), (54, 34, 0.3, 5), (160, 100, 0.2, 3)), 6)
    near = ImageChops.multiply(near, mod.point(lambda v: 212 + int(v * 0.17)))

    far_color = gradient(size, [(0.0, (255, 240, 252)), (1.0, (233, 213, 249))])
    near_color = gradient(size, [(0.0, (255, 253, 255)), (0.55, (251, 243, 253)),
                                 (1.0, (238, 224, 251))])

    img = Image.composite(far_color, sky, far)
    img = Image.composite(near_color, img, near)

    # мягкая подложка под облаками — объём без серых теней
    under = near.transform(size, Image.Transform.AFFINE, (1, 0, 0, 0, 1, -H * 0.014))
    under = under.filter(ImageFilter.GaussianBlur(7))
    under = ImageChops.subtract(under, near).point(lambda v: int(v * 0.42))
    img = Image.composite(gradient(size, [(0.0, (249, 237, 253)), (1.0, (233, 218, 249))]),
                          img, under)

    # мягкое свечение в верхнем левом углу — «солнце за облаками»
    glow = Image.new("L", size, 0)
    ImageDraw.Draw(glow).ellipse((-W * 0.30, -H * 0.50, W * 0.42, H * 0.34), fill=140)
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    img = Image.composite(gradient(size, [(0.0, (255, 231, 242)), (1.0, (252, 225, 243))]),
                          img, glow)

    return img.filter(ImageFilter.GaussianBlur(1.2))





_PUFF_CACHE = {}


def _clouds(rnd, size, bands):
    """Кучевые облака: кластеры залитых эллипсов, размытые в мягкую кромку.

    bands — гряды: (y_центр, разброс, масштаб, сколько облаков).
    Облака распределены по ширине стратами, чтобы кадр был заполнен равномерно.
    """
    wide, high = size
    layer = Image.new("L", size, 0)
    draw = ImageDraw.Draw(layer)
    for y_center, spread, scale, count in bands:
        for i in range(count):
            cx = (i + rnd.uniform(0.1, 0.9)) / float(count) * wide
            cy = y_center * high + rnd.uniform(-spread, spread) * high
            base = rnd.uniform(0.04, 0.075) * wide * scale
            # основание облака — несколько крупных эллипсов
            for _ in range(rnd.randint(6, 10)):
                r = base * rnd.uniform(0.7, 1.15)
                ox = cx + rnd.uniform(-1.5, 1.5) * base
                oy = cy + rnd.uniform(-0.35, 0.5) * base
                draw.ellipse((ox - r, oy - r * 1.15, ox + r, oy + r * 1.15), fill=255)
            # «барашки» — мелкие шарики по макушке, дают рваный силуэт
            for _ in range(rnd.randint(8, 14)):
                r = base * rnd.uniform(0.22, 0.5)
                ox = cx + rnd.uniform(-1.6, 1.6) * base
                oy = cy + rnd.uniform(-1.0, -0.15) * base
                draw.ellipse((ox - r, oy - r * 1.1, ox + r, oy + r * 1.1), fill=255)
    return layer.filter(ImageFilter.GaussianBlur(wide * 0.004))






def _fbmp(rnd, size, octaves, blur):
    """Взвешенная сумма октав шума — рваность краёв."""
    W_, H_ = size
    acc = [0.0] * (W_ * H_)
    for cols, rows, weight, extra in octaves:
        layer = octave(rnd, cols, rows, extra).tobytes()
        for i in range(len(acc)):
            acc[i] += layer[i] * weight
    img = Image.frombytes("L", size, bytes(min(255, int(v)) for v in acc))
    return img.filter(ImageFilter.GaussianBlur(blur))


def _cover(img, size):
    """Вписать с обрезкой: заполнить кадр целиком, лишнее срезать по центру."""
    w, h = size
    sw, sh = img.size
    k = max(w / float(sw), h / float(sh))
    nw, nh = max(w, int(round(sw * k))), max(h, int(round(sh * k)))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _polish(img):
    """Компенсация апскейла: контурная резкость, чуть насыщеннее и контрастнее."""
    img = img.filter(ImageFilter.UnsharpMask(radius=2.0, percent=65, threshold=2))
    img = ImageEnhance.Sharpness(img).enhance(1.15)
    img = ImageEnhance.Color(img).enhance(1.06)
    return ImageEnhance.Contrast(img).enhance(1.03)


def _shade(img, top=0.18, bottom=0.30):
    """Мягкое затемнение верхней и нижней полос: панели интерфейса читаются лучше."""
    w, h = img.size
    column = Image.new("L", (1, h))
    px = column.load()
    for y in range(h):
        t = y / float(h - 1)
        value = top * max(0.0, (0.35 - t) / 0.35) ** 1.6
        value += bottom * max(0.0, (t - 0.62) / 0.38) ** 1.5
        px[0, y] = int(round(255 * min(0.5, value)))
    mask = column.resize((w, h), Image.BILINEAR)
    night = Image.new("RGB", img.size, (12, 16, 30))
    return Image.composite(night, img, mask)


def from_photo(src, size=(W, H)):
    """Фон из фотографии: cover-кроп, лёгкая доводка, затемнение краёв."""
    with Image.open(src) as photo:
        img = photo.convert("RGB")
    img = _cover(img, size)
    return _shade(_polish(img))


def save(img, quality=86):
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "hhassist", "static")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    full = os.path.join(out_dir, "bg.jpg")
    img.save(full, "JPEG", quality=quality, optimize=True, progressive=True)
    print("saved %s (%d bytes, %dx%d)"
          % (full, os.path.getsize(full), img.size[0], img.size[1]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Фон веб-интерфейса.")
    ap.add_argument("--from", dest="src", metavar="PATH",
                    help="фотография-исходник (jpg/png/webp); без неё — рисуем облака")
    ap.add_argument("--quality", type=int, default=86, help="качество JPEG (по умолчанию 86)")
    args = ap.parse_args(argv)
    img = from_photo(args.src) if args.src else build()
    save(img, quality=args.quality)


if __name__ == "__main__":
    main()
