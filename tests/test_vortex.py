import json
from dataclasses import asdict
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from bwv.vortex import parse_deposits, portal_details, MAX_EXPORT_BYTES
from bwv.vortex_live import VORTEX_LIST
from bwv.object_aliases import target_matches
from bwv.prompt import preview_prompt, build_system
from bwv.llm_context import summarize_context
from bwv.model_projection import model_snapshot
from bwv.direct_tools import DirectCreatureToolSession
from test_surroundings import NearbyMemory
from test_control import playable
from test_creature_tools import frame
import test_direct_tools


class PortalMemory(NearbyMemory):
    def __init__(self, root):
        super().__init__()
        self.reader.game_directory = root
        self.portal, self.save, self.filename = 0x6900000, 0x6901000, 0x6902000
        self.bytes(VORTEX_LIST, struct.pack('<II', self.portal, 1))
        self.bytes(self.player + 0xA58, bytes(8))
        self.typed(self.portal, b'.?AVLandscapeVortexIn@@', 30)
        self.put(self.portal + 0x88, 0)
        self.bytes(self.portal + 0x14, struct.pack('<iif', 65536, 65536, 0))
        self.put(self.portal + 0xE4, 1)
        self.put(self.portal + 0xEC, self.save)
        self.typed(self.save, b'.?AVVortexSave@@', 31)
        self.put(self.save + 0x18, self.filename)
        self.bytes(self.save + 0x1C, b'\0')
        self.bytes(self.filename, b'Profiles\\example\\vortex.txt\0'.ljust(256, b'\0'))
        self.export = root / 'Profiles' / 'example' / 'vortex.txt'
        self.export.parent.mkdir(parents=True)
        self.export.write_bytes(b'CREATE_POT("1,2", 12, 0, 300)\n')


class VortexTests(unittest.TestCase):
    def test_deposit_parser_counts_export_records_without_executing_or_guessing_types(self):
        text = b'''CREATE_NEW_TREE(-1, "1,2", 13, 0, 1.0, 1.0, 1.0)
CREATE_NEW_ANIMAL("1,2", 8, -1, -1, 30)
CREATE_TOWN_VILLAGER(-1, "1,2", NORSE_SHEPHERD, 18)
CREATE_POT("1,2", 12, 0, 300)
CREATE_POT("1,2", 11, 1, 400)
CREATE_ONE_SHOT_SPELL_PU("1,2", "WATER")
CREATE_ONE_SHOT_SPELL_PU("1,2", "LIGHTNING_BOLT")
CREATE_MOBILE_STATIC("1,2", 5, 0, 0, 0, 0, 1)
PRIVATE_UNRECOGNIZED_LINE("do not include this")
CREATE_POT("1,2", 12, 0, -5)
CREATE_POT("1,2", 12, 0, 900)'''
        parsed = parse_deposits(text)
        self.assertEqual(parsed['resources'], {'food':300, 'wood':400})
        self.assertEqual(parsed['objects'], {'trees':1, 'animals':1, 'villagers':1,
            'resource_piles':2, 'miracle_bubbles':2, 'other_objects':1})
        self.assertEqual(parsed['miracle_bubbles'], {'water':1, 'lightning':1})
        self.assertEqual(parsed['unread_records'], 2)
        self.assertNotIn('PRIVATE', json.dumps(parsed))
        self.assertNotIn('cow', json.dumps(parsed))
        self.assertEqual(parse_deposits(b'')['status'], 'recorded_for_transfer')
        with self.assertRaises(ValueError):parse_deposits(b' '*(MAX_EXPORT_BYTES+1))

    def test_vortex_outside_beliefs_reaches_summary_inspection_and_alias_search(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = PortalMemory(Path(folder))
            context = memory.sample().context
            self.assertEqual(context.portal_read_errors, 0)
            portal = next(o for o in context.known_objects if o.kind == 'vortex')
            legacy = json.loads(preview_prompt(playable(surroundings=context), None).context_json)
            compact = model_snapshot(summarize_context(legacy))
            self.assertEqual(compact['surroundings']['vortices'], 1)
            self.assertEqual(compact['surroundings']['portals'][0]['portal']['deposits']['resources']['food'], 300)
            raw = frame()
            raw['observation']['surroundings'] = asdict(context)
            _, _, session, request = test_direct_tools.DirectToolsTests().session()
            session = DirectCreatureToolSession(lambda:raw, action_candidates=session.inner.action_candidates)
            session.bind_request(request)
            for alias in ('portal','vortices','whirlpool'):
                inspected = session.call('find_objects', {'kind':alias,'limit':10, 'scope':'nearby', 'detail':'detailed'})
                self.assertEqual(inspected['objects'][0]['type'], 'vortex')
                self.assertEqual(inspected['objects'][0]['portal']['direction'], 'one_way')
            found = session.call('find_objects', {'kind':'portal','center':None,'offset':0,'limit':10})
            self.assertEqual(found['objects'][0]['object_id'], portal.object_id)
            self.assertNotIn(folder, json.dumps(found))
            self.assertNotIn(str(memory.portal), json.dumps(found))
            self.assertNotIn('lookup_handle', json.dumps(found))

    def test_portal_near_hand_is_distinct_and_disappears_when_it_leaves_both_views(self):
        with tempfile.TemporaryDirectory() as folder:
            m = PortalMemory(Path(folder)); status=0x6800000
            m.typed(m.hand,b'.?AVGInterface@@',20);m.typed(status,b'.?AVGInterfaceStatus@@',21)
            m.put(m.hand+0x39C,status)
            m.bytes(status+0xC8,struct.pack('<fff',500,0,10))
            m.put(m.game+0x59C8,1000);m.put(m.game+0x59C4,1000)
            m.bytes(m.portal+0x14,struct.pack('<iif',50*65536,65536,0))
            context=m.sample().context
            self.assertNotIn('vortex',[o.kind for o in context.nearby_known_objects])
            self.assertIn('vortex',[o.kind for o in context.god_hand_objects])
            m.bytes(status+0xC8,struct.pack('<fff',10,0,10))
            self.assertNotIn('vortex',[o.kind for o in m.sample().context.known_objects])
            self.assertIsNone(m.reader._vortex_export)

    def test_active_export_is_cached_then_refreshes_on_append_rewrite_and_session_change(self):
        with tempfile.TemporaryDirectory() as folder:
            m=PortalMemory(Path(folder))
            with patch('bwv.vortex_live.parse_deposits', wraps=parse_deposits) as parser:
                m.sample();m.sample()
                self.assertEqual(parser.call_count,1)
                m.export.write_bytes(m.export.read_bytes()+b'CREATE_POT("1,2",11,1,75)\n')
                portal=m.sample().context.known_objects[0]
                self.assertEqual(portal.portal['deposits']['resources'],{'food':300,'wood':75})
                m.export.write_bytes(b'CREATE_POT("1,2",12,0,2)\n')
                self.assertEqual(m.sample().context.known_objects[0].portal['deposits']['resources'],{'food':2})
                m.observer['session']+=1
                m.sample()
                self.assertEqual(parser.call_count,4)
            m.export.unlink()
            context=m.sample().context
            self.assertEqual(context.known_objects[0].portal['deposits_status'],'unavailable')
            self.assertNotIn('deposits',context.known_objects[0].portal)
            self.assertGreater(context.portal_read_errors,0)

    def test_wrong_or_changing_list_and_missing_file_do_not_erase_other_surroundings(self):
        for fault in ('cycle','wrong_type','count','missing_export','outside_path','not_writer','exit','volcano'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as folder:
                m=PortalMemory(Path(folder))
                if fault=='cycle':m.put(m.portal+0x88,m.portal)
                if fault=='wrong_type':m.typed(m.portal,b'.?AVTree@@',30)
                if fault=='count':m.put(VORTEX_LIST+4,999)
                if fault=='missing_export':m.export.unlink()
                if fault=='outside_path':m.bytes(m.filename,b'..\\vortex.txt\0'.ljust(256,b'\0'))
                if fault=='not_writer':m.bytes(m.save+0x1C,b'\1')
                if fault=='exit':m.typed(m.portal,b'.?AVLandscapeVortexOut@@',30)
                if fault=='volcano':m.typed(m.portal,b'.?AVLandscapeVortexVolc@@',30)
                context=m.sample().context
                self.assertIn('tree',[o.kind for o in context.known_objects])
                portals=[o for o in context.known_objects if o.kind=='vortex']
                if fault in ('cycle','wrong_type','count','volcano'):self.assertFalse(portals)
                else:self.assertNotIn('deposits',portals[0].portal)

    def test_teleport_is_not_a_vortex_and_has_no_transfer_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            m=PortalMemory(Path(folder))
            m.bytes(VORTEX_LIST,bytes(8))
            m.bytes(m.player+0xA58,struct.pack('<II',m.portal,1))
            m.typed(m.portal,b'.?AVMagicTeleport@@',30)
            portal=m.sample().context.known_objects[0]
            self.assertEqual(portal.kind,'teleport')
            self.assertEqual(portal.portal,{'route':'same_island','direction':'two_way','temporary':True,'connected_exit_ids':[]})
            self.assertFalse(target_matches('portal',asdict(portal)))
            self.assertTrue(target_matches('teleport miracle',asdict(portal)))
            self.assertIsNone(m.reader._vortex_export)
            self.assertNotIn('deposits',portal_details({**portal.portal,'deposits':{'resources':{'food':999}}}))
        system=build_system()
        self.assertIn('deposited objects one way to the',system)
        self.assertIn('temporary two-way gates',system)


if __name__ == '__main__':unittest.main()
