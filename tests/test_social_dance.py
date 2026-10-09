"""A speech cue may start one social dance, never two physical actions."""
import unittest
from unittest.mock import patch

from bwv.action_probe import COMMAND
from bwv.direct_tools import DirectCreatureToolSession
from bwv.expressions import speech_expression
from test_action_tools import action_request
from test_creature_tools import frame
from test_world_actions import WorldHarness


class SocialDanceTests(unittest.TestCase):
    def setup_dance(self):
        h=WorldHarness()
        h.probe['self_action_catalog']={'version':5,'verified':True}
        h.probe['candidates'].append('dance')
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='dance_with_villagers')
        offer=h.pending.factory(h.obs());data=offer.offer()
        return h,entry,offer,data

    def test_one_of_three_choices_selects_group_and_others_stay_solo(self):
        for draw in range(3):
            h,entry,offer,_=self.setup_dance()
            with patch('bwv.pending_actions.random.randrange',return_value=draw) as rng:
                result=speech_expression('Let us dance! 💃',{'candidate':None},dispatcher=offer)
                self.assertEqual(result['status'],'waiting_for_native')
                h.pending.advance(h.obs(),allowed=True)
                h.pending.advance(h.obs(),allowed=True)
                speech_expression('💃',{'candidate':None},dispatcher=offer)
                rng.assert_called_once_with(3)
            self.assertEqual(len(h.sent),1)
            packet=COMMAND.unpack(h.sent[0])
            self.assertEqual((packet[1],packet[-2],packet[-1]),
                             (10,19,entry['token']) if draw==0 else (5,11,0))

    def test_missing_distant_or_wrong_kind_target_falls_back_without_lookup(self):
        for change in ('missing','distant','kind'):
            h,entry,offer,_=self.setup_dance()
            with patch('bwv.pending_actions.random.randrange',return_value=0):
                speech_expression('💃',{'candidate':None},dispatcher=offer)
            if change=='missing':h.probe['world_actions']['offers'].remove(entry)
            if change=='distant':entry['distance_engine_units']=121
            if change=='kind':entry['kind']='tree'
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1)
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(11,0))

    def test_target_lost_after_queue_rejection_falls_back_once(self):
        h,entry,offer,_=self.setup_dance();attempts=[]
        def reject_first(pid,packet):
            attempts.append(packet)
            if len(attempts)==1:return False
            h.sent.append(packet);return True
        h.send=reject_first
        with patch('bwv.pending_actions.random.randrange',return_value=0) as rng:
            speech_expression('💃',{'candidate':None},dispatcher=offer)
            deadline=h.pending.pending['expires']
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(h.pending.last['dispatch_check']['stage'],'before_native_queue')
            h.probe['world_actions']['offers'].remove(entry)
            self.assertEqual(h.pending.pending['expires'],deadline)
            h.pending.advance(h.obs(),allowed=True)
            h.pending.advance(h.obs(),allowed=True)
            rng.assert_called_once_with(3)
        self.assertEqual(len(attempts),2);self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(11,0))

    def test_unknown_receipt_never_falls_back_or_replays(self):
        h,entry,offer,_=self.setup_dance()
        def lost(pid,packet):h.sent.append(packet);raise TimeoutError()
        h.send=lost
        with patch('bwv.pending_actions.random.randrange',return_value=0):
            speech_expression('💃',{'candidate':None},dispatcher=offer)
        h.pending.advance(h.obs(),allowed=True)
        h.probe['world_actions']['offers'].remove(entry)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(h.pending.last['status'],'not_confirmed_no_retry')

    def test_action_conflict_and_menu_do_not_start_a_dance(self):
        h,_,offer,_=self.setup_dance()
        with patch('bwv.pending_actions.random.randrange') as rng:
            self.assertEqual(speech_expression('💃',{'candidate':'attack'},dispatcher=offer)['status'],
                             'skipped_action_conflict')
            rng.assert_not_called()
        with patch('bwv.pending_actions.random.randrange',return_value=0):
            speech_expression('💃',{'candidate':None},dispatcher=offer)
        h.pending.advance(h.obs(),allowed=False)
        self.assertFalse(h.sent)

    def test_variant_is_not_an_extra_model_tool_action(self):
        _,_,_,data=self.setup_dance()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        session=DirectCreatureToolSession(frame,action_candidates=data['candidates'])
        session.bind_request(request)
        self.assertNotIn('dance',session.actions)
        self.assertNotIn('dance_with_villagers',session.actions)
        self.assertNotIn('dance_with_villagers',session.menu)
