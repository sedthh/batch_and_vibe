from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
import time
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from bwv.watch import observations
from bwv.experience import BodyState, ExperienceSample
from bwv.native_text_live import NativeTextSample, NativeMessage
from bwv.surroundings import NearbyObject, SurroundingsContext
from bwv.environment import Environment
from bwv.miracle import MiracleContext, MiracleSample
from bwv.surroundings_live import SurroundingsReadError
from bwv.sampling import DetailRefresh
from bwv.island import IslandContext
from bwv.island_live import IslandSample
from test_control import playable


class ExperienceStreamTests(IsolatedAsyncioTestCase):
    async def test_god_hand_failure_is_optional_and_contents_recover_without_stale_values(self):
        from bwv.god_hand import GodHandContext
        from bwv.god_hand_live import GodHandSample
        from bwv.holding_live import HoldingSample
        base=playable();reader=Mock();paused=[False]
        frames=iter((GodHandContext('activated_miracle',activated_miracle='food'),
                     OSError('Synthetic private error'),GodHandContext('empty')))
        def read_hand(*args):
            value=next(frames)
            if isinstance(value,Exception):raise value
            return GodHandSample(base.session,base.creature_id,time.monotonic(),value)
        with ExitStack() as stack:
            overrides={
                'bwv.watch.snapshot':Mock(return_value={}),
                'bwv.watch.from_observer':lambda *args:replace(base,observed_at=time.monotonic()),
                'bwv.training_live.TrainingReader':Mock(return_value=reader),
                'bwv.control_live.read_pause':lambda *args:SimpleNamespace(paused=paused[0]),
                'bwv.control_live.with_pause':lambda obs,signal,*args:replace(obs,paused=signal.paused),
                'bwv.control_live.read_camera_control':Mock(return_value=None),
                'bwv.control_live.with_camera_control':lambda obs,*args:obs,
                'bwv.control_live.read_temple':Mock(return_value=None),
                'bwv.control_live.with_temple':lambda obs,*args:obs,
                'bwv.creature_live.read_creature':Mock(return_value=SimpleNamespace(
                    creature_id=base.creature_id,context=base.creature,script_owns_creature=False)),
                'bwv.surroundings_live.read_surroundings':Mock(return_value=SimpleNamespace(
                    session=base.session,creature_id=base.creature_id,context=base.surroundings)),
                'bwv.holding_live.read_holding':lambda *args:HoldingSample(
                    base.session,base.creature_id,time.monotonic(),base.holding),
                'bwv.god_hand_live.read_god_hand':Mock(side_effect=read_hand),
            }
            for name,value in overrides.items():stack.enter_context(patch(name,value))
            stream=observations(123,holding=True)
            try:
                first,_,_,error=await anext(stream)
                self.assertIsNone(error)
                self.assertEqual(first.god_hand.activated_miracle,'food')
                failed,_,_,error=await anext(stream)
                self.assertEqual(failed.creature_id,first.creature_id)
                self.assertTrue(failed.connected)
                self.assertEqual(failed.god_hand,GodHandContext())
                self.assertEqual(error,'god_hand:OSError')
                recovered,_,_,error=await anext(stream)
                self.assertIsNone(error)
                self.assertEqual(recovered.god_hand.contents,'empty')
                paused[0]=True
                stopped,_,_,_=await anext(stream)
                self.assertEqual(stopped.god_hand,GodHandContext())
                self.assertEqual(overrides['bwv.god_hand_live.read_god_hand'].call_count,3)
            finally:await stream.aclose()

    async def test_miracle_failure_preserves_feed_clears_knowledge_and_recovers(self):
        base=playable();reader=Mock();paused=[False]
        def knowledge(*args):
            return MiracleSample(base.session,base.creature_id,time.monotonic(),10,0,0,
                                 16,900,15)
        with ExitStack() as stack:
            overrides={
                'bwv.watch.snapshot':Mock(return_value={'ready':True}),
                'bwv.watch.from_observer':lambda *args:replace(base,observed_at=time.monotonic()),
                'bwv.training_live.TrainingReader':Mock(return_value=reader),
                'bwv.control_live.read_pause':lambda *args:SimpleNamespace(paused=paused[0]),
                'bwv.control_live.with_pause':lambda obs,signal,*args:replace(obs,paused=signal.paused),
                'bwv.control_live.read_camera_control':Mock(return_value=None),
                'bwv.control_live.with_camera_control':lambda obs,*args:obs,
                'bwv.control_live.read_temple':Mock(return_value=None),
                'bwv.control_live.with_temple':lambda obs,*args:obs,
                'bwv.creature_live.read_creature':Mock(return_value=SimpleNamespace(
                    creature_id=base.creature_id,context=base.creature,script_owns_creature=False)),
                'bwv.surroundings_live.read_surroundings':Mock(return_value=SimpleNamespace(
                    session=base.session,creature_id=base.creature_id,context=base.surroundings)),
            }
            for name,value in overrides.items():stack.enter_context(patch(name,value))
            read=stack.enter_context(patch('bwv.miracle_live.read_miracle',side_effect=knowledge))
            stream=observations(123,miracles=True)
            try:
                first,_,_,error=await anext(stream)
                self.assertIsNone(error)
                self.assertTrue(first.miracles.heal_knowledge.learning_threshold_met)
                for failure in (ValueError('Synthetic private details'), OSError('Synthetic private path')):
                    read.side_effect=failure
                    failed,state,_,error=await anext(stream)
                    self.assertTrue(failed.connected)
                    self.assertEqual(failed.creature_id,base.creature_id)
                    self.assertEqual(failed.creature,base.creature)
                    self.assertFalse(failed.paused)
                    self.assertTrue(state['ready'])
                    self.assertEqual(failed.miracles,MiracleContext())
                    self.assertEqual(error,'miracles:'+type(failure).__name__)
                read.side_effect=knowledge
                recovered,_,_,error=await anext(stream)
                self.assertIsNone(error)
                self.assertTrue(recovered.miracles.heal_knowledge.learning_threshold_met)
                self.assertEqual(recovered.miracles.learning,())
                paused[0]=True
                locked,_,_,error=await anext(stream)
                self.assertTrue(locked.paused)
                self.assertEqual(locked.miracles,MiracleContext())
                paused[0]=False
                read.side_effect=lambda *args:replace(knowledge(),session='different')
                changed,_,_,error=await anext(stream)
                self.assertFalse(changed.connected)
                self.assertIsNone(changed.creature_id)
                self.assertEqual(error,'miracles:ValueError')
            finally:await stream.aclose()
        reader.close.assert_called_once()

    async def test_cached_details_keep_fast_health_and_pause_live_and_refresh_on_demand(self):
        base=playable();phase=[0];reader=Mock();cache=DetailRefresh(interval=10000)
        reads=Mock(return_value=SimpleNamespace(session=base.session,creature_id=base.creature_id,context=base.surroundings))
        roster=IslandContext(True,({'god_id':'god_0','is_player':True},),True)
        def read_island(*args):
            if phase[0]==2:raise ValueError('Synthetic private diagnostic')
            return IslandSample(base.session,base.creature_id,roster)
        self.island_reader.side_effect=read_island
        def body(*args):
            return ExperienceSample(base.session,base.creature_id,time.monotonic(),100+phase[0],
                                    BodyState(health=1-phase[0]*.1))
        with ExitStack() as stack:
            overrides={
                'bwv.watch.snapshot':Mock(return_value={}),
                'bwv.watch.from_observer':lambda *args:replace(base,observed_at=time.monotonic()),
                'bwv.training_live.TrainingReader':Mock(return_value=reader),
                'bwv.control_live.read_pause':lambda *args:SimpleNamespace(paused=phase[0]==3),
                'bwv.control_live.with_pause':lambda obs,signal,now:replace(obs,paused=signal.paused),
                'bwv.control_live.read_camera_control':Mock(return_value=None),
                'bwv.control_live.with_camera_control':lambda obs,*args:obs,
                'bwv.control_live.read_temple':Mock(return_value=None),
                'bwv.control_live.with_temple':lambda obs,*args:obs,
                'bwv.creature_live.read_creature':lambda *args:SimpleNamespace(creature_id=base.creature_id,
                    context=base.creature,script_owns_creature=phase[0]==1),
                'bwv.surroundings_live.read_surroundings':reads,
                'bwv.experience_live.read_experience':body,
                'bwv.native_text_live.read_native_text':Mock(return_value=NativeTextSample(base.session,base.creature_id,0,None)),
            }
            for name,value in overrides.items():stack.enter_context(patch(name,value))
            stream=observations(123,experience=True,detail_refresh=cache)
            try:
                first,state,_,error=await anext(stream)
                self.assertIsNone(error)
                self.assertFalse(first.script_owns_creature)
                self.assertEqual(first.island,roster)
                sampled=state['detailed_sensors']['observed_at']
                phase[0]=1
                second,state,_,_=await anext(stream)
                self.assertEqual(reads.call_count,1)
                self.assertEqual(self.island_reader.call_count,1)
                self.assertEqual(state['detailed_sensors']['observed_at'],sampled)
                self.assertAlmostEqual(second.experience.body.health,.9)
                self.assertTrue(second.script_owns_creature)
                self.assertGreater(second.observed_at,first.observed_at)
                revision=cache.request();phase[0]=2
                third,state,_,error=await anext(stream)
                self.assertFalse(third.script_owns_creature)
                self.assertTrue(third.connected)
                self.assertFalse(third.island.available)
                self.assertEqual(error,'island:ValueError')
                self.assertAlmostEqual(third.experience.body.health,.8)
                self.assertEqual(reads.call_count,2)
                self.assertEqual(self.island_reader.call_count,2)
                self.assertEqual(state['detailed_sensors']['revision'],revision)
                phase[0]=3
                paused,state,_,_=await anext(stream)
                self.assertTrue(paused.paused)
                self.assertEqual(paused.island,IslandContext())
                self.assertIsNone(state['detailed_sensors']['observed_at'])
                phase[0]=4
                resumed,_,_,_=await anext(stream)
                self.assertFalse(resumed.paused)
                self.assertEqual(reads.call_count,3)
                self.assertEqual(self.island_reader.call_count,3)
                self.assertEqual(resumed.island,roster)
                self.assertAlmostEqual(resumed.experience.body.health,.6)
            finally:await stream.aclose()
        reader.close.assert_called_once()

    async def test_optional_town_needs_error_is_visible_without_fast_rescans(self):
        nearby=SurroundingsContext(True,(NearbyObject('house','house',4),),town_need_read_errors=1)
        base=playable();reader=Mock();cache=DetailRefresh(interval=10000)
        samples=Mock(return_value=SimpleNamespace(session=base.session,creature_id=base.creature_id,context=nearby))
        with ExitStack() as stack:
            overrides={
                'bwv.watch.snapshot':Mock(return_value={}),
                'bwv.watch.from_observer':lambda *args:replace(base,observed_at=time.monotonic()),
                'bwv.training_live.TrainingReader':Mock(return_value=reader),
                'bwv.control_live.read_pause':Mock(return_value=SimpleNamespace(paused=False)),
                'bwv.control_live.with_pause':lambda obs,*args:obs,
                'bwv.control_live.read_camera_control':Mock(return_value=None),
                'bwv.control_live.with_camera_control':lambda obs,*args:obs,
                'bwv.control_live.read_temple':Mock(return_value=None),
                'bwv.control_live.with_temple':lambda obs,*args:obs,
                'bwv.creature_live.read_creature':Mock(return_value=SimpleNamespace(creature_id=base.creature_id,
                    context=base.creature,script_owns_creature=False)),
                'bwv.surroundings_live.read_surroundings':samples,
            }
            for name,value in overrides.items():stack.enter_context(patch(name,value))
            stream=observations(123,control=True,detail_refresh=cache)
            try:
                for _ in range(3):
                    obs,state,_,error=await anext(stream)
                    self.assertTrue(obs.connected)
                    self.assertEqual(obs.surroundings,nearby)
                    self.assertEqual(error,'town_needs:read_unavailable')
                    self.assertIsNotNone(state['detailed_sensors']['observed_at'])
                self.assertEqual(samples.call_count,1)
                samples.return_value.context=replace(nearby,town_need_read_errors=0)
                cache.request()
                obs,_,_,error=await anext(stream)
                self.assertIsNone(error)
                self.assertEqual(obs.surroundings.town_need_read_errors,0)
                self.assertEqual(samples.call_count,2)
            finally:await stream.aclose()
        reader.close.assert_called_once()

    async def asyncSetUp(self):
        sensor=patch('bwv.environment_live.read_environment',return_value=Environment('morning',8,'engine_visual_clock'))
        self.environment_reader=sensor.start()
        self.addCleanup(sensor.stop)
        island=patch('bwv.island_live.read_island',side_effect=lambda *args:IslandSample(
            playable().session,playable().creature_id,IslandContext()))
        self.island_reader=island.start()
        self.addCleanup(island.stop)

    async def test_surroundings_failure_clears_counts_preserves_identity_and_recovers(self):
        nearby=SurroundingsContext(True,(NearbyObject('tree','tree',4),))
        base=playable(surroundings=nearby);reader=Mock()
        signal=SimpleNamespace(session=base.session,creature_id=base.creature_id,context=nearby)
        samples=Mock(side_effect=[signal,ValueError('Synthetic private path'),signal,
                                 SurroundingsReadError('freshness_limit'),
                                 SimpleNamespace(session='different',creature_id=base.creature_id,context=nearby)])
        with ExitStack() as stack:
            overrides={
                'bwv.watch.snapshot':Mock(return_value={}),
                'bwv.watch.from_observer':lambda *args:replace(base,observed_at=time.monotonic()),
                'bwv.training_live.TrainingReader':Mock(return_value=reader),
                'bwv.control_live.read_pause':Mock(return_value=SimpleNamespace(paused=False)),
                'bwv.control_live.with_pause':lambda obs,*args:obs,
                'bwv.control_live.read_camera_control':Mock(return_value=None),
                'bwv.control_live.with_camera_control':lambda obs,*args:obs,
                'bwv.control_live.read_temple':Mock(return_value=None),
                'bwv.control_live.with_temple':lambda obs,*args:obs,
                'bwv.creature_live.read_creature':Mock(return_value=SimpleNamespace(creature_id=base.creature_id,context=base.creature,script_owns_creature=False)),
                'bwv.surroundings_live.read_surroundings':samples,
            }
            for name,value in overrides.items():stack.enter_context(patch(name,value))
            stream=observations(123,control=True,detail_refresh=DetailRefresh(interval=0))
            try:
                first,_,_,_=await anext(stream)
                self.assertTrue(first.surroundings.available)
                failed,_,_,error=await anext(stream)
                self.assertTrue(failed.connected)
                self.assertEqual(failed.creature_id,base.creature_id)
                self.assertEqual(failed.surroundings,SurroundingsContext())
                self.assertEqual(error,'surroundings:ValueError')
                recovered,_,_,error=await anext(stream)
                self.assertIsNone(error)
                self.assertEqual(recovered.surroundings,nearby)
                limited,_,_,error=await anext(stream)
                self.assertTrue(limited.connected)
                self.assertEqual(limited.surroundings,SurroundingsContext())
                self.assertEqual(error,'surroundings:freshness_limit')
                changed,_,_,error=await anext(stream)
                self.assertFalse(changed.connected)
                self.assertIsNone(changed.creature_id)
                self.assertEqual(error,'surroundings:ValueError')
            finally:await stream.aclose()
        reader.close.assert_called_once()

    async def test_read_failure_and_pause_clear_baseline_without_hiding_existing_sensors(self):
        base=playable();phase=[0];reader=Mock()
        def read_experience(*args):
            if phase[0]==1:raise ValueError('Synthetic sensor failure')
            return ExperienceSample(base.session,base.creature_id,time.monotonic(),100+phase[0],
                                    BodyState(health=1-phase[0]*.1))
        def read_text(*args):
            if phase[0]==2:raise ValueError('Synthetic text failure')
            now=time.monotonic()
            return NativeTextSample(base.session if phase[0]!=4 else 'other',base.creature_id,now,
                                    NativeMessage('text','instinct' if phase[0]==0 else 'praise','Synthetic information',now))
        with ExitStack() as stack:
            overrides={
                'bwv.watch.snapshot':Mock(return_value={}),
                'bwv.watch.from_observer':lambda *args:replace(base,observed_at=time.monotonic()),
                'bwv.training_live.TrainingReader':Mock(return_value=reader),
                'bwv.control_live.read_pause':lambda *args:SimpleNamespace(paused=phase[0]==3),
                'bwv.control_live.with_pause':lambda obs,signal,now:replace(obs,paused=signal.paused),
                'bwv.control_live.read_camera_control':Mock(return_value=None),
                'bwv.control_live.with_camera_control':lambda obs,*args:obs,
                'bwv.control_live.read_temple':Mock(return_value=None),
                'bwv.control_live.with_temple':lambda obs,*args:obs,
                'bwv.creature_live.read_creature':Mock(return_value=SimpleNamespace(creature_id=base.creature_id,context=base.creature,script_owns_creature=False)),
                'bwv.surroundings_live.read_surroundings':Mock(return_value=SimpleNamespace(session=base.session,creature_id=base.creature_id,context=base.surroundings)),
                'bwv.experience_live.read_experience':read_experience,
                'bwv.native_text_live.read_native_text':read_text,
            }
            for name,value in overrides.items():stack.enter_context(patch(name,value))
            stream=observations(123,experience=True)
            try:
                for i in range(5):
                    phase[0]=i
                    obs,_,_,error=await anext(stream)
                    self.assertTrue(obs.connected)
                    self.assertEqual(error,'experience:ValueError' if i==1 else None)
                    self.assertEqual(obs.experience.health_changes,())
                    if i==1:self.assertEqual(obs.experience.validation,'read_failed_live_qa_pending')
                    elif i==3:self.assertIsNone(obs.experience.body.health)
                    else:self.assertAlmostEqual(obs.experience.body.health,1-i*.1)
                    if i==0:self.assertEqual(obs.experience.hover_thought_text,'Synthetic information')
                    else:self.assertIsNone(obs.experience.hover_thought_text)
                    self.assertEqual(bool(obs.experience.native_training_feedback),i==1)
            finally:await stream.aclose()
        reader.close.assert_called_once()
