"""Synthetic lyric service responses: no network or copyrighted song text."""
from concurrent.futures import Future
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from bwv.music import Playback
from bwv.music_lyrics import LyricsLookup, query_identity, select_lyrics
from bwv.music_tags import TrackTags
from bwv.model_projection import model_turn


def reading(title='Paper Moon',position=44):
    return Playback('playing',title,(10,77,0),position,180,
                    {'title':title,'artist':'Test Artist'},lyric_source=TrackTags({'title':title,'artist':'Test Artist'}))


def record(**changes):
    return dict({'artistName':'Test Artist','trackName':'Paper Moon','duration':180,
                 'syncedLyrics':'[00:40]Silver river\n[00:44]Carry us home',
                 'plainLyrics':'Silver river\nCarry us home'},**changes)


class LookupTests(unittest.TestCase):
    def test_query_cleans_video_labels_but_preserves_mix_and_version(self):
        self.assertEqual(query_identity({'artist':'Test Artist','title':'Test Artist - Paper Moon? (Official Video)'},225),
                         {'artist_name':'Test Artist','track_name':'Paper Moon?','duration':225})
        self.assertEqual(query_identity({'artist':'Artist','title':'Artist Song (Live)'},100)['track_name'],'Song (Live)')
        self.assertIsNone(query_identity({'title':'Missing Artist'},100))
        self.assertIsNone(query_identity({'artist':'Artist','title':'Song'},float('nan')))

    def test_exact_song_match_and_duration_control_timed_vs_plain(self):
        q=query_identity(reading().tags,180)
        self.assertTrue(select_lyrics([record()],q)['synced'])
        self.assertIsNone(select_lyrics([record(duration=195)],q)['synced'])
        self.assertTrue(select_lyrics([record(duration=195)],q)['plain'])
        self.assertEqual(select_lyrics([record(artistName='Other Artist'),record(trackName='Paper Moon (Remix)')],q),
                         {'synced':None,'plain':None})

    def test_background_lookup_cache_and_old_track_results_do_not_cross_tracks(self):
        with TemporaryDirectory() as directory:
            lookup=LyricsLookup(directory,enabled=True,clock=lambda:100)
            future=Future();lookup.pool=Mock();lookup.pool.submit.side_effect=[future,Future()]
            original=reading()
            self.assertIs(lookup.enrich(original),original)
            lookup.enrich(original)
            lookup.pool.submit.assert_called_once()
            future.set_result({'synced':record()['syncedLyrics'],'plain':record()['plainLyrics']})
            other=reading('Different Song')
            enriched_other=lookup.enrich(other)
            self.assertNotIn('lyrics',enriched_other.tags)
            enriched=lookup.enrich(original)
            self.assertEqual(enriched.tags['lyrics'][-1]['text'],'Carry us home')
            self.assertIs(lookup.enrich(original).lyric_source,enriched.lyric_source)
            self.assertEqual(original.tags,{'title':'Paper Moon','artist':'Test Artist'})
            lookup.close()
            cached=LyricsLookup(directory,enabled=True,clock=lambda:101)
            self.assertTrue(cached.enrich(original).lyric_source.lines)
            self.assertIsNone(cached.pool)
            cached.close()

    def test_disabled_embedded_and_unknown_tracks_never_request(self):
        lookup=LyricsLookup();lookup.pool=Mock()
        lookup.enrich(reading());lookup.pool.submit.assert_not_called()
        lookup.enabled=True
        for source in (TrackTags(lines=((0,'Embedded timed words'),)),
                       TrackTags(untimed=('Embedded plain words',))):
            lookup.enrich(replace(reading(),lyric_source=source))
        lookup.enrich(replace(reading(),tags={},lyric_source=None))
        lookup.pool.submit.assert_not_called()
        lookup.close();lookup.enrich(reading());lookup.pool.submit.assert_not_called()

    def test_rate_limit_honors_retry_after_and_does_not_issue_search(self):
        opener=Mock(side_effect=HTTPError('https://lrclib.net/api/get',429,'Limited',{'Retry-After':'120'},None))
        lookup=LyricsLookup(enabled=True,opener=opener,clock=lambda:100)
        q=query_identity(reading().tags,180)
        self.assertIsNone(lookup._fetch(q))
        self.assertGreaterEqual(lookup.next_request,220)
        self.assertIsNone(lookup._fetch(q))
        opener.assert_called_once()

    def test_request_is_identified_bounded_and_sends_no_file_or_player_words(self):
        response=BytesIO(json.dumps(record()).encode());response.headers={'Content-Type':'application/json'}
        opener=Mock(return_value=response)
        lookup=LyricsLookup(enabled=True,opener=opener)
        result=lookup._fetch(query_identity(reading().tags,180))
        self.assertTrue(result['synced'])
        request=opener.call_args.args[0]
        self.assertIn('artist_name=Test+Artist',request.full_url)
        self.assertIn('BlackAndWhiteLLMCreatureMod',request.get_header('User-agent'))
        self.assertNotIn('filename',request.full_url)
        self.assertEqual(opener.call_args.kwargs['timeout'],4)


class RefrainTests(unittest.TestCase):
    def test_plain_refrain_is_short_repeated_and_untimed(self):
        source=TrackTags(untimed=('[Chorus]','Silver river carry us home','Beneath the paper moon',
                                  'A different verse goes here','Silver river carry us home','Beneath the paper moon'))
        excerpt=source.at(90)['lyrics'][0]
        self.assertEqual(excerpt['match'],'refrain')
        self.assertNotIn('at_seconds',excerpt)
        self.assertIn('Silver river',excerpt['text'])
        self.assertLessEqual(len(excerpt['text'].encode()),320)
        self.assertNotIn('lyrics',TrackTags(untimed=('Unique line one','Unique line two')).at(0))
        self.assertNotIn('lyrics',TrackTags(untimed=('[Chorus]','Yeah','Yeah','[Chorus]')).at(0))

    def test_player_word_match_takes_priority_over_generic_refrain(self):
        raw={'snapshot':{},'music_during_recording':{'duration_seconds':2,
             'end':{'title':'Song','lyrics':[{'text':'Generic refrain','match':'refrain'}]},
             'start':{'title':'Song','lyrics':[{'text':'Specific matching verse','match':'words'}]}}}
        projected=model_turn(raw)
        self.assertNotIn('lyrics',projected['music_during_recording'].get('end',{}))
        self.assertEqual(projected['music_during_recording']['start']['lyrics'][0]['match'],'words')
        self.assertEqual(projected,model_turn(projected))
