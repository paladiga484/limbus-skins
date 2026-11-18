"""A boss's entrance: one title card, shown full screen the first time it steps into a wave
(the client patch's StoryPatch, intros.txt). The boss stands to the right in its colour's light,
its name large to the left over a bar of that colour, a line under it. Drawn with Pillow's own
font, so it needs nothing installed."""
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = (1280, 720)


def card(pose, name, line, rgb):
    """The card (RGBA, SIZE) for a boss: pose (its standing picture, RGBA), its name, a line
    under it, its colour (r, g, b)."""
    W, H = SIZE
    r, g, b = rgb
    out = Image.new("RGBA", SIZE, (6, 6, 8, 255))
    light = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    d = ImageDraw.Draw(light)
    d.ellipse((W * 0.45, -H * 0.2, W * 1.2, H * 1.15), fill=(r, g, b, 120))
    for k in range(6):                               # streaks falling across from the upper right
        x = W * (0.5 + 0.1 * k)
        d.polygon([(x, 0), (x + 40, 0), (x - 380, H), (x - 430, H)], fill=(r, g, b, 26))
    out.alpha_composite(light.filter(ImageFilter.GaussianBlur(60)))
    fig = pose.convert("RGBA")
    k = H * 0.92 / fig.height
    fig = fig.resize((max(1, int(fig.width * k)), max(1, int(fig.height * k))), Image.LANCZOS)
    if fig.width > W * 0.6:                          # a wide one (a crawler, wings): fit its width instead
        k = W * 0.6 / fig.width
        fig = fig.resize((max(1, int(fig.width * k)), max(1, int(fig.height * k))), Image.LANCZOS)
    halo = Image.new("RGBA", fig.size, (r, g, b, 0))
    halo.putalpha(fig.getchannel("A").filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(10))
                  .point(lambda v: v * 220 // 255))
    at = (int(W * 0.97 - fig.width), H - fig.height)
    out.alpha_composite(Image.alpha_composite(halo, fig), at)
    shade = Image.new("RGBA", SIZE, (0, 0, 0, 0))    # dark on the left, where the words go
    sd = ImageDraw.Draw(shade)
    for x in range(int(W * 0.62)):
        sd.line((x, 0, x, H), fill=(0, 0, 0, int(200 * (1 - x / (W * 0.62)))))
    out.alpha_composite(shade)
    t = ImageDraw.Draw(out)
    big = ImageFont.load_default(size=88)
    small = ImageFont.load_default(size=30)
    title = name.upper()
    while big.getlength(title) > W * 0.58 and big.size > 40:
        big = ImageFont.load_default(size=big.size - 6)
    y = int(H * 0.40)
    t.rectangle((70, y + big.size + 18, 70 + int(min(big.getlength(title), W * 0.55)), y + big.size + 24), fill=(r, g, b, 255))
    glow = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((70, y), title, font=big, fill=(r, g, b, 255))
    out.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
    t.text((70, y), title, font=big, fill=(245, 240, 232, 255))
    if line:
        t.text((72, y + big.size + 40), line, font=small, fill=(min(255, r + 90), min(255, g + 90), min(255, b + 90), 255))
    return out
