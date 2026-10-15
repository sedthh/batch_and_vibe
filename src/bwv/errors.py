"""Bounded local diagnostics without exception messages, credentials or locals."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import time
import traceback


class ChatNotSubmitted(ValueError):
    """Admission failed before a model call, wake or other question side effect."""
    def __init__(self, *, retryable=True, reason='chat_unavailable'):
        super().__init__(MESSAGES['chat_unavailable'])
        self.retryable = retryable
        self.reason = reason


MESSAGES = {
    'fullscreen_overlay_unavailable':'LLM paused: use windowed or launcher borderless mode for creature chat',
    'game_display_unavailable':'LLM paused: game window is unavailable or minimized',
    'chat_overlay_unavailable':'LLM paused: chat overlay is not running; restart the mod',
    'invalid_tool_call':'Creature tool call failed; see the LLM debugger',
    'tool_limit_reached':'Model needed more tool steps; try a simpler request or increase tool limits in Settings > Limits',
    'tool_context_unavailable':'Game context changed before the tool could run',
    'rate_limited':'Model rate limit reached; try again shortly',
    'request_timeout':'Model reply timed out',
    'connection_failed':'Could not reach the model',
    'authentication_failed':'Model authentication failed; check API key settings',
    'model_or_configuration_rejected':'Model configuration rejected; check settings',
    'provider_unavailable':'Model provider is unavailable',
    'provider_cooldown':'Model requests are temporarily cooling down',
    'message_limit_reached':'Mod message cap reached; increase or clear Maximum model messages per run in Settings > Limits, then restart the mod',
    'provider_busy':'The creature is already answering; wait for its reply',
    'invalid_request':'Could not prepare the creature context; see the LLM debugger',
    'request_too_large':'Creature context is too large to send; reduce history in settings or reset chat history',
    'response_too_large':'Model reply exceeded the supported size; try a shorter request',
    'unexpected_output':'Model returned an unsupported reply format; check model and API settings',
    'redirect_rejected':'Provider redirected the request; check its API base URL in settings',
    'invalid_response':'Model returned an invalid response',
    'invalid_reply':'Model returned an invalid reply',
    'malformed_speech':'Model returned broken reply text; please try again',
    'incomplete_response':'Model response was incomplete',
    'model_refusal':'Model declined the request',
    'chat_unavailable':"Chat is temporarily unavailable. Resume gameplay and wait for the mod to refresh the creature's status, then try again",
    'chat_cancelled':'Chat was cancelled because the game context changed',
    'chat_transport_failed':'Could not communicate with the mod',
    'chat_failed':'Chat request failed; see the LLM debugger',
    'action_failed':'Creature action was not sent; see the LLM debugger',
    'action_target_changed':'Action cancelled before execution: its target is no longer available',
    'held_object_changed':'The held object changed or is no longer available; the command was not sent',
    'action_unconfirmed':'Could not confirm whether the action was sent; not retrying automatically',
    'action_not_selected':'The model promised a cast but did not select an action',
    'internal_error':'Internal mod error; see the LLM debugger',
}


def error_banner(code):
    return '[ERROR: '+MESSAGES.get(code,'Request failed; see the LLM debugger')+']'


class ErrorLog:
    """Callers supply fixed codes and curated metadata, never raw provider bodies."""
    def __init__(self, *, clock=time.monotonic, limit=100):
        self.clock=clock;self.entries=deque(maxlen=limit);self.sequence=0

    def record(self, code, stage, *, error=None, details=None):
        event={'code':code,'stage':stage,'details':deepcopy(details or {})}
        if error is not None:
            event['exception_type']=type(error).__name__
            event['stack']=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(error.__traceback__)[-12:]]
        stamp=self.clock()
        if self.entries and all(self.entries[-1].get(k)==v for k,v in event.items()):
            self.entries[-1]['occurrences']+=1;self.entries[-1]['last_seen']=stamp
            return
        self.sequence+=1
        self.entries.append({'id':self.sequence,'first_seen':stamp,'last_seen':stamp,'occurrences':1,**event})

    def snapshot(self):return deepcopy(list(self.entries))
