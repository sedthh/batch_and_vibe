"""Synthetic home targets: no game reads, writes or provider requests."""
from copy import deepcopy
import unittest

from bwv.action_probe import COMMAND
from bwv.direct_tools import DirectCreatureToolSession
from bwv.temple_targets import with_player_temple
from bwv.world_actions import world_offers_available
from test_action_tools import action_request
from test_creature_tools import frame
from test_world_actions import WorldHarness


class TempleTargetTests(unittest.TestCase):
    def home(self):
        h=WorldHarness()
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
        entry.update(kind='temple',distance_engine_units=1500,player_temple=True)
        return h,entry

    def select(self,h):
        offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        tools=DirectCreatureToolSession(frame,action_candidates=data['candidates'])
        tools.bind_request(request)
        result=tools.call('move_to',{'target':'player_temple'})
        return offer,tools,result

    def test_distant_home_is_in_prompt_and_alias_dispatches_without_search(self):
        h,entry=self.home()
        self.assertIn('"player_temple"',with_player_temple('base',h.probe))
        offer,tools,_=self.select(h)
        offer.dispatch(tools.finalize_reply({})['decision'])
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1],entry['token'])

    def test_other_temple_does_not_supply_alias_or_prompt(self):
        h,entry=self.home();entry.pop('player_temple')
        self.assertEqual(with_player_temple('base',h.probe),'base')
        _,_,result=self.select(h)
        self.assertEqual(result['status'],'invalid_target')
        self.assertFalse(h.sent)

    def test_ownership_loss_cannot_retry_against_another_temple(self):
        h,entry=self.home();offer,tools,_=self.select(h)
        offer.dispatch(tools.finalize_reply({})['decision'])
        entry.pop('player_temple')
        h.probe['world_actions']['offers'].append({**entry,'token':500})
        h.pending.advance(h.obs(),allowed=True)
        self.assertFalse(h.sent)
        self.assertEqual(h.pending.snapshot()['status'],'cancelled_world_target_or_learning_changed')

    def test_alias_is_resolved_again_for_new_request(self):
        h,entry=self.home();offer,tools,_=self.select(h)
        old=tools.finalize_reply({})['decision']['target']
        entry['token']=900
        new_offer,new_tools,_=self.select(h)
        self.assertNotEqual(new_tools.finalize_reply({})['decision']['target'],old)
        new_offer.dispatch(new_tools.finalize_reply({})['decision'])
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1],900)

    def test_malformed_or_ambiguous_ownership_is_not_used(self):
        for case in ('wrong_kind','string_flag','duplicate'):
            with self.subTest(case=case):
                h,entry=self.home()
                if case=='wrong_kind':entry['kind']='tree'
                elif case=='string_flag':entry['player_temple']='true'
                else:h.probe['world_actions']['offers'].append({**deepcopy(entry),'token':900})
                self.assertFalse(world_offers_available(h.probe))
                self.assertEqual(with_player_temple('base',h.probe),'base')
