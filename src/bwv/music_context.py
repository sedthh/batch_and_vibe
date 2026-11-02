"""Compact model observations; acquisition details stay in diagnostics."""
import math
from .music_tags import MAX_LYRIC_BYTES, short_text
from .music_history import LISTENING_LABELS


def lyric_excerpt(value):
    if not isinstance(value,list):return []
    result = []
    remaining = MAX_LYRIC_BYTES
    for item in value[:2]:
        if not isinstance(item,dict):continue
        at = item.get('at_seconds')
        timed=type(at) in (int,float) and math.isfinite(at) and at>=0
        if not timed and item.get('match') not in ('words','refrain'):continue
        text = short_text(item.get('text'),remaining)
        if text:
            result.append({'at_seconds':at,'text':text} if timed else {'text':text,'match':item['match']})
            remaining -= len(text.encode('utf-8'))
    return result


def playback(value):
    if not isinstance(value, dict):return {}
    result = {key:value[key] for key in ('state','title','position_seconds','duration_seconds')
              if key in value}
    # Saved conversations from the first reader used milliseconds and length.
    if 'position_seconds' not in result and type(value.get('position_ms')) in (int,float):
        result['position_seconds'] = round(value['position_ms']/1000, 3)
    if 'duration_seconds' not in result and 'length_seconds' in value:
        result['duration_seconds'] = value['length_seconds']
    for key,limit in (('title',200),('artist',120),('genre',60)):
        if key in value:
            text = short_text(value[key],limit)
            if text:result[key] = text
            else:result.pop(key,None)
    bpm = value.get('tempo_bpm')
    if type(bpm) in (int,float) and math.isfinite(bpm) and 0 < bpm <= 1000:
        result['tempo_bpm'] = bpm
    lyrics = lyric_excerpt(value.get('lyrics'))
    if lyrics:result['lyrics'] = lyrics
    history=value.get('listening_history')
    if history in ('frequent','heavy_rotation'):history='favorite'
    if history in LISTENING_LABELS:
        result['listening_history']=history
    if value.get('on_repeat') is True:result['on_repeat']=True
    return result


def point(value):
    return {key:item for key,item in playback(value).items()
            if key in ('state','title','artist','position_seconds','lyrics','listening_history','on_repeat')}


def recording(value):
    if not isinstance(value, dict):return {}
    start, end = point(value.get('at_start',value.get('start'))), point(value.get('at_end',value.get('end')))
    result = {'duration_seconds':value.get('recording_seconds',value.get('duration_seconds'))}
    if start:result['start'] = start
    changes = [{'at_seconds':item.get('seconds_into_recording',item.get('at_seconds')), **point(item)}
               for item in value.get('changes',[])]
    if changes:result['changes'] = changes
    if end:
        last = changes[-1] if changes else start
        if all(end.get(key) == last.get(key) for key in ('title','artist','state','lyrics','listening_history','on_repeat')):
            if 'position_seconds' in end:result['end_position_seconds'] = end['position_seconds']
        else:result['end'] = end
    elif 'end_position_seconds' in value:
        result['end_position_seconds'] = value['end_position_seconds']
    if value.get('has_observation_gap') or value.get('incomplete'):result['incomplete'] = True
    if value.get('changes_truncated'):result['changes_truncated'] = True
    return result


def native(value):
    if not isinstance(value, dict):return {}
    result = {key:value[key] for key in ('mood_raw','feedback_raw') if key in value}
    mood = value.get('mood') or {}
    for source,dest in (('mood_engine_units','mood_raw'), ('recent_feedback_engine_units','feedback_raw')):
        if source in mood:result[dest] = mood[source]
    line = value.get('line_input') or {}
    if line.get('active') is True or 'beat' in line or 'tempo_bpm' in line:
        compact = {}
        for source,dest in (('recent_beat_detected','beat'), ('detected_tempo_bpm','tempo_bpm'),
                            ('beat','beat'), ('tempo_bpm','tempo_bpm')):
            if source in line:compact[dest] = line[source]
        if compact:result['line_input'] = compact
    return result


def compact_music_turn(current):
    """Also accepts already-projected turns when rebuilding history/tool rounds."""
    if not isinstance(current, dict):return
    for key in ('music','music_at_submission'):
        if key in current:current[key] = playback(current[key])
    if current.get('music_at_submission') == current.get('music'):
        current.pop('music_at_submission',None)
    if 'music_during_recording' in current:
        current['music_during_recording'] = recording(current['music_during_recording'])
    old = current.get('native_music')
    if isinstance(old, dict):
        # Migrate the old misplaced state in saved turns, too.
        if type(old.get('dance_animation_active')) is bool and isinstance(current.get('snapshot'),dict):
            current['snapshot'].setdefault('activity',{})['dancing'] = old['dance_animation_active']
        compact = native(old)
        if compact:current['native_music'] = compact
        else:current.pop('native_music',None)
    # One lyric allowance across the entire turn, prioritizing the captured voice
    # window over a potentially different song playing after transcription.
    remaining = MAX_LYRIC_BYTES
    count = 0
    seen = set()
    selected = False
    blocks=list(music_blocks(current))
    # A passage matching the player's words is more relevant than a general refrain.
    blocks.sort(key=lambda block:all(item.get('match')=='refrain' for item in block.get('lyrics',[])))
    for block in blocks:
        kept = []
        for item in lyric_excerpt(block.pop('lyrics',None)):
            identity = (block.get('title'),block.get('artist'),item.get('at_seconds'),item['text'])
            if selected or identity in seen or count >= 2:continue
            text = short_text(item['text'],remaining)
            if text:
                kept.append(dict(item,text=text))
                remaining -= len(text.encode('utf-8'))
                count += 1
                seen.add(identity)
        if kept:
            block['lyrics'] = kept
            selected = True
    # Removing duplicate excerpts can make previously distinct points identical.
    if current.get('music_at_submission') == current.get('music'):
        current.pop('music_at_submission',None)
    if 'music_during_recording' in current:
        current['music_during_recording'] = recording(current['music_during_recording'])


def music_blocks(value):
    if not isinstance(value,dict):return
    recording = value.get('music_during_recording')
    if isinstance(recording,dict):
        for key in ('end','start','at_end','at_start'):
            if isinstance(recording.get(key),dict):yield recording[key]
        for block in reversed(recording.get('changes') or []):
            if isinstance(block,dict):yield block
    for key in ('music_at_submission','music'):
        if isinstance(value.get(key),dict):yield value[key]


def omit_historical_music_lyrics(value):
    for block in music_blocks(value):block.pop('lyrics',None)
