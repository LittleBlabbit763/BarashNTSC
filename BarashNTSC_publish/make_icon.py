# make_icon.py
from PIL import Image

src = "icon.png"
dst = "icon.ico"

img = Image.open(src).convert("RGBA")

# Квадратное полотно (по центру), если картинка не квадратная
w, h = img.size
side = max(w, h)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(img, ((side - w) // 2, (side - h) // 2))

sizes = [16, 24, 32, 48, 64, 128, 256]
canvas.save(dst, format="ICO", sizes=[(s, s) for s in sizes])
print("saved", dst)