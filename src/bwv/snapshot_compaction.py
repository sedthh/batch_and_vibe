"""Share repeated facts within one snapshot; never cache facts between ticks."""
from copy import deepcopy

from .request_compaction import compact_json


def compact_snapshot(snapshot):
    """Edit an already copied model snapshot, leaving engine/tool data untouched."""
    towns = {town['town_id']: town for town in snapshot.get('towns_nearby', [])
             if isinstance(town, dict) and isinstance(town.get('town_id'), str)}
    if 'towns_near_god_hand' in snapshot:
        snapshot['towns_near_god_hand'] = [
            {'town_id': town['town_id']} if isinstance(town, dict)
            and town.get('town_id') in towns
            and compact_json(town) == compact_json(towns[town['town_id']]) else town
            for town in snapshot['towns_near_god_hand']]

    # Already projected snapshots are valid input during request rebuilding.
    if 'teleport_networks' in snapshot:
        return
    groups = {}
    for center in ('surroundings', 'hand_surroundings'):
        for gate in snapshot.get(center, {}).get('portals', []):
            if not isinstance(gate, dict) or gate.get('type') != 'teleport':
                continue
            portal = gate.get('portal')
            if not isinstance(portal, dict) or portal.get('route') != 'same_island':
                continue
            exits = portal.get('connected_exit_ids')
            identity = gate.get('object_id')
            if (not isinstance(identity, str) or not isinstance(exits, list)
                    or not all(isinstance(item, str) for item in exits)):
                continue
            members = tuple(sorted(set([identity, *exits])))
            common = {k: v for k, v in portal.items() if k != 'connected_exit_ids'}
            # Exact membership/metadata only: do not join separate owners' networks
            # or infer missing connections from a partial observation.
            key = (members, compact_json(common))
            groups.setdefault(key, (common, []))[1].append(gate)
    networks = []
    for (members, _), (common, gates) in groups.items():
        network = {**deepcopy(common), 'gate_ids': list(members)}
        reference = {'network': len(networks)}
        before = sum(len(compact_json(gate['portal'])) for gate in gates)
        # Include the table/container overhead; lone gates usually stay inline.
        after = len(compact_json(network)) + len(gates) * len(compact_json(reference))
        after += len(',"teleport_networks":[]') if not networks else 1
        if after >= before:
            continue
        networks.append(network)
        for gate in gates:
            gate['portal'] = dict(reference)
    if networks:
        snapshot['teleport_networks'] = networks
