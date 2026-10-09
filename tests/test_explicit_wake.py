import unittest
from copy import deepcopy

from bwv.action_probe import COMMAND, QuestionWakeAdapter
from bwv.dispatch import DecisionDispatcher
from bwv.creature_tools import CreatureToolSession
from test_pending_actions import Harness
from test_action_tools import action_request
from test_creature_tools import frame


class WakeHarness(Harness):
    def __init__(self):
        super().__init__()
        self.probe.update(wake_protocol=3,candidates=['wake_up'],activity={
            'verified':True,'sleep_in_sequence':True,'controller_mode':3,
            'animation':29,'current_subaction':132})

    def factory(self,obs,*,wake_action=False,**kwargs):
        if not wake_action:return super().factory(obs,**kwargs)
        return DecisionDispatcher(QuestionWakeAdapter(123,expected_identity=self.identity,
            read=lambda pid:deepcopy(self.native),send=self.send),clock=lambda:self.now)


class ExplicitWakeTests(unittest.TestCase):
    def test_search_tool_dispatches_exactly_one_native_wake(self):
        h=WakeHarness();offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        tools=CreatureToolSession(frame,action_candidates=data['candidates']);tools.bind_request(request)
        self.assertIn('wake_up',[a['action'] for a in tools.call('search_actions',{'query':'wake'})['actions']])
        tools.call('do_action',{'action':'wake_up','target':'self'})
        decision=tools.finalize_reply({'answer':'','decision':{'candidate':None}})['decision']
        offer.dispatch(decision)
        h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual((COMMAND.unpack(h.sent[0])[1],COMMAND.unpack(h.sent[0])[-2]),(3,4))

    def test_waking_naturally_before_dispatch_is_a_no_op(self):
        h=WakeHarness();h.enqueue('wake_up')
        h.probe['activity'].update(sleep_in_sequence=False,controller_mode=0,animation=0,current_subaction=0)
        h.probe['candidates']=[]
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.snapshot()['status'],'already_awake')
        self.assertFalse(h.sent)

    def test_menu_cancels_even_when_already_awake(self):
        h=WakeHarness();h.enqueue('wake_up');h.pending.advance(h.obs(),allowed=False)
        self.assertEqual(h.pending.snapshot()['status'],'cancelled_control_changed')
        self.assertFalse(h.sent)

    def test_old_bridge_does_not_offer_wake(self):
        h=WakeHarness();del h.probe['wake_protocol']
        self.assertNotIn('wake_up',h.pending.factory(h.obs()).offer()['candidates'])
