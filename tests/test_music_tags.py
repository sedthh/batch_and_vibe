"""Authored ID3 fixtures only; no real songs, player or model calls."""
from copy import deepcopy
import ctypes
import json
from itertools import product
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bwv.music import WinampReader, _Win32
from bwv.music_tags import MAX_TAG_BYTES, TagCache, TrackTags, read_tags, timed_lines
from bwv.model_projection import model_turn
from bwv.request_compaction import compact_messages
from test_music import FakeWinamp


def syncsafe(number):
    return bytes((number >> shift) & 127 for shift in (21,14,7,0))


def frame(name,content,version=3):
    size = syncsafe(len(content)) if version == 4 else len(content).to_bytes(4,'big')
    return name.encode('ascii')+size+b'\0\0'+content


def id3(*frames,version=3):
    body=b''.join(frames)
    return b'ID3'+bytes((version,0,0))+syncsafe(len(body))+body


class TagTests(unittest.TestCase):
    def test_real_parser_reads_unicode_tags_and_embedded_timed_lyrics(self):
        for version in (3,4):
            lyric = b'\x03eng\x02\x01\0'
            for text,seconds in (('Silver river',40),('Carry us home',44),('Later chorus',90)):
                lyric += text.encode()+b'\0'+int(seconds*1000).to_bytes(4,'big')
            data=id3(frame('TIT2',b'\x03'+ 'Paper Moon 日本語'.encode(),version),
                     frame('TPE1',b'\x03Test Artist',version),
                     frame('TCON',b'\x03Folk',version),frame('TBPM',b'\x03123',version),
                     frame('SYLT',lyric,version),version=version)
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'track.mp3';path.write_bytes(data)
                tags=read_tags(path)
            self.assertEqual(tags.fields,{'title':'Paper Moon 日本語','artist':'Test Artist',
                                         'genre':'Folk','tempo_bpm':123})
            self.assertEqual(tags.at(45)['lyrics'],[{'at_seconds':40,'text':'Silver river'},
                                                   {'at_seconds':44,'text':'Carry us home'}])
            self.assertNotIn('lyrics',tags.at(0))
            self.assertNotIn('lyrics',tags.at(70))

    def test_untimed_lyrics_and_unrequested_tags_are_never_exposed(self):
        data=id3(frame('TIT2',b'\x03Song'),frame('TALB',b'\x03Unneeded Album'),
                 frame('COMM',b'\x03eng\0Private Comment'),
                 frame('USLT',b'\x03eng\0'+b'whole song words '*2000))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'track.mp3';path.write_bytes(data)
            tags=read_tags(path)
        self.assertEqual(tags.at(45),{'title':'Song'})
        self.assertEqual(tags.lines,())
        self.assertTrue(tags.matching('whole song words'))
        self.assertLessEqual(len(tags.matching('whole song words')[0]['text'].encode()),320)

    def test_untimed_matching_handles_punctuation_but_does_not_classify_commands(self):
        tags=TrackTags(untimed=('Silver river, carry us home.',
                               'Burn down the village and build a paper moon.'))
        matched=tags.matching('CARRY, us HOME!')
        self.assertIn('carry us home',matched[0]['text'].lower())
        self.assertNotIn('at_seconds',matched[0])
        self.assertEqual(matched[0]['match'],'words')
        self.assertTrue(tags.matching('burn down the village'))
        for words in ('home','carry home','nothing matching here','[.*] ^$'):
            self.assertEqual(tags.matching(words),[])
        self.assertEqual(TrackTags(lines=((0,'Timed'),),untimed=tags.untimed).matching('carry us home'),[])

    def test_blank_cues_offsets_and_multibyte_budget(self):
        tags=TrackTags({},timed_lines(['[offset:1000]\n[00:40.00]'+ '鳥'*500+'\n[00:45.00]\n[01:00]Later']))
        self.assertEqual(tags.lines[0][0],39)
        excerpt=tags.at(40)['lyrics']
        self.assertLessEqual(sum(len(line['text'].encode()) for line in excerpt),320)
        self.assertNotIn('lyrics',tags.at(44))
        crowded=TrackTags({},((40,'x'*500),(44,'Current line'))).at(45)['lyrics']
        self.assertEqual(crowded[-1]['text'],'Current line')
        self.assertLessEqual(sum(len(line['text'].encode()) for line in crowded),320)

    def test_legacy_id3v1_tags(self):
        data=b'TAG'+b'Title'.ljust(30,b'\0')+b'Artist'.ljust(30,b'\0')+b'Album'.ljust(30,b'\0')
        data+=b'2000'+b'Comment'.ljust(30,b'\0')+bytes((13,))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'track.mp3';path.write_bytes(data)
            self.assertEqual(read_tags(path).fields,{'title':'Title','artist':'Artist','genre':'Pop'})

    def test_cache_reuses_tags_invalidates_edits_and_bounds_files(self):
        loader=Mock(return_value=TrackTags({'title':'Tagged'}))
        cache=TagCache(loader)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'track.mp3';path.write_bytes(b'initial')
            cache.get(str(path));cache.get(str(path))
            self.assertEqual(loader.call_count,1)
            path.write_bytes(b'changed length')
            cache.get(str(path));self.assertEqual(loader.call_count,2)
            for i in range(10):
                other=Path(directory)/f'{i}.mp3';other.write_bytes(b'')
                cache.get(str(other))
            self.assertEqual(len(cache.entries),8)
        for path in ('https://example.test/song.mp3',r'\\server\song.mp3','relative.mp3'):
            self.assertFalse(cache.get(path).fields)

    def test_bad_and_oversized_tags_leave_playback_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'track.mp3'
            for data in (b'no tags',b'ID3\x03\0\0'+syncsafe(MAX_TAG_BYTES+1),
                         b'ID3\x03\0\0'+syncsafe(100)+b'truncated',
                         id3(frame('TIT2',b''))):
                path.write_bytes(data)
                self.assertFalse(TagCache().get(str(path)).fields)

    def test_reader_uses_exact_file_and_rejects_midread_path_switch(self):
        api=FakeWinamp();api.filename=Mock(return_value=r'C:\Music\private.mp3')
        cache=Mock();cache.get.return_value=TrackTags({'title':'Tagged Song','artist':'Artist'},
                                                     ((40,'Current words'),))
        reader=WinampReader(api,cache)
        result=reader.read()
        self.assertEqual(result.title,'Tagged Song')
        self.assertEqual(result.tags['lyrics'][0]['text'],'Current words')
        api.filename.side_effect=[r'C:\Music\one.mp3',r'C:\Music\two.mp3']
        self.assertEqual(reader.read().state,'unavailable')
        api.filename.side_effect=OSError('Denied')
        self.assertEqual(reader.read().title,'Paper Moon')
        self.assertIsNone(reader.read().tags)


class FilenameTests(unittest.TestCase):
    def make_reader(self, payload, *, pointer=0x20000, unicode_supported=True, denied=False):
        kernel=Mock()
        kernel.OpenProcess.return_value=0 if denied else 100
        def architecture(handle,output):
            output._obj.value=1
            return 1
        kernel.IsWow64Process.side_effect=architecture
        def read(handle,address,buffer,size,received):
            offset=address-pointer
            if offset < 0:return 0
            data=payload[offset:offset+size].ljust(size,b'\0')
            ctypes.memmove(buffer,data,size)
            received._obj.value=size
            return 1
        kernel.ReadProcessMemory.side_effect=read
        reader=object.__new__(_Win32)
        reader.pid=Mock(return_value=77)
        reader._send=Mock(side_effect=lambda window,code,index:
                          pointer if code == 211 or unicode_supported else 1)
        return reader,kernel

    def test_unicode_page_boundary_read_and_handle_cleanup(self):
        path='C:\\Music\\日本語.mp3'
        reader,kernel=self.make_reader(path.encode('utf-16-le')+b'\0\0',pointer=0x20ffe)
        with patch('bwv.music.ctypes.WinDLL',return_value=kernel):
            self.assertEqual(reader.filename(10,77,0),path)
        self.assertEqual(kernel.ReadProcessMemory.call_args_list[0].args[3],2)
        kernel.OpenProcess.assert_called_once_with(0x1010,False,77)
        kernel.CloseHandle.assert_called_once_with(100)

    def test_legacy_ansi_fallback_and_process_race(self):
        reader,kernel=self.make_reader(b'C:\\Music\\song.mp3\0',unicode_supported=False)
        with patch('bwv.music.ctypes.WinDLL',return_value=kernel):
            self.assertEqual(reader.filename(10,77,0),r'C:\Music\song.mp3')
            reader.pid.return_value=88
            with self.assertRaises(OSError):reader.filename(10,77,0)
        self.assertEqual(kernel.CloseHandle.call_count,2)

    def test_denied_and_unterminated_reads_are_bounded(self):
        reader,kernel=self.make_reader(b'A'*16384)
        with patch('bwv.music.ctypes.WinDLL',return_value=kernel):
            with self.assertRaises(OSError):reader.filename(10,77,0)
        self.assertEqual(kernel.ReadProcessMemory.call_count,64)
        kernel.CloseHandle.assert_called_once_with(100)
        reader,kernel=self.make_reader(b'',denied=True)
        with patch('bwv.music.ctypes.WinDLL',return_value=kernel):
            with self.assertRaises(OSError):reader.filename(10,77,0)
        kernel.ReadProcessMemory.assert_not_called()


class ContextBudgetTests(unittest.TestCase):
    def test_untimed_excerpt_keeps_match_type_and_budget_instead_of_a_fake_timestamp(self):
        raw={'snapshot':{},'music_at_submission':{'title':'Song','lyrics':[
            {'text':'Words '*1000,'match':'words'}]}}
        result=model_turn(raw)
        excerpt=result['music_at_submission']['lyrics'][0]
        self.assertNotIn('at_seconds',excerpt)
        self.assertEqual(excerpt['match'],'words')
        self.assertLessEqual(len(excerpt['text'].encode()),320)
        self.assertEqual(result,model_turn(result))
    def test_repeated_projection_does_not_shift_or_duplicate_lyric_context(self):
        base={'state':'playing','title':'Song','position_seconds':45}
        variants=[base,dict(base,lyrics=[{'at_seconds':40,'text':'First'}]),
                  dict(base,lyrics=[{'at_seconds':44,'text':'Second'}]),
                  dict(base,position_seconds=44,lyrics=[{'at_seconds':44,'text':'Second'}])]
        for music,submitted,start,end in product(variants,repeat=4):
            raw={'snapshot':{},'music':music,'music_at_submission':submitted,
                 'music_during_recording':{'recording_seconds':2,'at_start':start,'at_end':end}}
            projected=model_turn(raw)
            self.assertEqual(projected,model_turn(projected))

    def test_recorded_track_wins_global_budget_and_projection_is_idempotent(self):
        start={'state':'playing','title':'Recorded Song','position_seconds':40,
               'lyrics':[{'at_seconds':40,'text':'鳥'*300}]}
        end=dict(start,position_seconds=44,lyrics=[{'at_seconds':44,'text':'Moon '*200}])
        current={'state':'playing','title':'Different song','position_seconds':5,
                 'lyrics':[{'at_seconds':5,'text':'Not the recorded lyric'}]}
        raw={'snapshot':{},'music':current,'music_at_submission':deepcopy(current),
             'music_during_recording':{'recording_seconds':4,'at_start':start,'at_end':end}}
        projected=model_turn(raw)
        self.assertEqual(projected,model_turn(projected))
        self.assertNotIn('lyrics',projected['music'])
        from bwv.music_context import music_blocks
        lines=[line for block in music_blocks(projected) for line in block.get('lyrics',[])]
        self.assertLessEqual(len(lines),2)
        self.assertLessEqual(sum(len(line['text'].encode()) for line in lines),320)
        self.assertTrue(lines[0]['text'].startswith('Moon'))
        self.assertIn('lyrics',raw['music'])

    def test_history_never_accumulates_excerpts_or_changes_player_words(self):
        lyrics=[{'at_seconds':40,'text':'Silver river'}]
        value={'snapshot':{},'god_question':'These are my own words',
               'music':{'title':'Paper Moon','lyrics':lyrics},
               'music_during_recording':{'start':{'title':'Paper Moon','lyrics':lyrics}}}
        messages=[{'role':'system','content':'System'}]+[
            {'role':'user','content':json.dumps(value)} for _ in range(30)]
        original=deepcopy(messages)
        result=compact_messages(messages)
        for message in result[1:-1]:
            parsed=json.loads(message['content'])
            self.assertEqual(parsed['god_question'],'These are my own words')
            self.assertNotIn('lyrics',parsed['music'])
            self.assertNotIn('lyrics',parsed['music_during_recording']['start'])
        self.assertIn('lyrics',json.loads(result[-1]['content'])['music'])
        self.assertEqual(compact_messages(result),result)
        self.assertEqual(messages,original)
