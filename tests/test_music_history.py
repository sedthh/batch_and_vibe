from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from bwv.music import MusicMonitor, Playback
from bwv.music_history import ListeningHistory, listening_label, song_key
from bwv.model_projection import model_turn
from bwv.music_tags import TrackTags


def track(name='Paper Moon', position=0, state='playing', index=0):
    return Playback(state,name,(10,77,index,name,None),position,180,
                    song_key=song_key(title=name))


class HistoryTests(unittest.TestCase):
    def test_repeat_detects_a_rotation_and_expires_as_other_songs_play(self):
        history=ListeningHistory()
        for now,name in enumerate(('A','B','C','A','B','C','A')):
            history.observe(track(name),now)
            self.assertEqual(history.on_repeat,now==6)
        self.assertEqual(len(history.recent),7)
        history.observe(track('A',position=1),7)
        history.observe(track('A',position=1,state='paused'),8)
        history.observe(track('A',position=1),9)
        self.assertEqual(len(history.recent),7)
        self.assertTrue(history.on_repeat)
        # Lifetime count remains, but a song can leave the recent rotation.
        for now in range(10,20):history.observe(track(f'Other {now}'),now)
        history.observe(track('A'),20)
        self.assertEqual(history.counts[song_key(title='A')],4)
        self.assertFalse(history.on_repeat)
        self.assertEqual(len(history.recent),10)

    def test_repeat_window_persists_and_old_history_keeps_counts_without_invented_starts(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'music-history.json'
            history=ListeningHistory(path)
            for now,name in enumerate(('A','B','A','B','A')):history.observe(track(name),now)
            reloaded=ListeningHistory(path)
            reloaded.observe(track('A',position=1),0)
            self.assertTrue(reloaded.on_repeat)
            self.assertEqual(len(reloaded.recent),5)
            self.assertEqual(reloaded.counts[song_key(title='A')],3)
            raw=json.loads(path.read_text());raw['version']=1;raw.pop('recent')
            path.write_text(json.dumps(raw))
            legacy=ListeningHistory(path);legacy.observe(track('A',position=1),0)
            self.assertEqual(legacy.counts[song_key(title='A')],3)
            self.assertFalse(legacy.on_repeat)
            self.assertEqual(legacy.recent,[])
            self.assertIsNone(legacy.error)

    def test_buckets_expose_words_only_and_mid_song_is_new(self):
        self.assertEqual([listening_label(n) for n in (0,1,2,3,4,9,10,24,25,49,50,999)],
                         ['new','new','rare','rare','occasional','occasional','regular',
                          'regular','favorite','favorite','favorite','favorite'])
        history=ListeningHistory()
        self.assertEqual(history.observe(track(position=60),0),'new')
        self.assertEqual(history.counts,{})
        raw={'snapshot':{},'music':{'state':'playing','listening_history':'frequent',
                                   'play_count':35,'listening_history_error':'private'}}
        self.assertEqual(model_turn(raw)['music'],{'state':'playing','listening_history':'favorite'})
        raw['music']['listening_history']='heavy_rotation'
        self.assertEqual(model_turn(raw)['music']['listening_history'],'favorite')

    def test_poll_pause_seek_gap_and_reconnect_do_not_increment(self):
        history=ListeningHistory();song=track();key=song.song_key
        self.assertEqual(history.observe(song,0),'new')
        for now,reading in ((1,track(position=1)),(2,track(position=1,state='paused')),
                            (3,track(position=1)),(4,track(position=90)),(5,track(position=0)),
                            (6,Playback('unavailable')),(7,track(position=1)),(100,track(position=0))):
            history.observe(reading,now)
        history.break_continuity();history.observe(track(position=0),101)
        self.assertEqual(history.counts[key],1)

    def test_new_tracks_explicit_restarts_and_natural_repeats_count(self):
        history=ListeningHistory();key=track().song_key
        history.observe(track(),0)
        history.observe(Playback('stopped'),1)
        self.assertEqual(history.observe(track(),2),'rare')
        history.observe(track(position=179),3)
        history.observe(track(position=0),4)
        self.assertEqual(history.counts[key],3)
        history.observe(track('Other song',0,index=1),5)
        self.assertEqual(history.observe(track(position=1),6),'occasional')
        history.observe(track(position=70),7)
        history.observe(track(position=0,index=2),8)
        self.assertEqual(history.counts[key],5)

    def test_paused_attachment_and_missing_positions_do_not_claim_starts(self):
        history=ListeningHistory()
        history.observe(track(state='paused'),0)
        history.observe(track(),1)
        history.observe(track('Other',None),2)
        history.observe(track('Other',0),3)
        self.assertEqual(history.counts,{})

    def test_persistence_hides_names_paths_and_does_not_recount_on_reload(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'music-history.json'
            history=ListeningHistory(path)
            history.observe(track(),0)
            reloaded=ListeningHistory(path)
            self.assertEqual(reloaded.observe(track(position=1),0),'new')
            self.assertEqual(reloaded.counts[track().song_key],1)
            reloaded.observe(Playback('stopped'),1)
            reloaded.observe(track(),2)
            raw=path.read_text()
            self.assertNotIn('Paper Moon',raw)
            self.assertEqual(json.loads(raw)['counts'][track().song_key],2)

    def test_invalid_store_is_preserved_and_tracking_survives(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'music-history.json';path.write_text('bad json')
            history=ListeningHistory(path)
            self.assertEqual(history.observe(track(),0),'new')
            self.assertEqual(path.read_text(),'bad json')
            self.assertEqual(history.error,'load_failed_original_preserved')

    def test_optional_filename_changes_do_not_inflate_counts(self):
        history=ListeningHistory();song=track()
        history.observe(song,0)
        recovered=replace(song,song_key=song_key(r'C:\Music\song.mp3'),
                          identity=(*song.identity[:4],r'C:\Music\song.mp3'),position_seconds=1)
        history.observe(recovered,1)
        self.assertEqual(history.counts[recovered.song_key],1)
        history.observe(song,2)
        self.assertEqual(history.counts[song.song_key],1)


class MonitorHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeat_flag_reaches_current_and_recorded_context_without_raw_counts(self):
        now=[0];reader=Mock()
        monitor=MusicMonitor(enabled=True,reader=reader,clock=lambda:now[0])
        for index,name in enumerate(('A','B','A','B','A')):
            now[0]=index*1.1;reader.read.return_value=track(name);await monitor.poll()
        self.assertTrue(monitor.snapshot()['on_repeat'])
        projected=model_turn({'snapshot':{},'music':monitor.snapshot(),
                             'music_during_recording':monitor.during_recording(4.5,4.6)})
        self.assertTrue(projected['music']['on_repeat'])
        self.assertTrue(projected['music_during_recording']['start']['on_repeat'])
        self.assertNotIn('recent',projected['music'])
        now[0]=5.5;reader.read.return_value=track('C');await monitor.poll()
        self.assertNotIn('on_repeat',monitor.snapshot())
        self.assertNotIn('on_repeat',model_turn({'snapshot':{},'music':{'on_repeat':False}})['music'])

    async def test_untimed_lookup_uses_recorded_song_after_playback_changes(self):
        now=[0];reader=Mock()
        reader.read.return_value=replace(track(position=40),lyric_source=TrackTags(
            untimed=('Silver river carry us home','Down to the village')))
        monitor=MusicMonitor(enabled=True,reader=reader,clock=lambda:now[0])
        await monitor.poll()
        recording=monitor.during_recording(.1,.5)
        now[0]=1.1
        reader.read.return_value=replace(track('Other',1,index=1),tags={
            'lyrics':[{'at_seconds':1,'text':'Other timed lyric'}]})
        await monitor.poll()
        music=monitor.snapshot()
        monitor.add_lyric_match('carry US home!',music,recording)
        projected=model_turn({'snapshot':{},'music':music,'music_during_recording':recording})
        from bwv.music_context import music_blocks
        excerpts=[item for block in music_blocks(projected) for item in block.get('lyrics',[])]
        self.assertEqual(len(excerpts),1)
        self.assertEqual(excerpts[0]['match'],'words')
        self.assertIn('carry us home',excerpts[0]['text'].lower())
        unknown=monitor.during_recording(-10,-5)
        monitor.add_lyric_match('Other timed lyric',monitor.snapshot(),unknown)
        self.assertNotIn('lyrics',unknown['at_start'])

    async def test_history_label_is_captured_for_voice_and_clear_preserves_counts(self):
        now=[0];reader=Mock();reader.read.return_value=track()
        monitor=MusicMonitor(enabled=True,reader=reader,clock=lambda:now[0])
        await monitor.poll()
        self.assertEqual(monitor.snapshot()['listening_history'],'new')
        now[0]=1.1;reader.read.return_value=Playback('stopped');await monitor.poll()
        now[0]=2.2;reader.read.return_value=track();await monitor.poll()
        self.assertEqual(monitor.snapshot()['listening_history'],'rare')
        self.assertEqual(monitor.during_recording(.1,2.3)['at_start']['listening_history'],'new')
        monitor.clear();now[0]=3.3;await monitor.poll()
        self.assertEqual(monitor.snapshot()['listening_history'],'rare')
