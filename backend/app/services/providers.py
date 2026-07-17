from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx


@dataclass(frozen=True)
class ProviderResult:
    content: str
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    cost: float | None = None


class Provider(Protocol):
    def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        model: str,
        temperature: float = 0,
        response_format: Mapping[str, Any] | None = None,
    ) -> ProviderResult: ...


class MockProvider:
    def __init__(self, responses: object | Sequence[object]) -> None:
        if isinstance(responses, Sequence) and not isinstance(responses, (str, bytes, dict)):
            self.responses = list(responses)
        else:
            self.responses = [responses]
        if not self.responses:
            raise ValueError("mock provider requires at least one response")
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        model: str = "mock",
        temperature: float = 0,
        response_format: Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "temperature": temperature,
                "response_format": response_format,
            }
        )
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, ProviderResult):
            return response
        content = response if isinstance(response, str) else json.dumps(response)
        return ProviderResult(content=content, model=model, usage={"mock_calls": 1}, cost=0)


class LiteLLMProvider:
    def __init__(self, *, api_key: str | None = None, api_base: str | None = None) -> None:
        self.api_key = api_key
        self.api_base = api_base

    def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        model: str,
        temperature: float = 0,
        response_format: Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        import litellm

        response = litellm.completion(
            model=model,
            messages=list(messages),
            temperature=temperature,
            response_format=response_format,
            api_key=self.api_key,
            api_base=self.api_base,
        )
        content = response.choices[0].message.content
        response_usage = getattr(response, "usage", None)
        usage = (
            response_usage.model_dump()
            if hasattr(response_usage, "model_dump")
            else dict(response_usage or {})
        )
        hidden = getattr(response, "_hidden_params", {}) or {}
        return ProviderResult(
            content=content or "",
            model=getattr(response, "model", model),
            usage=usage,
            cost=hidden.get("response_cost"),
        )


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        allow_insecure: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if not parsed.hostname or parsed.scheme not in {"https", "http"}:
            raise ValueError("LLM base URL must be absolute HTTP/HTTPS")
        if parsed.scheme != "https" and not allow_insecure:
            raise ValueError("LLM base URL must use HTTPS")
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.client = client

    def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        model: str,
        temperature: float = 0,
        response_format: Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = dict(response_format)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        own_client = self.client is None
        client = self.client or httpx.Client(timeout=60, trust_env=False)
        try:
            response = client.post(
                urljoin(self.base_url, "chat/completions"), json=payload, headers=headers
            )
            response.raise_for_status()
            body = response.json()
        finally:
            if own_client:
                client.close()
        choice = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        return ProviderResult(
            content=choice,
            model=body.get("model", model),
            usage=dict(usage),
            cost=body.get("cost") or usage.get("cost"),
        )


def test_profile(
    profile: Mapping[str, Any],
    api_key: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    """Run the same small completion path workers use, returning no secret details."""
    provider_type = str(
        profile.get("adapter") or profile.get("provider") or profile.get("type") or "mock"
    ).casefold()
    model = str(profile.get("model_name") or profile.get("model") or "mock")
    try:
        if provider_type == "mock":
            provider: Provider = MockProvider({"ok": True})
        elif provider_type in {"openai-compatible", "openai_compatible", "http"}:
            provider = OpenAICompatibleProvider(
                str(
                    profile.get("endpoint_url")
                    or profile.get("base_url")
                    or profile.get("api_base")
                ),
                api_key=api_key,
                allow_insecure=bool(profile.get("allow_insecure")),
                client=client,
            )
        else:
            provider = LiteLLMProvider(
                api_key=api_key,
                api_base=profile.get("endpoint_url")
                or profile.get("api_base")
                or profile.get("base_url"),
            )
        result = provider.complete(
            messages=[{"role": "user", "content": 'Reply with JSON: {"ok": true}'}],
            model=model,
            response_format={"type": "json_object"},
        )
        return {"ok": True, "model": result.model or model}
    except Exception as exc:  # Providers expose many transport-specific exception types.
        return {"ok": False, "error": type(exc).__name__}


test_profile.__test__ = False
