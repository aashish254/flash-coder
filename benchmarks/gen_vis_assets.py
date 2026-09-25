"""Generate the vis-suite screenshot assets (PIL-drawn UIs, ground truth known).

Each image is a simple rendered UI; the matching task asks the VLM to reproduce
it as self-contained HTML. Run: PYTHONPATH=. .venv/bin/python benchmarks/gen_vis_assets.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "assets" / "vis"
OUT.mkdir(parents=True, exist_ok=True)
W, H = 480, 320


def font(sz: int) -> ImageFont.FreeTypeFont:
    for p in ("/System/Library/Fonts/Helvetica.ttc",
              "/System/Library/Fonts/SFNS.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


def vis1_button():
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([170, 130, 310, 180], radius=10, fill="#2563eb")
    f = font(20)
    t = "Sign up"
    tw = d.textlength(t, font=f)
    d.text((240 - tw / 2, 143), t, fill="white", font=f)
    img.save(OUT / "vis1_button.png")


def vis2_login():
    img = Image.new("RGB", (W, H), "#f3f4f6")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([90, 40, 390, 285], radius=12, fill="white",
                        outline="#d1d5db")
    f, fs = font(24), font(14)
    t = "Login"
    d.text((240 - d.textlength(t, font=f) / 2, 58), t, fill="#111827", font=f)
    for i, lab in enumerate(("Email", "Password")):
        y = 105 + i * 55
        d.text((115, y), lab, fill="#374151", font=fs)
        d.rounded_rectangle([115, y + 20, 365, y + 48], radius=6,
                            fill="white", outline="#9ca3af")
    d.rounded_rectangle([115, 230, 365, 268], radius=8, fill="#2563eb")
    b = "Log in"
    d.text((240 - d.textlength(b, font=fs) / 2, 240), b, fill="white", font=fs)
    img.save(OUT / "vis2_login.png")


def vis3_navbar():
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 56], fill="#111827")
    fb, fl = font(20), font(14)
    d.text((20, 16), "Brand", fill="white", font=fb)
    x = 280
    for link in ("Home", "About", "Contact"):
        d.text((x, 20), link, fill="#d1d5db", font=fl)
        x += 30 + d.textlength(link, font=fl)
    d.text((20, 90), "Welcome to the page.", fill="#374151", font=fl)
    img.save(OUT / "vis3_navbar.png")


def vis4_pricing():
    img = Image.new("RGB", (W, H), "#eef2ff")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([130, 30, 350, 290], radius=14, fill="white",
                        outline="#c7d2fe")
    ft, fp, fs = font(22), font(30), font(13)
    t = "Pro"
    d.text((240 - d.textlength(t, font=ft) / 2, 50), t, fill="#111827", font=ft)
    pr = "$9/mo"
    d.text((240 - d.textlength(pr, font=fp) / 2, 85), pr, fill="#4f46e5", font=fp)
    for i, feat in enumerate(("Unlimited projects", "Priority support",
                              "Team sharing")):
        d.text((160, 140 + i * 26), feat, fill="#374151", font=fs)
    d.rounded_rectangle([160, 235, 320, 268], radius=8, fill="#4f46e5")
    b = "Buy"
    d.text((240 - d.textlength(b, font=fs) / 2, 245), b, fill="white", font=fs)
    img.save(OUT / "vis4_pricing.png")


def vis5_alert():
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([40, 120, 440, 190], radius=8, fill="#fef3c7",
                        outline="#f59e0b")
    fb, fs = font(16), font(14)
    d.text((60, 138), "Warning:", fill="#92400e", font=fb)
    d.text((60, 160), "Your trial ends in 3 days.", fill="#78350f", font=fs)
    img.save(OUT / "vis5_alert.png")


def vis6_dashboard():
    """2x2 grid of stat cards: number over label, gray page on white cards."""
    img = Image.new("RGB", (W, H), "#f9fafb")
    d = ImageDraw.Draw(img)
    fn, fl = font(28), font(13)
    cards = [(("1,234", "Users"), ("$5.6k", "Revenue")),
             (("42", "Open tickets"), ("98%", "Uptime"))]
    for row, pair in enumerate(cards):
        for col, (num, label) in enumerate(pair):
            x0, y0 = 30 + col * 220, 30 + row * 140
            d.rounded_rectangle([x0, y0, x0 + 200, y0 + 120], radius=10,
                                fill="white", outline="#e5e7eb")
            d.text((x0 + 18, y0 + 20), num, fill="#111827", font=fn)
            d.text((x0 + 18, y0 + 66), label, fill="#6b7280", font=fl)
    img.save(OUT / "vis6_dashboard.png")


def vis7_form():
    """Signup form: title, three stacked inputs, wide button, blue-tinted page."""
    img = Image.new("RGB", (W, H), "#eff6ff")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([100, 30, 380, 300], radius=12, fill="white",
                        outline="#bfdbfe")
    ft, fs = font(22), font(13)
    t = "Create account"
    d.text((240 - d.textlength(t, font=ft) / 2, 48), t, fill="#1e3a8a", font=ft)
    for i, ph in enumerate(("Full name", "Email", "Password")):
        y = 95 + i * 45
        d.rounded_rectangle([120, y, 360, y + 32], radius=6, fill="#f8fafc",
                            outline="#cbd5e1")
        d.text((130, y + 9), ph, fill="#94a3b8", font=fs)
    d.rounded_rectangle([120, 245, 360, 278], radius=8, fill="#2563eb")
    b = "Sign up"
    d.text((240 - d.textlength(b, font=fs) / 2, 254), b, fill="white", font=fs)
    img.save(OUT / "vis7_form.png")


if __name__ == "__main__":
    for fn in (vis1_button, vis2_login, vis3_navbar, vis4_pricing, vis5_alert,
               vis6_dashboard, vis7_form):
        fn()
        print("drew", fn.__name__)
