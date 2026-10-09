"""Read older editable sections without making bridge rules configurable again."""
import warnings
import re

SECTION_HEADERS = {'game_context': 'Game background',
 'role_and_world': 'Character and relationship',
 'learning': 'Learning and animal behavior',
 'behavior': 'Autonomy',
 'communication': 'Speaking style'}

LEGACY_REPLACEMENTS = (('Do not assume ownership\nor allegiance unless observed. ', ''),
 ('That dialogue is NOT supplied\n'
  'to you: do not assume you heard it or know their stated goals unless the player\n'
  'explains them. ',
  ''),
 ('This does not give you omniscience: rely on supplied observations,\n'
  'not imagined distant events.\n',
  ''),
 ('Use cast_miracle\n'
  "with your miracle_attempts_available map, independently of the god's available spells.\n",
  "Your learned miracles are independent of the god's available spells.\n"),
 ('Game knowledge explains possibilities;\n'
  'only the currently offered tools and observed abilities authorize actions.',
  ''),
 ('in answer and say', 'when speaking'),
 ("Help with the player's in-game requests honestly. If unsure how to help, use\n"
  'the offered action menu, or inspect_self/inspect_surrounding for details. Use available\n'
  'actions, not promises of obedience. If the exact request is unavailable, look for\n'
  "the closest available alternative that helps the player's goal. Explain what you\n"
  'will do instead, then attempt it using an offered tool or action. If no useful\n'
  'alternative exists or you do not know how, say so. Never invent capabilities or\n'
  'claim an action happened without evidence; report success only when confirmed.\n'
  'When God says "come to me", approach that hand, using only an offered action.\n',
  "Help with the player's in-game requests honestly and choose a useful alternative\n"
  'when the exact request is not possible. When God says "come to me", they mean\n'
  'their hand.\n'),
 ('Do not guess which behavior was trained. ', ''),
 ('Species, growth and condition affect abilities; use observed traits, not invented\nstats.',
  'Species, growth and condition affect abilities.'),
 (' Current readings override remembered mastery.', ''),
 ('Learning events indicate observations, not proven skill gains. ', ''),
 ('; knowing a spell or ending its casting plan does not prove success.', '.'),
 ('For routine unsolicited turns, return an empty answer and no say. Answer direct\n'
  'God questions clearly; this silence rule does not prevent autonomous tool use.\n'
  'Silence is a valid choice: return answer as an empty string and omit say (or use\n'
  'null if the schema requires it). Using a useful tool without spoken commentary\n'
  'is enough; do not invent work to fill a turn. A supported hidden emoji cue alone\n'
  'is also valid under the gesture rules, without accompanying words.\n',
  'Answer direct questions clearly. Silence and acting without commentary are valid\n'
  'choices; do not invent work to fill a turn.\n'),
 ('\nSpeech describes intentions, not proof that a proposed action has happened.', ''))

def migrate_prompt_sections(data, default_general):
    if type(data) is not dict:
        raise ValueError('Invalid prompt settings')
    if data.keys() <= {'general'}:
        return dict(data)
    legacy_keys = set(SECTION_HEADERS) | {'observation_guidance', 'memory'}
    if not data.keys() <= legacy_keys:
        raise ValueError('Unknown prompt section; only general is editable')
    for value in data.values():
        if (not isinstance(value, str) or not value.strip() or len(value) > 6000
                or any(ord(c) < 32 and c not in '\n\r\t' for c in value)):
            raise ValueError('Invalid legacy prompt section')
    pieces=[]
    headers=list(SECTION_HEADERS.items())
    for i, (key, title) in enumerate(headers):
        heading=re.search(r'(?m)^(?:## )?'+re.escape(title)+r'\n+',default_general)
        start=heading.end()
        end=(re.search(r'(?m)^(?:## )?'+re.escape(headers[i+1][1])+r'\n',default_general).start()
             if i+1 < len(headers) else len(default_general))
        text=data.get(key, default_general[start:end])
        for previous, replacement in LEGACY_REPLACEMENTS:
            text=text.replace(previous, replacement)
        pieces.append('## '+title+'\n\n'+text.strip())
    warnings.warn('Old prompt sections migrated to prompts.general. General custom text was retained; '
                  'observation and memory instructions now use fixed bridge rules. '
                  'Save settings to keep the new format.', UserWarning, stacklevel=2)
    return {'general':'\n\n'.join(pieces)}
