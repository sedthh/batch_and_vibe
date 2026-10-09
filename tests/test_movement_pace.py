import struct
import unittest
from unittest.mock import patch
from copy import deepcopy

from bwv.action_probe import ConversationActionAdapter, PositionActionAdapter, COMMAND
from bwv.direct_tools import DirectCreatureToolSession
from bwv.creature_tools import ToolArgumentError
from bwv.dispatch import DecisionDispatcher
from bwv.movement_pace import paced_packet
from test_pending_actions import Harness
from test_positions import PositionHarness
from test_action_tools import action_request
from test_creature_tools import frame
from test_world_actions import WorldHarness


class PaceTests(unittest.TestCase):
    def tools(self, h):
        offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        tools=DirectCreatureToolSession(frame,action_candidates=data['candidates']);tools.bind_request(request)
        return tools,offer

    def test_hand_pace_survives_tool_final_reply_queue_and_packet(self):
        for pace,flag in [('walk',0x10000000),('run',0x20000000),('auto',0)]:
            h=Harness();h.probe['movement_pace']={'version':1}
            def factory(obs,**kwargs):
                return DecisionDispatcher(ConversationActionAdapter(123,expected_identity=h.identity,
                    read=lambda pid:deepcopy(h.native),send=h.send,**kwargs),clock=lambda:h.now)
            h.pending.native_factory=factory
            tools,offer=self.tools(h)
            tools.call('move_to',{'destination':{'special':'god_hand'},'pace':pace})
            decision=tools.finalize_reply({})['decision'];self.assertEqual(decision['pace'],pace)
            offer.dispatch(decision);h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1)
            packet=COMMAND.unpack(h.sent[0]);self.assertEqual(packet[1],2|flag)
            self.assertEqual(packet[-2:],(3,7))

    def test_coordinate_payload_and_priority_survive_pace_flag(self):
        h=PositionHarness();h.probe['movement_pace']={'version':1}
        h.probe.update(conditions_allow_action=True,
            priority_commands={'version':1,'available':True,'candidates':h.probe['candidates']})
        adapter=PositionActionAdapter(123,expected_identity=h.identity,destination={'x':350,'z':280},
            read=lambda pid:deepcopy(h.native),pace='run',interrupt=True)
        packet=dict(adapter.capture(0).packets)['move_to_position']
        self.assertEqual(len(packet),48)
        self.assertEqual(COMMAND.unpack(packet[:40])[1],0xa000000b)
        self.assertEqual(struct.unpack('<ff',packet[40:]),(350,280))
        tools,_=self.tools(h);tools.call('move_to',{'destination':{'x':350,'z':280},'pace':'walk'})
        self.assertEqual(tools.finalize_reply({})['decision']['pace'],'walk')

    def test_unsupported_pace_and_nonmovement_are_rejected(self):
        packet=bytes(40)
        for action,pace,probe in [('approach_hand','run',{}),('sleep_here','run',{'movement_pace':{'version':1}}),
                                  ('move_to',4,{}),('move_to','sprint',{})]:
            with self.assertRaises(ValueError):paced_packet(packet,probe,action,pace)
        h=Harness();tools,_=self.tools(h)
        with self.assertRaises(ToolArgumentError):
            tools.call('do_action',{'action':'look_confused','target':None,'miracle':None,'pace':'run'})
        self.assertIsNone(tools.inner.selected_action)

    def test_second_tool_cannot_change_selected_pace(self):
        h=Harness();tools,_=self.tools(h)
        tools.call('move_to',{'destination':{'special':'god_hand'},'pace':'walk'})
        tools.call('move_to',{'destination':{'special':'god_hand'},'pace':'run'})
        self.assertEqual(tools.finalize_reply({})['decision']['pace'],'walk')

    def test_object_move_retains_action_target_with_running_pace(self):
        from bwv.action_probe import WorldActionAdapter
        h=WorldHarness();h.probe['movement_pace']={'version':1}
        tools,_=self.tools(h)
        target=tools.call('find_targets',{'kind':None,'limit':3,'scope':None,
            'action':'move_to','miracle':None,'center':None,'object_id':None})['action_targets'][0]['target_id']
        tools.call('move_to',{'destination':{'target_id':target},'pace':'run'})
        decision=tools.finalize_reply({})['decision']
        self.assertEqual(decision,{'candidate':'move_to','target':target,'pace':'run'})
        token=next(t['token'] for t in h.probe['world_actions']['offers'] if t['action']=='move_to')
        adapter=WorldActionAdapter(123,world_action='move_to',expected_world_token=token,
            expected_identity=h.identity,read=lambda pid:deepcopy(h.native),pace='run')
        packet=COMMAND.unpack(dict(adapter.capture(0).packets)['move_to'])
        self.assertEqual(packet[1],0x2000000a);self.assertEqual(packet[-2:],(19,token))

    def test_coordinate_transport_recognizes_pace_flags(self):
        from bwv.action_probe import submit
        packet=COMMAND.pack(0x42575041,0xa000000b,1,1,1,20,0)+struct.pack('<ff',20,30)
        # Stop before opening any pipe. The actual transport must accept the
        # extended coordinate payload even with both priority and pace flags.
        with patch('bwv.action_probe.ctypes.WinDLL',side_effect=RuntimeError('transport reached')) as kernel:
            with self.assertRaisesRegex(RuntimeError,'transport reached'):submit(123,packet)
            kernel.assert_called_once()
