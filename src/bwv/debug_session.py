"""Switch localhost debugging without restarting chat, observation or the overlay."""
import asyncio
import threading

from .qa import make_server


class DebugSession:
    def __init__(self, enabled=False, port=0, emit=print):
        self.enabled, self.port, self.emit = enabled, port, emit
        self.runtime = self.observer = None
        self.server = self.thread = self.url = None
        self.lock = asyncio.Lock()

    async def attach(self, runtime, observer):
        self.runtime, self.observer = runtime, observer
        try:
            await self.configure(self.enabled, self.port)
        except (OSError, ValueError) as error:
            self.enabled = False
            self.emit(f'ERROR: {error} Chat and the game observer remain running.')

    async def configure(self, enabled, port=0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('Debug port must be 1–65535, or blank for an available port.')
        async with self.lock:
            if self.runtime is None:
                raise ValueError('Debugger controls are not ready yet.')
            if enabled and self.server is not None and port in (self.port, self.server.server_port):
                self.port = port
                return
            server = thread = url = None
            if enabled:
                runtime = self.runtime
                try:
                    server, url = make_server(runtime, port, post_handlers={
                        'question': runtime.handler(runtime.question),
                        'configure': runtime.handler(runtime.configure),
                        'reset': runtime.handler(runtime.reset),
                        'force-tick': runtime.handler(runtime.force_tick)},
                        page_name='qa_live.html', debug=True, observer=self.observer)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                except (OSError, RuntimeError):
                    if server is not None:
                        server.server_close()
                    raise OSError('Cannot start the localhost debugger. The port may be in use; '
                                  'choose another port. Existing services were kept running.') from None
            # Bind successfully before replacing a working debugger.
            await self._stop_server()
            self.server, self.thread, self.url = server, thread, url
            self.enabled, self.port = bool(enabled), port
            self.observer.set_history_limit(120 if enabled else 0)
            if enabled:
                self.emit(f'Debug LLM: {url}llm/')
                self.emit(f'Debug observer: {url}observer/')
            else:
                self.emit('Debugging off. Snapshot history cleared; chat and creature memory stay active.')

    async def _stop_server(self):
        if self.server is not None:
            await asyncio.to_thread(self.server.shutdown)
            self.server.server_close()
            await asyncio.to_thread(self.thread.join, 2)
            self.server = self.thread = self.url = None

    async def close(self):
        async with self.lock:
            await self._stop_server()
            if self.observer is not None:
                self.observer.set_history_limit(0)
            self.runtime = self.observer = None
            self.enabled = False
