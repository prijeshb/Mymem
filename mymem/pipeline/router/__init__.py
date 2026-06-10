"""
mymem.pipeline.router
~~~~~~~~~~~~~~~~~~~~~
Public API — backward-compatible with all existing imports.

Interfaces:   IModelRegistry, ITaskRouter, IFallbackChain, ICostTracker
Value types:  ModelSpec, LLMCallable
Defaults:     DefaultModelRegistry, OllamaFallbackChain, SessionCostTracker
Entry points: ModelRouter, ConfigTaskRouter, router_from_settings
Utilities:    estimate_tokens, fits_context, estimate_cost
"""

from mymem.pipeline.router._types import (
    ModelSpec,
    LLMCallable,
    IModelRegistry,
    ITaskRouter,
    IFallbackChain,
    ICostTracker,
)
from mymem.pipeline.router._registry import DefaultModelRegistry
from mymem.pipeline.router._chain import OllamaFallbackChain
from mymem.pipeline.router._cost import SessionCostTracker
from mymem.pipeline.router._credentials import KeyMapCredentials, ProviderCredentials
from mymem.pipeline.router._utils import estimate_tokens, fits_context, estimate_cost
from mymem.pipeline.router._router import ConfigTaskRouter, ModelRouter, router_from_settings

__all__ = [
    # Interfaces
    "IModelRegistry",
    "ITaskRouter",
    "IFallbackChain",
    "ICostTracker",
    # Value types
    "ModelSpec",
    "LLMCallable",
    # Credentials
    "ProviderCredentials",
    "KeyMapCredentials",
    # Concrete implementations
    "DefaultModelRegistry",
    "OllamaFallbackChain",
    "SessionCostTracker",
    "ConfigTaskRouter",
    # Entry points
    "ModelRouter",
    "router_from_settings",
    # Utilities
    "estimate_tokens",
    "fits_context",
    "estimate_cost",
]
