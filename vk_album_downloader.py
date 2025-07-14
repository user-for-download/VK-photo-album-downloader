#!/usr/bin/env python3
"""
Async downloader for a single VK photo album.

Highlights
----------
• Handles both desktop and mobile URLs
• Fully async (httpx + aiofiles)
• Resume-safe (checkpoint file with already-scraped URLs)
• Structured logging (console + rotating file handler)
• Graceful retries with exponential back-off
• Concurrency limiter
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Iterable

import aiofiles
import httpx
import orjson
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

# ──────────────────────────────── CONFIG ──────────────────────────────── #

def get_default_api_headers() -> dict[str, str]:
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/108.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/x-www-form-urlencoded',
    }

def get_default_download_headers() -> dict[str, str]:
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/108.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }

@dataclass(slots=True, frozen=True)
class Settings:
    offset_step: int = 40
    min_delay: float = 0
    max_delay: float = 1
    concurrent_downloads: int = 8
    url_suffix_to_remove: str = '&from=bu&cs=240x0'
    headers_api: dict[str, str] = field(default_factory=get_default_api_headers)
    headers_download: dict[str, str] = field(default_factory=get_default_download_headers)


SETTINGS: Final = Settings()


# ──────────────────────────────── UTILS ───────────────────────────────── #

ALBUM_ID_RE = re.compile(r'album(-?\d+_\d+)')

def parse_album_id(url: str) -> str | None:
    """Return the VK album id portion: album-123_456 → -123_456."""
    m = ALBUM_ID_RE.search(url)
    return m.group(1) if m else None


def setup_logging(log_path: Path) -> None:
    fmt = "%(asctime)s [%(levelname)-8s] %(message)s"
    date_fmt = "%H:%M:%S"

    root = logging.getLogger()
    if root.hasHandlers():
        root.handlers.clear()
    root.setLevel(logging.INFO)

    # Console
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(fmt, date_fmt))
    root.addHandler(sh)

    # File (rotating: 5 × 10 MB)
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 2**20, backupCount=5, encoding='utf-8'
    )
    fh.setFormatter(logging.Formatter(fmt, date_fmt))
    root.addHandler(fh)


# ──────────────────────────────── SCRAPER ─────────────────────────────── #

class AlbumScraper:
    def __init__(
        self,
        album_url: str,
        dest_dir: Path,
        client: httpx.AsyncClient,
        settings: Settings = SETTINGS,
    ):
        self.album_url = album_url
        self.dest = dest_dir
        self.client = client
        self.sett = settings

        self.url_log = self.dest / "scraped_urls.txt"
        self.failed_log = self.dest / "failed_downloads.txt"

    # ── Scraping Phase ─────────────────────────────────────────────── #

    async def fetch_batch(self, offset: int) -> list[str]:
        """Fetch one HTML chunk, extract image URLs from CSS-style background-image fields."""
        payload = {'al': 1, 'offset': offset, 'part': 1, 'rev': 1}
        headers = {**self.sett.headers_api, 'Referer': self.album_url}

        async for attempt in AsyncRetrying(
            wait=wait_random_exponential(multiplier=1, max=20),
            stop=stop_after_attempt(3),
            retry=(
                retry_if_exception_type(httpx.HTTPError)
                | retry_if_exception_type(asyncio.TimeoutError)
            ),
            reraise=True,
        ):
            with attempt:
                logging.debug("POST offset=%s (try %s)", offset, attempt.retry_state.attempt_number)
                resp = await self.client.post(
                    self.album_url, headers=headers, data=payload, timeout=30.0
                )
                resp.raise_for_status()

        raw = resp.text.strip()
        if raw.startswith('<!--') and raw.endswith('-->'):
            raw = raw[4:-3].strip()

        try:
            data = orjson.loads(raw)
        except ValueError:
            logging.warning("Offset %s: payload not JSONish, skipping.", offset)
            return []

        payload_data: list = data.get('payload', [])
        html_blob = next(
            (item for item in payload_data[1] if isinstance(item, str) and 'background-image' in item), ''
        ) if len(payload_data) > 1 else ''

        urls = re.findall(r'url\((.*?)\)', html_blob)
        cleaned = [
            u.replace('\\/', '/').removesuffix(self.sett.url_suffix_to_remove) for u in urls
        ]
        return cleaned

    async def scrape_all_urls(self) -> list[str]:
        offset = 0
        all_urls: list[str] = []

        # (Re)load checkpoint if present
        if self.url_log.exists():
            async with aiofiles.open(self.url_log, 'r', encoding='utf-8') as f:
                existing = await f.readlines()
            all_urls.extend([u.strip() for u in existing])
            logging.info("Loaded %d URLs from checkpoint.", len(all_urls))

        while True:
            batch = await self.fetch_batch(offset)
            # Find only the URLs that are not already in our master list
            new_urls = [u for u in batch if u and u not in all_urls]

            if not new_urls and offset > 0: # If no *new* URLs are found after the first page
                logging.info("No more new photos found at offset %s. Scraping done.", offset)
                break

            if new_urls:
                all_urls.extend(new_urls)
                await self._append_to_checkpoint(new_urls)
                logging.info(
                    "Offset %s: %d new URLs (total unique %d).",
                    offset, len(new_urls), len(all_urls),
                )

            if not batch and offset == 0: # Handle case where album is empty or fails on first try
                logging.warning("Could not find any URLs on the first page. Aborting scrape.")
                break

            offset += self.sett.offset_step
            await asyncio.sleep(random.uniform(self.sett.min_delay, self.sett.max_delay))

        # Deduplicate once at the end for final list
        unique = sorted(set(all_urls))
        logging.info("Scraping finished — %d unique URLs.", len(unique))
        return unique

    async def _append_to_checkpoint(self, urls: Iterable[str]) -> None:
        async with aiofiles.open(self.url_log, 'a', encoding='utf-8') as f:
            await f.writelines(u + '\n' for u in urls)

    # ── Download Phase ─────────────────────────────────────────────── #

    async def download_one(self, url: str, index: int, sem: asyncio.Semaphore) -> None:
        file_ext = Path(url.split('?', 1)[0]).suffix or '.jpg'
        filename = f"{index:04d}{file_ext}"
        out_path = self.dest / filename

        if out_path.exists():
            logging.debug("Skip existing %s", filename)
            return

        async with sem:  # limit concurrency
            headers = {**self.sett.headers_download, 'Referer': self.album_url}
            try:
                async for attempt in AsyncRetrying(
                    wait=wait_random_exponential(multiplier=1, max=10),
                    stop=stop_after_attempt(3),
                    retry=retry_if_exception_type(httpx.HTTPError),
                    reraise=True,
                ):
                    with attempt:
                        resp = await self.client.get(
                            url, headers=headers, timeout=30.0, follow_redirects=True
                        )
                        resp.raise_for_status()

                async with aiofiles.open(out_path, 'wb') as f:
                    await f.write(resp.content)
                logging.info("✔ %s", filename)

            except Exception as e:
                logging.warning("✘ %s (%s)", filename, e)
                async with aiofiles.open(self.failed_log, 'a', encoding='utf-8') as f:
                    await f.write(url + '\n')

    async def download_all(self, urls: list[str]) -> None:
        sem = asyncio.Semaphore(self.sett.concurrent_downloads)
        tasks = [
            self.download_one(u, i, sem)
            for i, u in enumerate(urls, 1)
        ]
        await asyncio.gather(*tasks, return_exceptions=False)


# ──────────────────────────────── CLI / MAIN ─────────────────────────── #

async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Download all photos from a vk.com album.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-u", "--album-link", required=True, help="Full VK album URL (desktop or mobile).")
    parser.add_argument("-o", "--out", help="Destination directory (default: ./vk_album_<id>/)")
    args = parser.parse_args()

    raw_url = args.album_link or ""
    album_url = raw_url.replace('//m.vk.com', '//vk.com').split('?')[0]

    album_id = parse_album_id(album_url)
    if not album_id:
        sys.exit(f"❌  Could not parse album id from provided URL: {raw_url}")

    dest_dir = Path(args.out or f"vk_album_{album_id}")

    # Create the destination directory *before* setting up logging.
    dest_dir.mkdir(parents=True, exist_ok=True)

    log_path = dest_dir / "download.log"
    # Now, this call will succeed because the directory exists.
    setup_logging(log_path)

    logging.info("Normalized Album URL: %s", album_url)
    logging.info("Destination folder: %s", dest_dir)

    async with httpx.AsyncClient(http2=True) as client:
        # The scraper no longer needs to create the directory itself.
        scraper = AlbumScraper(album_url, dest_dir, client)
        urls = await scraper.scrape_all_urls()
        if not urls:
            logging.info("No URLs scraped — nothing to download.")
            return
        await scraper.download_all(urls)

    logging.info("👍  All done — images saved in %s", dest_dir)


def main() -> None:  # Sync wrapper because Windows + asyncio.run quirks
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")


if __name__ == "__main__":
    main()
