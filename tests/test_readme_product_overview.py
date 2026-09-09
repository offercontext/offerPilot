import re
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = {
    "docs/product-manual/screenshots/R03-05-page-create-saved.png",
    "docs/product-manual/screenshots/04-03-material-generated.png",
    "docs/product-manual/screenshots/R03-02-pilot-create-confirm.png",
    "docs/product-manual/screenshots/R08-studio-evidence.png",
    "docs/product-manual/screenshots/R08-offer-comparison-polished.png",
}


def test_readme_references_five_readable_product_screenshots():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    screenshots = re.findall(r"!\[[^\]]+\]\((docs/[^)]+\.png)\)", readme)
    assert len(screenshots) == 5
    assert set(screenshots) == SCREENSHOTS

    for relative_path in screenshots:
        image_path = (ROOT / relative_path).resolve()
        assert image_path.is_relative_to((ROOT / "docs").resolve())
        assert image_path.is_file()
        with Image.open(image_path) as image:
            assert image.format in {"PNG", "JPEG"}
            width, height = image.size
            studio_screenshot = image_path.name == "R08-studio-evidence.png"
            assert width >= (1280 if studio_screenshot else 1440)
            assert height >= 800
            assert width / height >= (1 if studio_screenshot else 1.25)
            colors = image.convert("RGB").resize((64, 36)).getcolors(64 * 36)
            assert colors is not None and len(colors) > 16


def test_readme_keeps_pilot_and_offer_negotiation_visible():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Pilot AI 助手" in readme
    assert "Haru" in readme
    assert "谈薪" in readme
    assert "默认需要你的确认" in readme
    assert "自动完成投递" not in readme
    assert "替你筛选最优 Offer" not in readme
