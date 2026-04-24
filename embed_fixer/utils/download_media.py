from __future__ import annotations

import asyncio
import hashlib
import io
import mimetypes
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import aiofiles
import aiohttp
import discord
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Sequence


def _safe_filename(url: str, media_type: str | None, *, unique: bool = False) -> str:
    parsed = urlsplit(url)
    basename = unquote(parsed.path.rsplit("/", maxsplit=1)[-1])
    basename = basename.split("?", maxsplit=1)[0] or "image"
    basename = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._") or "image"

    stem, dot, extension = basename.rpartition(".")
    if not dot:
        stem = basename
        extension = ""

    if media_type:
        content_type = media_type.split(";", maxsplit=1)[0].strip().lower()
        guessed_extension = mimetypes.guess_extension(content_type)
        if guessed_extension:
            extension = guessed_extension.removeprefix(".")
        elif "/" in content_type:
            extension = content_type.rsplit("/", maxsplit=1)[-1]

    filename = f"{stem}.{extension}" if extension else stem
    if not unique:
        return filename

    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"{digest}-{filename}"


class MediaDownloader:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        media_urls: Sequence[str],
        download_dir: str | Path | None = None,
    ) -> None:
        self.media_urls = media_urls
        self.session = session
        self.files: dict[str, discord.File] = {}
        self.local_filenames: dict[str, str] = {}
        self.download_dir = download_dir

    async def _download(self, url: str, *, spoiler: bool, filesize_limit: int) -> None:
        timeout = aiohttp.ClientTimeout(total=10)

        try:
            async with self.session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    return

                content_length = resp.headers.get("Content-Length")
                if content_length is not None and int(content_length) > filesize_limit:
                    return

                data = await resp.read()
                if len(data) > filesize_limit:
                    return

                media_type = resp.headers.get("Content-Type")
        except TimeoutError:
            logger.warning(f"Timeout downloading media {url}")
            return
        except Exception:
            logger.exception(f"Failed to download media {url}")
            return

        filename = _safe_filename(url, media_type, unique=self.download_dir is not None)

        if self.download_dir is not None:
            try:
                download_dir = self.download_dir
                if isinstance(download_dir, str):
                    download_dir = Path(download_dir)

                download_dir.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(download_dir / filename, "wb") as f:
                    await f.write(data)
            except Exception:
                logger.exception(f"Failed to save downloaded media {url}")
            else:
                self.local_filenames[url] = filename

        self.files[url] = discord.File(io.BytesIO(data), filename=filename, spoiler=spoiler)

    async def start(self, *, spoiler: bool, filesize_limit: int) -> None:
        async with asyncio.TaskGroup() as tg:
            for media_url in self.media_urls:
                tg.create_task(
                    self._download(media_url, spoiler=spoiler, filesize_limit=filesize_limit)
                )
