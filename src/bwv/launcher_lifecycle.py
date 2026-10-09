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


async def run_managed(factory, settings_path, *, window=True, game_launcher=None, debug_session=None):
    """Cancel once, then await all service cleanup. UI stays responsive meanwhile."""
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    ui = None
    service = None

    def request_stop():
        loop.call_soon_threadsafe(stop.set)

    def emit(text, **kwargs):
        print(text, flush=True)
        if ui is not None:
            ui.write(text)

    with console_shutdown(request_stop):
        try:
            if window:
                from .launcher_ui import StatusWindow
                ui = StatusWindow(settings_path, request_stop)
                if game_launcher is not None:ui.set_game_launcher(game_launcher)
                if debug_session is not None:ui.set_debug_session(debug_session)
            if debug_session is not None:debug_session.emit = emit
            emit('Stop the mod with Stop mod, the window close button, or Ctrl+C in the console. '
                 'The game stays running. Closing the game alone keeps the mod waiting for a restart.')
            service = asyncio.create_task(factory(emit))
            cancelled = False
            while not service.done():
                if ui is not None:
                    ui.pump()
                if stop.is_set() and not cancelled:
                    cancelled = True
                    service.cancel()
                await asyncio.sleep(.05)
            try:
                await service
            except asyncio.CancelledError:
                if not cancelled:
                    raise
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
