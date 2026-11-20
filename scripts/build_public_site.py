#!/usr/bin/env python3
"""Build the GitHub Pages artifact while КРЯКтика is on publication hold."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "_site"
HOLD_UNTIL = "2027-07-01"
EXCLUDED_ROOT_NAMES = {
    ".env.example",
    ".git",
    ".github",
    ".gitignore",
    "_site",
    "DEPLOYMENT-RU.md",
    "README.md",
    "documents",
    "scripts",
    "server.py",
    "портал.html",
    "портал",
    "портал-дизайн-система.html",
    "пользовательское-соглашение-портала.html",
    "КРЯКТИКА-НЕ-ПУБЛИКОВАТЬ-ДО-2027-07-01.md",
}
FORBIDDEN_HTML_MARKERS = ('href="/портал', "КРЯКтика")


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir()

    for source in ROOT.iterdir():
        if source.name in EXCLUDED_ROOT_NAMES:
            continue
        destination = OUTPUT / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

    unexpected = [name for name in EXCLUDED_ROOT_NAMES if (OUTPUT / name).exists()]
    if unexpected:
        raise RuntimeError(f"Excluded КРЯКтика files entered the artifact: {unexpected}")

    references: list[str] = []
    for page in OUTPUT.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        if any(marker in text for marker in FORBIDDEN_HTML_MARKERS):
            references.append(str(page.relative_to(OUTPUT)))
    if references:
        raise RuntimeError(f"Public HTML still references КРЯКтика: {references}")

    print(f"Public site built without КРЯКтика (publication hold until {HOLD_UNTIL}).")


if __name__ == "__main__":
    main()
