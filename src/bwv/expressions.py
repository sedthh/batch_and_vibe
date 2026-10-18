"""Fixed speech-to-gesture vocabulary; never interpret memory or tool output."""
from types import MappingProxyType
import json
import re
from .action_probe import HELD_GESTURES


# Fixed gesture recipes; engine offers determine availability for this creature.
EXPRESSION_GESTURES = MappingProxyType({
    '😕':'look_confused', '👋':'wave', '💃':'dance', '🤧':'sneeze',
    '🥶':'shiver', '🐾':'scratch', '🥵':'show_hotness', '🪑':'sit_down',
    '👀':'look_at_player', '🐺':'howl', '😂':'laugh', '😭':'cry',
    '💡':'show_realization', '🙏':'pray',
    '🔍':'look_at_held', '👂':'shake_held', '👃':'smell_held', '🫳':'stroke_held',
})
GESTURE_ACTIONS = frozenset(EXPRESSION_GESTURES.values()) | {'dance_with_villagers'}
RECOVERED_GESTURES = frozenset({'howl','laugh','cry','show_realization','pray'})
EXPRESSION_CONTRACT = """Hidden body-gesture cues: include at most one in say (or direct answer without say).
The bridge detects cues anywhere in that text. If several are present, only the
first supported cue in reading order is selected; unsupported emojis are ignored.
Fixed vocabulary (defined here, not repeated in observation messages):
😕 look_confused: make a brief puzzled/confused body gesture.
👋 wave: wave toward the player to greet or say goodbye.
💃 dance: dance to celebrate or play. The host chooses the villagers' group dance
with a one-in-three chance when a nearby villager target is available; otherwise
it uses the solo dance. No extra tool call or villager ID is needed for this cue.
🤧 sneeze: perform the sneezing animation; does not cause illness.
🥶 shiver: visibly shiver as if cold; does not change temperature.
🐾 scratch: scratch or groom the body; a leopard may lick itself instead.
🥵 show_hotness: pant or express heat/discomfort; does not cause heat or fire.
🪑 sit_down: sit on the ground; the posture varies with species.
👀 look_at_player: turn attention toward the player's camera.
🐺 howl: request the species' body howl animation, if its clip exists.
😂 laugh: request the species' whole-body laugh, not a facial-only expression.
😭 cry: request the species' body crying animation, if its clip exists.
💡 show_realization: request the body aha/realization animation.
🙏 pray: request the native body prayer animation in this fictional game.
🔍 look_at_held: look closely at the object in your own hand.
👂 shake_held: raise your held object to your ear and shake/listen to it.
👃 smell_held: bring your held object toward your nose and sniff it.
🫳 stroke_held: gently stroke the object in your own hand.
These four held-item cues require you to be holding something. They never fetch
an object and do not refer to the god's hand, self-grooming or being petted.
An inspection of your own held item normally picks examine, shake or smell at
random, never stroke. Include one of these four cues with that inspection to
choose its exact gesture instead of the random pick; this is still ONE action.
The cue alone plays the gesture without inspecting or returning item details.
Most cues request animations, not direct changes to needs, beliefs, alignment or
mood. The group-dance variant is a native social activity that recruits villagers.
Appearance and clip availability can vary by species; requests can fail.
No facial-only commands are available.
When God explicitly asks for a supported gesture, REQUEST IT with its cue; do not
replace it with a description, imagined tail-wagging, barking, or an unrelated tool.
For "wave hello to me", return answer="👋", say=null and
decision={"candidate":null}, with no do_action call. This requests a real wave.
If you include words, place the cue in say, or answer when say is null.
Never select wake_up merely to greet; the host handles waking for a gesture.
Use unsolicited cues sparingly; explicit gesture requests are not unsolicited.
No per-message gesture availability list is needed. Use this fixed vocabulary;
the bridge checks playback availability and silently skips cues the engine cannot
play. A skipped cue does not cancel the accompanying speech. Do not invent an
availability restriction or an apology just because a cue might not play.
No other emojis. UI strips emojis; empty/whitespace/emoji-only replies stay silent.
If any physical action tool selected an action (including held-item inspection),
use no emoji cues in answer or say except the held-item choice described above.
That choice changes the inspection's gesture; it never adds a second action.
This applies even though the final JSON decision is {"candidate":null}; the
host retains the tool selection. Legacy non-null decisions also exclude cues.
Competing actions skip gestures. The host may briefly wait for an eligible moment;
blocked, unavailable or expired gestures do not execute. Requesting a cue alone
does not confirm execution; use later engine feedback or the player's report."""


# Remove pictographs, flags, keycaps and their presentation/joining components
# from display copies. Keep the raw provider response in diagnostic memory.
_PICTOGRAPH = (r'[\U0001f000-\U0001faff\u2600-\u27bf\u00a9\u00ae\u203c\u2049'
              r'\u2122\u2139\u2194-\u2199\u21a9\u21aa\u231a\u231b\u2328\u23cf'
              r'\u23e9-\u23f3\u23f8-\u23fa\u24c2\u25aa\u25ab\u25b6\u25c0'
              r'\u25fb-\u25fe\u2934\u2935\u2b05-\u2b07\u2b1b\u2b1c\u2b50'
              r'\u2b55\u3030\u303d\u3297\u3299]')
_EMOJI_PART = _PICTOGRAPH + r'[\ufe0e\ufe0f\U0001f3fb-\U0001f3ff\U000e0020-\U000e007f]*'
_DISPLAY_EMOJI = re.compile(r'[#*0-9]\ufe0f?\u20e3|' + _EMOJI_PART + r'(?:\u200d' + _EMOJI_PART + r')*')
_DISPLAY_CONTROLS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')


def _protocol_fragment(text):
    value=text.strip()
    if not value:return False
    if value.startswith('```'):return True
    if value[0] not in '{[':return False
    if all(c in '{}[],:"` \t\r\n' for c in value):return True
    if re.match(r'^\{\s*"(?:answer|say|decision|candidate|tool_calls|function|memory)"\s*:',value):
        return True
    try:return isinstance(json.loads(value),(dict,list))
    except ValueError:return False


def malformed_speech(text):
    """Detect structural output accidentally placed inside a speech string."""
    return isinstance(text,str) and _protocol_fragment(_DISPLAY_EMOJI.sub('',text))


def display_speech(text):
    if not text.strip():
        return ''
    # A malformed apostrophe was observed as U+0019 followed by a semicolon.
    # Repair only that known sequence; preserve ordinary multilingual text.
    cleaned = text.replace('\x19;', "'").translate(str.maketrans({'\u2018':"'", '\u2019':"'"}))
    cleaned = _DISPLAY_CONTROLS.sub('', cleaned)
    cleaned = _DISPLAY_EMOJI.sub('', cleaned)
    if _protocol_fragment(cleaned):return ''
    if cleaned != text:
        cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned).strip()
    return cleaned


def display_reply(reply):
    return {key: display_speech(value) if key in ('answer', 'say') else value
            for key, value in reply.items()}


def expression_from_speech(text, *, reenable_cut_content=True):
    """Select the first supported cue anywhere in newly validated speech, once."""
    if not isinstance(text, str):
        return None
    # Read text order, not mapping order. Presentation selectors and skin-tone
    # modifiers do not change a supported base emoji's meaning.
    for character in text:
        if character in EXPRESSION_GESTURES and (reenable_cut_content
                or EXPRESSION_GESTURES[character] not in RECOVERED_GESTURES):
            return {'emoji': character, 'candidate': EXPRESSION_GESTURES[character]}
    return None


def speech_expression(text, decision, *, dispatcher=None, reenable_cut_content=True):
    """Use the supplied response-bound executor; never mint native capabilities.

    An enabled executor supplies a dispatcher bound to this response's original
    request. This helper deliberately cannot create an offer itself. A waiting
    intention or transport receipt is not evidence that a gesture happened.
    """
    cue = expression_from_speech(text, reenable_cut_content=reenable_cut_content)
    if cue is None:
        return None
    if decision != {'candidate': None}:
        if cue['candidate'] in HELD_GESTURES and decision == {'candidate':cue['candidate']}:
            return {**cue, 'status': 'selected_with_inspection'}
        return {**cue, 'status': 'skipped_action_conflict'}
    if dispatcher is None:
        return {**cue, 'status': 'skipped_executor_unavailable'}
    try:
        dispatch = getattr(dispatcher, 'dispatch_expression', dispatcher.dispatch)
        result = dispatch({'candidate': cue['candidate']})
        return {**cue, 'status': result['status'],
                **({'intent_id':result['intent_id']} if 'intent_id' in result else {})}
    except (ValueError, OSError, TimeoutError):
        # An unknown receipt must never be retried. Speech remains independent.
        return {**cue, 'status': 'not_confirmed_no_retry'}
