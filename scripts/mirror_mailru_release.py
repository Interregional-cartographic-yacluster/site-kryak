#!/usr/bin/env python3
"""Download public Cloud Mail.ru files listed in a manifest for GitHub Release upload."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DISPATCHER = "https://cloud.mail.ru/api/v2/dispatcher"
USER_AGENT = "Mozilla/5.0 (compatible; site-kryak-archive-mirror/1.0)"
CHUNK_SIZE = 1024 * 1024


def request(url: str, *, timeout: int = 180):
    return urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)


def download_host() -> str:
    with request(DISPATCHER, timeout=60) as response:
        payload = json.load(response)
    hosts = payload.get("body", {}).get("weblink_get") or []
    if not hosts:
        raise RuntimeError("Cloud Mail.ru dispatcher returned no public download host")
    return str(hosts[0]["url"]).rstrip("/")


def download(entry: dict, host: str, output: Path) -> None:
    public_url = entry["source_url"]
    marker = "/public/"
    if marker not in public_url:
        raise ValueError(f"Not a public Cloud Mail.ru URL: {public_url}")
    weblink = public_url.split(marker, 1)[1].strip("/")
    direct_url = f"{host}/{weblink}"
    destination = output / entry["release_filename"]
    temporary = destination.with_suffix(destination.suffix + ".part")

    last_error = None
    for attempt in range(1, 7):
        try:
            print(f"Downloading {entry['archive_id']} (attempt {attempt})")
            with request(direct_url, timeout=600) as response, temporary.open("wb") as target:
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type:
                    raise RuntimeError(f"Cloud returned HTML instead of a file for {public_url}")
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    target.write(chunk)
            size = temporary.stat().st_size
            expected = int(entry["expected_bytes"])
            # Published sizes are rounded, so allow a generous but useful range.
            if expected and not (expected * 0.70 <= size <= expected * 1.30):
                raise RuntimeError(f"Unexpected size for {public_url}: {size} bytes, expected about {expected}")
            if size < 100:
                raise RuntimeError(f"Downloaded file is implausibly small: {public_url}")
            temporary.replace(destination)
            print(f"Saved {destination.name}: {size} bytes")
            return
        except (HTTPError, URLError, OSError, RuntimeError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt == 6:
                break
            time.sleep(attempt * 5)
            # CDN hosts can rotate, but the direct URL remains valid for normal short runs.
    raise RuntimeError(f"Failed to download {public_url}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    entries = json.loads(args.manifest.read_text(encoding="utf-8"))["documents"]
    args.output.mkdir(parents=True, exist_ok=True)
    host = download_host()
    print(f"Using Cloud Mail.ru download host: {host}")
    for entry in entries:
        download(entry, host, args.output)

    downloaded = sorted(p.name for p in args.output.iterdir() if p.is_file())
    expected = sorted(entry["release_filename"] for entry in entries)
    if downloaded != expected:
        raise RuntimeError("Downloaded file set does not match the manifest")
    print(f"Downloaded all {len(downloaded)} archive files")


if __name__ == "__main__":
    main()
