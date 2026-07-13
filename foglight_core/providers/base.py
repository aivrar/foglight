"""Common provider adapter contract and validated registry."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

ProviderResult = tuple[bytes, str, int, str]


class ProviderAdapter(Protocol):
    provider_id: str

    def fetch(self, **params: object) -> ProviderResult: ...


@dataclass(frozen=True, slots=True)
class FunctionProviderAdapter:
    provider_id: str
    function: Callable[..., ProviderResult]

    def fetch(self, **params: object) -> ProviderResult:
        return self.function(**params)


class ProviderRegistry:
    def __init__(self, adapters: Iterable[ProviderAdapter] = ()) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter) -> None:
        if not adapter.provider_id or adapter.provider_id in self._adapters:
            raise ValueError(f"duplicate or empty provider id: {adapter.provider_id!r}")
        self._adapters[adapter.provider_id] = adapter

    def get(self, provider_id: str) -> ProviderAdapter:
        try:
            return self._adapters[provider_id]
        except KeyError as error:
            raise KeyError(f"unknown provider: {provider_id}") from error

    def fetch(self, provider_id: str, **params: object) -> ProviderResult:
        return self.get(provider_id).fetch(**params)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
