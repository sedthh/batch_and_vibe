"""Offline smoke scenarios. These observations and actions are all synthetic."""
from __future__ import annotations

import asyncio
from dataclasses import replace

from .control import ControlGate, ControlObservation
from .mock import MockProvider
from .planner import CreaturePlanner
from .actions import ActionIntent, PickupTarget


async def run() -> dict:
    results = []
    calls = 0

    def check(condition, name):
        if not condition:
            raise RuntimeError(f"Smoke scenario failed: {name}")
        results.append({"scenario": name, "passed": True})

    def observation(now, **changes):
        return replace(ControlObservation("synthetic-save", int(now * 100), now,
            creature_id="synthetic-creature", connected=True, supported=True,
            world_ready=True, loading=False, paused=False, cutscene=False,
            player_has_control=True, creature_present=True, script_owns_creature=False, in_temple=False,
            candidates=("rest:synthetic-home",)), **changes)

    now = [0.0]
    provider = MockProvider()
    planner = CreaturePlanner(provider, clock=lambda: now[0],
        gate=ControlGate(resume_delay=0), cooldown=0)
    try:
        for lock in [dict(in_temple=True), dict(creature_present=False, creature_id=None), dict(cutscene=True),
                     dict(script_owns_creature=True), dict(player_has_control=False),
                     dict(paused=True), dict(loading=True), dict(cutscene=None)]:
            now[0] += 1
            planner.update(observation(now[0], **lock))
            planner.tick()
            await asyncio.sleep(0)
        check(planner.calls_started == 0 and not provider.requests, "zero_calls_during_control_locks")
        now[0] += 1
        planner.update(observation(now[0]))
        planner.tick()
        await asyncio.sleep(0)
        proposal = planner.take_proposal()
        check(proposal is not None and proposal.candidate == "rest:synthetic-home", "valid_proposal")
        check(bool(provider.requests[0].interruption_summary), "local_resume_summary")
        planner.tick()
        await asyncio.sleep(0)
        now[0] += 1
        planner.update(observation(now[0], script_owns_creature=True))
        check(planner.take_proposal() is None, "queued_proposal_revoked")
    finally:
        calls += planner.calls_started
        await planner.close()

    release = asyncio.Event()
    provider = MockProvider(release=release, ignore_cancel=True)
    planner = CreaturePlanner(provider, clock=lambda: now[0], gate=ControlGate(resume_delay=0))
    try:
        planner.update(observation(now[0]))
        planner.tick()
        await provider.entered.wait()
        now[0] += 1
        planner.update(observation(now[0], cutscene=True, context_event="Synthetic side-quest cutscene"))
        await asyncio.sleep(0)  # Deliver cancellation before restoring control.
        now[0] += 1
        planner.update(observation(now[0]))
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        check(planner.take_proposal() is None and planner.results_discarded == 1,
              "late_reply_rejected_after_cutscene")
    finally:
        release.set()
        calls += planner.calls_started
        await planner.close()

    for name, provider, timeout in [
        ("invalid_command_rejected", MockProvider(reply="invented:command"), 1),
        ("provider_failure_handled", MockProvider(fail=True), 1),
        ("provider_timeout_handled", MockProvider(delay=60), 0.01),
    ]:
        planner = CreaturePlanner(provider, clock=lambda: now[0],
            gate=ControlGate(resume_delay=0), timeout=timeout)
        try:
            planner.update(observation(now[0]))
            planner.tick()
            await provider.entered.wait()
            # Bounded completion wait for these controlled synthetic providers.
            for _ in range(100):
                if planner.results_discarded or planner.last_error:
                    break
                await asyncio.sleep(0.002)
            check(planner.take_proposal() is None and bool(planner.results_discarded or planner.last_error), name)
        finally:
            calls += planner.calls_started
            await planner.close()

    provider = MockProvider()
    planner = CreaturePlanner(provider, clock=lambda: now[0], gate=ControlGate(resume_delay=0))
    try:
        planner.update(observation(now[0]))
        planner.tick()
        await asyncio.sleep(0)
        now[0] += 0.1
        planner.update(observation(now[0], candidates=("rest:different-home",)))
        check(planner.take_proposal() is None, "unavailable_target_rejected")
    finally:
        calls += planner.calls_started
        await planner.close()

    targets = (PickupTarget('tree-a','instance-a','tree','pickup:tree-a',2,False,True),
               PickupTarget('tree-b','instance-b','tree','pickup:tree-b',5,True,True))
    planner = CreaturePlanner(MockProvider(reply=ActionIntent('pick_up','tree').as_dict()),
        clock=lambda: now[0], gate=ControlGate(resume_delay=0))
    try:
        planner.update(observation(now[0], pickup_targets=targets,
                                   candidates=tuple(t.candidate for t in targets)))
        planner.tick()
        await planner._task
        proposal = planner.take_proposal()
        check(proposal is not None and proposal.resolved_action is not None
              and proposal.resolved_action.target.object_id == 'tree-b',
              'intent_resolves_nearest_eligible_target')
    finally:
        calls += planner.calls_started
        await planner.close()

    from .conversation import ConversationBroker, ConversationMemory
    from .mock import MockConversationProvider
    from .experience import BodyState, NativeInstinct, Experience
    provider = MockConversationProvider([{'answer':'Synthetic creature answer.',
        'decision':{'candidate':None}, 'memory':'Synthetic QA memory.', 'say':'Synthetic speech.'}])
    gate = ControlGate(resume_delay=0)
    gate.update(observation(now[0],experience=Experience(BodyState(health=.8),NativeInstinct(11,'Eat'))),now[0])
    broker = ConversationBroker(provider,gate,clock=lambda:now[0],memory=ConversationMemory())
    try:
        reply = await broker.ask('Synthetic God question')
        check(reply['decision']=={'candidate':None}
              and provider.requests[0].current['snapshot']['instinct']['native_plan']=='Eat'
              and broker.memory.notes=='Synthetic QA memory.'
              and broker.speech.snapshot(now[0])['current']['text']=='Synthetic speech.',
              'dummy_conversation_context_memory_and_speech')
    finally:
        calls += broker.calls_started
        await broker.close()

    return {"mode": "synthetic_offline", "network_calls": 0, "game_actions": 0,
            "mock_requests": calls, "scenarios": results}
