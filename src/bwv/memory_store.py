"""Local, bounded, atomic conversation storage. Contains no game data or keys."""
from pathlib import Path
import json
import os
import tempfile


def default_memory_path():
    root = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
    return root / 'BlackAndWhiteGenAI' / 'creature-memory.json'


class MemoryStore:
    MAX_BYTES = 512_000

    def __init__(self, path):
        self.path = Path(path)
        self.error = None
        self.writable = True

    def load(self):
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(self.MAX_BYTES + 1)
            if len(raw) > self.MAX_BYTES:
                raise ValueError('Memory file too large')
            data = json.loads(raw)
            if (type(data) is not dict or data.get('version') not in (1,2)
                    or set(data) != ({'version','notes','recent_pairs'} |
                                    ({'entries'} if data['version']==2 else set()))):
                raise ValueError('Invalid memory file')
            if not isinstance(data['notes'], str) or len(data['notes']) > 100_000:
                raise ValueError('Invalid stored notes')
            if type(data['recent_pairs']) is not list or len(data['recent_pairs']) > 1000:
                raise ValueError('Invalid stored exchanges')
            if data['version']==2:
                entries=data['entries']
                if (type(entries) is not list or len(entries)>2000 or any(
                        type(e) is not dict or set(e)!={'id','text'}
                        or not isinstance(e['id'],str) or not 1<=len(e['id'])<=64
                        or not isinstance(e['text'],str) or not e['text'].strip() for e in entries)
                        or len({e['id'] for e in entries})!=len(entries)
                        or '\n\n'.join(e['text'] for e in entries)!=data['notes']):
                    raise ValueError('Invalid memory entries')
            for pair in data['recent_pairs']:
                if (type(pair) is not dict or not {'input','answer','decision','action_status'} <= pair.keys()
                        or not pair.keys() <= {'input','answer','decision','action_status','say','action_request_id','action_target'}
                        or type(pair['input']) is not dict or not isinstance(pair['answer'],str)
                        or type(pair['decision']) is not dict
                        or pair['action_status'] not in ('no_action','proposal_not_executed',
                            'queued_unconfirmed','dispatch_unconfirmed','waiting_for_native',
                            'cancelled_control_changed','cancelled_target_changed','cancelled_reset',
                            'cancelled_shutdown','cancelled_restart','superseded_by_question',
                            'superseded','expired','native_offer_unavailable','conditions_changed','rejected',
                            'already_awake','cancelled_world_target_or_learning_changed',
                            'cancelled_miracle_unavailable','cancelled_miracle_target_changed',
                            'cancelled_pickup_target_changed','cancelled_held_object_changed',
                            'cancelled_throw_target_changed','cancelled_wake_replaced',
                            'cancelled_wake_unconfirmed','cancelled_asleep_again',
                            'cancelled_retry_limit','cancelled_put_down_replaced',
                            'cancelled_put_down_not_empty','cancelled_hand_preparation_limit',
                            'cancelled_put_down_failed',
                            'wake_not_confirmed_no_retry','cancelled_wake_not_queued')
                        or ('action_request_id' in pair and (not isinstance(pair['action_request_id'],str)
                            or not 1 <= len(pair['action_request_id']) <= 64))
                        or ('say' in pair and not isinstance(pair['say'],str))
                        or ('action_target' in pair and type(pair['action_target']) is not dict)):
                    raise ValueError('Invalid stored exchange')
            # Reject non-finite JSON too; stored content is always historical data.
            json.dumps(data, allow_nan=False)
            return data
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError):
            self.error = 'load_failed_original_preserved'
            self.writable = False
            return None

    def save(self, notes, pairs, entries=None):
        if not self.writable:
            return
        temporary = None
        try:
            payload = json.dumps({'version':2 if entries is not None else 1,'notes':notes,'recent_pairs':pairs,
                                 **({'entries':entries} if entries is not None else {})},
                                 ensure_ascii=False,allow_nan=False).encode('utf-8')
            if len(payload) > self.MAX_BYTES:
                raise ValueError('Memory file too large')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='.creature-memory-',
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.error = None
        except (OSError, ValueError):
            self.error = 'save_failed_previous_file_preserved'
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
