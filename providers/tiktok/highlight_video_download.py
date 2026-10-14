"""Hardened, worker-only transport for ephemeral TikTok Highlight MP4 descriptors."""

import asyncio
import hashlib
import os
import struct
from pathlib import Path

from core.media_download import DownloadedAsset
from providers.base import MediaNotFound, SourceUnavailable
from providers.tiktok.image_download import TikTokImageDownloader


class TikTokHighlightMp4Downloader:
    """Acquire one descriptor through the pinned-IP HTTPS transport.

    The existing image downloader owns the security-sensitive HTTP/1.1 parser,
    DNS pinning, peer verification, TLS hostname verification, redirect checks,
    and response-size enforcement. This class only changes byte validation and
    output normalization for MP4 assets; descriptor values never leave process
    memory or appear in exceptions.
    """

    _MAX_BOXES = 10_000
    def __init__(
        self,
        *,
        max_bytes: int = 1_000_000_000,
        max_redirects: int = 3,
        max_candidates: int = 3,
        timeout_seconds: int = 30,
        candidate_deadline_seconds: int = 120,
        resolve=None,
        open_connection=None,
        transport: TikTokImageDownloader | None = None,
    ):
        if transport is not None and (resolve is not None or open_connection is not None):
            raise ValueError("transport hooks require a dedicated transport")
        self.max_bytes = max_bytes
        self.max_candidates = max_candidates
        self.candidate_deadline_seconds = candidate_deadline_seconds
        self._transport = transport or TikTokImageDownloader(
            max_bytes=max_bytes,
            max_redirects=max_redirects,
            max_candidates=max_candidates,
            timeout_seconds=timeout_seconds,
            candidate_deadline_seconds=candidate_deadline_seconds,
            resolve=resolve,
            open_connection=open_connection,
        )

    async def download_asset(self, candidates, destination: Path) -> DownloadedAsset:
        """Try bounded candidates and return only validated MP4 bytes."""
        unique = []
        for candidate in candidates or ():
            if isinstance(candidate, str) and candidate not in unique:
                unique.append(candidate)
        if not unique:
            raise MediaNotFound("TikTok Highlight video has no acquisition candidate")

        destination.parent.mkdir(parents=True, exist_ok=True)
        last_error = None
        for candidate in unique[:self.max_candidates]:
            temporary = destination.with_name(f"{destination.name}.candidate")
            try:
                async with asyncio.timeout(self.candidate_deadline_seconds):
                    await self._transport._fetch(candidate, temporary)
                    return await asyncio.to_thread(
                        self._validate_and_move, temporary, destination, self.max_bytes,
                    )
            except TimeoutError:
                last_error = SourceUnavailable("TikTok Highlight video download timed out")
            except (MediaNotFound, SourceUnavailable) as exc:
                last_error = exc
            except Exception:
                last_error = SourceUnavailable("TikTok Highlight video download failed")
            finally:
                temporary.unlink(missing_ok=True)

        if isinstance(last_error, MediaNotFound):
            raise last_error
        raise SourceUnavailable("TikTok Highlight video download failed") from None

    async def download(self, candidates, destination: Path) -> DownloadedAsset:
        """Compatibility seam for the Highlight media dispatcher."""
        return await self.download_asset(candidates, destination)

    @classmethod
    def _validate_and_move(cls, source: Path, destination: Path, max_bytes: int) -> DownloadedAsset:
        try:
            size = source.stat().st_size
        except OSError:
            raise SourceUnavailable("local temporary file failure") from None
        if size < 16 or size > max_bytes or not cls._is_structurally_valid_mp4(source, size):
            raise MediaNotFound("TikTok Highlight video validation failed")

        temporary = destination.with_name(destination.name + ".part")
        final_destination = destination.with_suffix(".mp4")
        try:
            digest = hashlib.sha256()
            copied = 0
            with source.open("rb") as input_file, temporary.open("wb") as output_file:
                while chunk := input_file.read(256 * 1024):
                    copied += len(chunk)
                    if copied > max_bytes:
                        raise MediaNotFound("TikTok Highlight video exceeds archive size limit")
                    digest.update(chunk)
                    output_file.write(chunk)
            if copied != size:
                raise MediaNotFound("TikTok Highlight video validation failed")
            os.replace(temporary, final_destination)
            return DownloadedAsset(final_destination, copied, digest.hexdigest(), "video/mp4")
        except OSError:
            raise SourceUnavailable("local temporary file failure") from None
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def _is_structurally_valid_mp4(cls, path: Path, file_size: int) -> bool:
        """Perform bounded ISO-BMFF top-level box validation without decoding.

        A usable MP4 must begin with an ``ftyp`` box and include both movie
        metadata (``moov`` or fragmented ``moof``) and media bytes (``mdat``).
        This rejects HTML/JSON/error responses and malformed/truncated box
        layouts while avoiding a heavyweight media decoder in the worker.
        """
        offset = 0
        boxes = 0
        saw_ftyp = saw_metadata = saw_media = False
        try:
            with path.open("rb") as file:
                while offset < file_size and boxes < cls._MAX_BOXES:
                    header = file.read(8)
                    if len(header) != 8:
                        return False
                    box_size, box_type = struct.unpack(">I4s", header)
                    header_size = 8
                    if box_size == 1:
                        extended = file.read(8)
                        if len(extended) != 8:
                            return False
                        box_size = struct.unpack(">Q", extended)[0]
                        header_size = 16
                    elif box_size == 0:
                        box_size = file_size - offset
                    if box_size < header_size or box_size > file_size - offset:
                        return False
                    if any(byte < 0x20 or byte > 0x7E for byte in box_type):
                        return False
                    if boxes == 0:
                        if box_type != b"ftyp" or box_size < 16:
                            return False
                        saw_ftyp = True
                    if box_type == b"moov" or box_type == b"moof":
                        saw_metadata = True
                    if box_type == b"mdat" and box_size > header_size:
                        saw_media = True
                    offset += box_size
                    file.seek(offset)
                    boxes += 1
        except OSError:
            return False
        return offset == file_size and boxes < cls._MAX_BOXES and saw_ftyp and saw_metadata and saw_media
