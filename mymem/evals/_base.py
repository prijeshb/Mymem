"""
Base abstractions for the evaluation framework.

Design:
  Evaluator[T]   — Generic Template Method ABC
  RunContext     — Value object holding shared execution state

The Evaluator contract:

  1. validate(eval_input)   — fail-fast input guard (sync, optional)
  2. run(eval_input, ctx)   — the actual evaluation logic (async, required)
  3. grade(result)          — turn raw result into PASS/WARN/FAIL (sync, required)

`evaluate()` is the template method: it orchestrates validate → run.
`grade()` is a separate utility that callers invoke on the returned result.
Each concrete result dataclass carries its own `grade` field, set inside `run()`.

Example usage:

    class MyEval(Evaluator[MyResult]):
        async def run(self, eval_input, ctx):
            # ... do evaluation ...
            score = compute_score(eval_input)
            return MyResult(score=score, grade=self.grade(score))

        def grade(self, result):
            return "PASS" if result.score >= 0.8 else "FAIL"

    result = await MyEval().evaluate(my_input)
    print(result.grade)  # PASS or FAIL
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar, final

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Shared execution context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunContext:
    """
    Immutable execution context passed to every evaluator run.

    Carry per-run state here rather than as positional args so adding new
    context fields doesn't break existing subclass signatures.
    """
    db_path: Path | None = None
    dry_run: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Evaluator[T] — Generic Template Method ABC
# ---------------------------------------------------------------------------

class Evaluator(ABC, Generic[T]):
    """
    Generic base for all evaluation modules.

    Type parameter T is the concrete result dataclass for this evaluator
    (e.g. ExtractionConsensusResult, ChunkingEvalResult).

    Template Method pattern:
      evaluate() orchestrates validate → run.
      grade() is a utility subclasses implement; callers invoke it on the result.

    Open/Closed: evaluation logic lives in subclasses; the orchestration
    protocol here is never modified.
    """

    # ------------------------------------------------------------------ #
    # Template method — @final prevents subclass override                 #
    # ------------------------------------------------------------------ #

    @final
    async def evaluate(self, eval_input: Any, ctx: RunContext | None = None) -> T:
        """
        Run the full evaluation lifecycle: validate → run.

        Returns the populated result object. The concrete subclass is
        responsible for setting the grade field inside run() by calling
        self.grade(result_in_progress) before returning.
        """
        ctx = ctx or RunContext()
        self.validate(eval_input)
        return await self.run(eval_input, ctx)

    # ------------------------------------------------------------------ #
    # Abstract — must be implemented by every subclass                    #
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def run(self, eval_input: Any, ctx: RunContext) -> T:
        """
        Execute the evaluation and return the result object.

        Call self.grade() inside this method to compute the grade and set
        it on the result before returning.
        """
        ...

    @abstractmethod
    def grade(self, result: T) -> str:
        """
        Translate a result object into a grade string: PASS | WARN | FAIL.

        Stateless: must not have side effects. Called inside run() by the
        subclass to populate the result's grade field.
        """
        ...

    # ------------------------------------------------------------------ #
    # Hook — subclasses may override for stricter validation              #
    # ------------------------------------------------------------------ #

    def validate(self, eval_input: Any) -> None:
        """
        Validate input before run().  Raise ValueError on invalid input.

        Default implementation is permissive. Override in subclasses that
        require specific fields or types.
        """
        pass
