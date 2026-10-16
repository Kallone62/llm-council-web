"""Convert the original SVG-rasterized PNG to a multi-resolution Windows ICO.

Called after frontend/scripts/render-icons.mjs by the Windows release workflow.
"""
from pathlib import Path

from PIL import Image


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    brand = root / "build" / "brand"
    source = brand / "icon-256.png"
    if not source.exists():
        raise SystemExit(f"Missing rasterized logo: {source}")
    with Image.open(source) as png:
        image = png.convert("RGBA")
        image.save(
            brand / "LLMCouncilWeb.ico",
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                   (64, 64), (128, 128), (256, 256)],
        )
    print(f"Generated {brand / 'LLMCouncilWeb.ico'}")


if __name__ == "__main__":
    main()
