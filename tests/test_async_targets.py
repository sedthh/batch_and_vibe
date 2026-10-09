"""Slow native lookups must not starve the live observation loop."""
import asyncio
from copy import deepcopy
import threading
import unittest

from bwv.creature_tools import ToolContextUnavailable
from test_creature_tools import frame
from test_tool_interface import bound
from test_world_actions import WorldHarness


class AsyncTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_keeps_observations_updating_and_commits_on_owner_thread(self):
        h=WorldHarness();original=h.pending.source;owner=threading.get_ident()
        obj={'object_id':'far-field','kind':'field','distance_engine_units':600,'lookup_handle':987}
        def source():
            self.assertEqual(threading.get_ident(),owner)
            value=original();value['observation']['surroundings']['known_objects']=[obj]
            return value
        h.pending.source=source
        offer=h.pending.factory(h.obs());offer.offer()
        started=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
        probe=deepcopy(h.probe)
        probe['world_actions']['offers'].append({'action':'move_to','token':999,'kind':'field',
            'object_handle':987,'distance_engine_units':600})
        def lookup(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),owner)
            loop.call_soon_threadsafe(started.set)
            if not release.wait(2):raise TimeoutError('Synthetic lookup timed out')
            return probe
        offer.lookup=lookup
        task=asyncio.create_task(offer.resolve_object_async('far-field','move_to'))
        try:
            await asyncio.wait_for(started.wait(),1)
            # The game feed can advance past its freshness budget while the
            # lookup waits; the current observation is still read on the loop.
            h.now+=2
            self.assertFalse(task.done());self.assertFalse(h.sent)
            release.set()
            targets=await asyncio.wait_for(task,1)
            self.assertEqual(targets[0]['object_id'],'far-field')
            self.assertFalse(h.sent)
        finally:
            release.set()
            await asyncio.gather(task,return_exceptions=True)

    async def test_scene_change_during_async_tool_lookup_does_not_select_action(self):
        source=frame();source['observation']['surroundings']={'known_objects':[
            {'object_id':'store','kind':'storage','distance_engine_units':40}]}
        h=WorldHarness();session,offer,_=bound(h,source,lambda *a:[])
        entered=asyncio.Event();release=asyncio.Event();valid=True
        async def resolve(identity,recipe):
            entered.set();await release.wait()
            return [{'object_id':identity,'kind':'storage','target_id':'world-'+'a'*32,'distance_engine_units':40}]
        def validate():
            if not valid:raise ToolContextUnavailable('Synthetic scene changed')
            return source
        session.supplier=session.inner.supplier=validate
        session.async_target_resolver=resolve
        task=asyncio.create_task(session.call_async('do_action',{'action':'eat','target':'store'}))
        await asyncio.wait_for(entered.wait(),1)
        valid=False;release.set()
        with self.assertRaises(ToolContextUnavailable):await task
        self.assertIsNone(session.inner.selected_action);self.assertFalse(h.sent)

    async def test_cancelled_native_lookup_cannot_add_late_capability(self):
        h=WorldHarness();original=h.pending.source
        def source():
            value=original();value['observation']['surroundings']['known_objects']=[
                {'object_id':'field','kind':'field','distance_engine_units':20,'lookup_handle':987}]
            return value
        h.pending.source=source
        offer=h.pending.factory(h.obs());offer.offer()
        started=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
        def lookup(*args,**kwargs):
            loop.call_soon_threadsafe(started.set);release.wait(2)
            return h.probe
        offer.lookup=lookup
        before=deepcopy(offer.targets)
        task=asyncio.create_task(offer.resolve_object_async('field','move_to'))
        try:
            await asyncio.wait_for(started.wait(),1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            release.set();await asyncio.sleep(.02)
            self.assertEqual(offer.targets,before);self.assertIsNone(offer.lookup_cache)
            self.assertFalse(h.sent)
        finally:release.set()
