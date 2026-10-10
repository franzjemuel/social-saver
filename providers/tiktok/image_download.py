"""Worker-only, SSRF-resistant acquisition of concrete TikTok image assets."""

import asyncio
import hashlib
import ipaddress
import os
import socket
import ssl
import warnings
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image, UnidentifiedImageError

from core.media_download import DownloadedAsset
from providers.base import MediaNotFound, SourceUnavailable


class TikTokImageDownloader:
    """Download one image from scanner candidates without using provider sessions.

    The downloader resolves each host itself, connects to that resolved public IP,
    and validates the peer IP. Redirect targets repeat the whole check, so a DNS
    answer cannot be swapped for a private address between validation and connect.
    """

    _CONTENT_TYPES = {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png"), "WEBP": ("image/webp", ".webp")}
    _MAX_HEADER_BYTES = 32 * 1024
    _MAX_CHUNK_LINE_BYTES = 1024
    _MAX_TRAILER_BYTES = 8 * 1024
    _MAX_TRAILERS = 16
    _HEADER_TOKEN_CHARS = frozenset("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

    def __init__(self, *, max_bytes: int = 25 * 1024 * 1024, max_redirects: int = 3,
                 max_candidates: int = 3, timeout_seconds: int = 20,
                 candidate_deadline_seconds: int = 60,
                 resolve=None, open_connection=None):
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.max_candidates = max_candidates
        self.timeout_seconds = timeout_seconds
        self.candidate_deadline_seconds = candidate_deadline_seconds
        self._resolve = resolve or self._resolve_public_ips
        self._open_connection = open_connection or asyncio.open_connection

    async def download_asset(self, candidates, destination: Path) -> DownloadedAsset:
        """Try a bounded ordered candidate set and return verified private bytes."""
        unique = []
        for candidate in candidates or ():
            if isinstance(candidate, str) and candidate not in unique:
                unique.append(candidate)
        if not unique:
            raise MediaNotFound("TikTok image has no acquisition candidate")
        destination.parent.mkdir(parents=True, exist_ok=True)
        last_error = None
        for candidate in unique[:self.max_candidates]:
            temporary = destination.with_name(f"{destination.name}.candidate")
            try:
                async with asyncio.timeout(self.candidate_deadline_seconds):
                    await self._fetch(candidate, temporary)
                    return await asyncio.to_thread(self._validate_and_move, temporary, destination, self.max_bytes)
            except TimeoutError:
                last_error = SourceUnavailable("TikTok image download timed out")
            except (MediaNotFound, SourceUnavailable) as exc:
                last_error = exc
            except Exception:
                last_error = SourceUnavailable("TikTok image download failed")
            finally:
                temporary.unlink(missing_ok=True)
        if isinstance(last_error, MediaNotFound):
            raise last_error
        raise SourceUnavailable("TikTok image download failed") from None

    async def _fetch(self, raw_url: str, destination: Path) -> None:
        url = raw_url
        for _ in range(self.max_redirects + 1):
            parsed = self._validate_url(url)
            ips = await self._resolve(parsed.hostname, parsed.port or 443)
            reader, writer = await self._connect(parsed.hostname, parsed.port or 443, ips)
            try:
                target = parsed.path or "/"
                if parsed.query:
                    target = f"{target}?{parsed.query}"
                host_header = parsed.hostname if parsed.port in (None, 443) else f"{parsed.hostname}:{parsed.port}"
                writer.write(
                    f"GET {target} HTTP/1.1\r\nHost: {host_header}\r\n"
                    "Accept: image/jpeg,image/png,image/webp\r\nConnection: close\r\n\r\n".encode("ascii"),
                )
                async with asyncio.timeout(self.timeout_seconds):
                    await writer.drain()
                status, headers = await self._read_headers(reader)
                if status in {301, 302, 303, 307, 308}:
                    location = headers.get("location")
                    if not location:
                        raise SourceUnavailable("TikTok image redirect failed")
                    url = urljoin(url, location)
                    continue
                if status != 200:
                    raise SourceUnavailable("TikTok image download failed")
                await self._copy_body(reader, headers, destination)
                return
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        raise SourceUnavailable("TikTok image redirect limit reached")

    @staticmethod
    def _validate_url(value: str):
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError:
            raise MediaNotFound("TikTok image candidate is unsafe") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or port not in (None, 443)):
            raise MediaNotFound("TikTok image candidate is unsafe")
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost"} or host.endswith((".localhost", ".local")):
            raise MediaNotFound("TikTok image candidate is unsafe")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise MediaNotFound("TikTok image candidate is unsafe")
        except ValueError:
            if "." not in host:
                raise MediaNotFound("TikTok image candidate is unsafe")
        return parsed

    async def _resolve_public_ips(self, host: str, port: int) -> tuple[str, ...]:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                resolved = await asyncio.get_running_loop().getaddrinfo(
                    host, port, type=socket.SOCK_STREAM,
                )
        except Exception:
            raise SourceUnavailable("TikTok image DNS resolution failed") from None
        ips = []
        for _, _, _, _, address in resolved:
            ip = address[0]
            try:
                if ipaddress.ip_address(ip).is_global and ip not in ips:
                    ips.append(ip)
            except ValueError:
                continue
        if not ips:
            raise MediaNotFound("TikTok image candidate is unsafe")
        return tuple(ips)

    async def _connect(self, host: str, port: int, ips: tuple[str, ...]):
        context = ssl.create_default_context()
        last_error = None
        for ip in ips:
            try:
                if not ipaddress.ip_address(ip).is_global:
                    raise MediaNotFound("TikTok image connection is unsafe")
            except ValueError:
                raise MediaNotFound("TikTok image connection is unsafe") from None
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    reader, writer = await self._open_connection(
                        ip, port, ssl=context, server_hostname=host, limit=self._MAX_HEADER_BYTES,
                    )
                peer = writer.get_extra_info("peername")
                peer_ip = peer[0] if peer else None
                if peer_ip not in ips or not ipaddress.ip_address(peer_ip).is_global:
                    writer.close()
                    raise MediaNotFound("TikTok image connection is unsafe")
                return reader, writer
            except MediaNotFound:
                raise
            except Exception as exc:
                last_error = exc
        raise SourceUnavailable("TikTok image connection failed") from last_error

    async def _read_headers(self, reader):
        try:
            async with asyncio.timeout(self.timeout_seconds):
                raw = await reader.readuntil(b"\r\n\r\n")
        except Exception:
            raise SourceUnavailable("TikTok image response failed") from None
        if len(raw) > self._MAX_HEADER_BYTES:
            raise MediaNotFound("TikTok image response is invalid")
        try:
            lines = raw.decode("iso-8859-1").split("\r\n")
        except UnicodeDecodeError:
            raise MediaNotFound("TikTok image response is invalid") from None
        try:
            version, code, _ = lines[0].split(" ", 2)
            status = int(code)
        except (IndexError, ValueError):
            raise MediaNotFound("TikTok image response is invalid") from None
        if version != "HTTP/1.1" or not 100 <= status <= 599:
            raise MediaNotFound("TikTok image response is invalid")
        headers = {}
        for line in lines[1:]:
            if not line:
                continue
            if line[:1] in {" ", "\t"} or ":" not in line:
                raise MediaNotFound("TikTok image response is invalid")
            key, value = line.split(":", 1)
            if not self._valid_header_name(key) or key.lower() in headers:
                raise MediaNotFound("TikTok image response is invalid")
            if any(ord(character) < 0x20 and character != "\t" for character in value):
                raise MediaNotFound("TikTok image response is invalid")
            headers[key.lower()] = value.strip()
        return status, headers

    async def _copy_body(self, reader, headers, destination: Path) -> None:
        length = headers.get("content-length")
        transfer_encoding = headers.get("transfer-encoding")
        if transfer_encoding is not None and length is not None:
            raise MediaNotFound("TikTok image response is invalid")
        if transfer_encoding is not None and transfer_encoding.lower() != "chunked":
            raise MediaNotFound("TikTok image response is invalid")
        if transfer_encoding is None and length is None:
            raise MediaNotFound("TikTok image response is invalid")
        if length is not None:
            try:
                if not length.isascii() or not length.isdecimal():
                    raise ValueError
                expected = int(length)
            except ValueError:
                raise MediaNotFound("TikTok image response is invalid") from None
            if expected < 1 or expected > self.max_bytes:
                raise MediaNotFound("TikTok image exceeds archive size limit")
        total = 0
        temporary = destination.with_name(destination.name + ".part")
        try:
            with temporary.open("wb") as file:
                if transfer_encoding is not None:
                    while True:
                        line = await self._readline(reader, self._MAX_CHUNK_LINE_BYTES)
                        try:
                            size_token, *extensions = line[:-2].split(b";", 1)
                            if not size_token or any(byte not in b"0123456789abcdefABCDEF" for byte in size_token):
                                raise ValueError
                            if extensions and (len(extensions[0]) > self._MAX_CHUNK_LINE_BYTES - len(size_token) - 1
                                               or any(byte < 0x20 or byte == 0x7f for byte in extensions[0])):
                                raise ValueError
                            chunk_size = int(size_token, 16)
                        except ValueError:
                            raise MediaNotFound("TikTok image response is invalid") from None
                        if chunk_size == 0:
                            await self._read_trailers(reader)
                            break
                        if chunk_size < 0 or chunk_size > self.max_bytes - total:
                            raise MediaNotFound("TikTok image exceeds archive size limit")
                        remaining = chunk_size
                        while remaining:
                            chunk = await self._read_exactly(reader, min(256 * 1024, remaining))
                            remaining -= len(chunk)
                            total = self._write_chunk(file, chunk, total)
                        await self._read_exactly(reader, 2)
                else:
                    remaining = expected
                    while remaining:
                        chunk = await self._read_exactly(reader, min(256 * 1024, remaining))
                        remaining -= len(chunk)
                        total = self._write_chunk(file, chunk, total)
            if total == 0:
                raise MediaNotFound("TikTok image validation failed")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_chunk(self, file, chunk: bytes, total: int) -> int:
        total += len(chunk)
        if total > self.max_bytes:
            raise MediaNotFound("TikTok image exceeds archive size limit")
        file.write(chunk)
        return total

    async def _readline(self, reader, maximum: int) -> bytes:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                line = await reader.readline()
        except Exception:
            raise SourceUnavailable("TikTok image response failed") from None
        if not line.endswith(b"\r\n") or len(line) > maximum:
            raise MediaNotFound("TikTok image response is invalid")
        return line

    async def _read_trailers(self, reader) -> None:
        total = 0
        names = set()
        for _ in range(self._MAX_TRAILERS):
            line = await self._readline(reader, self._MAX_CHUNK_LINE_BYTES)
            total += len(line)
            if total > self._MAX_TRAILER_BYTES:
                raise MediaNotFound("TikTok image response is invalid")
            if line == b"\r\n":
                return
            if line[:1] in {b" ", b"\t"} or b":" not in line:
                raise MediaNotFound("TikTok image response is invalid")
            key, value = line[:-2].split(b":", 1)
            try:
                name = key.decode("ascii").lower()
            except UnicodeDecodeError:
                raise MediaNotFound("TikTok image response is invalid") from None
            if (not self._valid_header_name(name) or name in names
                    or name in {"content-length", "transfer-encoding"}
                    or any(byte < 0x20 and byte != 0x09 for byte in value)):
                raise MediaNotFound("TikTok image response is invalid")
            names.add(name)
        raise MediaNotFound("TikTok image response is invalid")

    @classmethod
    def _valid_header_name(cls, value: str) -> bool:
        return bool(value) and value.isascii() and all(character in cls._HEADER_TOKEN_CHARS for character in value)

    async def _read_exactly(self, reader, size: int) -> bytes:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await reader.readexactly(size)
        except Exception:
            raise SourceUnavailable("TikTok image response failed") from None

    @classmethod
    def _validate_and_move(cls, source: Path, destination: Path, max_bytes: int) -> DownloadedAsset:
        try:
            if source.stat().st_size < 1 or source.stat().st_size > max_bytes:
                raise MediaNotFound("TikTok image validation failed")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as image:
                    image.verify()
                with Image.open(source) as image:
                    image.load()
                    if image.format not in cls._CONTENT_TYPES or image.width < 1 or image.height < 1:
                        raise MediaNotFound("TikTok image validation failed")
                    if image.width * image.height > 40_000_000:
                        raise MediaNotFound("TikTok image validation failed")
                    content_type, suffix = cls._CONTENT_TYPES[image.format]
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise MediaNotFound("TikTok image validation failed") from None
        temporary = destination.with_name(destination.name + ".part")
        try:
            digest = hashlib.sha256()
            with source.open("rb") as input_file, temporary.open("wb") as output_file:
                while chunk := input_file.read(256 * 1024):
                    digest.update(chunk)
                    output_file.write(chunk)
            final_destination = destination.with_suffix(suffix)
            os.replace(temporary, final_destination)
            return DownloadedAsset(final_destination, final_destination.stat().st_size, digest.hexdigest(), content_type)
        finally:
            temporary.unlink(missing_ok=True)
