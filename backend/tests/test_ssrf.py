import pytest

from app.services.image_downloader import ImageDownloadError, validate_public_url


def resolver(address: str):
    return lambda host, port, **kwargs: [(2, 1, 6, "", (address, port))]


@pytest.mark.parametrize(
    "url,address",
    [
        ("http://127.0.0.1/a", "127.0.0.1"),
        ("http://metadata/a", "169.254.169.254"),
        ("http://internal/a", "10.0.0.2"),
        ("file:///etc/passwd", "93.184.216.34"),
    ],
)
def test_rejects_non_public_destinations(url: str, address: str) -> None:
    with pytest.raises(ImageDownloadError):
        validate_public_url(url, resolver(address))


def test_accepts_public_http_destination() -> None:
    assert validate_public_url("https://example.com/a", resolver("93.184.216.34"))
