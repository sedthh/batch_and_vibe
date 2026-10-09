"""Deterministic, offline implementation of the production Provider interface."""
from __future__ import annotations

import asyncio
import math
from copy import deepcopy

from .planner import PlanningRequest
from .actions import ActionIntent


class MockProvider:
    def __init__(self, *, reply: str | ActionIntent | dict | None = None, delay: float = 0,
                 fail: bool = False, release: asyncio.Event | None = None,
                 ignore_cancel: bool = False):
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("Invalid mock delay")
        self.reply, self.delay, self.fail = reply, delay, fail
        self.release, self.ignore_cancel = release, ignore_cancel
        self.entered = asyncio.Event()
        self.requests: list[PlanningRequest] = []

    async def choose(self, request: PlanningRequest) -> str | ActionIntent | dict | None:
        self.requests.append(request)
        self.entered.set()
        try:
            if self.release is not None:
                await self.release.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            if not self.ignore_cancel:
                raise
            if self.release is not None:
                await self.release.wait()
        if self.fail:
            raise ConnectionError("Synthetic provider failure")
        if self.reply is not None:
            return self.reply
        return request.candidates[0] if request.candidates else None


class MockConversationProvider:
    """Deterministic conversation-protocol dummy; no network or model inference.

    Use a separate/in-memory ConversationMemory for QA, never production notes.
    Optional scripted replies exercise intents, memory updates and speech.
    """
    def __init__(self, replies=(), *, delay=0):
        if not math.isfinite(delay) or delay < 0:
            raise ValueError('Invalid mock delay')
        self.replies = deepcopy(list(replies))
        self.delay = delay
        self.requests = []
        self.entered = asyncio.Event()

    async def respond(self,request):
        self.requests.append(request)
        self.entered.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.replies:
            return self.replies.pop(0)
        question = request.current.get('god_question')
        return {'answer':('Dummy provider received your question: '+question) if question else '',
                'decision':{'candidate':None}}
