from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = [
    "01-workspace-overview.png",
    "02-application-materials.png",
    "03-pilot-confirmation.png",
    "04-interview-practice.png",
    "05-offer-negotiation.png",
]
ASSET_DIR = ROOT / "docs" / "assets" / "readme" / "2026-08-13"


def test_readme_references_five_wide_product_screenshots():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for filename in SCREENSHOTS:
        relative_path = f"docs/assets/readme/2026-08-13/{filename}"
        assert relative_path in readme
        image_path = ASSET_DIR / filename
        assert image_path.is_file()
        with Image.open(image_path) as image:
            assert image.format == "PNG"
            width, height = image.size
            assert width >= 1440
            assert height >= 800
            assert width / height >= 1.25
            colors = image.convert("RGB").resize((64, 36)).getcolors(64 * 36)
            assert colors is not None and len(colors) > 16


def test_readme_keeps_pilot_and_offer_negotiation_visible():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Pilot 有何不同" in readme
    assert "Haru" in readme
    assert "谈薪" in readme
    assert "自动完成投递" not in readme
    assert "替你筛选最优 Offer" not in readme
