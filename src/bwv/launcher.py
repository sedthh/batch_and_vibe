"""User-facing Windows entry point. No process IDs or service URLs required."""
import argparse
import asyncio
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

from .compat import inspect_install, sha256, VM_PATH, unsupported_reason
from .llm import load_settings, init_settings
from .processes import game_processes

_relaunched = False


def arguments(argv=None):
    parser = argparse.ArgumentParser(description='Start Black & White creature chat. Start the mod before the game. '
        'Uses settings.json by default. Ctrl+C stops the mod without closing the game.')
    parser.add_argument('--game', type=Path, help='Game folder; remembered privately after selection')
    parser.add_argument('--config', type=Path, help='Private settings file (default: settings.json)')
    parser.add_argument('--dummy', action='store_true', help='Use deterministic test replies without any provider calls')
    parser.add_argument('--debug', action='store_true', help='Enable and print browser debugger URLs')
    parser.add_argument('--port', type=int, help='Localhost port for both debug pages; requires --debug (default: a free port)')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--actions', dest='actions', action='store_true', default=True,
                         help='Enable creature actions (default)')
    actions.add_argument('--no-actions', dest='actions', action='store_false',
                         help='Diagnostic mode: chat and observation only, with no creature commands')
    parser.add_argument('--no-ticks', action='store_true', help='Only send direct questions; disable automatic creature thinking')
    parser.add_argument('--no-overlay', action='store_true', help='Do not show in-game chat overlay')
    parser.add_argument('--no-window', action='store_true', help='Console only; Ctrl+C or closing the console stops the mod')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--install', action='store_true', help='Install/update the compiled bridge while the game is closed, then exit')
    group.add_argument('--uninstall', action='store_true', help='Restore the original DLL while the game is closed, then exit')
    group.add_argument('--configure', action='store_true', help='Edit model, API key and basic settings without starting the mod or requesting elevation')
    parser.add_argument('--bridge', type=Path, default=Path('build/native-held-actions/ScriptLibraryR.dll'))
    args = parser.parse_args(argv)
    if args.port is not None:
        if not args.debug:
            parser.error('--port requires --debug; normal startup has no HTTP server')
        if not 1 <= args.port <= 65535:
            parser.error('--port must be between 1 and 65535')
    return args


def show_install_success(*, continuing=False, uninstall=False):
    """Notify the user from the elevated installer, after verification succeeds."""
    message = ('Uninstall completed successfully.\n\n'
               'The original game DLL has been restored.\n'
               'Your saves, mod settings and creature memory were kept.' if uninstall else
               'Installation completed successfully.\n\n'
               'The mod bridge is installed and verified.\n'
               'Chat history cleared. Saved memories kept.\n\n'
               + ('Press OK to continue starting the mod.' if continuing else
                  'Start the mod with Start-Mod.cmd before launching Black & White.'))
    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        user.MessageBoxW.restype = ctypes.c_int
        if not user.MessageBoxW(None, message,
                'Black & White LLM Creature Mod', 0x40 | 0x10000):
            raise OSError('Windows could not display the confirmation')
    except OSError as error:
        operation = 'Uninstall' if uninstall else 'Installation'
        print(f'WARNING: {operation} succeeded, but its confirmation popup failed: {error}',
              file=sys.stderr, flush=True)


def ensure_admin(argv):
    global _relaunched
    if os.name != 'nt':
        raise RuntimeError('This launcher requires Windows.')
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.IsUserAnAdmin.restype = ctypes.c_int
    if shell.IsUserAnAdmin():
        return True
    if game_processes():
        raise RuntimeError('Close Black & White, then start the mod again. Administrator elevation '
                           'during gameplay can freeze the game display. No elevation was requested.')
    print('Requesting administrator access before the game starts.', flush=True)
    shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_void_p
    params = subprocess.list2cmdline(['-m', 'bwv.launcher', *argv])
    result = shell.ShellExecuteW(None, 'runas', sys.executable, params, str(Path.cwd()), 1)
    if result is None or result <= 32:
        raise RuntimeError('Administrator access was declined or could not start the launcher. Nothing was changed.')
    _relaunched = True
    return False


@contextmanager
def instance_lock(path):
    """Windows releases this byte-range lock even if the launcher crashes."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('The mod launcher is already running for this folder. Use that window or close it first.') from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def choose_game(explicit, storage):
    candidate = explicit
    if candidate is None and storage.exists():
        try:
            candidate = Path(json.loads(storage.read_text('utf-8'))['game_directory'])
        except (OSError, ValueError, KeyError, TypeError):
            print('ERROR: Saved launcher folder is invalid. Please select the game folder again.', flush=True)
    if candidate is None:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk(); window.withdraw()
        try:
            selected = filedialog.askdirectory(title='Choose the Black & White folder containing runblack.exe')
        finally:
            window.destroy()
        if not selected:
            raise ValueError('No game folder selected. Start again or pass --game with its folder.')
        candidate = Path(selected)
    candidate = candidate.resolve(strict=True)
    if not (candidate / 'runblack.exe').is_file():
        raise ValueError('The selected folder does not contain runblack.exe. Use --game to select the correct folder.')
    storage.parent.mkdir(parents=True, exist_ok=True)
    temporary = storage.with_suffix('.tmp')
    temporary.write_text(json.dumps({'game_directory': str(candidate)}, indent=2), encoding='utf-8')
    temporary.replace(storage)
    return candidate


def check_installed(root):
    report = inspect_install(root)
    if report['status'] != 'experimental' or not report['abi_candidate']:
        raise ValueError(unsupported_reason(report)+' Nothing changed.')
    if not report['installed']:
        raise ValueError('The bridge is not installed. Close the game and run Start-Mod.cmd --install first.')
    ledger = json.loads((root / 'bwv-install.json').read_text('utf-8'))
    if (ledger.get('schema_version') != 1 or sha256(root / VM_PATH) != ledger.get('bridge_sha256')
            or report['vm_sha256'] != ledger.get('original_vm_sha256')):
        raise ValueError('Installed bridge or original backup differs from its manifest. Repair the installation before starting.')


def finish_install(root):
    """Verify first, then retire conversation data from the previous tool set."""
    check_installed(root)
    print('Bridge installed and verified.', flush=True)
    from .memory_store import clear_installed_history
    result = clear_installed_history()
    if not result['history_cleared']:
        raise RuntimeError('The bridge is installed, but some saved chat history could not be cleared. '
                           'Those files were preserved. Close the mod and retry --install.')
    print('Previous chat history cleared. Saved creature memories were kept.', flush=True)


def ensure_installed(root, bridge, *, window=True):
    """Install a missing bridge, never overwrite a damaged/unrecognized install."""
    report = inspect_install(root)
    if report['status'] != 'experimental' or not report['abi_candidate']:
        raise ValueError(unsupported_reason(report)+' Nothing changed.')
    if not report['installed']:
        if game_processes():
            raise RuntimeError('Close Black & White and restart the mod to install its missing bridge. Nothing changed.')
        if not bridge.is_file():
            raise ValueError('The compiled mod bridge is missing. Close the game and run Setup-Mod.cmd, then start the mod again.')
        from .install import update
        print('Mod bridge is missing. Installing it now...', flush=True)
        update(root, bridge)
        finish_install(root)
        if window:
            show_install_success(continuing=True)
    else:
        check_installed(root)


def prepare_provider(settings, settings_path, *, dummy):
    if dummy:
        return
    from .credentials import credential_environment
    if settings.model == 'YOUR_MODEL_ID':
        raise ValueError('Choose a model in settings.json. Put its API key in .local/.env '
                         '(the variable named by api_key_env), or use --dummy for offline testing.')
    credentials = credential_environment(settings.api_key_env, settings_path.parent / '.local' / '.env')
    if settings.api_key_env is not None and not credentials.get(settings.api_key_env):
        raise ValueError('The configured API key is missing. Set the environment variable named by '
                         'api_key_env, or put it in .local/.env beside settings.json. No provider calls were made.')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = arguments(argv)
    if not (args.configure or args.install or args.uninstall):
        # Console only: do not route this through the status window's emit().
        print('CLOSING THIS WINDOW WILL ALSO CLOSE THE MOD.', flush=True)
    try:
        # Help/argument errors need no elevation, and provider setup errors are
        # shown before asking the user to close the game for elevation.
        config_path = (args.config or Path('settings.json')).resolve()
        if not config_path.exists() and args.config is None:
            init_settings(config_path)
            print('Created settings.json with editable defaults. Credentials belong in .local/.env.', flush=True)
        if args.configure:
            from .launcher_ui import SettingsWindow
            SettingsWindow(config_path).root.mainloop()
            return 0
        if not (args.install or args.uninstall):
            settings, config_path = load_settings(config_path)
            try:
                prepare_provider(settings, config_path, dummy=args.dummy)
            except ValueError as error:
                if args.no_window:
                    raise
                print(f'ERROR: {error}', file=sys.stderr, flush=True)
                print('Model setup is needed. Save your settings in the setup window to continue.', flush=True)
                from .launcher_ui import SettingsWindow
                SettingsWindow(config_path).root.mainloop()
                settings, config_path = load_settings(config_path)
                prepare_provider(settings, config_path, dummy=args.dummy)
        if not ensure_admin(argv):
            return 0
        with instance_lock(config_path.parent / '.local' / 'launcher.lock'):
            root = choose_game(args.game, config_path.parent / '.local' / 'launcher.json')
            if args.install or args.uninstall:
                if game_processes():
                    raise RuntimeError('Close every Black & White instance before installing or removing the bridge. Nothing changed.')
                if args.uninstall:
                    from .install import uninstall
                    uninstall(root)
                    print('Original DLL restored. Save files were not accessed.', flush=True)
                    if not args.no_window:
                        show_install_success(uninstall=True)
                else:
                    if not args.bridge.is_file():
                        raise ValueError('Compiled bridge not found. Run Setup-Mod.cmd first, then retry --install.')
                    from .install import update
                    update(root, args.bridge)
                    finish_install(root)
                    if not args.no_window:
                        show_install_success()
                return 0
            ensure_installed(root, args.bridge, window=not args.no_window)
            from .supervisor import play
            from .launcher_lifecycle import run_managed
            from .game_display import GameLauncher
            from .debug_session import DebugSession
            debug_session = DebugSession(args.debug, args.port or 0)
            game_launcher = None if args.no_window else GameLauncher(root)
            automatic = not args.no_ticks

            def apply_settings(updated):
                nonlocal settings, automatic
                prepare_provider(updated, config_path, dummy=args.dummy)
                if debug_session.runtime is not None:
                    automatic = debug_session.runtime.automatic
                settings = updated

            asyncio.run(run_managed(lambda emit: play(root, settings, config_path, dummy=args.dummy, actions=args.actions,
                             debug=args.debug, overlay=not args.no_overlay, automatic=automatic,
                             port=args.port or 0, emit=emit, debug_session=debug_session), config_path, window=not args.no_window,
                             game_launcher=game_launcher, debug_session=debug_session,
                             apply_settings=apply_settings))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        # Provider exceptions already redact credentials; never dump settings or env.
        print(f'ERROR: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    code = main()
    if code and sys.stdin.isatty() and sys.stdout.isatty() and not _relaunched:
        try:
            input('Press Enter to close this launcher window...')
        except (EOFError, KeyboardInterrupt):
            pass
    raise SystemExit(code)
