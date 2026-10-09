"""Stable home alias backed by fresh, ownership-verified native offers."""
from .world_actions import world_offers_available

PLAYER_TEMPLE_ID = 'player_temple'
HOME_GUIDANCE = '''## Your god's temple

Your home is the creature pen at your player god's temple. The temple's stable target ID is
"player_temple". Use move_to(destination={"special":"player_temple"}, pace="auto")
to head toward your home temple without searching nearby objects first. This
targets the temple; it does not select a separate pen location or prove that you
entered the pen. Other
actions can use this ID only when the temple is a compatible offered target.
This ID means the current player's current temple, not a saved object address.
The host resolves it anew and checks ownership for each action; it is not tied
to the nearby summary radius. Loading a save or changing islands can change
the actual temple. If it becomes unavailable, do not substitute another god's
temple or claim you arrived. Movement still uses native pathfinding.'''


def with_player_temple(system, probe):
    if any(e['action']=='move_to' and e.get('player_temple') is True
           for e in world_offers_available(probe or {}, planning=True)):
        return system+'\n\n'+HOME_GUIDANCE
    return system
