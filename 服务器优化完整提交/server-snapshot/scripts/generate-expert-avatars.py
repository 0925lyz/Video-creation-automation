from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT = Path.home() / ".workbuddy/plugins/marketplaces/my-experts/plugins/jaguartv-content-ops/avatars"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
AVATARS = {
    "team.png": ("JT", "#111719", "#20c997"),
    "jaguartv-content-ops-team-lead.png": ("QC", "#2b193d", "#e64980"),
    "source-scout.png": ("SY", "#19324a", "#4dabf7"),
    "ptbr-localization-editor.png": ("PY", "#3b2032", "#f06595"),
    "video-quality-reviewer.png": ("YH", "#18362b", "#51cf66"),
}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(FONT, 150)
    for name, (label, background, accent) in AVATARS.items():
        image = Image.new("RGB", (512, 512), background)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((56, 56, 456, 456), radius=96, fill=accent)
        draw.rounded_rectangle((76, 76, 436, 436), radius=80, fill=background)
        box = draw.textbbox((0, 0), label, font=font)
        x = (512 - (box[2] - box[0])) / 2
        y = (512 - (box[3] - box[1])) / 2 - 12
        draw.text((x, y), label, font=font, fill="white")
        image.save(OUTPUT / name, optimize=True)


if __name__ == "__main__":
    main()
