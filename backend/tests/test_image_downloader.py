from io import BytesIO

import httpx
from PIL import Image

from app.services.image_downloader import download_image, normalize_image, process_image_urls


def public_resolver(host: str, port: int, **_: object) -> list[tuple[object, ...]]:
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def test_bounded_download_and_normalization() -> None:
    source = BytesIO()
    Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(source, "PNG")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=source.getvalue())
        )
    )
    downloaded = download_image(
        "https://example.com/a.png", client=client, resolver=public_resolver
    )
    normalized, metadata = normalize_image(downloaded, canvas_size=100)
    with Image.open(BytesIO(normalized)) as image:
        assert image.size == (100, 100)
        assert image.mode == "RGB"
    assert metadata["content_type"] == "image/jpeg"


def test_batch_reuses_matching_request_cache_and_isolates_failures() -> None:
    source = BytesIO()
    Image.new("RGB", (1, 1)).save(source, "PNG")
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url.endswith("bad"):
            raise ValueError("bad image")
        return source.getvalue()

    stored: list[str] = []
    results = process_image_urls(
        ["https://x/1", "https://x/1", "https://x/bad"],
        downloader=fetch,
        store=lambda name, data: stored.append(name) or name,
    )
    assert [result["status"] for result in results] == ["ok", "ok", "error"]
    assert results[1]["cached"] is True
    assert calls == ["https://x/1", "https://x/bad"]
    assert len(stored) == 1


def test_batch_can_discard_normalized_bytes_after_storage() -> None:
    source = BytesIO()
    Image.new("RGB", (1, 1)).save(source, "PNG")
    cache = {}

    results = process_image_urls(
        ["https://x/1", "https://x/1"],
        cache=cache,
        downloader=lambda _: source.getvalue(),
        store=lambda name, _: name,
        retain_data=False,
    )

    assert all("data" not in result for result in results)
    assert results[1]["cached"] is True
    assert next(iter(cache.values()))[0] is None
