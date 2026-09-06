from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from autocoder.domain import (
    AgentAction,
    AttemptPlan,
    FailureContext,
    IntakeDecision,
    JudgeVerdict,
    TestContract,
)


class ModelProvider(ABC):
    name: str

    async def intake(self, *, prompt: str, attachments: list[str], model: str) -> IntakeDecision:
        return IntakeDecision(ambiguous=False)

    async def plan_attempt(
        self,
        *,
        prompt: str,
        attempt_number: int,
        workspace: Path,
        failure: FailureContext | None,
        model: str,
    ) -> AttemptPlan:
        step = "Build and validate the requested Python workspace."
        return AttemptPlan(
            summary=step,
            steps=[step],
            failure_response=failure.message if failure else None,
        )

    @abstractmethod
    async def next_action(
        self,
        *,
        prompt: str,
        plan: AttemptPlan,
        workspace: Path,
        transcript: list[dict[str, str]],
        model: str,
    ) -> AgentAction:
        raise NotImplementedError

    @abstractmethod
    async def generate_tests(
        self,
        *,
        prompt: str,
        workspace: Path,
        previous: TestContract | None,
        failure: FailureContext | None,
        model: str,
    ) -> TestContract:
        raise NotImplementedError

    async def judge(
        self,
        *,
        prompt: str,
        criteria: str,
        stdout: str,
        stderr: str,
        exit_code: int | None,
        model: str,
    ) -> JudgeVerdict:
        raise NotImplementedError
