import struct
import unittest
from copy import deepcopy
from dataclasses import replace

from bwv.action_probe import PositionActionAdapter, COMMAND, encode
from bwv.dispatch import DecisionDispatcher
from bwv.direct_tools import DirectCreatureToolSession
from bwv.positions import object_position, checked_position, parse_position_target
from bwv.llm_context import god_hand_context, creature_position_context
from test_pending_actions import Harness
from test_action_tools import action_request
from test_creature_tools import frame
from test_world_actions import WorldHarness


class PositionHarness(Harness):
    def __init__(self):
        super().__init__()
        self.probe['candidates'].append('move_to_position')
        self.probe['position_movement']={'version':11,'verified':True,'map_size':{'x':2560,'z':1280}}
        self.probe['movement'].update(creature_position={'x':100,'z':200},god_hand_position={'x':110,'z':205})

    def factory(self,obs,*,destination=None,**kwargs):
        if destination is not None:
            return DecisionDispatcher(PositionActionAdapter(123,destination=destination,
                expected_identity=(obs.session,obs.creature_id),read=lambda pid:deepcopy(self.native),
                send=self.send),clock=lambda:self.now)
        return super().factory(obs,**kwargs)


class PositionTests(unittest.TestCase):
    def test_coordinate_tool_selects_one_bound_command_without_acting_during_lookup(self):
        h=PositionHarness();offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        tools=DirectCreatureToolSession(frame,action_candidates=data['candidates']);tools.bind_request(request)
        definitions={d['name']:d for d in tools.definitions()}
        self.assertIn('move_to',definitions)
        self.assertNotIn('move_to_position',definitions)
        self.assertTrue(any('x' in d.get('properties',{}) for d in definitions['move_to']['parameters']['properties']['target']['anyOf']))
        result=tools.call('move_to',{'destination':{'x':350.25,'z':280}})
        self.assertFalse(result['executed']);self.assertFalse(h.sent)
        self.assertEqual(tools.call('move_to',{'destination':{'x':450,'z':280}})['status'],'action_already_selected')
        offer.dispatch(tools.finalize_reply({})['decision'])
        h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        packet=h.sent[0];self.assertEqual(len(packet),48)
        self.assertEqual(COMMAND.unpack(packet[:40])[1],11)
        self.assertEqual(COMMAND.unpack(packet[:40])[-2:],(20,0))
        self.assertEqual(struct.unpack('<ff',packet[40:]),(350.25,280))

    def test_coordinate_request_is_cancelled_on_save_or_creature_change(self):
        for replacement in ({'session':'123:4'},{'creature_id':'another-creature'}):
            h=PositionHarness();offer=h.pending.factory(h.obs());offer.offer()
            offer.dispatch({'candidate':'move_to_position','target':'position:350.00:280.00'})
            h.pending.advance(replace(h.obs(),**replacement),allowed=True)
            self.assertFalse(h.sent);self.assertIsNone(h.pending.pending)

    def test_invalid_and_outside_coordinates_never_create_native_packets(self):
        h=PositionHarness()
        for point in ({'x':float('nan'),'z':1},{'x':float('inf'),'z':1},
                      {'x':True,'z':2},{'x':0,'z':1},{'x':-5,'z':1},
                      {'x':2560,'z':1},{'x':10,'z':1280},{'x':1,'z':2,'y':3}):
            with self.subTest(point=point),self.assertRaises(ValueError):
                encode(h.native,{'candidate':'move_to_position'},destination=point)
        for target in ('position:nan:12','position:1:2','tree-a','position:1.00:2.00:extra'):
            with self.assertRaises(ValueError):parse_position_target(target)

    def test_older_bridge_does_not_offer_coordinate_tool(self):
        h=Harness();offer=h.pending.factory(h.obs());data=offer.offer()
        self.assertNotIn('move_to_position',data['candidates'])
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        tools=DirectCreatureToolSession(frame,action_candidates=data['candidates']);tools.bind_request(request)
        self.assertNotIn('move_to_position',{d['name'] for d in tools.definitions()})

    def test_creature_and_hand_coordinates_are_fresh_and_identity_bound(self):
        h=PositionHarness();source=h.source()
        self.assertEqual(creature_position_context(source,h.now),{'x':100,'z':200})
        self.assertEqual(god_hand_context(source,h.now)['position'],{'x':110,'z':205})
        self.assertIsNone(creature_position_context(source,h.now+2))
        source['observation']['creature_id']='other'
        self.assertIsNone(god_hand_context(source,h.now))

    def test_only_nearby_buildings_and_creatures_expose_positions(self):
        for kind in ('tree','mushroom','animal','toy','food_pile','fence','debris','rock'):
            self.assertEqual(object_position({'kind':kind,'distance_engine_units':1,'position':{'x':100,'z':200}}),{})
        for kind in ('house','storage','temple','creature','nursery'):
            entry={'kind':kind,'distance_engine_units':360,'position':{'x':100,'z':200}}
            self.assertIn('position',object_position(entry))
            entry['distance_engine_units']=361
            self.assertEqual(object_position(entry),{})
            entry['distance_to_god_hand_engine_units']=10
            self.assertIn('position',object_position(entry))

    def test_inspection_target_positions_follow_same_allowlist_as_observed_objects(self):
        for kind,distance,visible in (('house',100,True),('creature',360,True),('house',500,False),('tree',10,False)):
            h=WorldHarness()
            entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
            entry.update(kind=kind,distance_engine_units=distance,position={'x':100,'z':200})
            offer=h.pending.factory(h.obs());data=offer.offer()
            target=next(t for t in data['action_targets']['move_to']['objects'] if t['kind']==kind)
            self.assertEqual('position' in target,visible)

    def test_coordinate_completion_requires_arrival_not_just_plan_end(self):
        from bwv.action_feedback import action_result
        probe={'result_current_creature':True,'requested_action':'move_to_position',
            'request_at_uptime_ms':100,'result_phase':'finished','outcome':'native_plan_changed_after_action',
            'plan_observed':True,'script_owned':False,'completion':'unconfirmed'}
        self.assertEqual(action_result(probe,'1:1')['status'],'plan_ended')
        probe['completion']='reached_position_target'
        self.assertEqual(action_result(probe,'1:1')['status'],'completed')
