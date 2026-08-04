#may or may not be used 

from PIL import Image, ImageDraw 

def make_icon(path, draw_fn):
    img = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw_fn(draw)
    img.save(path)

make_icon("icons/idle.png", lambda d: d.ellipse([4, 4, 18, 18], outline=(0, 0, 0, 255), width=2))
make_icon("icons/recording.png", lambda d: d.ellipse([4, 4, 18, 18], fill=(0, 0, 0, 255)))