"""Shared test doubles: fake ADK models that replace Claude, so the real ADK pipeline runs
without network or cost."""

import asyncio
import re

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

USAGE = types.GenerateContentResponseUsageMetadata(
    prompt_token_count=3000, cached_content_token_count=2200, candidates_token_count=400, thoughts_token_count=100,
)


def model_response(text, finish=types.FinishReason.STOP, model="claude-opus-5-5"):
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        usage_metadata=USAGE, finish_reason=finish, model_version=model,
    )


def answer(text, finish=types.FinishReason.STOP):
    return (text, finish)


class ScriptedLlm(BaseLlm):
    """Returns prepared answers in order and records every request it receives."""

    model: str = "claude-opus-5-5"
    _answers: list = PrivateAttr(default_factory=list)
    _requests: list = PrivateAttr(default_factory=list)

    @classmethod
    def with_answers(cls, *answers):
        llm = cls()
        llm._answers = list(answers)
        return llm

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        self._requests.append(llm_request)
        text, finish = self._answers.pop(0)
        yield model_response(text, finish)


_PAPER_ID = re.compile(r'<paper id="([^"]+)"')


class PaperAwareLlm(BaseLlm):
    """Answers by the paper ID in the request, so parallel calls get the right answer.

    `answers` maps paper ID -> (text, finish reason). Tracks how many calls run at once.
    """

    model: str = "claude-opus-5-5"
    delay: float = 0.0
    _answers: dict = PrivateAttr(default_factory=dict)
    _running: int = PrivateAttr(default=0)
    _max_running: int = PrivateAttr(default=0)
    _seen: list = PrivateAttr(default_factory=list)

    @classmethod
    def with_answers(cls, answers, delay=0.0):
        llm = cls(delay=delay)
        llm._answers = dict(answers)
        return llm

    @property
    def max_running(self):
        return self._max_running

    @property
    def seen_paper_ids(self):
        return self._seen

    async def generate_content_async(self, llm_request, stream=False):
        text_parts = [p.text for c in llm_request.contents for p in c.parts if p.text]
        paper_id = next(match.group(1) for part in text_parts if (match := _PAPER_ID.search(part)))
        self._seen.append(paper_id)
        self._running += 1
        self._max_running = max(self._max_running, self._running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            text, finish = self._answers[paper_id]
            yield model_response(text, finish)
        finally:
            self._running -= 1
