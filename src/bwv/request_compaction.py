"""Lossless wire-only history compression; current observations stay complete."""
from copy import deepcopy
import json
import re


HISTORY_CONTRACT = """The last user message always supplies a complete current snapshot. Earlier
messages are history, not current state. A historical snapshot_changes recursively
updates the preceding historical snapshot; replace lists/scalars as a whole.
snapshot_removed lists key paths to remove from that historical snapshot (unknown,
not zero). Unchanged historical fields are omitted. A full snapshot resets that
history baseline. The current snapshot replaces it entirely: never carry missing
old facts into the current world. Questions, replies, elapsed times, events and
action outcomes retain their original meaning; repeated event IDs are one event."""


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def generated_section(system, name, content):
    """Replace one host-owned block, without treating user Markdown as a delimiter.

    Explicit boundaries also keep trailing profile/memory data out of replacement.
    A callable replacement is important: content can contain literal backslashes.
    """
    start = f'<!-- bwv:{name} -->'
    end = f'<!-- /bwv:{name} -->'
    block = '\n\n'+start+'\n'+content+'\n'+end
    pattern = re.compile(r'\n\n'+re.escape(start)+r'\n.*?\n'+re.escape(end), re.DOTALL)
    found = False
    def replace(match):
        nonlocal found
        if found:
            return ''
        found = True
        return block
    updated = pattern.sub(replace, system)
    return updated if found else system+block


def _changes(previous, current, path=()):
    changed = {}
    removed = [list((*path, key)) for key in previous if key not in current]
    for key, value in current.items():
        old = previous.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            nested, missing = _changes(old, value, (*path, key))
            if nested:
                changed[key] = nested
            removed.extend(missing)
        elif (key not in previous or type(value) is not type(old) or value != old
              or isinstance(value, list) and compact_json(value) != compact_json(old)):
            changed[key] = value
    return changed, removed


def _apply_changes(previous, changed, removed):
    """Used only when a caller projects an already compacted request again."""
    result = deepcopy(previous)
    def merge(target, patch):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = deepcopy(value)
    merge(result, changed)
    for path in removed:
        parent = result
        for key in path[:-1]:
            parent = parent[key]
        parent.pop(path[-1], None)
    return result


def compact_messages(messages):
    """Rebuild per request, never append to saved conversation or cross requests.

    The first retained historical observation is always complete. This makes
    rolling history, reconnects and restarts independent of discarded messages.
    Use a patch only when it is smaller; never drop fields, rounds or exchanges.
    """
    result = []
    previous = None
    last_user = max((i for i, m in enumerate(messages) if m.get('role') == 'user'), default=-1)
    for index, original in enumerate(messages):
        message = dict(original)
        if message.get('role') not in ('user', 'assistant'):
            result.append(message)
            continue
        try:
            value = json.loads(message['content'])
        except (KeyError, TypeError, ValueError):
            result.append(message)
            continue
        if message['role'] == 'user' and isinstance(value, dict):
            if isinstance(value.get('snapshot_changes'), dict) and previous is not None:
                value['snapshot'] = _apply_changes(previous, value.pop('snapshot_changes'),
                                                   value.pop('snapshot_removed', []))
            current = value.get('snapshot')
            if isinstance(current, dict):
                # A save/scene transition must not inherit a historical baseline.
                if value.get('session_event') or value.get('memory_reconciliation'):
                    previous = None
                if previous is not None and index != last_user:
                    changes, removed = _changes(previous, current)
                    patch = {'snapshot_changes': changes}
                    if removed:
                        patch['snapshot_removed'] = removed
                    if len(compact_json(patch)) < len(compact_json({'snapshot': current})):
                        value = {k: v for k, v in value.items() if k != 'snapshot'} | patch
                previous = current
        message['content'] = compact_json(value)
        result.append(message)
    return result
