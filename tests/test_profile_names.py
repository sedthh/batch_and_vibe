from contextlib import contextmanager
import json
from unittest import TestCase

from bwv.creature import CreatureContext
from bwv.profile_names import (PROFILE_KEY, PROMPT_PREFIX, ProfileNames,
                               profile_name_to_key, read_profile_names, with_profile_names)
from bwv.prompt import preview_prompt
from test_control import playable


def binary(text):return ((text+'\0').encode('utf-16-le'),3)


class RegistryFixture:
    HKEY_CURRENT_USER=1;KEY_QUERY_VALUE=1;KEY_WOW64_32KEY=512
    def __init__(self, creature=binary('Pebble')):
        self.values={(PROFILE_KEY,'Profile'):binary('Guide'),
                     (PROFILE_KEY+'\\Profiles\\_G_u_i_d_e','creature name'):creature}
        self.queries=[]
    @contextmanager
    def OpenKey(self,hive,path,reserved,access):yield path
    def QueryValueEx(self,key,name):
        self.queries.append((key,name))
        if (key,name) not in self.values:raise FileNotFoundError()
        return self.values[key,name]


class ProfileNameTests(TestCase):
    def test_reads_only_selected_profile_and_creature_display_name(self):
        registry=RegistryFixture()
        self.assertEqual(read_profile_names(registry),ProfileNames('Guide','Pebble'))
        self.assertEqual([name for _,name in registry.queries],['Profile','creature name','Profile'])
        self.assertEqual(profile_name_to_key('A 2é'),'_A0020_200e9')

    def test_missing_blank_malformed_and_switched_profiles_do_not_reuse_names(self):
        for value in (binary(''),binary('   '),(b'bad',3),(b'X\0',3),(123,4)):
            with self.subTest(value=value):
                self.assertEqual(read_profile_names(RegistryFixture(value)),ProfileNames('Guide'))
        registry=RegistryFixture();registry.values.clear()
        self.assertEqual(read_profile_names(registry),ProfileNames())
        registry=RegistryFixture();original=registry.QueryValueEx
        def query(key,name):
            if name=='Profile' and len(registry.queries)>1:return binary('Someone Else')
            return original(key,name)
        registry.QueryValueEx=query
        self.assertEqual(read_profile_names(registry),ProfileNames())

    def test_prompt_names_are_optional_system_data_not_tick_context(self):
        for names in (ProfileNames(),ProfileNames('Guide'),ProfileNames(creature_name='Pebble'),
                      ProfileNames('Guide','Pebble')):
            prompt=preview_prompt(playable(creature=CreatureContext(names=names),candidates=()),None)
            self.assertEqual(json.loads(prompt.context_json),{})
            if names==ProfileNames():self.assertNotIn(PROMPT_PREFIX,prompt.system)
            else:
                data=json.loads(prompt.system.split(PROMPT_PREFIX)[1])
                self.assertEqual(set(data),{key for key,value in vars(names).items() if value})
        initial=with_profile_names('System',ProfileNames('Guide','Pebble'))
        renamed=with_profile_names(initial,ProfileNames('Guide','Fern'))
        self.assertNotIn('Pebble',renamed)
        self.assertEqual(renamed.count(PROMPT_PREFIX),1)
        self.assertEqual(with_profile_names(renamed,ProfileNames()),'System')
