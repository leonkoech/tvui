"""Screensaver photo with two frosted-glass panels baked in (live blur is too slow/unsupported
on the 2014 TV). Panel rectangles are in rem at 1920 wide (1rem = 24px) and must match the
#saver .glass rules in index.html."""
from PIL import Image, ImageDraw, ImageFilter

REM = 24
PANELS = [  # (left, top, width, height) in rem
    (17.4, 12.4, 29.0, 21.4),   # greeting, names, clock, date
    (1920 / REM - 15.6 - 16.4, 12.4, 16.4, 12.6),  # weather (right edge at 15.6rem from the right)
]
RADIUS = 1.6

src = Image.open(r"C:\Users\17866\tvui-build\bgsrc\cottage.png").convert("RGB")
src = src.resize((1920, round(src.height * 1920 / src.width)), Image.LANCZOS)
top = (src.height - 1080) // 2
img = src.crop((0, top, 1920, top + 1080))

for (l, t, w, h) in PANELS:
    box = tuple(round(v * REM) for v in (l, t, l + w, t + h))
    glass = img.crop(box).filter(ImageFilter.GaussianBlur(22))
    glass = Image.blend(glass, Image.new("RGB", glass.size, (255, 255, 255)), 0.42)  # milky frost
    mask = Image.new("L", glass.size, 0)
    r = round(RADIUS * REM)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, glass.width - 1, glass.height - 1), r, fill=255)
    # soft shadow under the panel
    shadow = Image.new("L", img.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 10, box[2], box[3] + 10), r, fill=70)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    img = Image.composite(Image.new("RGB", img.size, (60, 70, 60)), img, shadow)
    img.paste(glass, box[:2], mask)
    # fine light edge, like Apple's glass
    edge = Image.new("RGBA", glass.size, (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle((0, 0, glass.width - 1, glass.height - 1), r,
                                           outline=(255, 255, 255, 170), width=2)
    img.paste(edge, box[:2], edge)

img.save(r"C:\Users\17866\tvui-pkg\src\tvui\static\saver.jpg", quality=88, optimize=True)
print("saver.jpg with glass panels:", [tuple(round(v * REM) for v in p) for p in PANELS])
