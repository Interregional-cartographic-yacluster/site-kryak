#!/usr/bin/env python3
"""Generate static clean-URL entry points for every КРЯКтика screen."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "портал.html"
OUTPUT = ROOT / "портал"
ROUTES = (
    "главная",
    "услуги",
    "услуга",
    "новое-обращение",
    "обращения",
    "рабочий-стол",
    "проекты",
    "проект",
    "документы",
    "уведомления",
    "профиль",
    "управление",
)


def main() -> None:
    content = SOURCE.read_text(encoding="utf-8")
    OUTPUT.mkdir(exist_ok=True)
    expected = {f"{route}.html" for route in ROUTES}
    for old_file in OUTPUT.glob("*.html"):
        if old_file.name not in expected:
            old_file.unlink()
    for route in ROUTES:
        (OUTPUT / f"{route}.html").write_text(content, encoding="utf-8")
    print(f"Generated {len(ROUTES)} portal routes in {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
