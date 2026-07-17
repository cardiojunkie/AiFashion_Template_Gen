from __future__ import annotations

import hashlib
import ipaddress
import socket
from collections.abc import Callable, MutableMapping, Sequence
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError


class ImageDownloadError(ValueError):
    """A safe, row-reportable image download failure."""


Resolver = Callable[..., Sequence[tuple[Any, ...]]]


def validate_public_url(url: str, resolver: Resolver = socket.getaddrinfo) -> str:
    """Reject malformed and non-public HTTP(S) destinations before every request."""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ImageDownloadError("image URL is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageDownloadError("only absolute HTTP/HTTPS image URLs are allowed")
    if parsed.username or parsed.password:
        raise ImageDownloadError("image URLs cannot contain credentials")
    try:
        addresses = {
            ipaddress.ip_address(item[4][0].split("%", 1)[0])
            for item in resolver(parsed.hostname, parsed.port or 0, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise ImageDownloadError("image host could not be resolved") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ImageDownloadError("image host resolves to a non-public address")
    return url


def download_image(
    url: str,
    *,
    max_bytes: int = 15 * 1024 * 1024,
    timeout: float = 15,
    retries: int = 2,
    max_redirects: int = 5,
    resolver: Resolver = socket.getaddrinfo,
    client: httpx.Client | None = None,
) -> bytes:
    """Fetch a bounded image with SSRF checks repeated across redirects."""
    own_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)
    try:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            current_url = url
            try:
                for _ in range(max_redirects + 1):
                    validate_public_url(current_url, resolver)
                    context = client.stream("GET", current_url, headers={"Accept": "image/*"})
                    with context as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise ImageDownloadError("redirect is missing a location")
                            current_url = urljoin(current_url, location)
                            continue
                        if response.status_code >= 500 and attempt < retries:
                            raise httpx.HTTPStatusError(
                                "image server error", request=response.request, response=response
                            )
                        response.raise_for_status()
                        declared = response.headers.get("content-length")
                        try:
                            if declared and int(declared) > max_bytes:
                                raise ImageDownloadError("image exceeds the download byte limit")
                        except ValueError as exc:
                            raise ImageDownloadError("image has an invalid content length") from exc
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            data.extend(chunk)
                            if len(data) > max_bytes:
                                raise ImageDownloadError("image exceeds the download byte limit")
                        if not data:
                            raise ImageDownloadError("image response is empty")
                        return bytes(data)
                raise ImageDownloadError("image exceeded the redirect limit")
            except ImageDownloadError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt == retries:
                    break
        raise ImageDownloadError("image request failed after retries") from last_error
    finally:
        if own_client:
            client.close()


def normalize_image(
    data: bytes,
    *,
    canvas_size: int = 1500,
    quality: int = 90,
    max_pixels: int = 40_000_000,
) -> tuple[bytes, dict[str, object]]:
    """Apply orientation, flatten alpha, and aspect-fit onto a white JPEG canvas."""
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > max_pixels:
                raise ImageDownloadError("image exceeds the pixel limit")
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                alpha_image = image.convert("RGBA")
                white = Image.new("RGBA", alpha_image.size, "white")
                white.alpha_composite(alpha_image)
                image = white.convert("RGB")
            else:
                image = image.convert("RGB")
            image.thumbnail((canvas_size, canvas_size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (canvas_size, canvas_size), "white")
            canvas.paste(
                image,
                ((canvas_size - image.width) // 2, (canvas_size - image.height) // 2),
            )
            output = BytesIO()
            canvas.save(output, "JPEG", quality=quality, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageDownloadError("response is not a supported image") from exc
    normalized = output.getvalue()
    return normalized, {
        "width": canvas_size,
        "height": canvas_size,
        "bytes": len(normalized),
        "checksum": hashlib.sha256(normalized).hexdigest(),
        "content_type": "image/jpeg",
    }


def process_image_urls(
    urls: Sequence[str],
    *,
    cache: MutableMapping[str, tuple[bytes | None, dict[str, object]]] | None = None,
    store: Callable[[str, bytes], str] | None = None,
    downloader: Callable[[str], bytes] = download_image,
    canvas_size: int = 1500,
    quality: int = 90,
    max_pixels: int = 40_000_000,
    retain_data: bool = True,
) -> list[dict[str, object]]:
    """Process independently so one bad row never aborts the batch."""
    cache = cache if cache is not None else {}
    results: list[dict[str, object]] = []
    for position, url in enumerate(urls, start=1):
        key = hashlib.sha256(f"{url}\0{canvas_size}\0{quality}\0{max_pixels}".encode()).hexdigest()
        try:
            cached = key in cache
            normalized, metadata = cache.get(key) or normalize_image(
                downloader(url),
                canvas_size=canvas_size,
                quality=quality,
                max_pixels=max_pixels,
            )
            filename = f"image_{position}_{metadata['checksum'][:12]}.jpg"
            storage_key = str(metadata.get("storage_key") or "")
            if not storage_key:
                storage_key = store(filename, normalized) if store else filename
                metadata = {**metadata, "storage_key": storage_key}
                cache[key] = (normalized if retain_data else None, metadata)
            result = {
                "position": position,
                "url": url,
                "status": "ok",
                "cached": cached,
                "storage_key": storage_key,
                **{name: value for name, value in metadata.items() if name != "storage_key"},
            }
            if retain_data:
                result["data"] = normalized
            results.append(result)
        except (ImageDownloadError, ValueError, httpx.HTTPError) as exc:
            results.append({"position": position, "url": url, "status": "error", "error": str(exc)})
    return results
