"""Local synthetic pipe servers exercise transport failures without the game."""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import os
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

from bwv.pipe import snapshot, _connect_snapshot


@contextmanager
def server(payload):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel.ConnectNamedPipe.restype = wintypes.BOOL
    kernel.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # This Python process is not a game host; reserve its own unique PID pipe.
    pipe = kernel.CreateNamedPipeW(rf"\\.\pipe\bwv-observer-{os.getpid()}",
        0x00080002, 0, 1, 4096, 0, 1000, None)
    if pipe == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    done = threading.Event()
    failures = []

    def serve():
        try:
            if not kernel.ConnectNamedPipe(pipe, None) and ctypes.get_last_error() != 535:
                raise ctypes.WinError(ctypes.get_last_error())
            if payload is not None:
                written = wintypes.DWORD()
                if not kernel.WriteFile(pipe, payload, len(payload), ctypes.byref(written), None):
                    raise ctypes.WinError(ctypes.get_last_error())
            done.wait(3)
        except Exception as failure:
            failures.append(failure)
        finally:
            kernel.CloseHandle(pipe)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield os.getpid()
    finally:
        done.set()
        thread.join(4)
        if failures:
            raise failures[0]
        if thread.is_alive():
            raise RuntimeError("Synthetic pipe did not finish")


@unittest.skipUnless(os.name == "nt", "Windows named pipes")
class PipeTests(unittest.TestCase):
    def test_competing_reader_between_wait_and_open_retries_same_deadline(self):
        kernel=Mock()
        kernel.WaitNamedPipeW.return_value=True
        kernel.CreateFileW.side_effect=[wintypes.HANDLE(-1).value,123]
        with patch('bwv.pipe.ctypes.get_last_error',return_value=231), \
                patch('bwv.pipe.time.monotonic',side_effect=[10,10.1,10.2]), \
                patch('bwv.pipe.time.sleep') as sleep:
            self.assertEqual(_connect_snapshot(kernel,'synthetic-pipe',12),123)
        self.assertEqual(kernel.CreateFileW.call_count,2)
        waits=[call.args[1] for call in kernel.WaitNamedPipeW.call_args_list]
        self.assertEqual(waits[0],2000)
        self.assertLess(waits[1],waits[0])
        sleep.assert_called_once_with(.01)

    def test_busy_connection_cannot_extend_deadline(self):
        kernel=Mock()
        kernel.WaitNamedPipeW.return_value=True
        kernel.CreateFileW.return_value=wintypes.HANDLE(-1).value
        with patch('bwv.pipe.ctypes.get_last_error',return_value=231), \
                patch('bwv.pipe.time.monotonic',side_effect=[10,10.1,12]), \
                patch('bwv.pipe.time.sleep'):
            with self.assertRaises(TimeoutError):_connect_snapshot(kernel,'synthetic-pipe',12)
        kernel.CreateFileW.assert_called_once()

    def test_connection_loss_and_access_denial_are_not_retried(self):
        for wait_ok,error_code in ((False,2),(True,5)):
            kernel=Mock()
            kernel.WaitNamedPipeW.return_value=wait_ok
            kernel.CreateFileW.return_value=wintypes.HANDLE(-1).value
            with patch('bwv.pipe.ctypes.get_last_error',return_value=error_code), \
                    patch('bwv.pipe.time.monotonic',return_value=10), \
                    patch('bwv.pipe.time.sleep') as sleep:
                with self.assertRaises(OSError):_connect_snapshot(kernel,'synthetic-pipe',12)
            kernel.WaitNamedPipeW.assert_called_once()
            self.assertEqual(kernel.CreateFileW.call_count,int(wait_ok))
            sleep.assert_not_called()

    def test_expanded_catalog_larger_than_old_64k_limit_is_received_intact(self):
        data={'world_actions':{'offers':[{'token':i,'action':'poo_on','kind':'field',
              'distance_engine_units':20.0} for i in range(1024)]}}
        raw=(json.dumps(data)+'\n').encode()
        self.assertGreater(len(raw),65536)
        with server(raw) as pid:
            result=snapshot(pid)
            self.assertEqual(result['world_actions'],data['world_actions'])

    def test_connected_but_silent_server_times_out(self):
        with server(None) as pid:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                snapshot(pid, timeout_ms=150)
            self.assertLess(time.monotonic() - started, 2)

    def test_non_object_response_is_rejected(self):
        with server(b"[]\n") as pid:
            with self.assertRaisesRegex(ValueError, "Invalid bridge response"):
                snapshot(pid)

    def test_response_without_timestamp_is_not_fresh(self):
        with server(b'{"protocol":1}\n') as pid:
            self.assertIsNone(snapshot(pid)["source_age_ms"])


if __name__ == "__main__":
    unittest.main()
