"""A deterministic typography cover, reviewed as part of the exact draft."""
from pathlib import Path


def create_cover(title, directory):
    from PIL import Image, ImageDraw, ImageFont
    fonts = [Path('/mnt/c/Windows/Fonts/msyh.ttc'), Path('C:/Windows/Fonts/msyh.ttc'),
             Path('/usr/share/fonts/opentype/unifont/unifont.otf')]
    font_file = next((p for p in fonts if p.exists()), None)
    if font_file is None:
        raise RuntimeError('缺少中文字体；请提供配图')
    image = Image.new('RGB', (1080, 1440), '#f3f0e8')
    draw = ImageDraw.Draw(image)
    heading = ImageFont.truetype(str(font_file), 82)
    label = ImageFont.truetype(str(font_file), 34)
    draw.rectangle((84, 160, 100, 1220), fill='#365746')
    draw.text((145, 190), '学习 · 观察 · 记录', font=label, fill='#365746')
    lines, line = [], ''
    for char in title:
        if draw.textlength(line + char, font=heading) > 780:
            lines.append(line)
            line = ''
        line += char
    if line:
        lines.append(line)
    for index, line in enumerate(lines):
        draw.text((145, 450 + index * 130), line, font=heading, fill='#20382d')
    draw.line((145, 1150, 950, 1150), fill='#b9c2b7', width=2)
    draw.text((145, 1180), '从问题出发，把思考写下来。', font=label, fill='#536451')
    output = Path(directory) / 'cover.png'
    image.save(output)
    return output
