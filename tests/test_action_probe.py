import struct
import unittest
from unittest.mock import patch
from bwv.action_probe import dummy_decision, encode, run, COMMAND


def offered():
    return {'session':3,'source_age_ms':20,'action_probe':{
        'experimental_manual_only':True,'supported':True,'transport_ready':True,
        'phase':'ready','candidate':'look_confused','engine_calls':0,
        'epoch':3,'generation':4,'offered_at_uptime_ms':100}}


class ActionProbeTests(unittest.TestCase):
    def test_night_window_requires_player_origin_and_new_bridge_capability(self):
        from bwv.action_probe import NativeActionAdapter, PLAYER_COMMAND_FLAG, NIGHT_PRIORITY_FLAG
        state=offered();state.update(pid=123,captured_uptime_ms=110)
        contract=state['action_probe']['player_command_priority']={'version':1,'available':True}
        night=NativeActionAdapter(123,read=lambda pid:state,player_command=True,night_priority=True)
        self.assertEqual(COMMAND.unpack(night.capture(0).packets[0][1])[1],1|PLAYER_COMMAND_FLAG)
        contract['night_priority_version']=1
        self.assertEqual(COMMAND.unpack(night.capture(0).packets[0][1])[1],1|PLAYER_COMMAND_FLAG|NIGHT_PRIORITY_FLAG)
        day=NativeActionAdapter(123,read=lambda pid:state,player_command=True)
        self.assertEqual(COMMAND.unpack(day.capture(0).packets[0][1])[1],1|PLAYER_COMMAND_FLAG)
        automatic=NativeActionAdapter(123,read=lambda pid:state,night_priority=True)
        self.assertEqual(COMMAND.unpack(automatic.capture(0).packets[0][1])[1],1)

    def test_player_priority_is_host_opt_in_and_requires_advertised_bridge(self):
        from bwv.action_probe import NativeActionAdapter, PLAYER_COMMAND_FLAG
        from bwv.dispatch import DecisionDispatcher
        state=offered();state.update(pid=123,captured_uptime_ms=110)
        adapter=NativeActionAdapter(123,read=lambda pid:state)
        self.assertEqual(COMMAND.unpack(adapter.capture(0).packets[0][1])[1],1)
        dispatcher=DecisionDispatcher(adapter,clock=lambda:0)
        dispatcher.mark_player_command()
        self.assertEqual(COMMAND.unpack(adapter.capture(0).packets[0][1])[1],1) # old DLL
        state['action_probe']['player_command_priority']={'version':1,'available':True}
        dispatcher.offer()
        self.assertEqual(COMMAND.unpack(dispatcher._offer.packets[0][1])[1],1|PLAYER_COMMAND_FLAG)
        with self.assertRaises(ValueError):dispatcher.mark_player_command()
        automatic=NativeActionAdapter(123,read=lambda pid:state)
        self.assertEqual(COMMAND.unpack(automatic.capture(0).packets[0][1])[1],1)

    def test_priority_requires_native_contract_and_does_not_mutate_snapshot(self):
        from bwv.action_probe import NativeActionAdapter, priority_state, PRIORITY_FLAG
        state=offered();state.update(pid=123,captured_uptime_ms=110)
        probe=state['action_probe'];probe.update(phase='finished',engine_calls=5,
            conditions_allow_action=True,candidates=[],
            action_lifecycle={'version':1,'repeated_actions_enabled':True,
                'in_flight':True,'cooldown_remaining_ms':9000},
            priority_commands={'version':1,'available':True,'candidates':['look_confused']})
        self.assertIsNone(dummy_decision(state)['candidate'])
        adapter=NativeActionAdapter(123,interrupt=True,read=lambda pid:state)
        offer=adapter.capture(0)
        self.assertEqual(COMMAND.unpack(offer.packets[0][1])[1],PRIORITY_FLAG|1)
        self.assertTrue(adapter.still_valid(offer,'look_confused'))
        self.assertEqual(probe['phase'],'finished')
        probe['conditions_allow_action']=False
        self.assertFalse(adapter.still_valid(offer,'look_confused'))
        self.assertIsNone(dummy_decision(priority_state(state))['candidate'])
        probe.pop('priority_commands')
        with self.assertRaises(ValueError):adapter.capture(0)

    def test_object_approach_is_manual_and_binds_native_target_token(self):
        from bwv.action_probe import NativeActionAdapter, ConversationActionAdapter
        from copy import deepcopy
        state=offered();state.update(pid=123,captured_uptime_ms=110)
        probe=state['action_probe'];probe['candidates']=['approach_object']
        self.assertIsNone(dummy_decision(state,'approach_object')['candidate'])
        target={'protocol':4,'manual_only':True,'kind':'tree','target_generation':7,'distance_engine_units':14}
        probe['object_movement']=target
        packet=encode(state,{'candidate':'approach_object'})
        self.assertEqual(COMMAND.unpack(packet),(0x42575041,4,3,4,100,5,7))
        self.assertNotIn('approach_object',ConversationActionAdapter.actions)
        adapter=NativeActionAdapter(123,read=lambda pid:deepcopy(state))
        offer=adapter.capture(0)
        self.assertTrue(adapter.still_valid(offer,'approach_object'))
        probe['object_movement']={**target,'target_generation':8}
        self.assertFalse(adapter.still_valid(offer,'approach_object'))
        for key,value in (('protocol',True),('protocol',2),('manual_only',False),('kind','villager'),
                          ('target_generation',0),('target_generation',True),('target_generation',2**32),
                          ('distance_engine_units',7),('distance_engine_units',31),('distance_engine_units',float('nan'))):
            with self.subTest(key=key,value=value):
                probe['object_movement']={**target,key:value}
                self.assertIsNone(dummy_decision(state,'approach_object')['candidate'])
    def test_repeat_build_requires_idle_lifecycle_and_completed_cooldown(self):
        state=offered();probe=state['action_probe'];probe['engine_calls']=4
        self.assertIsNone(dummy_decision(state)['candidate'])
        lifecycle={'version':1,'repeated_actions_enabled':True,'in_flight':False,
                   'cooldown_remaining_ms':0}
        probe['action_lifecycle']=lifecycle
        self.assertEqual(dummy_decision(state)['candidate'],'look_confused')
        for key,value in (('version',2),('version',True),('repeated_actions_enabled',False),
                          ('in_flight',True),('in_flight',None),('cooldown_remaining_ms',1),
                          ('cooldown_remaining_ms',False)):
            with self.subTest(key=key,value=value):
                probe['action_lifecycle']={**lifecycle,key:value}
                self.assertIsNone(dummy_decision(state)['candidate'])
        probe['action_lifecycle']=lifecycle
        for phase in ('queued','accepted','native_plan_observed','finished','rejected'):
            probe['phase']=phase
            self.assertIsNone(dummy_decision(state)['candidate'])

    def test_restarted_conversation_rejects_old_executor_before_pipe_access(self):
        from bwv.action_probe import ActionProcessChanged, ConversationActionAdapter, QuestionWakeAdapter
        reads = []
        for adapter in (ConversationActionAdapter, QuestionWakeAdapter):
            with self.assertRaises(ActionProcessChanged):
                adapter(123, expected_identity=('456:3', 'synthetic-creature'),
                        read=lambda pid: reads.append(pid))
        self.assertEqual(reads, [])

    def test_question_wake_uses_current_identity_and_short_protocol(self):
        from copy import deepcopy
        import hashlib
        from bwv.action_probe import QuestionWakeAdapter
        from test_dispatch import frame
        state=frame()
        state['action_probe'].update(candidates=['wake_up'],wake_protocol=3,allowed_action_mask=8,
            conversation_actions={'creature_handle':42,'mental_handle':43})
        identity=('123:3',hashlib.sha256(b'123:3:42:43').hexdigest()[:24])
        adapter=QuestionWakeAdapter(123,expected_identity=identity,read=lambda pid:deepcopy(state))
        offer=adapter.capture(0)
        self.assertLessEqual(offer.expires_at,1)
        self.assertEqual(offer.candidates,('wake_up',))
        self.assertEqual(COMMAND.unpack(offer.packets[0][1])[1],3)
        state['action_probe']['conversation_actions']['creature_handle']=44
        self.assertFalse(adapter.still_valid(offer,'wake_up'))

    def test_wake_is_separate_short_lived_manual_protocol(self):
        from bwv.action_probe import NativeActionAdapter, ConversationActionAdapter
        state=offered()
        state['action_probe']['candidates']=['wake_up']
        self.assertIsNone(dummy_decision(state,'wake_up')['candidate'])
        state['action_probe']['wake_protocol']=3
        packet=encode(state,dummy_decision(state,'wake_up'))
        self.assertEqual(COMMAND.unpack(packet),(0x42575041,3,3,4,100,4,0))
        self.assertNotIn('wake_up',ConversationActionAdapter.actions)
        state['action_probe'].update(allowed_action_mask=4,candidates=['approach_hand'],
            movement={'verified':True,'distance_to_hand_engine_units':12})
        self.assertFalse(NativeActionAdapter(123)._available(state,'approach_hand'))

    def test_approach_hand_requires_verified_target_without_nearby_limit(self):
        state = offered()
        state['action_probe']['candidates'] = ['approach_hand']
        self.assertEqual(dummy_decision(state, 'approach_hand'), {'candidate': None})
        state['action_probe']['movement'] = {'verified': True, 'distance_to_hand_engine_units': 12}
        decision = dummy_decision(state, 'approach_hand')
        self.assertEqual(COMMAND.unpack(encode(state, decision))[-2:], (3, 0))
        for distance in (.5, 7.99, 31, 800, 12000):
            state['action_probe']['movement']['distance_to_hand_engine_units'] = distance
            self.assertEqual(dummy_decision(state, 'approach_hand'), {'candidate': 'approach_hand'})
        for distance in (None, True, 0, -1, float('nan'), float('inf')):
            state['action_probe']['movement']['distance_to_hand_engine_units'] = distance
            self.assertEqual(dummy_decision(state, 'approach_hand'), {'candidate': None})
    def test_sleep_requires_explicit_choice_and_a_live_offer(self):
        state=offered()
        self.assertEqual(dummy_decision(state,'sleep_here'),{'candidate':None})
        with self.assertRaises(ValueError):encode(state,{'candidate':'sleep_here'})
        state['action_probe']['candidates']=['look_confused','sleep_here']
        self.assertEqual(dummy_decision(state),{'candidate':'look_confused'})
        decision=dummy_decision(state,'sleep_here')
        self.assertEqual(COMMAND.unpack(encode(state,decision))[-2:],(2,0))
        with patch('bwv.action_probe.snapshot',return_value=state), patch('bwv.action_probe.submit') as submit:
            result=run(123,action='sleep_here')
            self.assertEqual(result['dummy_decision'],{'candidate':'sleep_here'})
            submit.assert_not_called()

    def test_fixed_dummy_choice_and_exact_wire_layout(self):
        state=offered(); decision=dummy_decision(state)
        self.assertEqual(decision,{'candidate':'look_confused'})
        packet=encode(state,decision)
        self.assertEqual(len(packet),40)
        self.assertEqual(COMMAND.unpack(packet),(0x42575041,1,3,4,100,1,0))
        with self.assertRaises(ValueError):encode(state,{'candidate':'pick_up'})

    def test_missing_stale_consumed_blocked_or_unsupported_offer_sends_nothing(self):
        for key,value in (('supported',False),('transport_ready',False),('phase','queued'),
                          ('candidate','invented'),('engine_calls',1),('experimental_manual_only',False)):
            state=offered();state['action_probe'][key]=value
            self.assertEqual(dummy_decision(state),{'candidate':None})
        state=offered();state['source_age_ms']=1001
        self.assertEqual(dummy_decision(state),{'candidate':None})
        state['source_age_ms']=float('nan')
        self.assertEqual(dummy_decision(state),{'candidate':None})
        self.assertEqual(dummy_decision({}),{'candidate':None})

    def test_invalid_or_different_session_capability_rejected(self):
        for key,value in (('epoch',4),('generation',True),('offered_at_uptime_ms',-1),('generation',2**64)):
            state=offered();state['action_probe'][key]=value
            with self.assertRaises(ValueError):encode(state,{'candidate':'look_confused'})

    def test_preview_never_submits_and_no_offer_never_waits_for_future_eligibility(self):
        with patch('bwv.action_probe.snapshot',return_value=offered()), patch('bwv.action_probe.submit') as submit:
            result=run(123)
            self.assertFalse(result['submitted']);submit.assert_not_called()
        state=offered();state['action_probe']['phase']='unavailable'
        with patch('bwv.action_probe.snapshot',return_value=state), patch('bwv.action_probe.submit') as submit:
            with self.assertRaises(ValueError):run(123,execute=True)
            submit.assert_not_called()
