"""Authenticated local JSON transport for the overlay; no HTTP listener or pickle."""
import json
from multiprocessing.connection import Client, Listener
from multiprocessing import AuthenticationError
import re
import secrets
import threading
from .errors import MESSAGES, ChatNotSubmitted

MAX_RESPONSE = 8 * 1024 * 1024


def parse_endpoint(endpoint):
    match = re.fullmatch(r'bwv-pipe:([a-f0-9]{32}):([a-f0-9]{64})', endpoint)
    if not match:
        raise ValueError('Invalid local chat pipe endpoint')
    return '\\\\.\\pipe\\bwv-chat-' + match[1], bytes.fromhex(match[2])


class ChatPipeServer:
    def __init__(self, state, question):
        self.endpoint = f'bwv-pipe:{secrets.token_hex(16)}:{secrets.token_hex(32)}'
        address, key = parse_endpoint(self.endpoint)
        self.listener = Listener(address, family='AF_PIPE', authkey=key)
        self.state, self.question = state, question
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stopped.is_set():
            try:
                connection = self.listener.accept()
            except AuthenticationError:
                continue
            except (OSError, EOFError):
                if not self.stopped.is_set():
                    print('ERROR: Chat pipe listener failed. Restart the launcher to restore the overlay.', flush=True)
                return
            # A question may take a full provider turn; state polling must keep working.
            threading.Thread(target=self._request, args=(connection,), daemon=True).start()

    def _request(self, connection):
        try:
            with connection:
                if not connection.poll(2):
                    return
                try:
                    request = json.loads(connection.recv_bytes(8192))
                    if type(request) is not dict or set(request) != {'method', 'body'}:
                        raise ValueError('Invalid chat request')
                    if request['method'] == 'state' and request['body'] is None:
                        result = self.state()
                    elif request['method'] == 'question':
                        result = self.question(request['body'])
                    else:
                        raise ValueError('Unknown chat request')
                    body = json.dumps({'result': result}, allow_nan=False).encode('utf-8')
                    if len(body) > MAX_RESPONSE:
                        raise ValueError('Chat state is too large')
                except ChatNotSubmitted as error:
                    body = json.dumps({'error':MESSAGES['chat_unavailable'],
                        'not_submitted':True,'retryable':error.retryable}).encode()
                except (ValueError, RuntimeError, KeyError, TypeError):
                    body = json.dumps({'error': MESSAGES['chat_unavailable']}).encode()
                connection.send_bytes(body)
        except (OSError, EOFError):
            pass  # caller closed its connection; the next request can reconnect

    def close(self):
        self.stopped.set()
        self.listener.close()
        self.thread.join(2)


class ChatPipeClient:
    def __init__(self, endpoint):
        self.address, self.key = parse_endpoint(endpoint)

    def call(self, method, body=None, timeout=2):
        with Client(self.address, family='AF_PIPE', authkey=self.key) as connection:
            connection.send_bytes(json.dumps({'method': method, 'body': body}).encode('utf-8'))
            if not connection.poll(timeout):
                raise TimeoutError('Creature chat did not respond in time')
            response = json.loads(connection.recv_bytes(MAX_RESPONSE))
        if 'error' in response:
            if response.get('not_submitted') is True:
                raise ChatNotSubmitted(retryable=response.get('retryable') is True)
            raise ValueError(response['error'])
        return response['result']
