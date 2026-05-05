"""Abstract interfaces and value types for the router package."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Awaitable


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    context_tokens: int
    cost_per_1m_input: float
    cost_per_1m_output: float


LLMCallable = Callable[..., Awaitable[str]]


class IModelRegistry(ABC):
    """Look up and register model specifications by name."""

    @abstractmethod
    def get(self, name: str) -> ModelSpec | None: ...

    @abstractmethod
    def register(self, spec: ModelSpec) -> None: ...

    @abstractmethod
    def all_names(self) -> list[str]: ...


class ITaskRouter(ABC):
    """Map task names to preferred model names."""

    @abstractmethod
    def model_for(self, task: str) -> str: ...

    @abstractmethod
    def update(self, task: str, model: str) -> None: ...


class IFallbackChain(ABC):
    """Build an ordered list of models to attempt for a given preferred model."""

    @abstractmethod
    def build(
        self,
        preferred: str,
        registry: IModelRegistry,
        provider: str,
    ) -> list[str]: ...


class ICostTracker(ABC):
    """Track and report LLM call costs within a session."""

    @abstractmethod
    def record(
        self,
        model: str,
        in_tokens: int,
        out_tokens: int,
        registry: IModelRegistry,
    ) -> None: ...

    @abstractmethod
    def session_total(self) -> float: ...

    @abstractmethod
    def alert_threshold(self) -> float: ...
