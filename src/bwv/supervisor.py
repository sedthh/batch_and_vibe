"""Own one observer, chat service and overlay across game restarts; no disk captures."""
import asyncio
from dataclasses import replace
import subprocess
import sys
import time

from .control import ControlObservation
from .processes import game_processes, select_game
from .qa import Inspector, collect


def chat_error(code):
    from .errors import MESSAGES
    advice = {
        'authentication_failed': 'Check the API key in .local/.env and restart. Provider requests are disabled.',
        'model_or_configuration_rejected': 'Check model, API type and provider settings, then restart. Provider requests are disabled.',
        'rate_limited': 'The provider rate limit was reached; later scheduled turns can retry.',
        'provider_unavailable': 'The provider is unavailable; later scheduled turns can retry.',
        'message_limit_reached': 'The mod message cap was reached, not a provider rate limit. '
            'Increase or clear Maximum model messages per run in Settings > Limits '
            '(max_messages in settings.json; null or omitted means unlimited), then restart the mod.',
        'request_timeout': 'The provider timed out. The service remains running; a later turn can retry.',
        'connection_failed': 'Could not reach the provider. Check connectivity; later scheduled turns can retry.',
    }
    return f'Chat error: {code}. ' + advice.get(code,
        MESSAGES.get(code,'The service remains running. Check settings, connectivity or the debugger for details.'))


class Notices:
    """Print changes, then reminders for persistent errors instead of a polling flood."""
    def __init__(self, emit=print, clock=time.monotonic):
        self.emit, self.clock, self.last = emit, clock, {}

    def show(self, channel, text, *, error=False):
        now = self.clock()
        previous, at = self.last.get(channel, (None, 0))
        if text != previous or (error and now - at >= 30):
            self.emit(text, flush=True)
            self.last[channel] = (text, now)


class ObserverSupervisor:
    def __init__(self, root, inspector, notices, *, discover=game_processes, validator=None, collector=collect):
        self.root, self.inspector, self.notices = root, inspector, notices
        self.discover, self.validator, self.collector = discover, validator, collector
        self.active = None
        self.task = None

    @property
    def pid(self):
        return self.active.pid if self.active is not None else None

    async def detach(self):
        self.active = None  # revoke dispatch before awaiting sensor cleanup
        self.inspector.update(ControlObservation('disconnected', 0, time.monotonic()), {}, None, None)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def step(self):
        try:
            target = select_game(await asyncio.to_thread(self.discover), self.root)
        except (OSError, RuntimeError) as error:
            await self.detach()
            self.notices.show('attach', f'Cannot discover the game: {error} Retrying in 3 seconds.', error=True)
            return
        if target != self.active:
            await self.detach()
        if target is None:
            self.notices.show('attach', 'Waiting for the game. Launch it normally from the selected folder; reconnect is automatic.')
            return
        if self.task and self.task.done():
            try:
                self.task.result()
            except Exception as error:
                self.notices.show('sensor', f'Observer stopped ({type(error).__name__}). Reconnecting.', error=True)
            await self.detach()
        if self.active is None:
            try:
                if self.validator is not None:
                    await asyncio.to_thread(self.validator, target.pid)
            except (OSError, ValueError) as error:
                self.notices.show('attach', f'Cannot attach: {error} Retrying in 3 seconds. '
                    'Check the installed bridge and run the launcher as administrator before the game.', error=True)
                return
            self.active = target
            self.task = asyncio.create_task(self.collector(self.inspector, target.pid, float('inf')))
            self.notices.show('attach', 'Game attached. Ctrl+Enter opens creature chat when gameplay allows it.')


def validate_reader(pid):
    from .training_live import TrainingReader
    from .pipe import snapshot
    with TrainingReader(pid):
        snapshot(pid, timeout_ms=750)


async def play(root, settings, settings_path, *, dummy=False, actions=True, debug=False,
               overlay=True, automatic=True, seconds=43200, port=0, emit=print, debug_session=None):
    from .qa_live import serve
    if not actions:
        settings = replace(settings, wake_on_question=False)
    notices = Notices(emit)
    notices.show('installation', f'Black & White installation: {root.resolve()}')
    inspector = Inspector(limit=120 if debug and debug_session is None else 0, history_interval=3.0)
    supervisor = ObserverSupervisor(root, inspector, notices, validator=validate_reader)
    await supervisor.detach()
    overlay_process = None
    runtime_ref = []
    from .game_display import DisplayMonitor, DisplayWindows
    display = DisplayMonitor(DisplayWindows(), lambda: supervisor.pid)
    if overlay:display.overlay_alive=lambda: overlay_process is not None and overlay_process.poll() is None

    def ready(url, runtime):
        nonlocal overlay_process
        runtime_ref.append(runtime)
        runtime.set_display_blocker(display.reason)
        emit('Dummy backend: no API calls.' if dummy else 'Using the provider and model in settings.json.', flush=True)
        emit('Creature actions enabled.' if actions else 'Diagnostic chat-only mode: creature actions disabled by --no-actions.', flush=True)
        emit('Automatic creature thinking enabled.' if automatic else 'Automatic creature thinking off; direct questions remain available.', flush=True)
        if overlay:
            try:
                overlay_process = subprocess.Popen([sys.executable, '-m', 'bwv.cli', 'overlay', url,
                    '--seconds', str(seconds), '--stop-on-stdin'], stdin=subprocess.PIPE,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as error:
                emit(f'ERROR: Could not start the chat overlay: {error}. Observer and chat remain available.', flush=True)

    chat = asyncio.create_task(serve(None, port=port, seconds=seconds,
        config=None if dummy else settings, dummy_settings=settings if dummy else None,
        settings_path=settings_path, debug=debug, current_action_pid=(lambda: supervisor.pid) if actions else None,
        automatic=automatic, on_ready=ready, local_pipe=True,
        request_details=inspector.detail_refresh.request,
        debug_observer=inspector if debug or debug_session is not None else None, debug_session=debug_session,
        source_reader=inspector.latest_frame))
    try:
        while not chat.done():
            await supervisor.step()
            reason=display.reason()
            from .errors import MESSAGES
            notices.show('display', MESSAGES.get(reason,'Creature chat display ready.') if reason
                         else 'Creature chat display ready; model calls may resume when gameplay allows.')
            frame = inspector.latest_frame() or {}
            error = frame.get('diagnostics', {}).get('error')
            if error:
                notices.show('sensor', f'Sensor error: {error}. Unavailable readings are omitted; observation continues.', error=True)
            else:
                if notices.last.get('sensor', (None,))[0]:
                    notices.show('sensor', 'Sensor readings recovered.')
            if runtime_ref:
                error = getattr(runtime_ref[0].provider, 'last_error', None) or runtime_ref[0].last_error
                if error and supervisor.pid is not None:
                    notices.show('chat', chat_error(error), error=True)
                elif not error and notices.last.get('chat', (None,))[0]:
                    notices.show('chat', 'Chat service recovered.')
            if overlay_process is not None and overlay_process.poll() is not None:
                emit(f'ERROR: Chat overlay exited (code {overlay_process.returncode}). '
                      'Observer and chat remain running. Restart the launcher to restore the overlay.', flush=True)
                overlay_process = None
            await asyncio.sleep(3)
        await chat  # surface configuration, bind and other startup failures
    finally:
        supervisor.active = None  # revoke native dispatch immediately on stop
        if overlay_process is not None and overlay_process.poll() is None:
            from .launcher_lifecycle import request_overlay_close
            request_overlay_close(overlay_process)
        chat.cancel()
        await asyncio.gather(chat, return_exceptions=True)
        await supervisor.detach()
        if overlay_process is not None and overlay_process.poll() is None:
            try:
                await asyncio.to_thread(overlay_process.wait, timeout=1)
            except subprocess.TimeoutExpired:
                emit('ERROR: Overlay did not close in time; stopping the overlay process.', flush=True)
                overlay_process.terminate()
                await asyncio.to_thread(overlay_process.wait)
        emit('Mod stopped. The game and its saves were not closed or changed.', flush=True)
