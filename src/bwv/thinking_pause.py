"""One native decision pause per model turn; no background timer or scanner."""
from __future__ import annotations

import asyncio
import hashlib
import math
import secrets

from .action_probe import COMMAND, submit
from .pipe import snapshot


class ThinkingPause:
    def __init__(self, pid, observation, *, read=snapshot, send=submit):
        self.pid, self.observation = pid, observation
        self.read, self.send = read, send
        self.packet = None
        self.status = 'not_requested'
        self.started = False
        self.control_revision = None

    async def _send(self, packet):
        write = asyncio.create_task(asyncio.to_thread(self.send, self.pid, packet))
        try:
            return await asyncio.shield(write)
        except asyncio.CancelledError:
            # Complete the bounded pipe write before cleanup can send end.
            # Otherwise a canceled worker could acquire the pause AFTER end.
            try:
                await asyncio.shield(write)
            except Exception:
                pass
            raise

    async def begin(self, timeout):
        state = await asyncio.to_thread(self.read, self.pid)
        probe = state.get('action_probe', {})
        contract = probe.get('thinking_pause', {})
        if contract.get('version') not in (1,2) or contract.get('available') is not True:
            self.status = 'bridge_unavailable'
            return
        capability = probe.get('conversation_actions', {})
        creature, mental = capability.get('creature_handle'), capability.get('mental_handle')
        session = f"{self.pid}:{state.get('session')}"
        identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        if (state.get('pid') != self.pid or (session, identity) !=
                (self.observation.session, self.observation.creature_id)
                or type(state.get('source_age_ms')) is not int or not 0 <= state['source_age_ms'] <= 1500):
            self.status = 'reading_changed'
            return
        if not math.isfinite(timeout) or not 0 < timeout <= 125:
            self.status = 'timeout_unavailable'
            return
        token = secrets.randbits(64) or 1
        # Store cleanup BEFORE sending: cancellation or a lost receipt must
        # still release a request that may have reached the game.
        self.packet = COMMAND.pack(0x42575041, 14, probe['epoch'], token, 0, 0, creature)
        request = COMMAND.pack(0x42575041, 14, probe['epoch'], token,
                               probe['offered_at_uptime_ms'], math.ceil((timeout+5)*1000), creature)
        if not await self._send(request):
            self.status = 'not_accepted'
            return
        self.status = 'thinking'
        self.started = True
        revision = contract.get('control_revision')
        if type(revision) is int:self.control_revision = revision

    async def action_allowed(self):
        """One final cached-bridge read, not a timer or another world scan."""
        if not self.started:return True
        try:
            state = await asyncio.to_thread(self.read, self.pid)
            probe = state.get('action_probe', {})
            pause = probe.get('thinking_pause', {})
            _, _, epoch, token, _, _, creature = COMMAND.unpack(self.packet)
            capability = probe.get('conversation_actions', {})
            allowed = (state.get('pid') == self.pid and state.get('session') == epoch
                and capability.get('creature_handle') == creature
                and pause.get('token') == token
                and type(state.get('source_age_ms')) is int and 0 <= state['source_age_ms'] <= 1500
                and pause.get('control_taken') is not True
                and pause.get('status') in ('thinking', 'native_agenda_changed'))
        except (OSError, ValueError, TimeoutError, TypeError):
            allowed = False
        if allowed:
            revision = pause.get('control_revision')
            if type(revision) is int:self.control_revision = revision
        else:self.status = 'action_cancelled_control_changed'
        return allowed

    async def close(self):
        packet, self.packet = self.packet, None
        if packet is not None:
            await self._send(packet)
        self.status = 'released'
