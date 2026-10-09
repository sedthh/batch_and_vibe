"""Native agenda adapters shared by manual QA and conversation actions.

Requires the separately compiled action bridge. Each command uses a fresh
native capability; the standalone manual experiment defaults to preview.
"""
from __future__ import annotations

import ctypes
import hashlib
from ctypes import wintypes
import json
import math
import os
import struct
import time
from copy import deepcopy

from .pipe import snapshot
from .held_objects import HELD_KINDS
from .world_actions import WORLD_ACTIONS, WORLD_KINDS, OBJECT_TARGET_LIMIT, world_offers_available, valid_target_metadata
from .dispatch import ActionOffer, DecisionDispatcher, DummyActionProvider, ActionNotSent

COMMAND = struct.Struct('<IIQQQII')
PRIORITY_FLAG = 0x80000000
PLAYER_COMMAND_FLAG = 0x40000000


def priority_state(state):
    """Use only a native-advertised priority capability, never invent one."""
    probe=state.get('action_probe',{})
    contract=probe.get('priority_commands',{})
    if contract.get('version')!=1 or contract.get('available') is not True:
        return state
    if (probe.get('conditions_allow_action') is not True
            or probe.get('action_lifecycle',{}).get('repeated_actions_enabled') is not True):
        return state
    state=deepcopy(state);probe=state['action_probe']
    probe['phase']='ready';probe['candidates']=list(contract.get('candidates',()))
    probe['action_lifecycle'].update(in_flight=False,cooldown_remaining_ms=0)
    state['_priority_command']=True
    return state
ACTIONS = {'look_confused': 1, 'sleep_here': 2, 'approach_hand': 3, 'wake_up': 4, 'approach_object': 5}
SELF_ACTIONS = ('sleep_here','wave','sneeze','shiver','scratch','show_hotness','dance')
ACTIONS.update(dict(zip(SELF_ACTIONS[1:],range(6,12))))
HELD_ACTIONS = ('put_down','eat_held','discard_held')
# Keep wire IDs stable; retired examine_held used 13.
ACTIONS.update(put_down=12, eat_held=14, discard_held=21)
ACTIONS['throw_held_at']=15
ACTIONS['pick_up']=16
MIRACLE_ACTIONS=('cast_heal_self','cast_water_tree')
ACTIONS.update(cast_heal_self=17,cast_water_tree=18)
ACTIONS.update({name:19 for name in WORLD_ACTIONS})
ACTIONS['move_to_position']=20
from .positions import map_bounds, checked_position
PICKUP_KINDS=('tree','rock','villager','toy','food_pile','wood_pile','animal','miracle_bubble','mushroom')


def miracle_actions_available(probe):
    contract=probe.get('miracle_actions',{})
    if contract.get('version')!=9 or contract.get('verified') is not True:return ()
    result=[]
    if contract.get('heal_learning_observed') is True:result.append('cast_heal_self')
    if contract.get('water_learning_observed') is True and any(t['kind']=='tree' for t in pickup_targets_available(probe)):
        result.append('cast_water_tree')
    return tuple(result)


def pickup_targets_available(probe, *, planning=False):
    contract=probe.get('pickup_action',{})
    if (contract.get('version')!=8 or contract.get('verified') is not True
            or (not (planning and probe.get('hand_preparation_protocol')==1) and (
                contract.get('hand_empty') is not True
                or probe.get('held_actions',{}).get('target_generation')!=0))):return ()
    targets=contract.get('targets')
    if not isinstance(targets,list) or len(targets)>OBJECT_TARGET_LIMIT:return ()
    seen=set();result=[]
    for t in targets:
        if not isinstance(t,dict):return ()
        token,distance=t.get('token'),t.get('distance_engine_units')
        if (type(token) is not int or not 0<token<=0xffffffff or token in seen
                or t.get('kind') not in PICKUP_KINDS or type(distance) not in (int,float)
                or not math.isfinite(distance) or not 0<distance<=20000):return ()
        if not valid_target_metadata(t):return ()
        seen.add(token)
        if not planning and t.get('pickup_possible') is False:continue
        result.append(dict(token=token,kind=t['kind'],distance_engine_units=distance,
            **{key:t[key] for key in ('on_fire','distance_to_god_hand_engine_units','object_handle',
                'pickup_possible','pickup_reason','bubble_spell') if key in t}))
    return tuple(result)


def throw_targets_available(probe):
    contract=probe.get('throw_action',{})
    held=probe.get('held_actions',{}).get('target_generation')
    if (not held_actions_available(probe) or contract.get('version')!=7
            or contract.get('verified') is not True or contract.get('held_generation')!=held):return ()
    targets=contract.get('targets')
    if not isinstance(targets,list) or len(targets)>OBJECT_TARGET_LIMIT:return ()
    seen=set();result=[]
    for t in targets:
        if not isinstance(t,dict):return ()
        token,distance=t.get('token'),t.get('distance_engine_units')
        if (type(token) is not int or not 0<token<=0xffffffff or token in seen
                or t.get('kind') not in WORLD_KINDS[1:]
                or type(distance) not in (int,float) or not math.isfinite(distance) or not 0<distance<=20000):return ()
        if not valid_target_metadata(t):return ()
        seen.add(token);result.append(dict(token=token,kind=t['kind'],distance_engine_units=distance,
            **{key:t[key] for key in ('on_fire','distance_to_god_hand_engine_units','object_handle') if key in t}))
    return tuple(result)


def held_actions_available(probe):
    held=probe.get('held_actions',{})
    token=held.get('target_generation')
    if (held.get('version') != 6 or held.get('verified') is not True
            or type(token) is not int or not 0 < token <= 0xffffffff
            or held.get('kind') not in HELD_KINDS):
        return ()
    edible = (held.get('can_eat') is True if held.get('edibility_version') == 1
              else held['kind'] == 'food_pile')
    return tuple(action for action in HELD_ACTIONS
        if (action!='eat_held' or edible)
        and (action!='discard_held' or held.get('discard_version')==1))


def self_catalog_available(probe):
    catalog=probe.get('self_action_catalog',{})
    return catalog.get('version') == 5 and catalog.get('verified') is True


class ActionProcessChanged(ValueError):
    """The observer followed a restart but the opt-in executor did not."""


def _action_budget_available(probe):
    calls = probe.get('engine_calls')
    if type(calls) is not int or calls < 0:
        return False
    lifecycle = probe.get('action_lifecycle')
    if lifecycle is None:
        return calls == 0  # Legacy bridge retains its one-action allowance.
    if not isinstance(lifecycle, dict):
        return False
    return (type(lifecycle.get('version')) is int and lifecycle['version'] == 1
            and lifecycle.get('in_flight') is False
            and type(lifecycle.get('cooldown_remaining_ms')) is int
            and lifecycle['cooldown_remaining_ms'] == 0
            and (calls == 0 or lifecycle.get('repeated_actions_enabled') is True))


def dummy_decision(state, action='look_confused'):
    """The dummy can only choose a current offer. No invented action or target."""
    probe = state.get('action_probe')
    if action not in ACTIONS:
        raise ValueError('Unknown manual test action')
    if (not isinstance(probe, dict) or probe.get('supported') is not True
            or probe.get('transport_ready') is not True or probe.get('phase') != 'ready'
            or action not in probe.get('candidates', [probe.get('candidate')]) or not _action_budget_available(probe)
            or probe.get('experimental_manual_only') is not True
            or type(state.get('source_age_ms')) not in (float, int)
            or not 0 <= state['source_age_ms'] <= 1000):
        return {'candidate': None}
    if action=='move_to_position' and map_bounds(probe) is None:return {'candidate':None}
    if action == 'approach_hand':
        movement = probe.get('movement', {})
        distance = movement.get('distance_to_hand_engine_units')
        if (movement.get('verified') is not True or type(distance) not in (int, float)
                or not math.isfinite(distance) or distance<=0):
            return {'candidate': None}
    if action == 'wake_up' and probe.get('wake_protocol') != 3:
        return {'candidate': None}
    if action in SELF_ACTIONS[1:] and not self_catalog_available(probe):
        return {'candidate': None}
    if action in HELD_ACTIONS and action not in held_actions_available(probe):
        return {'candidate': None}
    if action=='throw_held_at' and not throw_targets_available(probe):return {'candidate':None}
    if action=='pick_up' and not pickup_targets_available(probe):return {'candidate':None}
    if action in MIRACLE_ACTIONS and action not in miracle_actions_available(probe):return {'candidate':None}
    if action == 'approach_object':
        target = probe.get('object_movement', {})
        generation, distance = target.get('target_generation'), target.get('distance_engine_units')
        if (type(target.get('protocol')) is not int or target['protocol'] != 4
                or target.get('manual_only') is not True or target.get('kind') not in ('tree', 'rock')
                or type(generation) is not int or not 0 < generation <= 0xffffffff
                or type(distance) not in (int, float) or not 8 <= distance <= 30):
            return {'candidate': None}
    return {'candidate': action}


def encode(state, decision, *, throw_target=None, pickup_target=None, miracle_target=None, world_token=None, destination=None):
    if (not isinstance(decision, dict) or set(decision) != {'candidate'}
            or decision['candidate'] not in ACTIONS
            or dummy_decision(state, decision['candidate']) != decision):
        raise ValueError('No current eligible dummy action')
    probe = state['action_probe']
    values = [probe.get(key) for key in ('epoch','generation','offered_at_uptime_ms')]
    if any(type(value) is not int or not 0 <= value < 2**64 for value in values):
        raise ValueError('Invalid native action offer')
    if probe['epoch'] != state.get('session'):
        raise ValueError('Native action offer has the wrong session')
    action = decision['candidate']
    version = (11 if action=='move_to_position' else 10 if action in WORLD_ACTIONS else 9 if action in MIRACLE_ACTIONS else 8 if action=='pick_up' else 7 if action=='throw_held_at' else 6 if action in HELD_ACTIONS else 5 if action in SELF_ACTIONS and self_catalog_available(probe) else
               4 if action == 'approach_object' else 3 if action == 'wake_up' else 1)
    target = probe['object_movement']['target_generation'] if action == 'approach_object' else 0
    if action in WORLD_ACTIONS:
        if type(world_token) is not int or world_token not in {e['token'] for e in world_offers_available(probe) if e['action']==action}:
            raise ValueError('Select an offered action/target pair')
        target=world_token
    if action in HELD_ACTIONS:target=probe['held_actions']['target_generation']
    if action=='throw_held_at':
        if type(throw_target) is not int or throw_target not in {t['token'] for t in throw_targets_available(probe)}:
            raise ValueError('Select an offered throw destination')
        target=throw_target
    if action=='pick_up':
        if type(pickup_target) is not int or pickup_target not in {t['token'] for t in pickup_targets_available(probe)}:
            raise ValueError('Select an offered pickup object')
        target=pickup_target
    if action=='cast_water_tree':
        if type(miracle_target) is not int or miracle_target not in {t['token'] for t in pickup_targets_available(probe) if t['kind']=='tree'}:
            raise ValueError('Select an offered tree to water')
        target=miracle_target
    if state.get('_priority_command') is True:version |= PRIORITY_FLAG
    packet=COMMAND.pack(0x42575041,version,*values,ACTIONS[action],target)
    if action=='move_to_position':
        point=checked_position(destination,map_bounds(probe))
        packet+=struct.pack('<ff',point['x'],point['z'])
    return packet


class NativeActionAdapter:
    """Keep engine capability tokens private and bound to their original process."""
    actions = tuple(a for a in ACTIONS if a not in ('throw_held_at','pick_up','cast_water_tree','move_to_position') and a not in WORLD_ACTIONS)
    lifetime_seconds = 1
    def __init__(self, pid, *, read=None, send=None, interrupt=False, pace=None, player_command=False):
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            raise ValueError('Invalid action process')
        self.pid = pid
        reader=read or snapshot
        self.read=(lambda pid:priority_state(reader(pid))) if interrupt else reader
        self.send=send or submit
        self.pace=pace
        self.player_command=player_command

    def _identity(self, state):
        probe = state['action_probe']
        # Native generation changes on creature replacement and every eligibility
        # transition, even if the creature becomes eligible again before a reply.
        if state.get('pid') != self.pid:
            raise ValueError('Action offer belongs to another process')
        return (self.pid, state['session'], probe['epoch'], probe['generation'])

    def capture(self, now):
        state = self.read(self.pid)
        from .movement_pace import paced_packet, MOVEMENT_ACTIONS
        packets = tuple((action, self._player_packet(state, paced_packet(self._packet(state, action),state['action_probe'],action,self.pace))) for action in self.actions
                        if self._available(state, action) and (self.pace is None or action in MOVEMENT_ACTIONS))
        if not packets:
            raise ValueError('No current eligible action; manual test not queued')
        probe = state['action_probe']
        # Source age plus any lag between the cached frame and native offer count
        # against the native one-second command lifetime.
        captured = state.get('captured_uptime_ms')
        offered = probe['offered_at_uptime_ms']
        if type(captured) is not int or captured < offered:
            raise ValueError('Invalid native action timestamp')
        remaining = self.lifetime_seconds - (state['source_age_ms'] + captured - offered) / 1000
        if remaining <= 0:
            raise ValueError('Native action offer expired')
        return ActionOffer(self._identity(state), packets, now + remaining)

    def _player_packet(self, state, packet):
        contract=state['action_probe'].get('player_command_priority',{})
        if not self.player_command or contract.get('version')!=1 or contract.get('available') is not True:
            return packet
        result=bytearray(packet)
        version=struct.unpack_from('<I',result,4)[0]
        struct.pack_into('<I',result,4,version|PLAYER_COMMAND_FLAG)
        return bytes(result)

    def _available(self, state, action):
        return (state.get('action_probe', {}).get('allowed_action_mask') != 4
                and dummy_decision(state, action)['candidate'] is not None)

    def _packet(self, state, action):
        return encode(state, {'candidate': action})

    def still_valid(self, offer, candidate):
        state = self.read(self.pid)
        try:
            if not (self._available(state, candidate) and self._identity(state) == offer.identity):
                return False
            if candidate == 'approach_object':
                original = dict(offer.packets).get(candidate)
                return original is not None and COMMAND.unpack(original)[-1] == state['action_probe']['object_movement']['target_generation']
            if candidate in HELD_ACTIONS:
                original=dict(offer.packets).get(candidate)
                return original is not None and COMMAND.unpack(original)[-1]==state['action_probe']['held_actions']['target_generation']
            return True
        except (KeyError, TypeError, ValueError):
            return False

    def submit(self, packet):
        return self.send(self.pid, packet)


class ConversationActionAdapter(NativeActionAdapter):
    """Opt-in delayed dummy QA: original 30s offer, native lifecycle enforced.

    Expanded self actions, including sleep, require the verified v5 catalog.
    The older v2 bridge continues to offer only its original actions; an old
    bridge never silently receives a longer-lived v1 packet.
    """
    actions = ('look_confused', 'approach_hand', *SELF_ACTIONS)
    lifetime_seconds = 30

    def __init__(self, pid, *, expected_identity, expected_hand_generation=None, **kwargs):
        super().__init__(pid, **kwargs)
        if expected_hand_generation is not None and (type(expected_hand_generation) is not int
                or not 0 < expected_hand_generation <= 0xffffffff):
            raise ValueError('Invalid expected hand target')
        self.expected_hand_generation = expected_hand_generation
        if (type(expected_identity) is not tuple or len(expected_identity) != 2
                or any(type(v) is not str or not v for v in expected_identity)):
            raise ValueError('Expected conversation identity is required')
        self.expected_identity = expected_identity
        if expected_identity[0].partition(':')[0] != str(pid):
            raise ActionProcessChanged('Reconnect the action bridge to the current game process')

    def _identity(self, state):
        identity = super()._identity(state)
        contract = state['action_probe']['conversation_actions']
        handles = [contract.get(key) for key in ('creature_handle', 'mental_handle')]
        if any(type(value) is not int or not 0 < value <= 0xffffffff for value in handles):
            raise ValueError('Native creature identity unavailable')
        session = f"{self.pid}:{state['session']}"
        creature = hashlib.sha256(f'{session}:{handles[0]}:{handles[1]}'.encode()).hexdigest()[:24]
        if (session, creature) != self.expected_identity:
            raise ValueError('Conversation and native creature identities differ')
        return identity

    def _available(self, state, action):
        contract = state.get('action_probe', {}).get('conversation_actions', {})
        return (action in self.actions and contract.get('version') == 2
                and contract.get('max_offer_age_ms') == 30000
                and (action not in SELF_ACTIONS or self_catalog_available(state.get('action_probe',{})))
                and (action != 'approach_hand' or self.expected_hand_generation is None
                     or contract.get('hand_target_generation') == self.expected_hand_generation)
                and (action != 'approach_hand' or
                     type(contract.get('hand_target_generation')) is int
                     and 0 < contract['hand_target_generation'] <= 0xffffffff)
                and dummy_decision(state, action)['candidate'] is not None)

    def _packet(self, state, action):
        values = list(COMMAND.unpack(super()._packet(state, action)))
        values[1] = (values[1] & PRIORITY_FLAG) | (5 if action in SELF_ACTIONS else 2)
        if action == 'approach_hand':
            values[-1] = state['action_probe']['conversation_actions']['hand_target_generation']
        return COMMAND.pack(*values)

    def still_valid(self, offer, candidate):
        state = self.read(self.pid)
        try:
            if not self._available(state, candidate) or self._identity(state) != offer.identity:
                return False
            if candidate == 'approach_hand':
                original = COMMAND.unpack(dict(offer.packets)[candidate])[-1]
                return original == state['action_probe']['conversation_actions']['hand_target_generation']
            return True
        except (KeyError, TypeError, ValueError):
            return False


class PositionActionAdapter(ConversationActionAdapter):
    """A coordinate destination bound to the selected creature and island session."""
    actions=('move_to_position',)
    lifetime_seconds=1

    def __init__(self,pid,*,destination,**kwargs):
        super().__init__(pid,**kwargs)
        self.destination=deepcopy(destination)

    def _available(self,state,action):
        if not super()._available(state,action):return False
        try:checked_position(self.destination,map_bounds(state['action_probe']))
        except ValueError:return False
        return True

    def _packet(self,state,action):
        return encode(state,{'candidate':action},destination=self.destination)


class HeldActionAdapter(ConversationActionAdapter):
    """Fresh v6 command bound to the held object selected before inference."""
    actions = HELD_ACTIONS
    lifetime_seconds = 1

    def __init__(self,pid,*,expected_held_generation,**kwargs):
        if type(expected_held_generation) is not int or not 0 < expected_held_generation <= 0xffffffff:
            raise ValueError('Expected held-object target is required')
        self.expected_held_generation=expected_held_generation
        super().__init__(pid,**kwargs)

    def _available(self,state,action):
        return (action in held_actions_available(state.get('action_probe',{}))
                and state['action_probe']['held_actions']['target_generation']==self.expected_held_generation
                and super()._available(state,action))

    def _packet(self,state,action):
        return NativeActionAdapter._packet(self,state,action)

    def still_valid(self,offer,candidate):
        return NativeActionAdapter.still_valid(self,offer,candidate)


class ThrowActionAdapter(ConversationActionAdapter):
    actions=('throw_held_at',)
    lifetime_seconds=1

    def __init__(self,pid,*,expected_throw_generation,expected_held_generation,**kwargs):
        for value in (expected_throw_generation,expected_held_generation):
            if type(value) is not int or not 0<value<=0xffffffff:raise ValueError('Invalid throw binding')
        self.throw_token=expected_throw_generation;self.held_token=expected_held_generation
        super().__init__(pid,**kwargs)

    def _available(self,state,action):
        probe=state.get('action_probe',{})
        return (probe.get('held_actions',{}).get('target_generation')==self.held_token
            and self.throw_token in {t['token'] for t in throw_targets_available(probe)}
            and super()._available(state,action))

    def _packet(self,state,action):
        return encode(state,{'candidate':action},throw_target=self.throw_token)


class PickupActionAdapter(ConversationActionAdapter):
    actions=('pick_up',)
    lifetime_seconds=1

    def __init__(self,pid,*,expected_pickup_generation,**kwargs):
        if type(expected_pickup_generation) is not int or not 0<expected_pickup_generation<=0xffffffff:
            raise ValueError('Invalid pickup binding')
        self.pickup_token=expected_pickup_generation
        super().__init__(pid,**kwargs)

    def _available(self,state,action):
        return (self.pickup_token in {t['token'] for t in pickup_targets_available(state.get('action_probe',{}))}
            and super()._available(state,action))

    def _packet(self,state,action):
        return encode(state,{'candidate':action},pickup_target=self.pickup_token)


class MiracleActionAdapter(ConversationActionAdapter):
    lifetime_seconds=1

    def __init__(self,pid,*,miracle_action,expected_miracle_target=None,**kwargs):
        if miracle_action not in MIRACLE_ACTIONS:raise ValueError('Unknown miracle action')
        if miracle_action=='cast_water_tree' and (type(expected_miracle_target) is not int or not 0<expected_miracle_target<=0xffffffff):
            raise ValueError('Expected tree target is required')
        self.actions=(miracle_action,)
        self.miracle_target=expected_miracle_target
        super().__init__(pid,**kwargs)

    def _available(self,state,action):
        return (super()._available(state,action) and (action!='cast_water_tree' or
            self.miracle_target in {t['token'] for t in pickup_targets_available(state.get('action_probe',{})) if t['kind']=='tree'}))

    def _packet(self,state,action):
        return encode(state,{'candidate':action},miracle_target=self.miracle_target)


class WorldActionAdapter(ConversationActionAdapter):
    lifetime_seconds=1

    def __init__(self,pid,*,world_action,expected_world_token,**kwargs):
        if world_action not in WORLD_ACTIONS:raise ValueError('Unknown world action')
        if type(expected_world_token) is not int or not 0<expected_world_token<=0xffffffff:
            raise ValueError('Expected world action/target token required')
        self.actions=(world_action,);self.world_token=expected_world_token
        super().__init__(pid,**kwargs)

    def _available(self,state,action):
        return (super()._available(state,action) and any(e['action']==action and e['token']==self.world_token
            for e in world_offers_available(state.get('action_probe',{}))))

    def _packet(self,state,action):
        return encode(state,{'candidate':action},world_token=self.world_token)


class TreeApproachAdapter(ConversationActionAdapter):
    """Fresh v4 packet for one tree bound before inference; never retarget."""
    actions = ('approach_object',)
    lifetime_seconds = 1

    def __init__(self, pid, *, expected_object_generation, **kwargs):
        if type(expected_object_generation) is not int or not 0 < expected_object_generation <= 0xffffffff:
            raise ValueError('Expected tree target is required')
        self.expected_object_generation = expected_object_generation
        super().__init__(pid, **kwargs)

    def _available(self, state, action):
        target = state.get('action_probe', {}).get('object_movement', {})
        return (action == 'approach_object' and target.get('kind') == 'tree'
                and target.get('target_generation') == self.expected_object_generation
                and super()._available(state, action))

    def _packet(self, state, action):
        return NativeActionAdapter._packet(self, state, action)

    def still_valid(self, offer, candidate):
        return NativeActionAdapter.still_valid(self, offer, candidate)


def lookup_target(pid, object_handle, *, expected_session, expected_creature):
    """Reserve an observed object in the next bounded target offer; never acts."""
    if type(object_handle) is not int or not 0<object_handle<=0xffffffff:
        raise ValueError('Invalid lookup identity')
    state=snapshot(pid)
    def checked(value):
        probe=value.get('action_probe',{})
        if type(value.get('source_age_ms')) not in (int,float) or not 0<=value['source_age_ms']<=1500:
            raise ValueError('Target lookup observation is stale')
        if (value.get('session')!=expected_session or probe.get('target_lookup_protocol')!=12
                or probe.get('conversation_actions',{}).get('creature_handle')!=expected_creature):
            raise ValueError('Target lookup identity changed or bridge unavailable')
        return probe
    probe=checked(state)
    packet=COMMAND.pack(0x42575041,12,probe['epoch'],probe['generation'],
        probe['offered_at_uptime_ms'],0,object_handle)
    if not submit(pid,packet):raise ValueError('Target lookup rejected; refresh observation')
    deadline=time.monotonic()+.8
    while time.monotonic()<deadline:
        time.sleep(.04)
        probe=checked(snapshot(pid))
        if probe.get('lookup_observed_object')==object_handle:
            return probe
    raise ValueError('Object was not found in current native beliefs')


def submit(pid, packet):
    if os.name != 'nt':
        raise OSError('The action experiment requires Windows')
    if type(pid) is not int or not 0 < pid <= 0xffffffff or type(packet) is not bytes or len(packet) not in (COMMAND.size, COMMAND.size+8):
        raise ValueError('Invalid action experiment request')
    header=COMMAND.unpack(packet[:COMMAND.size])
    coordinate_packet=(header[1] & ~(PRIORITY_FLAG|PLAYER_COMMAND_FLAG|0x30000000))==11 and header[-2]==20
    if len(packet)!=(COMMAND.size+8 if coordinate_packet else COMMAND.size):
        raise ValueError('Action packet length does not match its protocol')
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
        wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD]
    kernel.WaitNamedPipeW.restype = wintypes.BOOL
    kernel.WriteFile.argtypes = [wintypes.HANDLE,wintypes.LPCVOID,wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID]
    kernel.ReadFile.argtypes = [wintypes.HANDLE,wintypes.LPVOID,wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID]
    kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE,wintypes.LPVOID,wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = rf'\\.\pipe\bwv-action-probe-{pid}'
    if not kernel.WaitNamedPipeW(name,1000):
        raise ActionNotSent('action_pipe_unavailable',ctypes.get_last_error())
    handle = kernel.CreateFileW(name,0xC0000000,0,None,3,0,None)
    if handle == ctypes.c_void_p(-1).value:
        raise ActionNotSent('action_pipe_open_failed',ctypes.get_last_error())
    try:
        size=wintypes.DWORD()
        if not kernel.WriteFile(handle,packet,len(packet),ctypes.byref(size),None) or size.value != len(packet):
            raise OSError('Action request was not delivered; do not retry blindly')
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            available=wintypes.DWORD()
            if not kernel.PeekNamedPipe(handle,None,0,None,ctypes.byref(available),None):
                raise OSError('Action receipt unavailable; inspect state before retrying')
            if available.value >= 4:
                result=wintypes.DWORD()
                if not kernel.ReadFile(handle,ctypes.byref(result),4,ctypes.byref(size),None) or size.value != 4:
                    raise OSError('Invalid action receipt')
                if result.value not in (0,1):
                    raise OSError('Unknown action receipt; execution is not confirmed')
                return result.value == 1
            time.sleep(.01)
        raise TimeoutError('Action receipt timed out; inspect state before retrying')
    finally:
        kernel.CloseHandle(handle)


class QuestionWakeAdapter(ConversationActionAdapter):
    """Host-requested waking, bound to a direct question's current identity.

    Never part of the model's action catalog and never a delayed model decision.
    """
    actions = ('wake_up',)
    lifetime_seconds = 1

    def _available(self, state, action):
        return action == 'wake_up' and NativeActionAdapter._available(self, state, action)

    def _packet(self, state, action):
        return NativeActionAdapter._packet(self, state, action)


def run(pid, *, execute=False, seconds=15, action='look_confused'):
    if type(seconds) is not int or not 1 <= seconds <= 30:
        raise ValueError('Action observation duration must be 1 to 30 seconds')
    state=snapshot(pid)
    decision=dummy_decision(state, action)
    report={'mode':'manual_native_action_experiment','dummy_decision':decision,
        'provider_calls':0,'submitted':False,'action_probe':state.get('action_probe')}
    if not execute:
        return report
    # The provider sees names only; the dispatcher retains original capability
    # tokens and revalidates the engine before sending the structured decision.
    dispatcher=DecisionDispatcher(NativeActionAdapter(pid))
    request=dispatcher.offer()
    decision=DummyActionProvider(action).choose(request)
    result=dispatcher.dispatch(decision)
    report.update(dummy_decision=decision, dispatch=result, submitted=result['queued'])
    print(json.dumps({'dummy_decision':decision,'queued':report['submitted'],
                      'completed':False,'provider_calls':0}),flush=True)
    if not report['submitted']:
        return report
    deadline=time.monotonic()+seconds
    transitions=[]; previous=None
    while time.monotonic()<deadline:
        current=snapshot(pid)
        if current.get('session') != state.get('session'):
            report['interrupted']='session_changed'; break
        probe=current.get('action_probe',{})
        if type(current.get('source_age_ms')) not in (int,float) or not 0 <= current['source_age_ms'] <= 1500:
            report['interrupted']='observation_stale'; break
        signature=(probe.get('phase'),probe.get('current_plan'),probe.get('outcome'),probe.get('script_owned'),
                   json.dumps(probe.get('activity'),sort_keys=True),
                   json.dumps(probe.get('movement'),sort_keys=True))
        if signature != previous:
            transitions.append(probe); previous=signature
            print(json.dumps({'action_probe':probe}),flush=True)
        report['action_probe']=probe
        if probe.get('phase') in ('finished','rejected'):
            break
        time.sleep(.1)
    report['transitions']=transitions
    report['visual_confirmation_required']=True
    return report
