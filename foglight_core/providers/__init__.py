"""Provider adapter interfaces and V1 compatibility adapters."""

from .base import FunctionProviderAdapter, ProviderAdapter, ProviderRegistry, ProviderResult
from .canonical import (
    CORE_CANONICAL_ADAPTERS,
    NormalizationResult,
    normalize_provider,
    project_legacy_panel,
)

__all__ = [
    "FunctionProviderAdapter",
    "CORE_CANONICAL_ADAPTERS",
    "NormalizationResult",
    "ProviderAdapter",
    "ProviderRegistry",
    "ProviderResult",
    "normalize_provider",
    "project_legacy_panel",
]
