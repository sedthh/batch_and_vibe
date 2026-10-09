"""Synthetic person metadata; no process attachment or game assets."""
from dataclasses import asdict
import json
import unittest

from bwv.nearby_people import person_details
from bwv.nearby_people_live import read_person, TICKS_PER_YEAR
from bwv.surroundings import NearbyObject, SurroundingsContext
from bwv.prompt import preview_prompt
from bwv.llm_context import summarize_context
from bwv.direct_tools import DirectCreatureToolSession
from test_control import playable
from test_surroundings import NearbyMemory
from test_creature_tools import frame
from test_world_actions import WorldHarness
from test_action_tools import action_request


def fixture(state=118, role=1, sex=1, age=32, special=False):
    m=NearbyMemory()
    runtime=b'.?AVSpecialVillager@@' if special else b'.?AVVillager@@'
    m.typed(m.tree,runtime,10)
    m.put(m.reader.word(m.tree)+0x8D0,0x5ECAF0)
    m.bytes(m.tree+0x25,b'\0')
    m.bytes(m.tree+0x8C,bytes((state,121,15)))
    m.bytes(m.tree+0xF2,bytes((role,)))
    m.put(TICKS_PER_YEAR,100)
    m.put(m.tree+0xA0,(100-age*100)&0xFFFFFFFF)
    m.put(m.tree+0x28,0x6300000)
    m.typed(0x6300000,b'.?AVGVillagerInfo@@',11)
    m.put(0x6300000+0x1F8,sex)
    return m,runtime


class NearbyPeopleTests(unittest.TestCase):
    def test_inspection_metadata_uses_current_not_final_or_previous_activity(self):
        m,_=fixture()
        context=m.sample().context
        self.assertEqual(context.person_read_errors,0)
        person=context.nearby_known_objects[0].person
        self.assertEqual(person,{'current_activity':'eating','assigned_role':'farmer',
            'sex':'female','age_years':32,'is_actor':False,'script_controlled':False})
        current=frame();current['observation']['surroundings']=asdict(context)
        session=DirectCreatureToolSession(lambda:current)
        obj=session.call('inspect_surrounding',{'kind':'villager','limit':3})['objects'][0]
        self.assertEqual(obj['person'],person)
        self.assertNotIn('life_status',person)

    def test_scripted_people_and_special_class_are_not_confused(self):
        for special in (False,True):
            for state,flag,expected in ((4,0,True),(204,0,True),(1,4,True),(118,0,False)):
                m,runtime=fixture(state=state,special=special)
                m.bytes(m.tree+0x25,bytes((flag,)))
                result,errors=read_person(m.reader,m.tree,100,runtime)
                self.assertEqual(errors,0)
                self.assertIs(result['is_actor'],expected)
                self.assertIs(result['script_controlled'],flag==4)

    def test_dead_counts_exclude_actors_and_do_not_count_dying_as_dead(self):
        for state,status in ((13,'dying'),(14,'dying'),(15,'dead'),(16,None),(17,None),(226,None)):
            m,_=fixture(state=state)
            m.bytes(m.tree+0x25,b'\x04')
            context=m.sample().context
            self.assertEqual(context.nearby_known_objects[0].person.get('life_status'),status)
            compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
            expected='dead_people_nearby' if state==15 else 'important_people_nearby'
            self.assertEqual(compact['surroundings'],{expected:1})

    def test_counts_are_disjoint_for_each_location_and_empty_counts_omitted(self):
        objects=tuple(NearbyObject(str(i),'villager',i,person=p) for i,p in enumerate((
            {'is_actor':False}, {'is_actor':True}, {'is_actor':True,'life_status':'dead'},
            {'is_actor':False,'life_status':'dead'}, {'is_actor':False,'life_status':'dying'})))
        context=SurroundingsContext(True,objects,god_hand_available=True,god_hand_objects=objects[2:4])
        compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
        self.assertEqual(compact['surroundings'],{
            'people_nearby':2,'important_people_nearby':1,'dead_people_nearby':2})
        self.assertEqual(compact['surroundings_near_god_hand'],{'dead_people_nearby':2})
        self.assertNotIn('surroundings',summarize_context(json.loads(preview_prompt(
            playable(surroundings=SurroundingsContext(True)),None).context_json)))

    def test_game_age_wrap_and_invalid_values_do_not_invent_details(self):
        m,runtime=fixture(age=32)
        self.assertEqual(read_person(m.reader,m.tree,100,runtime)[0]['age_years'],32)
        for value in (0,0xFFFFFFFF):
            m.put(TICKS_PER_YEAR,value)
            result,errors=read_person(m.reader,m.tree,100,runtime)
            self.assertNotIn('age_years',result);self.assertGreater(errors,0)
        m,runtime=fixture(role=255,sex=99)
        result,_=read_person(m.reader,m.tree,100,runtime)
        self.assertNotIn('assigned_role',result);self.assertNotIn('sex',result)
        m.put(m.reader.word(m.tree)+0x8D0,123)
        self.assertNotIn('age_years',read_person(m.reader,m.tree,100,runtime)[0])

    def test_transition_and_read_failures_keep_people_without_false_dead_count(self):
        m,runtime=fixture(state=15)
        original=m.reader.read;calls=0
        def changing(address,size):
            nonlocal calls
            if address==m.tree+0x8C:
                calls+=1
                return bytes((15 if calls%2 else 14,))
            return original(address,size)
        m.reader.read=changing
        result,errors=read_person(m.reader,m.tree,100,runtime)
        self.assertNotIn('life_status',result);self.assertNotIn('current_activity',result)
        self.assertGreater(errors,0)
        m.reader.read=original;m.put(m.tree+0x28,0)
        context=m.sample().context
        self.assertEqual(context.nearby_known_objects[0].person['life_status'],'dead')
        self.assertNotIn('sex',context.nearby_known_objects[0].person)
        self.assertGreater(context.person_read_errors,0)

    def test_person_validation_omits_unverified_fields(self):
        self.assertEqual(person_details({'sex':'unknown','age_years':True,
            'life_status':'healthy','assigned_role':'king','is_actor':1,'name':'invented'}),{})
        with self.assertRaises(ValueError):NearbyObject('x','tree',1,person={'is_actor':True})

    def test_actor_count_still_selects_people_cadence(self):
        from bwv.cadence import activity_tier
        raw={'observation':{'surroundings':{'available':True}}}
        self.assertEqual(activity_tier(raw,{'surroundings':{'important_people_nearby':1}})[0],
                         'settlement')
        self.assertEqual(activity_tier(raw,{'surroundings':{'dead_people_nearby':1}})[0],
                         'quiet')

    def test_action_target_metadata_matches_exact_identity_and_expires(self):
        h=WorldHarness()
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
        entry.update(kind='villager',object_handle=123456,distance_engine_units=20)
        data=h.pending.factory(h.obs()).offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        current=frame()
        session=DirectCreatureToolSession(lambda:current,action_candidates=data['candidates'])
        session.bind_request(request)
        target=session.call('find_targets',{'kind':'villager','limit':3,'scope':'known'})['action_targets'][0]
        person={'sex':'male','age_years':40,'is_actor':True,'current_activity':'walking_to_object'}
        current['observation']['surroundings']['god_hand_objects']=[{
            'object_id':target['object_id'],'kind':'villager','person':person},
            {'object_id':'other','kind':'villager','person':{'sex':'female'}}]
        found=session.call('find_targets',{'kind':'villager','limit':3,'scope':'known'})
        self.assertEqual(found['action_targets'][0]['person'],person)
        current['observation']['surroundings']['god_hand_objects'].pop(0)
        self.assertNotIn('person',session.call('find_targets',{
            'kind':'villager','limit':3,'scope':'known'})['action_targets'][0])
        self.assertFalse(h.sent)
