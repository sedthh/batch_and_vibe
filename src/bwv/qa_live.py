"""Live JSON -> dummy or configured LLM chat; engine writes disabled by default.

The existing read-only inspector is the sole source. A separate conversation gate
permits text-only reflection with incomplete gameplay-control coverage; it never
changes the engine gate or produces engine action capabilities. An explicit
process-bound opt-in supplies independently verified native capabilities for
direct questions, including provider tool selections. Automatic turns stay read-only.
"""
from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import TimeoutError as FutureTimeout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from http.client import HTTPException
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from .control import ControlGate, ControlObservation
from .action_probe import ActionProcessChanged
from .conversation import (ConversationBroker, ConversationMemory,
                           current_prompt, question_unavailable)
from .memory_store import default_memory_path
from .planner import PlanningRequest
from .prompt import CreaturePrompt
from .prompt_settings import DEFAULT_PROMPTS
from .creature import CreatureContext, CreatureTrait
from .profile_names import ProfileNames, read_profile_names, with_profile_names
from .qa import make_server
from .qa_demo import DemoProvider
from .cadence import AdaptiveUpdates, CadenceSettings
from .creature_tools import CreatureToolSession, ToolContextUnavailable, TOOL_CONTRACT, ACTION_CATALOG
from .direct_tools import DirectCreatureToolSession
from .llm_context import summarize_context, god_hand_context, creature_position_context, CONTEXT_CONTRACT
from .expressions import display_reply
from .errors import ErrorLog, error_banner, ChatNotSubmitted
from .action_feedback import action_result
from .sleep import sleep_state, sleep_phase
from .feedback_context import FeedbackContext
from .model_projection import model_snapshot


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Inspector redirects are not accepted')


class InspectorSource:
    def __init__(self, url):
        parsed = urlsplit(url)
        import re
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.port or not re.fullmatch(r'/[A-Za-z0-9_-]{24,64}/', parsed.path)):
            raise ValueError('Use the tokenized local inspector URL on 127.0.0.1')
        self.url = url+'state'
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def read(self):
        try:
            with self.opener.open(Request(self.url, headers={'Cache-Control':'no-cache'}), timeout=1) as response:
                raw = response.read(8*1024*1024+1)
        except HTTPException:
            raise ValueError('Invalid inspector HTTP response') from None
        if len(raw) > 8*1024*1024:
            raise ValueError('Inspector response is too large')
        return json.loads(raw)['current']


class _ConversationGate(ControlGate):
    """Known scene locks for text only; these leases never authorize engine writes."""
    def _reasons(self, now):
        reasons = [r for r in super()._reasons(now) if r not in ('unsupported','unknown_control')]
        blocker=getattr(self,'display_blocker',None)
        if blocker:
            reason=blocker()
            if reason:reasons.append(reason)
        obs = self.observation
        if obs is not None:
            if any(getattr(obs, name) is None for name in ('paused','in_temple','cutscene')):
                reasons.append('scene_state_unverified')
            if obs.creature_present is not True or not obs.creature_id:
                reasons.append('creature_unavailable')
        return tuple(dict.fromkeys(reasons))


class LiveDummyProvider(DemoProvider):
    async def respond(self, request):
        result = await super().respond(request)
        result['answer'] = result['answer'].replace('demo memory','dummy memory')
        if request.current.get('god_question','').strip().lower() in ('status','what are you doing?'):
            snapshot = request.current['snapshot']
            parts = ['Dummy report from the latest game observation.']
            plan = snapshot.get('activity',{}).get('native_plan')
            if plan:
                parts.append('Native plan: '+plan+'. This describes a plan, not a completed action.')
            learned=(snapshot.get('self') or {}).get('miracles',{}).get('learning_threshold_met')
            if learned:
                parts.append('Miracle learning thresholds met: '+', '.join(learned)+'.')
            if snapshot.get('activity',{}).get('observations'):
                parts.extend(snapshot['activity']['observations'])
            if not snapshot:
                parts.append('Current world context is unavailable.')
            result['answer'] = ' '.join(parts)[:3000]
        # Native AI continues. No invented candidates or unverified targets.
        result['decision'] = {'candidate':None}
        question = request.current.get('god_question', '').strip().lower()
        candidate = {'come to me': 'approach_hand', 'look confused': 'look_confused',
                     'approach tree':'approach_object'}.get(question)
        if question in ('wave','sneeze','shiver','scratch','show_hotness','dance','sleep_here','put_down','eat_held'):
            candidate=question
        if candidate and request.current.get('conversation_only') is False:
            if candidate in request.current.get('snapshot', {}).get('candidates', ()):
                result['decision'] = {'candidate': candidate}
                result['answer'] = {'approach_hand':'I will try to come to your hand.',
                    'approach_object':'I will try to approach the offered tree.',
                    'look_confused':'I will try a confused gesture.'}.get(candidate,'I will try to '+candidate.replace('_',' ')+'.')
        return result


class LiveChatRuntime:
    def __init__(self, state_dir=None, *, clock=time.monotonic, names_reader=None, provider=None,
                 settings=None, settings_path=None, action_factory=None, wake_factory=None, wait_actions=False,
                 request_details=None, thinking_pause_factory=None):
        self.loop = asyncio.get_running_loop()
        self.clock = clock
        self.request_details = request_details
        self.names_reader = read_profile_names if names_reader is None else names_reader
        self.is_llm = provider is not None
        self.action_factory = action_factory
        self.pending_actions = None
        self._reported_action_failure = None
        if wait_actions:
            if action_factory is None:
                raise ValueError('Waiting actions require an explicit executor')
            from .pending_actions import PendingActions
            self.pending_actions = PendingActions(action_factory,lambda:self.source,clock=clock,wake_factory=wake_factory)
            self.action_factory = self.pending_actions.factory
        self.wake_factory = wake_factory
        self.mode = 'live_llm_chat' if self.is_llm else 'live_dummy_preview'
        self.config = provider.config if self.is_llm else None
        self.settings=settings or self.config
        self.settings_path=Path(settings_path) if settings_path is not None else None
        self.prompt_settings=self.settings.prompts if self.settings else DEFAULT_PROMPTS
        root = Path(state_dir) if state_dir is not None else default_memory_path().parent/('live-llm' if self.is_llm else 'live-dummy')
        if self.is_llm:
            from .token_usage import TokenUsage
            provider.token_usage=TokenUsage(root/'token-usage.json')
        path = root/('creature-memory.json' if self.is_llm else 'dummy-memory.json')
        self.cadence_path=root/'config.local.json'
        self.cadence=self.settings.cadence if self.settings else CadenceSettings()
        self.cadence_storage_error=None
        try:
            if self.settings_path is None and self.cadence_path.exists():
                raw_settings=self.cadence_path.read_bytes()
                if len(raw_settings)>4096:raise ValueError('Oversized settings')
                self.cadence=CadenceSettings.parse(json.loads(raw_settings),self.cadence)
        except (OSError,ValueError,TypeError):self.cadence_storage_error='cadence_settings_unavailable'
        if not self.is_llm and path.resolve() == default_memory_path().resolve():
            raise ValueError('Dummy memory must be isolated')
        history_limits = ({'pairs':self.settings.history_pairs,
                           'history_chars':self.settings.history_chars} if self.settings else {})
        self.memory = ConversationMemory(path=path, **history_limits)
        self.feedback_context = FeedbackContext()
        # Keep visible chat stable while silent automatic exchanges rotate the
        # shorter model context. Restarts recover only the persisted last N pairs.
        self.dialogue = deque((display_reply(deepcopy(pair)) for pair in self.memory.snapshot()['recent_pairs']
                              if pair['input'].get('god_question') or pair.get('say')
                              or (pair['input'].get('session_event') and pair.get('answer','').strip())),maxlen=50)
        self.gate = _ConversationGate(resume_delay=.75,preserve_stale_seconds=5)
        self.provider = provider if self.is_llm else LiveDummyProvider()
        if self.is_llm:self.provider.tool_factory=self._tool_session
        self._cadence_turn_identity=None
        self.request_timeout = self.config.timeout_seconds+5 if self.is_llm else 15
        self.broker = ConversationBroker(self.provider,self.gate,clock=clock,timeout=self.request_timeout,
            memory=self.memory,prompt_builder=self._prompt,on_turn=self._turn_activity,
            action_factory=self.action_factory,
            on_question=self._question_action if wake_factory or self.pending_actions else None,
            wake_followup_actions=self.pending_actions is not None,session_greetings=True,
            wait_for_context=self._wait_for_turn_context,thinking_pause_factory=thinking_pause_factory)
        self.broker.speech.reenable_cut_content = self.settings.reenable_cut_content if self.settings else True
        self.updates = self._new_updates()
        self.automatic = not self.is_llm
        self.source = None
        self._last_auto = None
        self._tasks = set()
        self._forced_tick = None
        self._state_lock = threading.Lock()
        self._published = {}
        self.scheduled = {}
        self.last_error = None
        self.errors = ErrorLog(clock=clock)
        self.last_chat_error = None
        self._diagnostic_conditions = {}
        self.ingest(None)

    def _new_updates(self):
        return AdaptiveUpdates(self.cadence,min(self.cadence.active_seconds,
            self.settings.event_cooldown_seconds if self.settings else 10))

    def set_display_blocker(self, blocker):
        self.gate.display_blocker=blocker
        if self.is_llm:self.provider.request_blocker=blocker

    def display_blocked(self):
        blocker=getattr(self.gate,'display_blocker',None)
        return blocker() if blocker else None

    def question_scene_blocked(self):
        return self.display_blocked() or question_unavailable(self.gate.observation,self.clock()) or ('scene_state_unverified'
            if 'scene_state_unverified' in self.gate.poll(self.clock()).reasons else None)

    async def _wait_for_context(self, *, allow_paused=False):
        """Wait for a new sample, never refresh a cached timestamp or replay work."""
        original=self.gate.observation
        identity=(original.session,original.creature_id) if original else None
        epoch=self.broker._epoch
        try:
            async with asyncio.timeout(self.gate.preserve_stale_seconds):
                while True:
                    obs=self.gate.observation
                    if self.broker._epoch!=epoch or obs is None:
                        return  # The caller reports the actual identity/scene lock.
                    if allow_paused and obs.connected and obs.paused is True:
                        return
                    if (obs.session,obs.creature_id)!=identity:return
                    self.gate.poll(self.clock())
                    if not self.gate.waiting_for_observation:return
                    await asyncio.sleep(.05)
        except TimeoutError:
            self.errors.record('tool_context_unavailable','context_validation',
                details={'reason':'observation_refresh_timeout'})
            raise ToolContextUnavailable('Fresh creature observation did not return') from None

    async def _refresh_details(self, *, force=False):
        if self.request_details is None or self.source is None or self.question_scene_blocked():return
        sampled=(self.source.get('diagnostics',{}).get('detailed_sensors') or {}).get('observed_at')
        if not force and type(sampled) in (int,float) and 0<=self.clock()-sampled<1:return
        original=self.gate.observation
        identity=(original.session,original.creature_id)
        epoch=self.broker._epoch
        revision=self.request_details()
        try:
            async with asyncio.timeout(self.gate.preserve_stale_seconds):
                while True:
                    # A detail scan can outlast the fast observation's freshness
                    # window. Wait for that same feed to catch up, without
                    # repeating the scan or the provider request.
                    await self._wait_for_context()
                    obs=self.gate.observation
                    if (self.broker._epoch!=epoch or obs is None
                            or (obs.session,obs.creature_id)!=identity or self.question_scene_blocked()
                            or obs.cutscene is True or obs.in_temple is True
                            or obs.player_has_control is False):return
                    details=((self.source or {}).get('diagnostics',{}).get('detailed_sensors') or {})
                    if details.get('revision',-1)>=revision:return
                    await asyncio.sleep(.05)
        except TimeoutError:
            self.errors.record('tool_context_unavailable','context_validation',
                details={'reason':'detailed_sensor_refresh_timeout'})
            raise ToolContextUnavailable('World observation did not refresh') from None

    async def _wait_for_turn_context(self, *, allow_paused=False):
        await self._wait_for_context(allow_paused=allow_paused)
        if not allow_paused:
            await self._refresh_details(force=True)
            await self._wait_for_context()

    def _turn_activity(self, stage, request):
        if stage == 'accepted':
            self.feedback_context.accepted(request.current.get('snapshot', {}))
            return
        obs=self.gate.observation
        identity=(obs.session,obs.creature_id) if obs else None
        if not self.gate.poll(self.clock()).allowed:
            self.updates.reset()
            return
        if stage=='started':
            if self.updates.identity!=identity:
                self.updates.reset();self.updates.identity=identity
            self._cadence_turn_identity=(id(self.updates),identity)
            self.updates.conversation_activity(self.clock(),request.current.get('snapshot'),
                self.memory.without_notes(request.messages[0]['content']))
        elif self._cadence_turn_identity==(id(self.updates),identity):
            self.updates.conversation_activity(self.clock())

    def _tool_session(self):
        original=self.gate.observation
        identity=(original.session,original.creature_id) if original else None
        epoch=self.broker._epoch
        def fresh():
            obs=self.gate.observation;now=self.clock()
            reason=('display_unavailable' if self.display_blocked() else
                    'observation_missing' if obs is None or self.source is None else
                    'conversation_changed' if self.broker._epoch!=epoch else
                    'creature_changed' if (obs.session,obs.creature_id)!=identity else
                    question_unavailable(obs,now))
            if reason is None:
                reason=next((name+'_unavailable' for name in ('paused','in_temple','cutscene')
                    if getattr(obs,name) is not False),None)
            if reason is None and (obs.loading is True or obs.script_owns_creature is True or obs.player_has_control is False):
                reason='script_or_loading_control'
            if reason:
                self.errors.record('tool_context_unavailable','context_validation',details={'reason':reason})
                raise ToolContextUnavailable('Creature observation unavailable')
            _,current=self._prompt(obs,now)
            return {'observation':self.source['observation'],'prompt':{'context':current['snapshot']}}
        offer=self.pending_actions.active_offer if self.pending_actions else None
        def resolve(object_id, action):
            fresh()
            return offer.resolve_object(object_id,action) if offer else []
        async def resolve_async(object_id, action):
            await self._wait_for_context()
            fresh()
            targets=await offer.resolve_object_async(object_id,action) if offer else []
            await self._wait_for_context()
            fresh()
            return targets
        async def validate_async():
            # The turn already requested details once. Tools use the continuously
            # refreshed view; validation must not trigger another island scan.
            await self._wait_for_context()
            fresh()
        return DirectCreatureToolSession(fresh,memory=self.memory,target_resolver=resolve,
            async_target_resolver=resolve_async,async_validator=validate_async,
            action_candidates=tuple(ACTION_CATALOG) if self.action_factory is not None else ())

    def ingest(self, frame, *, publish=True, copy_source=True):
        """Accept a sample; internal readers may share an immutable published frame.

        The observer replaces frames instead of editing published ones. Other
        callers retain the defensive copy, and all public payloads are copies.
        Freshness/scene checks still run even when a frame is shared or repeated.
        """
        now = self.clock()
        try:
            if not isinstance(frame,dict):
                raise ValueError('No current inspector frame')
            raw = frame['observation']; diag = frame['diagnostics']; prompt = frame['prompt']
            # A scripted scene can stop VM snapshots too. Keep independently
            # observed locks even when the cached creature timestamp is stale;
            # this carries no world data or permission to make a request.
            scene_locks = {
                'cutscene':raw.get('cutscene') is True or diag.get('script_camera_owned') is True,
                'script_owns_creature':raw.get('script_owns_creature') is True,
                'in_temple':raw.get('in_temple') is True or diag.get('in_temple') is True,
            }
            source_stamp=raw.get('observed_at')
            source_fresh=(type(source_stamp) in (int,float) and math.isfinite(source_stamp)
                          and -.1 <= now-source_stamp <= 1.5)
            if raw.get('connected') is True and any(scene_locks.values()) and not source_fresh:
                session=raw.get('session');sequence=raw.get('sequence')
                if not isinstance(session,str) or type(sequence) is not int:raise ValueError('Invalid scene observation')
                self.source=None
                self.gate.update(ControlObservation(session,sequence,0,connected=True,
                    **{name:True for name,locked in scene_locks.items() if locked}),now)
                self._cancel_pending('cancelled_control_changed')
                self.broker._sync_identity();self.broker.cancel_unavailable_questions()
                self.last_error=None
                if publish:self.publish()
                return
            # Pause is sampled independently while the VM timestamp/creature
            # snapshot can stop updating. Keep only this blocking scene fact;
            # never make cached world context fresh or reuse it for new requests.
            if raw.get('connected') is True and raw.get('paused') is True and diag.get('paused') is True:
                session=raw.get('session');sequence=raw.get('sequence')
                if not isinstance(session,str) or type(sequence) is not int:raise ValueError('Invalid menu observation')
                self.source=None
                self.gate.update(ControlObservation(session,sequence,0,connected=True,paused=True),now)
                self._cancel_pending('cancelled_control_changed')
                self.broker._sync_identity();self.broker.cancel_unavailable_questions()
                self.last_error=None
                if publish:self.publish()
                return
            if not isinstance(prompt['system'],str) or not isinstance(prompt['context'],dict):
                raise ValueError('Invalid inspector prompt')
            # The source and consumer run on the same host. Preserve the source
            # monotonic time; polling an old frame must never make it fresh.
            stamp = raw['observed_at']
            if type(stamp) not in (float,int) or not math.isfinite(stamp):
                raise ValueError('Unavailable source timestamp')
            if not -.1 <= now-stamp <= self.gate.preserve_stale_seconds:
                raise ValueError('Stale source observation')
            identity = raw.get('creature_id')
            if identity is not None and (not isinstance(identity,str) or not identity):
                raise ValueError('Invalid creature identity')
            creature = raw.get('creature',{})
            form=CreatureContext(species=creature.get('species'),
                species_source=creature.get('species_source','unknown'),
                species_traits=tuple(CreatureTrait(**t) for t in creature.get('species_traits',[])),
                individual_traits=tuple(CreatureTrait(**t) for t in creature.get('individual_traits',[])))
            # Live identity is supplied only after the selected creature's owner
            # and runtime type checks. This remains a text-conversation assumption,
            # not proof of availability during every scripted story sequence.
            present = raw.get('creature_present')
            if present is None:
                present = bool(identity and creature.get('species_source') == 'engine')
            fields = {}
            for name in ('loading','world_ready','paused','in_temple','cutscene',
                         'script_owns_creature','player_has_control'):
                value = raw.get(name)
                if value is not None and type(value) is not bool:
                    raise ValueError('Invalid control field')
                fields[name] = value
            if fields['cutscene'] is None and diag.get('script_camera_owned') is False:
                fields['cutscene'] = False
            obs = ControlObservation(raw['session'], raw['sequence'],stamp,
                connected=raw['connected'] is True, supported=False,
                creature_id=identity, creature_present=present, creature=form, **fields)
            self.source = deepcopy(frame) if copy_source else frame
            if now-stamp<=self.gate.max_age:self.feedback_context.ingest(frame, now)
            self.gate.update(obs,now)
            self.last_error = None
        except (KeyError,TypeError,ValueError) as error:
            self.source = None
            self.gate.update(ControlObservation('disconnected',0,now),now)
            self.last_error = str(error)
            self.errors.record('source_unavailable','observation',error=error)
        self.broker._sync_identity()
        self.broker.cancel_unavailable_questions()
        if (self.pending_actions and not self.gate.poll(now).allowed
                and not self.gate.waiting_for_observation):
            self._cancel_pending('cancelled_control_changed')
        if publish:self.publish()

    def _prompt(self, obs, now, *, question=None, blockers=()):
        if question is not None and self.question_scene_blocked():raise ValueError(self.question_scene_blocked())
        system, current = current_prompt(obs,now,question=question,blockers=blockers,
                                         prompt_settings=self.prompt_settings)
        if self.source is not None and 'context_unavailable' not in current:
            context = deepcopy(self.source['prompt']['context'])
            # This runtime has no engine capability catalog or executor.
            context.pop('candidates',None); context.pop('available_intents',None)
            from .action_feedback import interrupted_results
            probe=self.source.get('diagnostics', {}).get('action_probe')
            result = action_result(probe, obs.session)
            results=interrupted_results(probe,obs.session)+([result] if result else [])
            if results:
                context['action_results'] = results
            context.pop('sleeping',None)
            context.pop('sleep_phase',None)
            phase=sleep_phase(self.source,now)
            if phase:context['sleep_phase']=phase
            current['snapshot'] = summarize_context(context,self.source['observation'],
                probe=(self.source.get('diagnostics') or {}).get('action_probe'))
            current['snapshot'] = self.feedback_context.project(current['snapshot'], now)
            hand=god_hand_context(self.source,now)
            if hand is not None:
                current['snapshot'].setdefault('god_hand',{}).update(hand)
                instinct=((self.source['observation'].get('experience') or {}).get('instinct') or {})
                if instinct.get('plan_id') == 89:  # Native GoToHand, not a guessed intention.
                    current['snapshot'].setdefault('activity',{})['native_plan_target']={'kind':'god_hand',**hand}
            point=creature_position_context(self.source,now)
            if point:current['snapshot'].setdefault('self',{})['position']=point
        system+='\n\n## World and creature observations\n\n'+CONTEXT_CONTRACT+'\n\n## Tool use\n\n'+TOOL_CONTRACT
        names = self.names_reader() if obs and obs.connected and obs.creature_id else ProfileNames()
        self.character_names = names
        system = with_profile_names(system, names)
        from .temple_targets import with_player_temple
        system = with_player_temple(system, (self.source or {}).get('diagnostics',{}).get('action_probe'))
        from .island import with_island_roster
        if 'context_unavailable' not in current:
            system = with_island_roster(system, ((self.source or {}).get('observation') or {}).get('island'))
        return system, current

    def _cancel_automatic(self, *, include_forced=True):
        for task in tuple(self._tasks):
            if include_forced or task is not self._forced_tick:task.cancel()

    def _finished(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            self.last_error = type(task.exception()).__name__
            self._report_error('automatic_update',task.exception())

    def _report_error(self, stage, error):
        provider_code=self.provider.last_error if self.is_llm else None
        code=(provider_code if isinstance(error,RuntimeError) and provider_code else
              'chat_unavailable' if isinstance(error,ValueError) else 'chat_failed')
        self.last_chat_error=code
        self.errors.record(code,stage,error=error,details={
            'provider_requests':self.provider.calls if self.is_llm else 0,
            'pending_questions':len(self.broker._questions),
            'game_actions':self._game_actions()})
        self.broker.speech.publish(error_banner(code),self.clock(),{'candidate':None},source='error')
        self.publish()

    async def step(self):
        now = self.clock()
        status = self.gate.poll(now)
        obs = self.gate.observation or ControlObservation('disconnected',0,now)
        if self.pending_actions:
            self.pending_actions.advance(obs,allowed=status.allowed,
                temporary_unavailable=self.gate.waiting_for_observation)
            result=self.pending_actions.snapshot()
            if result:
                self.memory.update_dispatch(result)
                if (self.broker.last_execution or {}).get('intent_id') == result.get('intent_id'):
                    self.broker.last_execution=deepcopy(result)
                failure=result.get('status','')
                key=(result.get('intent_id'),failure)
                if (failure in ('expired','not_confirmed_no_retry','native_offer_unavailable')
                        or (failure.startswith('cancelled_') and failure not in
                            ('cancelled_control_changed','cancelled_shutdown','cancelled_reset'))):
                    if key!=self._reported_action_failure:
                        self._reported_action_failure=key
                        code=('held_object_changed' if failure=='cancelled_held_object_changed' else
                              'action_target_changed' if 'target' in failure else
                              'action_unconfirmed' if failure=='not_confirmed_no_retry' else 'action_failed')
                        self.errors.record(code,'action_execution',details=result)
                        self.broker.speech.publish(error_banner(code),now,{'candidate':None},source='error')
        system, current = self._prompt(obs,now)
        # Unknown scene sensors stop previews too; reset cadence for a full
        # snapshot after resumption instead of retaining a pre-lock delta.
        cadence_obs = obs if status.allowed else replace(obs,connected=False)
        provider_wait=self.is_llm and not self.provider.ready()
        self.scheduled = self.updates.update(cadence_obs,
            CreaturePrompt(system,json.dumps(current['snapshot'])),status,now,source=self.source,
            busy=bool(provider_wait or self.broker._active or self.broker._questions or self._tasks))
        if provider_wait and self.scheduled['state']=='conversation_in_progress':
            self.scheduled['state']='waiting_for_provider'
        if ((not status.allowed and not self.gate.waiting_for_observation)
                or self.scheduled['state']=='suspended_sleep'):
            if not ((obs.paused is True or self.display_blocked()) and self.broker._active is not None):
                self._cancel_automatic()
        elif not self.automatic:
            self._cancel_automatic(include_forced=False)
        update = self.scheduled.get('last_update')
        if (self.automatic and status.allowed and current['snapshot'] and update
                and self.scheduled['state']=='preview_prepared'
                and (not self.is_llm or self.provider.ready())
                and update['number'] != self._last_auto):
            lease = self.gate.issue(now)
            if lease:
                self._last_auto = update['number']
                update['delivery'] = 'queued_for_llm' if self.is_llm else 'queued_for_dummy'
                task = asyncio.create_task(self.broker.choose(PlanningRequest(lease,(),None,reflection_only=True)))
                self._tasks.add(task); task.add_done_callback(self._finished)
        self.publish(prepared=(system,current))

    def _cancel_pending(self, reason):
        if self.pending_actions:
            self.pending_actions.cancel(reason)
            result=self.pending_actions.snapshot()
            if result:
                self.memory.update_dispatch(result)
                if (self.broker.last_execution or {}).get('intent_id') == result.get('intent_id'):
                    self.broker.last_execution=deepcopy(result)

    def _question_action(self, obs):
        self._cancel_pending('superseded_by_question')
        return self._wake_for_question(obs) if self.wake_factory else None

    def _wake_for_question(self, obs):
        now = self.clock()
        if not self.gate.poll(now).allowed:
            return {'status':'scene_blocked','queued':False}
        if sleep_state(self.source, now) is not True:
            return {'status':'sleep_not_observed','queued':False}
        previous_request = (self.source.get('diagnostics',{}).get('action_probe') or {}).get('request_at_uptime_ms')
        try:
            dispatcher = self.wake_factory(obs)
            if hasattr(dispatcher,'mark_player_command'):dispatcher.mark_player_command()
            offer = dispatcher.offer()
        except ActionProcessChanged:
            return {'status':'action_process_changed','queued':False,
                    'reason':'Reconnect the action bridge after restarting the game'}
        except (OSError, ValueError, TimeoutError):
            return {'status':'wake_not_offered','queued':False}
        if offer['candidates'] != ['wake_up']:
            return {'status':'wake_not_offered','queued':False}
        try:
            result = dispatcher.dispatch({'candidate':'wake_up'})
            if result.get('queued') is True and type(previous_request) is int and previous_request >= 0:
                result = {**result,'wake_after_request':previous_request}
            return result
        except (OSError, ValueError, TimeoutError):
            return {'status':'not_confirmed_no_retry','queued':False}

    async def question(self, payload):
        self.last_chat_error=None
        try:return await self._question(payload)
        except ChatNotSubmitted as error:
            self.errors.record('chat_unavailable','question_admission',
                details={'not_submitted':True,'retryable':error.retryable,'reason':error.reason})
            self.publish()
            raise
        except Exception as error:
            self._report_error('direct_question',error)
            raise

    async def _question(self, payload):
        if (type(payload) is not dict or 'question' not in payload
                or not payload.keys() <= {'question','expected_identity'}):
            raise ValueError('Invalid question')
        from .conversation import validate_question
        validate_question(payload['question'])
        if 'expected_identity' in payload:
            expected = payload['expected_identity']
            if (type(expected) is not list or len(expected)!=2
                    or not all(isinstance(v,str) and v for v in expected)):
                raise ValueError('Invalid expected creature identity')
        try:
            await self._wait_for_context()
        except ToolContextUnavailable:
            raise ChatNotSubmitted(reason='observation_refresh_timeout') from None
        obs = self.gate.observation
        # A missing reading says nothing about who the recipient is. Let the
        # overlay retain/retry an unsent message until a verified identity returns.
        if ('expected_identity' in payload and obs is not None
                and obs.connected and obs.creature_id is not None):
            if (expected[0] != obs.session or
                    (obs.creature_id is not None and expected[1] != obs.creature_id)):
                raise ChatNotSubmitted(retryable=False,reason='recipient_changed')
        blocked=self.question_scene_blocked()
        if blocked:raise ChatNotSubmitted(reason=blocked)
        # Explicit questions take priority over obsolete automatic work.
        self._cancel_automatic()
        try:
            reply = await self.broker.ask(payload['question'])
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():raise
            raise ValueError('Chat cancelled because the creature or game is unavailable') from None
        if not reply.get('answer','').strip() and not reply.get('say','').strip():
            name=self.character_names.creature_name or 'The creature'
            # Presentation acknowledgement, never a fabricated model reply or
            # a claim that an action completed. Keep it out of model history.
            self.broker.speech.publish('['+name+' is obeying your command]',self.clock(),
                {'candidate':None},source='acknowledgement')
        self.dialogue.append({'input':{'god_question':payload['question']},**reply})
        self.publish()
        return {'mode':self.mode,'reply':reply,
                'last_message':self.broker.speech.snapshot(self.clock())['last_message'],
                'external_model_calls':self.provider.calls if self.is_llm else 0,
                'game_actions':self._game_actions(), 'action_dispatch':deepcopy(self.broker.last_execution),
                'wake_dispatch':deepcopy(self.broker.last_wake)}

    def _game_actions(self):
        if self.action_factory is None and self.wake_factory is None:
            return 0
        # A queue receipt is not execution. Missing diagnostics stay unknown.
        probe = (self.source or {}).get('diagnostics', {}).get('action_probe') or {}
        value = probe.get('agenda_calls')
        return value if type(value) is int and value >= 0 else None

    def reset_available(self):
        return not (self.provider.active or self.broker._active or self.broker._questions
                    or self.broker._waiting_entry
                    or self.broker._auto or any(not task.done() for task in self._tasks))

    def force_tick_unavailable(self,context=None):
        now=self.clock()
        if not self.gate.poll(now).allowed:return 'Creature unavailable or scene blocked'
        if sleep_state(self.source,now) is True:return 'Creature is sleeping'
        if not self.reset_available():return 'Wait for the pending reply'
        if self.is_llm and not self.provider.ready():
            from .errors import MESSAGES
            return MESSAGES.get(self.provider.status().get('last_error'),'Model temporarily unavailable')
        if context is None:context=self._prompt(self.gate.observation,now)[1]['snapshot']
        if not context:return 'No active world information to send'
        return None

    async def force_tick(self,payload):
        if type(payload) is not dict or payload:raise ValueError('Force tick expects an empty object')
        reason=self.force_tick_unavailable()
        if reason:raise ValueError(reason)
        lease=self.gate.issue(self.clock())
        if lease is None:raise ValueError('Creature unavailable')
        before=self.broker.calls_started
        task=asyncio.create_task(self.broker.choose(PlanningRequest(lease,(),None,reflection_only=True)))
        self._forced_tick=task;self._tasks.add(task);task.add_done_callback(self._finished)
        self.publish()
        try:
            await task
            return {'sent':self.broker.calls_started>before,'mode':self.mode}
        except asyncio.CancelledError:
            raise ValueError('Status update cancelled because the context or conversation changed') from None
        finally:
            self._tasks.discard(task)
            if self._forced_tick is task:self._forced_tick=None
            self.publish()

    async def reset(self,payload):
        if (type(payload) is not dict or set(payload)!={'scope'}
                or payload['scope'] not in ('history','memory','statistics')):
            raise ValueError('Invalid reset scope')
        if not self.reset_available():raise ValueError('Wait for pending replies before resetting')
        if payload['scope']=='statistics':
            if not self.is_llm:raise ValueError('No model statistics in dummy mode')
            self.provider.token_usage.reset()
            self.provider.usage={}
            # Operational message limits are not usage statistics; retain them.
            self.publish()
            return {'reset':'statistics'}
        self._cancel_pending('cancelled_reset')
        self.memory.reset(payload['scope'])
        if payload['scope']=='history':
            self.dialogue.clear()
            self.broker.speech.clear()
            self.provider.requests.clear()
            if self.is_llm:self.provider.last_tool_trace=[]
        self.publish()
        if self.memory.store and self.memory.store.error:
            raise ValueError('Reset could not be saved; check memory storage status')
        return {'reset':payload['scope']}

    async def configure(self,payload):
        allowed = {'automatic','cadence'} if self.is_llm else {'automatic','delay_seconds','cadence'}
        if type(payload) is not dict or not payload or not payload.keys() <= allowed:
            raise ValueError('Invalid chat configuration')
        if 'automatic' in payload and type(payload['automatic']) is not bool:
            raise ValueError('Invalid automatic setting')
        if 'delay_seconds' in payload and (type(payload['delay_seconds']) not in (int,float)
                or payload['delay_seconds'] not in (0,1,3,10)):
            raise ValueError('Invalid dummy delay')
        enabled = payload.get('automatic',self.automatic)
        cadence=CadenceSettings.parse(payload['cadence'],self.cadence) if 'cadence' in payload else self.cadence
        if 'cadence' in payload:
            from dataclasses import asdict
            if self.settings_path is not None:
                from .llm import LLMConfig
                # Preserve other sections edited on disk since startup.
                disk=LLMConfig.load(self.settings_path) if self.settings_path.exists() else self.settings or LLMConfig.defaults()
                replace(disk,cadence=cadence).save(self.settings_path)
            else:
                try:
                    self.cadence_path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=self.cadence_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(asdict(cadence)),encoding='utf-8')
                    temporary.replace(self.cadence_path)
                except OSError:
                    raise ValueError('Could not save private cadence settings') from None
            self.cadence_storage_error=None
        if enabled != self.automatic or cadence!=self.cadence:
            self._cancel_automatic()
            self.cadence=cadence
            self.updates = self._new_updates(); self._last_auto = None
        self.automatic = enabled
        if not self.is_llm:
            self.provider.delay = payload.get('delay_seconds',self.provider.delay)
        self.publish()
        return {'configured':True}

    def publish(self, *, prepared=None):
        from dataclasses import asdict
        now = self.clock()
        status = self.gate.poll(now)
        obs = self.gate.observation
        system, current = prepared if prepared is not None else self._prompt(obs,now)
        # Preserve errors even after later polls or successful calls clear the
        # current status. Deduplicate ongoing storage and dispatch conditions.
        memory = self.memory.snapshot()
        conditions={'memory_storage':memory.get('storage_error'),
                    'cadence_storage':self.cadence_storage_error}
        if self.is_llm:conditions['usage_storage']=self.provider.token_usage.snapshot().get('storage_error')
        for name,value in (('action_dispatch',self.broker.last_execution),('wake_dispatch',self.broker.last_wake),
                           ('pending_action',self.pending_actions.snapshot() if self.pending_actions else None)):
            status_value=(value or {}).get('status','')
            if any(word in status_value for word in ('failed','unavailable','unconfirmed','not_confirmed','expired','cancelled','rejected')):
                conditions[name]={k:v for k,v in value.items() if k in ('status','candidate','stage','reason','winerror')}
        for name,value in conditions.items():
            if value and value!=self._diagnostic_conditions.get(name):
                self.errors.record('operation_error',name,details={'condition':value})
        self._diagnostic_conditions=deepcopy(conditions)
        metadata = {'mode':self.mode,'automatic':self.automatic,
            'waiting_for_observation':self.gate.waiting_for_observation,
            'errors':self.errors.snapshot(),'last_chat_error':self.last_chat_error,
            'cadence':asdict(self.cadence),'cadence_storage_error':self.cadence_storage_error,
            'settings_scope':'shared_file' if self.settings_path else 'service_local',
            'settings_restart_required_for':'provider, limits and prompt edits',
            'delay_seconds':None if self.is_llm else self.provider.delay,
            'dummy_requests':0 if self.is_llm else self.broker.calls_started,
            'external_model_calls':self.provider.calls if self.is_llm else 0,
            'game_actions':self._game_actions(),'engine_execution_enabled':self.action_factory is not None,
            'action_mode':(('bounded_model_question_actions' if self.is_llm else 'bounded_dummy_question_actions') if self.pending_actions else
                           'single_attempt_questions' if self.action_factory else 'disabled'),
            'action_dispatch':deepcopy(self.broker.last_execution),
            'thinking_pause':self.broker.thinking_pause_status,
            'pending_action':self.pending_actions.snapshot() if self.pending_actions else None,
            'wake_on_question_enabled':self.wake_factory is not None,
            'wake_dispatch':deepcopy(self.broker.last_wake),
            'pending_questions':len(self.broker._questions)+int(self.broker._waiting_entry is not None
                and self.broker._waiting_entry.question is not None),'pending_tick':self.broker._auto is not None,
            'merged_ticks':self.broker.merged_ticks,'active_requests':self.provider.active,
            'max_active_requests':self.provider.max_active,
            'last_error':self.last_error or self.broker.last_error,
            'last_request':list(self.provider.requests[-1].messages) if self.provider.requests else None,
            'availability_basis':'fresh_selected_owned_creature; story availability not fully verified',
            'control_coverage':'known pause, temple, script-camera and creature-script locks; other story locks unverified'}
        if self.is_llm:
            metadata['provider'] = self.provider.status()
        if self.settings is not None:
            # Explicitly requested local debug configuration. Never include key
            # values, credential variable names, dotenv contents or file paths.
            effective=asdict(self.settings)
            effective.pop('api_key_env',None)
            effective.pop('voice',None)  # Overlay-only device/model paths and credential variable.
            effective['cadence']=asdict(self.cadence)
            effective['prompts']=asdict(self.prompt_settings)
            metadata['settings']=effective
        frame = {'recorded_at':datetime.now(timezone.utc).isoformat(),
            'mode':self.mode,'provider_calls':self.provider.calls if self.is_llm else 0,'game_actions':self._game_actions(),
            'gate':{'allowed':status.allowed,'reasons':list(status.reasons)},
            'question_unavailable':self.question_scene_blocked(),
            'memory':memory,'speech':self.broker.speech.snapshot(now),
            'answer_status':self.broker.answer_status(now),
            'reset_available':self.reset_available(),
            'force_tick_unavailable':self.force_tick_unavailable(current['snapshot']),
            'dialogue':list(self.dialogue),
            'character_names':asdict(self.character_names),
            'model_context':model_snapshot(current['snapshot']),
            'prompt':{'system':self.memory.system_prompt(system),'context':current['snapshot']},'status_updates':deepcopy(self.scheduled)}
        with self._state_lock:
            # The source is a published read-only frame, replaced rather than
            # mutated. Public readers get their own copy below.
            self._published = {'current':frame,'runtime':metadata,'source':self.source}

    def payload(self, number=None):
        with self._state_lock:
            return deepcopy(self._published)

    def debug_payload(self, sections=()):
        """Copy large diagnostic sections only when their browser panel is open."""
        from .qa import DEBUG_SECTIONS
        sections = frozenset(sections)
        if not sections <= DEBUG_SECTIONS:raise ValueError('Unknown debug section')
        if 'full' in sections:return self.payload()
        with self._state_lock:
            full = self._published
            frame = {k:v for k,v in full.get('current', {}).items() if k != 'prompt'}
            memory = dict(frame.get('memory', {}))
            pairs = memory.get('recent_pairs', [])
            memory['recent_pair_count'] = len(pairs)
            last = pairs[-1] if pairs else None
            frame['last_decision'] = ({'decision':last.get('decision'), 'status':last.get('action_status')}
                                      if last else None)
            if 'history' not in sections:memory.pop('recent_pairs', None)
            frame['memory'] = memory
            frame['dialogue'] = [{'input':{'god_question':pair.get('input', {}).get('god_question')},
                                 'answer':pair.get('answer'), 'say':pair.get('say')}
                                for pair in frame.get('dialogue', [])]
            frame['status_updates'] = {k:v for k,v in frame.get('status_updates', {}).items()
                                       if k != 'last_update'}
            metadata = {k:v for k,v in full.get('runtime', {}).items()
                        if k not in ('last_request', 'settings')}
            for section, key in (('request', 'last_request'), ('settings', 'settings')):
                if section in sections and key in full.get('runtime', {}):
                    metadata[key] = full['runtime'][key]
            if 'provider' in metadata:
                metadata['provider'] = {k:v for k,v in metadata['provider'].items()
                    if 'tools' in sections or k not in ('tool_trace', 'offered_tools')}
            return deepcopy({'current':frame, 'runtime':metadata})

    def overlay_payload(self):
        """Only chat display and control facts; no world catalog or debug history."""
        def keep(value, names):
            return {name:value[name] for name in names if name in value}
        with self._state_lock:
            full=self._published
            current=keep(full.get('current',{}),('gate','question_unavailable',
                'speech','answer_status','character_names'))
            runtime=keep(full.get('runtime',{}),('mode','last_chat_error',
                'waiting_for_observation','active_requests','external_model_calls'))
            runtime['provider']=keep(full.get('runtime',{}).get('provider',{}),
                ('last_error','ready','disabled','retry_after_seconds'))
            raw=full.get('source')
            source=None if raw is None else {
                'observation':keep(raw.get('observation',{}),('session','creature_id',
                    'connected','observed_at','paused','in_temple','cutscene',
                    'loading','script_owns_creature','player_has_control')),
                'diagnostics':keep(raw.get('diagnostics',{}),
                    ('paused','in_temple','cutscene','script_camera_owned'))}
            return deepcopy({'current':current,'runtime':runtime,'source':source})

    def preview_question(self, question):
        raise ValueError('Use the live chat question route')

    def handler(self, method):
        async def invoke(payload):
            try:return await method(payload)
            except Exception as error:
                if method!=self.question:self._report_error(method.__name__,error)
                raise
        def handle(payload):
            future = asyncio.run_coroutine_threadsafe(invoke(payload),self.loop)
            try:
                return future.result(timeout=(self.broker.max_questions+1)*
                    (self.request_timeout+self.broker.provider_wait_timeout)+5)
            except FutureTimeout:
                future.cancel()
                self.loop.call_soon_threadsafe(self._report_error,method.__name__,TimeoutError())
                raise RuntimeError('Chat request timed out') from None
        return handle

    async def close(self):
        self._cancel_pending('cancelled_shutdown')
        self._cancel_automatic()
        await self.broker.close()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks),return_exceptions=True)
        if self.is_llm:
            await self.provider.close()


class LiveDummyRuntime(LiveChatRuntime):
    """Retain the fixed offline/live QA entry point with no network provider."""
    def __init__(self, state_dir=None, *, clock=time.monotonic, names_reader=None, settings=None, settings_path=None,
                 action_factory=None, wake_factory=None):
        super().__init__(state_dir, clock=clock, names_reader=names_reader,
                         settings=settings,settings_path=settings_path,action_factory=action_factory,
                         wake_factory=wake_factory)


async def serve(source_url, port=0, seconds=43200, *, state_dir=None, token=None, config=None,
                settings_path=None, dummy_settings=None, action_pid=None, debug=False,
                current_action_pid=None, on_ready=None, automatic=None,
                source_reader=None, local_pipe=False, debug_observer=None, debug_session=None,
                request_details=None):
    if type(seconds) is not int or not 1 <= seconds <= 43200 or type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid live chat server limits')
    read_source = source_reader if source_reader is not None else InspectorSource(source_url).read
    action_factory = None
    wake_factory = None
    thinking_pause_factory = None
    actions_enabled = action_pid is not None or current_action_pid is not None
    if actions_enabled:
        from .action_probe import ConversationActionAdapter, QuestionWakeAdapter, TreeApproachAdapter, HeldActionAdapter
        from .dispatch import DecisionDispatcher
        if action_pid is not None and (type(action_pid) is not int or not 0 < action_pid <= 0xffffffff):
            raise ValueError('Invalid experimental action process')
        def process_for(obs):
            pid = current_action_pid() if current_action_pid is not None else action_pid
            if type(pid) is not int or not 0 < pid <= 0xffffffff or obs.session.partition(':')[0] != str(pid):
                raise ValueError('Game detached or changed before action selection')
            return pid
        if config is not None:
            from .thinking_pause import ThinkingPause
            def thinking_pause_factory(obs):
                return ThinkingPause(process_for(obs), obs)
        def action_factory(obs, *, expected_hand_generation=None, expected_object_generation=None, expected_held_generation=None,expected_throw_generation=None,expected_pickup_generation=None,miracle_action=None,expected_miracle_target=None,wake_action=False,world_action=None,expected_world_token=None,destination=None,pace=None,player_command=False):
            action_pid = process_for(obs)
            priority = dict(player_command=player_command, night_priority=obs.environment.time_of_day == 'night')
            if destination is not None:
                from .action_probe import PositionActionAdapter
                return DecisionDispatcher(PositionActionAdapter(action_pid,interrupt=True,**priority,pace=pace,
                    expected_identity=(obs.session,obs.creature_id),destination=destination))
            if world_action is not None:
                from .action_probe import WorldActionAdapter
                return DecisionDispatcher(WorldActionAdapter(action_pid,interrupt=True,**priority,pace=pace,expected_identity=(obs.session,obs.creature_id),
                    world_action=world_action,expected_world_token=expected_world_token))
            if wake_action:
                return DecisionDispatcher(QuestionWakeAdapter(action_pid,interrupt=True,**priority,expected_identity=(obs.session,obs.creature_id)))
            if miracle_action is not None:
                from .action_probe import MiracleActionAdapter
                return DecisionDispatcher(MiracleActionAdapter(action_pid,interrupt=True,**priority,expected_identity=(obs.session,obs.creature_id),
                    miracle_action=miracle_action,expected_miracle_target=expected_miracle_target))
            if expected_pickup_generation is not None:
                from .action_probe import PickupActionAdapter
                return DecisionDispatcher(PickupActionAdapter(action_pid,interrupt=True,**priority,expected_identity=(obs.session,obs.creature_id),
                    expected_pickup_generation=expected_pickup_generation))
            if expected_throw_generation is not None:
                from .action_probe import ThrowActionAdapter
                return DecisionDispatcher(ThrowActionAdapter(action_pid,interrupt=True,**priority,expected_identity=(obs.session,obs.creature_id),
                    expected_throw_generation=expected_throw_generation,expected_held_generation=expected_held_generation))
            if expected_held_generation is not None:
                return DecisionDispatcher(HeldActionAdapter(action_pid,
                    interrupt=True,**priority,expected_identity=(obs.session,obs.creature_id),expected_held_generation=expected_held_generation))
            if expected_object_generation is not None:
                return DecisionDispatcher(TreeApproachAdapter(action_pid,
                    interrupt=True,**priority,pace=pace,expected_identity=(obs.session, obs.creature_id),
                    expected_object_generation=expected_object_generation))
            return DecisionDispatcher(ConversationActionAdapter(action_pid,
                interrupt=True,**priority,pace=pace,expected_identity=(obs.session, obs.creature_id),
                expected_hand_generation=expected_hand_generation))
        if config is None or config.wake_on_question:
            def wake_factory(obs):
                return DecisionDispatcher(QuestionWakeAdapter(process_for(obs),
                    interrupt=True,night_priority=obs.environment.time_of_day == 'night',expected_identity=(obs.session, obs.creature_id)))
    provider = None
    if config is not None:
        if config.wake_on_question:
            from .action_probe import QuestionWakeAdapter
            from .dispatch import DecisionDispatcher
            def wake_factory(obs):
                # This is a host reaction to an explicit question, not a model
                # action capability. The adapter rechecks identity and guards.
                pid=process_for(obs) if current_action_pid is not None else int(obs.session.partition(':')[0])
                return DecisionDispatcher(QuestionWakeAdapter(pid,
                    interrupt=True,night_priority=obs.environment.time_of_day == 'night',expected_identity=(obs.session,obs.creature_id)))
        from .llm import LLMProvider
        from .credentials import credential_environment
        credentials_path = (Path(settings_path).parent if settings_path is not None else Path.cwd()) / '.local' / '.env'
        provider = LLMProvider(config, environ=credential_environment(config.api_key_env, credentials_path))
    server = thread = pipe_server = runtime = None
    url = None
    try:
        runtime = LiveChatRuntime(state_dir, provider=provider,settings=config or dummy_settings,
                                  settings_path=settings_path, action_factory=action_factory,
                                  wake_factory=wake_factory,wait_actions=actions_enabled,request_details=request_details,
                                  thinking_pause_factory=thinking_pause_factory)
        if automatic is not None:
            runtime.automatic = automatic
        if debug_session is not None:
            await debug_session.attach(runtime, debug_observer)
        elif debug or not local_pipe:
            try:
                server,url = make_server(runtime,port,token,post_handlers={
                    'question':runtime.handler(runtime.question),'configure':runtime.handler(runtime.configure),
                    'reset':runtime.handler(runtime.reset),'force-tick':runtime.handler(runtime.force_tick)},
                    page_name='qa_live.html',debug=debug,observer=debug_observer)
            except OSError as error:
                raise RuntimeError('Could not open the localhost debugger port. Close the other server '
                                   'or choose another --port. No fallback port was selected.') from error
            thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        if local_pipe:
            from .chat_pipe import ChatPipeServer
            pipe_server = ChatPipeServer(runtime.overlay_payload, runtime.handler(runtime.question))
    except BaseException:
        if debug_session is not None:
            await debug_session.close()
        if server is not None:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
        if runtime is not None:
            await runtime.close()
        elif provider is not None:
            await provider.close()
        raise
    if debug and debug_session is None:
        print(f'Debug LLM: {url + "llm/" if debug_observer is not None else url}', flush=True)
        if debug_observer is not None:
            print(f'Debug observer: {url}observer/', flush=True)
    if not local_pipe:
        print(json.dumps({'api_url':url,'qa_url':url if debug else None,'debug_ui':debug,
                          'mode':runtime.mode,'external_model_calls':0,'game_actions':0}),flush=True)
    deadline = time.monotonic()+seconds
    try:
        if on_ready is not None:
            on_ready(pipe_server.endpoint if pipe_server is not None else url, runtime)
        while time.monotonic()<deadline:
            try:
                frame = await asyncio.to_thread(read_source)
            except (OSError,ValueError,KeyError):
                frame = None
            runtime.ingest(frame,publish=False,copy_source=source_reader is None)
            await runtime.step()
            await asyncio.sleep(.25)
    finally:
        if debug_session is not None:
            await debug_session.close()
        await runtime.close()
        if pipe_server is not None:
            await asyncio.to_thread(pipe_server.close)
        if server is not None:
            await asyncio.to_thread(server.shutdown)
            server.server_close(); thread.join(2)


def run(source_url, port=0, seconds=43200, *, state_dir=None, config_path=None, action_pid=None, debug=False):
    from .llm import load_settings
    settings,config_path=load_settings(config_path)
    asyncio.run(serve(source_url,port,seconds,state_dir=state_dir,
                     settings_path=config_path,dummy_settings=settings,action_pid=action_pid,debug=debug))
    return {'stopped':True,'external_model_calls':0,'game_actions':None if action_pid else 0}


def run_llm(source_url, config_path, port=0, seconds=43200, *, state_dir=None, action_pid=None, debug=False):
    from .llm import load_settings
    config,config_path = load_settings(config_path)
    asyncio.run(serve(source_url,port,seconds,state_dir=state_dir,config=config,settings_path=config_path,
                     action_pid=action_pid,debug=debug))
    return {'stopped':True,'game_actions':None if action_pid is not None or config.wake_on_question else 0}
