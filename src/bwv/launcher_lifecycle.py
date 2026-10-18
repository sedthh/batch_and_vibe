"""One cooperative shutdown path for the launcher window and console controls."""
import asyncio
from contextlib import contextmanager
import ctypes
import os
import threading


@contextmanager
def console_shutdown(request_stop):
    """Give Python cleanup time before Windows terminates a closed console."""
    finished = threading.Event()
    handler = kernel = None
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def control(event):
            if event not in (0, 1, 2, 5, 6):
                return False
            request_stop()
            # Close/logoff/shutdown handlers run on a Windows thread. Returning
            # immediately would let Windows kill us before asyncio's finallys.
            if event in (2, 5, 6):
                finished.wait(4)
            return True

        handler = callback_type(control)
        kernel.SetConsoleCtrlHandler.argtypes = [callback_type, ctypes.c_int]
        kernel.SetConsoleCtrlHandler.restype = ctypes.c_int
        if not kernel.SetConsoleCtrlHandler(handler, True):
            raise OSError('Could not register graceful console shutdown.')
    try:
        yield
    finally:
        finished.set()
        if handler is not None:
            kernel.SetConsoleCtrlHandler(handler, False)


async def run_managed(factory, settings_path, *, window=True, game_launcher=None, debug_session=None,
                      apply_settings=None):
    """Stop or restart services serially, keeping the launcher responsive during cleanup."""
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    restart = asyncio.Event()
    ui = None
    service = None

    def request_stop():
        loop.call_soon_threadsafe(stop.set)

    def emit(text, **kwargs):
        print(text, flush=True)
        if ui is not None:
            ui.write(text)

    def request_restart(config):
        if stop.is_set() or restart.is_set():
            raise ValueError('The mod is already stopping or applying settings.')
        # Validate before stopping the working services. Only an explicit UI save
        # reaches this callback; there is no settings-file polling.
        apply_settings(config)
        restart.set()
        if ui is not None:
            ui.set_restarting(True)
        emit('Settings saved. Applying them to the mod...')

    with console_shutdown(request_stop):
        try:
            if window:
                from .launcher_ui import StatusWindow
                ui = StatusWindow(settings_path, request_stop)
                if apply_settings is not None:ui.restart = request_restart
                if game_launcher is not None:ui.set_game_launcher(game_launcher)
                if debug_session is not None:ui.set_debug_session(debug_session)
            if debug_session is not None:debug_session.emit = emit
            emit('Stop the mod with Stop mod, the window close button, or Ctrl+C in the console. '
                 'The game stays running. Closing the game alone keeps the mod waiting for a restart.')
            service = asyncio.create_task(factory(emit))
            while True:
                cancelled = False
                debug_options = None
                while not service.done():
                    if ui is not None:
                        ui.pump()
                    # A debugger toggle/port change already in progress must
                    # finish before its selected state is saved for the restart.
                    debug_busy = ui is not None and getattr(ui, 'debug_task', None) is not None
                    if not cancelled and (stop.is_set() or (restart.is_set() and not debug_busy)):
                        if restart.is_set() and debug_session is not None:
                            debug_options = debug_session.enabled, debug_session.port
                        cancelled = True
                        service.cancel()
                    await asyncio.sleep(.05)
                try:
                    await service
                except asyncio.CancelledError:
                    if not cancelled:
                        raise
                if stop.is_set() or not restart.is_set():
                    break
                if debug_options is not None:
                    debug_session.enabled, debug_session.port = debug_options
                restart.clear()
                service = asyncio.create_task(factory(emit))
                if ui is not None:ui.set_restarting(False)
        finally:
            if service is not None and not service.done():
                service.cancel()
                await asyncio.gather(service, return_exceptions=True)
            if ui is not None:
                ui.close()


def request_overlay_close(process):
    """EOF on the launcher's private child stdin requests normal Tk cleanup."""
    if process.stdin is not None:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass  # the overlay has already closed its end
