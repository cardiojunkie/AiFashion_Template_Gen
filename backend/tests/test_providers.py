import httpx

from app.services.providers import MockProvider, OpenAICompatibleProvider, test_profile


def test_mock_and_compatible_provider() -> None:
    mock = MockProvider({"color": "red"})
    assert '"color": "red"' in mock.complete(messages=[], model="mock").content

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "model": "demo",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"total_tokens": 1},
            },
        )
    )
    provider = OpenAICompatibleProvider(
        "https://llm.example/v1", client=httpx.Client(transport=transport)
    )
    assert provider.complete(messages=[], model="demo").usage == {"total_tokens": 1}
    assert test_profile({"provider": "mock", "model": "demo"}) == {"ok": True, "model": "demo"}
