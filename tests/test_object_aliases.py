"""Alias searches preserve the actual observed kinds and action leases."""
from copy import deepcopy
import unittest

from bwv.creature_tools import CreatureToolSession, TOOL_CONTRACT
from bwv.direct_tools import DirectCreatureToolSession
from bwv.object_aliases import kind_matches, target_matches
from test_action_tools import action_request
from test_creature_tools import frame
from test_world_actions import WorldHarness

TOY_NAMES=('toy', 'toys', 'ball', 'toy balls', 'football', 'soccer balls',
           'beach ball', 'beachballs', 'rubber balls', 'playthings',
           'cuddly toy', 'cuddly toys', 'teddy', 'teddies', 'teddy bears',
           'soft toys', 'stuffed toys', 'stuffed animals', 'plush toys', 'plushies',
           'die', 'dice', 'toy die', 'toy dice', ' Toy-Die ')


class ObjectAliasTests(unittest.TestCase):
    def test_toy_aliases_keep_player_hand_and_creature_searches_separate(self):
        data=frame()
        toy={'object_id':'hand-side-toy','kind':'toy','distance_engine_units':500,
             'distance_to_god_hand_engine_units':0,'details':{'health_engine_units':1.0}}
        tree={'object_id':'creature-side-tree','kind':'tree','distance_engine_units':3,
              'distance_to_god_hand_engine_units':497}
        data['observation']['surroundings'].update(known_objects=[tree,toy],
            nearby_known_objects=[tree],god_hand_objects=[toy],god_hand_available=True)
        session=DirectCreatureToolSession(lambda:deepcopy(data))
        for alias in TOY_NAMES:
            with self.subTest(alias=alias):
                args={'type':alias,'scope':'nearby','detail':'detailed','limit':10}
                found=session.call('find_objects',{**args,'center':'god_hand'})['objects']
                self.assertEqual([o['object_id'] for o in found],['hand-side-toy'])
                self.assertEqual(found[0]['type'],'toy')
                self.assertNotIn('object_type',found[0]) # Alias is not subtype evidence.
                self.assertEqual(session.call('find_objects',{**args,'center':'creature'})['objects'],[])
                self.assertFalse(target_matches(alias,tree))
        self.assertTrue(kind_matches('miracle ball','miracle_bubble'))
        self.assertFalse(kind_matches('miracle ball','toy'))

    def test_wood_targets_completed_workshops_stores_and_unfinished_buildings(self):
        for alias,kind,complete in (('scaffolding maker','workshop',True),
                ('warehouse','storage',True),('town centre','town_centre',False),('house','house',True)):
            with self.subTest(kind=kind,complete=complete):
                h=WorldHarness()
                entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='cast_wood')
                entry.update(kind=kind,object_handle=123456)
                offer=h.pending.factory(h.obs());data=offer.offer()
                request=action_request(data['candidates']);request.current['snapshot'].update(data)
                current=frame()
                target_info=data['action_targets']['cast_wood']['objects'][0]
                current['observation']['surroundings']['known_objects']=[{
                    'object_id':target_info['object_id'],'kind':kind,'distance_engine_units':20,
                    'details':{'construction_complete':complete}}]
                session=DirectCreatureToolSession(lambda:current,action_candidates=data['candidates'])
                session.bind_request(request)
                found=session.call('find_targets',{'type':alias,'action':'cast_miracle','miracle':'wood','limit':3})
                target=found['action_targets'][0]
                self.assertEqual(target['type'],kind.replace('_',' '))
                self.assertEqual(target['details']['construction_complete'],complete)
                result=session.call('do_action',{'action':'cast_miracle','miracle':'wood','target':target['object_id']})
                self.assertEqual(result['status'],'selected_pending_final_reply')
                offer.dispatch(session.finalize_reply({})['decision'])
                h.pending.advance(h.obs(),allowed=True)
                self.assertEqual(len(h.sent),1)

    def test_fire_synonyms_require_an_explicit_burning_reading(self):
        for alias in ('fire', 'fires', 'Flames', 'flame', 'blaze', 'blazes',
                      'burning', ' On Fire ', 'on-fire', 'alight', 'ablaze',
                      'burning object', 'burning objects', 'object on fire', 'objects on fire'):
            for kind in ('tree', 'house', 'villager', 'bonfire', 'miracle_bubble'):
                with self.subTest(alias=alias,kind=kind):
                    self.assertTrue(target_matches(alias,{'kind':kind,'on_fire':True}))
                    self.assertFalse(target_matches(alias,{'kind':kind,'on_fire':False}))
                    self.assertFalse(target_matches(alias,{'kind':kind}))
        self.assertFalse(target_matches('fire',{'kind':'miracle_bubble','miracle':'fireball'}))

    def test_fire_inspection_filters_both_centers_without_renaming_objects(self):
        data=frame()
        objects=[{'object_id':'burning-house','kind':'house','on_fire':True,
                  'distance_engine_units':30,'distance_to_god_hand_engine_units':15},
                 {'object_id':'cold-tree','kind':'tree','on_fire':False,
                  'distance_engine_units':3,'distance_to_god_hand_engine_units':4},
                 {'object_id':'unknown-fire','kind':'bonfire',
                  'distance_engine_units':8,'distance_to_god_hand_engine_units':6}]
        data['observation']['surroundings'].update(nearby_known_objects=objects,
            god_hand_objects=objects,god_hand_available=True)
        for session_type in (CreatureToolSession,DirectCreatureToolSession):
            session=session_type(lambda:deepcopy(data))
            for center in ('creature','god_hand'):
                if session_type is CreatureToolSession:
                    result=session.call('inspect_surrounding',{'kind':'flames','limit':10,'center':center})
                else:
                    result=session.call('find_objects',{'type':'flames','limit':10,'center':center,'scope':'nearby','detail':'detailed'})
                self.assertEqual([o['object_id'] for o in result['objects']],['burning-house'])
                self.assertEqual(result['objects'][0].get('type',result['objects'][0].get('kind')),'house')

    def test_fire_search_returns_a_usable_water_target(self):
        h=WorldHarness()
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='cast_water_object')
        entry.update(kind='house',on_fire=True)
        offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        session=DirectCreatureToolSession(frame,action_candidates=data['candidates'])
        session.bind_request(request)
        found=session.call('find_targets',{'kind':'fire','action':'cast_miracle',
            'miracle':'water','limit':10})['action_targets']
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['type'],'house')
        self.assertIs(found[0]['on_fire'],True)
        session.call('do_action',{'action':'cast_miracle','miracle':'water','target':found[0]['target_id']})
        offer.dispatch(session.finalize_reply({})['decision'])
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)

    def test_names_are_normalized_without_guessing_unrelated_objects(self):
        for alias, kind in [(' Warehouses ', 'storage'), ('Town-Center', 'totem'),
                            ('town centre', 'town_centre'), ('totems', 'town_centre'),
                            ('Wheat Fields', 'field'), ('nurseries', 'nursery'),
                            ('graveyards', 'cemetery'), ('miracle spheres', 'miracle_bubble'),
                            ('crèches', 'nursery'), ('Day Care', 'nursery'),
                            ('daycares', 'nursery'), ('kindergartens', 'nursery'),
                            ('miracle stands', 'miracle_dispenser'),
                            ('pedestals', 'miracle_dispenser'), ('miracle pedestal', 'miracle_dispenser'),
                            ('scaffolding maker','workshop'), ('scaffold creators','workshop'),
                            ('scaffolding workshop','workshop'),
                            ('Spell-Stand', 'miracle_dispenser'), ('bubbles', 'miracle_bubble')]:
            self.assertTrue(kind_matches(alias, kind), alias)
        self.assertTrue(kind_matches(None, 'tree'))
        self.assertFalse(kind_matches('home', 'house'))
        self.assertFalse(kind_matches('warehouse', 'house'))
        self.assertFalse(kind_matches('town', 'villager'))

    def test_inspection_keeps_totem_and_town_centre_identities_separate(self):
        data=frame()
        objects=[{'kind':kind,'object_id':kind+'-1','distance_engine_units':distance,'distance_to_god_hand_engine_units':distance}
                 for kind,distance in [('totem',8),('town_centre',10),('house',3)]]
        data['observation']['surroundings'].update(
            nearby_known_objects=objects, god_hand_objects=objects, god_hand_available=True)
        s=CreatureToolSession(lambda:deepcopy(data))
        for center in ('creature','god_hand'):
            result=s.call('inspect_surrounding',{'kind':'Town Center','limit':10,'center':center})
            self.assertEqual({o['object_id'] for o in result['objects']}, {'totem-1','town_centre-1'})

    def test_alias_search_returns_same_action_target_and_dispatches_it(self):
        for alias,kind in [('warehouse','storage'), ('town center','totem'), ('totem','town_centre'),
                           *((name,'toy') for name in TOY_NAMES)]:
            with self.subTest(alias=alias,kind=kind):
                h=WorldHarness()
                entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
                entry.update(kind=kind,distance_engine_units=100)
                offer=h.pending.factory(h.obs());data=offer.offer()
                request=action_request(data['candidates']);request.current['snapshot'].update(data)
                s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
                found=s.call('find_targets',{'kind':alias,'limit':3})['action_targets'][0]
                inspected=s.call('find_targets',{'kind':alias,'limit':3,'action':'move_to', 'scope':'nearby'})['action_targets'][0]
                self.assertEqual(found,inspected)
                self.assertEqual(found['type'],kind.replace('_',' '))
                self.assertFalse(h.sent)
                s.call('move_to',{'destination':{'target_id':found['target_id']}})
                offer.dispatch(s.finalize_reply({})['decision'])
                h.pending.advance(h.obs(),allowed=True)
                self.assertEqual(len(h.sent),1)

    def test_prompt_explains_aliases_without_replacing_target_ids(self):
        self.assertIn('totem = town centre/town center',TOOL_CONTRACT)
        self.assertIn('storage = warehouse/storehouse',TOOL_CONTRACT)
        self.assertIn('Aliases are search filters, not action target IDs.',TOOL_CONTRACT)
