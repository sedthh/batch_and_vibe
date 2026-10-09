"""Provider-neutral, one-shot dispatch of adapter-issued action capabilities.

Adapters own eligibility and final engine-thread validation. Providers only see
candidate names; they cannot replace a capability or refresh a late decision.
Enabled conversation runtimes may dispatch from questions or automatic turns.
"""
from dataclasses import dataclass
import math
import threading
import time


class ActionNotSent(OSError):
    """Transport positively failed before writing any command bytes."""
    def __init__(self, reason, winerror=None):
        super().__init__(reason)
        self.reason, self.winerror = reason, winerror


@dataclass(frozen=True)
class ActionOffer:
    identity: tuple
    packets: tuple[tuple[str, bytes], ...]
    expires_at: float

    @property
    def candidates(self):
        return tuple(name for name, _ in self.packets)


class DecisionDispatcher:
    """One decision and at most one transport attempt, including unknown receipts."""
    def __init__(self, adapter, *, clock=time.monotonic):
        self.adapter, self.clock = adapter, clock
        self._lock = threading.Lock()
        self._offer = None
        self._consumed = False

    def mark_player_command(self):
        """Host-assigned origin; never a model tool argument."""
        with self._lock:
            if self._offer is not None or self._consumed:
                raise ValueError('Command origin must be set before offering actions')
            if hasattr(self.adapter,'player_command'):
                self.adapter.player_command=True

    def offer(self):
        with self._lock:
            if self._offer is not None or self._consumed:
                raise ValueError('This manual decision already has an offer')
            self._offer = self.adapter.capture(self.clock())
            return {'candidates': list(self._offer.candidates)}

    def dispatch(self, decision):
        with self._lock:
            if self._consumed or self._offer is None:
                raise ValueError('No unused manual decision offer')
            # This offer is single-use. The host may obtain a fresh offer after
            # explicit pre-execution rejection, never after an uncertain receipt.
            self._consumed = True
            if (type(decision) is not dict or set(decision) != {'candidate'}
                    or decision['candidate'] is not None and type(decision['candidate']) is not str):
                raise ValueError('Invalid action decision')
            candidate = decision['candidate']
            if candidate is None:
                return {'status': 'no_action', 'queued': False}
            packet = dict(self._offer.packets).get(candidate)
            if packet is None:
                raise ValueError('Action was not offered')
            now = self.clock()
            if not math.isfinite(now) or now > self._offer.expires_at:
                return {'status': 'expired', 'queued': False}
            try:
                valid=self.adapter.still_valid(self._offer, candidate)
            except (OSError, ValueError, TimeoutError) as error:
                return {'status':'validation_unavailable','queued':False,
                        'stage':'before_transport','error_type':type(error).__name__}
            if not valid:
                return {'status': 'conditions_changed', 'queued': False}
            # Re-reading the engine may itself take time. Keep the original
            # packet and deadline; never substitute a fresh offer here.
            now = self.clock()
            if not math.isfinite(now) or now > self._offer.expires_at:
                return {'status': 'expired', 'queued': False}
            try:
                queued = self.adapter.submit(packet)
            except ActionNotSent as error:
                return {'status':'transport_unavailable','queued':False,
                        'stage':'before_write','reason':error.reason,'winerror':error.winerror}
            if not queued:
                # A received negative acknowledgement guarantees this packet
                # was not queued. Lost receipts remain exceptions, never this.
                return {'status':'rejected','queued':False,'stage':'before_native_queue'}
            return {'status':'queued','queued':True}


class DummyActionProvider:
    """Fixed structured response through the same dispatcher as a future provider."""
    def __init__(self, candidate='look_confused'):
        self.candidate = candidate

    def choose(self, request):
        return {'candidate': self.candidate if self.candidate in request['candidates'] else None}
