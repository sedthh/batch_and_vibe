"""Existing gate travel is a destination-bound action, separate from casting."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import struct
import tempfile
import unittest

from bwv.action_probe import COMMAND
from bwv.direct_tools import DirectCreatureToolSession
from bwv.object_details import public_object, known_objects
from bwv.tool_docs import tool_documentation
from bwv.world_actions import world_offers_available
from test_action_tools import action_request
from test_creature_tools import frame
from test_world_actions import WorldHarness
from test_vortex import PortalMemory
from bwv.vortex_live import VORTEX_LIST


class TeleportTravelTests(unittest.TestCase):
    def test_portal_beliefs_do_not_duplicate_or_replace_portal_metadata(self):
        for runtime,category in ((b'.?AVMagicTeleport@@','teleport'),
                                 (b'.?AVLandscapeVortexIn@@','vortex')):
            with self.subTest(category=category),tempfile.TemporaryDirectory() as directory:
                m=PortalMemory(Path(directory))
                m.typed(m.hand,b'.?AVGInterface@@',20);status=0x6800000
                m.typed(status,b'.?AVGInterfaceStatus@@',21);m.put(m.hand+0x39C,status)
                m.bytes(status+0xC8,struct.pack('<fff',10,0,10))
                m.put(m.game+0x59C8,1000);m.put(m.game+0x59C4,1000)
                m.typed(m.portal,runtime,30);m.put(m.node+0x30,m.portal)
                if category=='teleport':
                    m.bytes(VORTEX_LIST,bytes(8))
                    m.bytes(m.player+0xA58,struct.pack('<II',m.portal,1))
                context=m.sample().context
                for view in (context.known_objects,context.nearby_known_objects,context.god_hand_objects):
                    obj,=view
                    self.assertEqual(obj.kind,category)
                    self.assertIsNotNone(obj.portal)
                    self.assertIsNone(obj.engine_class)
                projected,=map(public_object,known_objects(asdict(context)))
                self.assertEqual(projected['kind'],category)
                self.assertEqual(projected['name'],category)
                self.assertIn('connected_exit_ids' if category=='teleport' else 'deposits',projected['portal'])

    def bind(self, h):
        offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        tools=DirectCreatureToolSession(frame,action_candidates=data['candidates'])
        tools.bind_request(request)
        return offer,tools

    def test_one_action_targets_exit_without_learning_or_casting(self):
        h=WorldHarness()
        h.probe['world_actions']['offers']=[e for e in h.probe['world_actions']['offers']
            if e['action']=='use_teleport']
        offer,tools=self.bind(h)
        self.assertIn('use_teleport',tools.menu)
        self.assertNotIn('teleport',tools.spells)
        target=tools._targets('use_teleport')[0]['target_id']
        result=tools.call('do_action',{'action':'use_teleport','target':target,'miracle':None})
        self.assertEqual(result['status'],'selected_pending_final_reply')
        self.assertIn('destination/EXIT',tool_documentation(tools.definitions()))
        offer.dispatch(tools.finalize_reply({})['decision'])
        for _ in range(3):h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(19,h.probe['world_actions']['offers'][0]['token']))

    def test_non_gate_and_stale_exit_never_become_other_destinations(self):
        h=WorldHarness();_,tools=self.bind(h)
        tree=tools._targets('move_to')[0]['target_id']
        self.assertEqual(tools.call('do_action',{'action':'use_teleport','target':tree,'miracle':None})['status'],'invalid_target')
        self.assertIsNone(tools.inner.selected_action)
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='use_teleport')
        for kind in ('tree','vortex','self'):
            entry['kind']=kind
            self.assertNotIn('use_teleport',[e['action'] for e in world_offers_available(h.probe)])
        item={'candidate':'use_teleport','result':{'target':{'kind':'teleport'}},'retargets':0}
        self.assertFalse(h.pending._retarget(item,[], 'world_token'))

    def test_old_bridge_does_not_offer_travel(self):
        h=WorldHarness()
        h.probe['world_actions']['offers']=[e for e in h.probe['world_actions']['offers'] if e['action']!='use_teleport']
        _,tools=self.bind(h)
        self.assertNotIn('use_teleport',tools.menu)

    def test_nearby_gate_exposes_real_connected_far_exit_but_no_other_network(self):
        with tempfile.TemporaryDirectory() as directory:
            m=PortalMemory(Path(directory));far=m.portal+0x4000
            m.bytes(VORTEX_LIST,bytes(8))
            m.bytes(m.player+0xA58,struct.pack('<II',m.portal,2))
            m.typed(m.portal,b'.?AVMagicTeleport@@',30)
            m.typed(far,b'.?AVMagicTeleport@@',30)
            m.put(m.portal+0x88,far);m.put(far+0x88,0)
            m.bytes(far+0x14,struct.pack('<iif',100*65536,100*65536,0))
            context=m.sample().context
            gates=[o for o in context.known_objects if o.kind=='teleport']
            self.assertEqual(len(gates),2)
            self.assertEqual(len([o for o in context.nearby_known_objects if o.kind=='teleport']),1)
            for gate in gates:
                other=next(o for o in gates if o.object_id!=gate.object_id)
                self.assertEqual(public_object(asdict(gate))['portal']['connected_exit_ids'],[other.object_id])
            m.bytes(m.player+0xA58,struct.pack('<II',m.portal,1));m.put(m.portal+0x88,0)
            only=next(o for o in m.sample().context.known_objects if o.kind=='teleport')
            self.assertEqual(only.portal['connected_exit_ids'],[])
            m.bytes(m.portal+0x14,struct.pack('<iif',100*65536,100*65536,0))
            self.assertFalse([o for o in m.sample().context.known_objects if o.kind=='teleport'])
