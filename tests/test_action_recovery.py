"""Multi-step action execution without duplicate effects or stale targets."""
import unittest
from copy import deepcopy
from bwv.action_probe import COMMAND, HeldActionAdapter, pickup_targets_available
from bwv.dispatch import DecisionDispatcher
from bwv.world_actions import world_offers_available
from test_world_actions import WorldHarness


class RecoveryHarness(WorldHarness):
    def __init__(self):
        super().__init__()
        self.probe.update(hand_preparation_protocol=1, request_at_uptime_ms=40)

    def factory(self,obs,*,expected_held_generation=None,**kwargs):
        if expected_held_generation is None:return super().factory(obs,**kwargs)
        return DecisionDispatcher(HeldActionAdapter(123,expected_identity=self.identity,
            expected_held_generation=expected_held_generation,
            read=lambda pid:deepcopy(self.native),send=self.send),clock=lambda:self.now)

    def hold(self):
        self.probe['held_actions'].update(kind='tree',target_generation=7)
        self.probe['pickup_action']['hand_empty']=False
        self.probe['candidates']=['put_down']

    def dropped(self,**changes):
        self.probe['held_actions']['target_generation']=0
        self.probe['pickup_action']['hand_empty']=True
        self.probe.update(request_at_uptime_ms=41,requested_action='put_down',
                          result_current_creature=True,result_phase='finished',**changes)


class RecoveryTests(unittest.TestCase):
    def test_rejected_put_down_reoffers_then_continues_original_command_once(self):
        h=RecoveryHarness();h.hold();calls=[]
        def race(pid,packet):
            calls.append(packet)
            if len(calls)==1:
                h.probe['generation']+=1
                return False
            h.sent.append(packet);return True
        h.send=race;h.select('fish');deadline=h.pending.pending['expires']
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['waiting_reason'],'put_down_retry')
        self.assertFalse(h.sent)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.pending['drop_attempts'],1)
        self.assertEqual(h.pending.pending['expires'],deadline)
        for _ in range(3):h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(calls),2)
        h.dropped();h.probe['candidates']=['fish']
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual([COMMAND.unpack(p)[-2] for p in h.sent],[12,19])
        self.assertEqual(len(calls),3)

    def test_fishing_uses_self_and_is_never_advertised_as_a_miracle(self):
        h=RecoveryHarness();data=h.pending.factory(h.obs()).offer()
        self.assertIn('fish',data['candidates'])
        self.assertEqual(data['action_targets']['fish']['objects'][0]['kind'],'self')
        h.select('fish');h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2],19)

    def test_drop_then_world_action_waits_for_fresh_empty_hand(self):
        h=RecoveryHarness();h.hold()
        self.assertFalse(world_offers_available(h.probe))
        self.assertTrue(world_offers_available(h.probe,planning=True))
        h.select('cast_water_object');deadline=h.pending.pending['expires']
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(12,7))
        for _ in range(3):h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        h.dropped();h.probe['candidates']=['cast_water_object']
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),2)
        self.assertEqual(COMMAND.unpack(h.sent[1])[-2],19)
        self.assertEqual(h.pending.inflight[0]['expires'],deadline)
        self.assertEqual(h.pending.last['hand_preparation'],'empty_hand_observed')

    def test_pickup_is_plannable_while_holding_but_not_directly_executable(self):
        h=RecoveryHarness();h.hold()
        self.assertFalse(pickup_targets_available(h.probe))
        self.assertTrue(pickup_targets_available(h.probe,planning=True))
        h.enqueue_pickup();h.pending.advance(h.obs(),allowed=True)
        h.dropped();h.probe['candidates']=['pick_up']
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual([COMMAND.unpack(p)[-2] for p in h.sent],[12,16])

    def test_chat_wake_receipt_is_consumed_before_put_down_receipt(self):
        h=RecoveryHarness();h.hold()
        offer=h.pending.factory(h.obs(),wake_result={'queued':True,'wake_after_request':39})
        data=offer.offer();target=data['action_targets']['fish']['objects'][0]['target_id']
        offer.dispatch({'candidate':'fish','target':target})
        h.probe.update(requested_action='wake_up',result_phase='finished',
            result_current_creature=True,completion='awake_observed',
            outcome='native_wake_observed',script_owned=False)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2],12)
        h.dropped();h.probe['candidates']=['fish']
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),2)

    def test_reacquired_object_has_bounded_put_downs(self):
        h=RecoveryHarness();h.hold();h.select('fish')
        h.pending.advance(h.obs(),allowed=True)
        # One observed drop, but final action is briefly unavailable.
        h.dropped();h.probe['candidates']=[]
        h.pending.advance(h.obs(),allowed=True)
        h.hold();h.probe['held_actions']['target_generation']=8
        h.pending.advance(h.obs(),allowed=True)
        h.dropped();h.probe['request_at_uptime_ms']=42;h.probe['candidates']=[]
        h.pending.advance(h.obs(),allowed=True)
        h.hold();h.probe['held_actions']['target_generation']=9
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),2)
        self.assertEqual(h.pending.last['status'],'cancelled_hand_preparation_limit')

    def test_uncertain_drop_or_not_empty_never_proceeds(self):
        for failure in ('lost','still_held','menu'):
            h=RecoveryHarness();h.hold();h.select('fish')
            if failure=='lost':
                def lost(pid,packet):h.sent.append(packet);raise TimeoutError()
                h.send=lost
            h.pending.advance(h.obs(),allowed=True)
            if failure=='still_held':
                h.probe.update(request_at_uptime_ms=41,requested_action='put_down',
                               result_current_creature=True,result_phase='finished')
            h.pending.advance(h.obs(),allowed=failure!='menu')
            h.dropped();h.probe['candidates']=['fish']
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1,failure)
            self.assertIsNone(h.pending.pending)

    def test_retarget_same_kind_with_same_action_and_bounded_attempts(self):
        h=RecoveryHarness();h.select('attack')
        original=h.probe['world_actions']['offers'][0]
        h.probe['world_actions']['offers'].remove(original)
        h.probe['world_actions']['offers'].extend([
            {**original,'token':700,'kind':'villager','distance_engine_units':1},
            {**original,'token':701,'distance_engine_units':15}])
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[-1])[-1],701)
        self.assertEqual(h.pending.last['retarget_count'],1)
        # Confirm rejection BEFORE execution, then retry once at another tree.
        h.probe.update(requested_action='attack',request_at_uptime_ms=41,
                       result_current_creature=True,result_phase='rejected',
                       outcome='world_target_changed_before_dispatch')
        h.probe['world_actions']['offers'].append({**original,'token':702})
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[-1])[-1],702)
        h.probe.update(request_at_uptime_ms=42)
        h.probe['world_actions']['offers'].append({**original,'token':703})
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),2)
        self.assertIsNone(h.pending.pending)

    def test_success_unknown_effect_or_lost_receipt_never_replays(self):
        for failure in ('finished','timeout','unknown_rejection'):
            h=RecoveryHarness();h.select('cast_food')
            if failure=='timeout':
                def lost(pid,packet):h.sent.append(packet);raise TimeoutError()
                h.send=lost
            h.pending.advance(h.obs(),allowed=True)
            h.probe.update(requested_action='cast_food',request_at_uptime_ms=41,
                result_current_creature=True,result_phase='finished' if failure=='finished' else 'rejected',
                outcome='native_plan_changed_after_action' if failure=='finished' else 'body_animation_sequence_mismatch')
            for _ in range(5):h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1,failure)

    def test_new_command_menu_expiry_or_identity_change_cancels_retry(self):
        for change in ('new','menu','expiry','identity'):
            h=RecoveryHarness();h.select('attack');h.pending.advance(h.obs(),allowed=True)
            h.probe.update(requested_action='attack',request_at_uptime_ms=41,
                result_current_creature=True,result_phase='rejected',outcome='world_target_changed_before_dispatch')
            if change=='new':h.pending.cancel('superseded_by_question')
            if change=='expiry':h.now+=31
            if change=='identity':h.identity=('123:4','different')
            h.pending.advance(h.obs(),allowed=change!='menu')
            self.assertEqual(len(h.sent),1,change)

    def test_transport_precheck_retries_stop_after_three(self):
        h=RecoveryHarness();h.select('fish')
        factory=h.pending.native_factory
        def unavailable(obs,**kwargs):
            d=factory(obs,**kwargs)
            d.adapter.still_valid=lambda *args:False
            return d
        h.pending.native_factory=unavailable
        for _ in range(4):h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'cancelled_retry_limit')
        self.assertFalse(h.sent)
