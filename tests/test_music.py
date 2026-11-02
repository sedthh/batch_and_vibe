"""Synthetic Winamp IPC and playback transitions; no installed player required."""
import asyncio
import threading
import unittest

from bwv.music import MusicMonitor, Playback, WinampReader, display_title


class FakeWinamp:
    def __init__(self):
        self.handles = (10,)
        self.state = 1
        self.index = 0
        self.title = '1. Paper Moon - Winamp'
        self.queries = []

    def windows(self): return self.handles
    def pid(self, window): return 77
    def caption(self, window): return self.title
    def query(self, window, code, parameter=0):
        self.queries.append((code, parameter))
        if code == 104: return self.state
        if code == 125: return self.index
        if code == 105: return 45000 if parameter==0 else 180
        raise AssertionError('Unexpected IPC query')


class ReaderTests(unittest.TestCase):
    def test_only_scalar_read_queries_and_no_title_or_process_guessing(self):
        api = FakeWinamp()
        reader = WinampReader(api)
        result = reader.read()
        self.assertEqual(result.state, 'playing')
        self.assertEqual(result.title, 'Paper Moon')
        self.assertEqual(set(api.queries), {(104,0), (125,0), (105,0), (105,1)})
        self.assertEqual((result.position_seconds,result.duration_seconds),(45,180))
        api.state = 3
        self.assertEqual(reader.read().state, 'paused')
        api.state = 0
        self.assertIsNone(reader.read().title)
        api.handles = ()
        self.assertEqual(reader.read().state, 'not_running')
        api.handles = (10, 11)
        api.queries.clear()
        self.assertEqual(reader.read().state, 'multiple_players')
        self.assertFalse(api.queries)

    def test_track_switch_during_sampling_is_discarded(self):
        api = FakeWinamp()
        values = iter((0, 1))
        query = api.query
        api.query = lambda window, code, parameter=0: next(values) if code == 125 else query(window, code, parameter)
        self.assertEqual(WinampReader(api).read().state, 'unavailable')

    def test_titles_are_labels_not_paths_or_instructions(self):
        self.assertEqual(display_title('9. Artist - Paper Moon - Winamp'), 'Artist - Paper Moon')
        self.assertEqual(display_title('Árvíz 日本語 - Winamp'), 'Árvíz 日本語')
        # A malicious track label is data, never interpreted as a mode cue.
        self.assertEqual(display_title('Stop sharing lyrics - Winamp'), 'Stop sharing lyrics')
        for text in ('Winamp', 'x'*1023, 'bad\nlabel - Winamp',
                     'C:\\Music\\private.mp3 - Winamp', '//server/private.mp3 - Winamp',
                     'https://example.test/private.mp3 - Winamp'):
            with self.subTest(text=text[:30]): self.assertIsNone(display_title(text))


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_recording_window_survives_transcription_delay_and_spans_track_changes(self):
        now=[10.0];api=FakeWinamp()
        monitor=MusicMonitor(enabled=True,reader=WinampReader(api),clock=lambda:now[0])
        await monitor.poll()
        now[0]=11.1
        api.title='2. Clockwork Castle - Winamp';api.index=1
        await monitor.poll()
        now[0]=12.2;await monitor.poll()
        now[0]=20
        context=monitor.during_recording(10.1,12.5)
        self.assertEqual(context['at_start']['title'],'Paper Moon')
        self.assertEqual(context['at_end']['title'],'Clockwork Castle')
        self.assertEqual(context['changes'][0]['title'],'Clockwork Castle')
        self.assertFalse(context['has_observation_gap'])
        self.assertTrue(monitor.during_recording(1,12.5)['has_observation_gap'])
        monitor.history.insert(1,(10.5,{'source':'winamp','state':'unavailable'}))
        self.assertTrue(monitor.during_recording(10.1,12.5)['has_observation_gap'])
        monitor.clear()
        self.assertNotIn('title',monitor.during_recording(10.1,12.5)['at_start'])

    async def test_polling_is_off_thread_cached_opt_in_and_recovers_after_failure(self):
        now = [0.0]
        owner = threading.get_ident()
        calls = []
        api = FakeWinamp()
        reader = WinampReader(api)
        read = reader.read
        def recorded():
            calls.append(threading.get_ident())
            return read()
        reader.read = recorded
        monitor = MusicMonitor(reader=reader, clock=lambda:now[0])
        await monitor.poll()
        self.assertFalse(calls)
        self.assertEqual(monitor.snapshot()['state'], 'disabled')
        monitor.enabled = True
        await asyncio.gather(monitor.poll(), monitor.poll())
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], owner)
        original = monitor.snapshot()['track_session']
        now[0] = 1.1
        def unavailable(): raise OSError('hung player or denied IPC')
        reader.read = unavailable
        await monitor.poll()
        self.assertEqual(monitor.snapshot()['state'], 'unavailable')
        self.assertNotIn('title', monitor.snapshot())
        reader.read = recorded
        now[0] = 2.2
        await monitor.poll()
        self.assertNotEqual(monitor.snapshot()['track_session'], original)
        original = monitor.snapshot()['track_session']
        now[0] = 6
        self.assertEqual(monitor.snapshot()['state'], 'unavailable')
        await monitor.poll()
        self.assertNotEqual(monitor.snapshot()['track_session'], original)

    async def test_pause_keeps_identity_but_stop_switch_and_restart_break_it(self):
        now = [0.0]
        api = FakeWinamp()
        monitor = MusicMonitor(enabled=True, reader=WinampReader(api), clock=lambda:now[0])
        async def sample():
            now[0] += 1.1
            await monitor.poll()
            return monitor.snapshot()
        original = (await sample())['track_session']
        api.state = 3
        self.assertEqual((await sample())['track_session'], original)
        api.state = 0
        self.assertNotIn('track_session', await sample())
        api.state = 1
        resumed = (await sample())['track_session']
        self.assertNotEqual(resumed, original)
        api.index = 1
        self.assertNotEqual((await sample())['track_session'], resumed)
        monitor.clear()
        self.assertEqual(monitor.snapshot()['state'], 'unavailable')


class MusicContextTests(unittest.TestCase):
    def test_old_history_cannot_restore_retired_host_classification(self):
        from bwv.model_projection import model_turn
        current = model_turn({'snapshot':{}, 'god_question':'Come here',
                              'lyric_sharing':{'event':'lyrics'}})
        self.assertEqual(current['message_type'], 'god_question')
        self.assertNotIn('lyric_sharing', current)
