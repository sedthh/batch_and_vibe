"""Small local launcher/settings windows. No network calls or credential display."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import re
import tempfile

from . import __version__
from .llm import LLMConfig


WINDOW_TITLE = f'Black & White LLM Creature Mod v{__version__}'


PROVIDER_PRESETS = {
    'OpenAI': {'api': 'responses', 'base_url': 'https://api.openai.com/v1',
               'api_key_env': 'OPENAI_API_KEY', 'output_format': 'json_schema',
               'token_parameter': 'max_completion_tokens'},
    'Claude (compatibility API)': {'api': 'chat_completions',
               'base_url': 'https://api.anthropic.com/v1',
               'api_key_env': 'ANTHROPIC_API_KEY', 'output_format': 'json_object',
               'token_parameter': 'max_tokens'},
    'Gemini (compatibility API)': {'api': 'chat_completions',
               'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
               'api_key_env': 'GEMINI_API_KEY', 'output_format': 'json_schema',
               'token_parameter': 'max_tokens'},
    'Custom / local compatible': {'api': 'chat_completions', 'base_url': '',
               'api_key_env': '', 'output_format': 'json_schema',
               'token_parameter': 'max_tokens'},
}


def apply_provider_preset(values, key, name):
    """Update the unsaved form only; never carry a typed secret to another provider."""
    for field, value in PROVIDER_PRESETS[name].items():
        values[field].set(value)
    values['model'].set('')
    values['reasoning_effort'].set('')
    key.set('')


def save_basics(path, values, key='', *, voice_key=''):
    """Merge form fields into the latest private settings; preserve advanced fields."""
    path = Path(path)
    data = asdict(LLMConfig.load(path))
    if 'general_prompt' in values:
        data['prompts'] = {'general': values['general_prompt']}
    if 'reenable_cut_content' in values:
        data['reenable_cut_content'] = values['reenable_cut_content']
    from .voice_settings_ui import voice_form_values
    data['voice'].update(voice_form_values(values))
    for field in ('model', 'api', 'base_url', 'output_format', 'token_parameter'):
        data[field] = values[field].strip()
    data['api_key_env'] = values['api_key_env'].strip() or None
    data['reasoning_effort'] = values['reasoning_effort'].strip() or None
    try:
        data['max_messages'] = int(values['max_messages']) if values['max_messages'].strip() else None
    except ValueError:
        raise ValueError('Maximum model messages must be a whole number, or blank for no limit.') from None
    for field in ('max_output_tokens', 'max_tool_rounds', 'max_tool_calls'):
        try:
            data[field] = int(values[field])
        except ValueError:
            raise ValueError(f'{field} must be a whole number.') from None
    for field in ('timeout_seconds', 'event_cooldown_seconds'):
        try:
            data[field] = float(values[field])
        except ValueError:
            raise ValueError(f'{field} must be a number.') from None
    for field in ('active_seconds', 'settlement_seconds', 'quiet_seconds', 'distant_seconds', 'jitter_percent'):
        try:
            data['cadence'][field] = float(values[field])
        except ValueError:
            raise ValueError(f'{field} must be a number.') from None
    config = LLMConfig(**data)
    if config.model == 'YOUR_MODEL_ID':
        raise ValueError('Enter the model ID supplied by your provider or local server.')
    credentials = {}
    for name, secret in ((config.api_key_env, key.strip()), (config.voice.api_key_env, voice_key.strip())):
        if not secret: continue
        if name is None or any(ord(c) < 33 or ord(c) > 126 or c in '\"\'' for c in secret):
            raise ValueError('Use a single-line API key and set its environment-variable name.')
        if name in credentials and credentials[name] != secret:
            raise ValueError('Use different key variable names for different LLM and voice API keys.')
        credentials[name] = secret
    env_path = path.parent / '.local' / '.env'
    content = None
    if credentials:
        try:
            previous = env_path.read_text('utf-8-sig') if env_path.exists() else ''
        except (OSError, UnicodeError):
            raise ValueError('Cannot read the private API-key file. Nothing was saved.') from None
        pattern = re.compile(r'^\s*(?:export\s+)?(?:' + '|'.join(re.escape(n) for n in credentials) + r')\s*=')
        lines = [line for line in previous.splitlines() if not pattern.match(line)]
        content = '\n'.join([*lines, *(f'{n}={v}' for n, v in credentials.items()), ''])
    # Validation happens before either file is modified. Blank key preserves it.
    config.save(path)
    if content is not None:
        temporary = None
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=env_path.parent, prefix='.env.', suffix='.tmp',
                    mode='w', encoding='utf-8', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(env_path)
        except OSError:
            raise ValueError('Settings were saved, but the API key could not be saved. '
                             'The previous key was preserved; correct file permissions and retry.') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return config


class SettingsWindow:
    def __init__(self, path, parent=None, *, on_saved=None):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
        self.path = Path(path)
        config = asdict(LLMConfig.load(path))
        self.root = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.root.title('Creature mod settings')
        self.root.resizable(False, False)
        self.values = {}
        frame = ttk.Frame(self.root, padding=16); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Model and basic settings', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(frame, text=('Save to apply changes. The game can stay open.' if on_saved else
            'Saved locally for the next mod start. Saving sends no API requests.')).pack(anchor='w', pady=(4, 10))
        tabs = ttk.Notebook(frame); tabs.pack(fill='both', expand=True)
        connection = ttk.Frame(tabs, padding=12); tabs.add(connection, text='Connection')
        from .voice_settings_ui import VoiceTab
        self.voice_tab = VoiceTab(tabs, config['voice'], self.values)
        limits = ttk.Frame(tabs, padding=12); tabs.add(limits, text='Limits')
        cadence = ttk.Frame(tabs, padding=12); tabs.add(cadence, text='Timing')
        prompt = ttk.Frame(tabs, padding=12); tabs.add(prompt, text='General prompt')
        ttk.Label(prompt, text='Customize game background, personality and speaking style.\n'
            'Tool, engine and memory rules are added separately and cannot be edited here.',
            wraplength=570).pack(anchor='w', pady=(0, 8))
        self.general_prompt = ScrolledText(prompt, width=68, height=20, wrap='word', undo=True)
        self.general_prompt.insert('1.0', config['prompts']['general'])
        self.general_prompt.pack(fill='both', expand=True)

        def field(panel, name, label, choices=None, source=config):
            row = panel.grid_size()[1]
            value = source.get(name)
            variable = tk.StringVar(value='' if value is None else str(value))
            self.values[name] = variable
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
            widget = (ttk.Combobox(panel, textvariable=variable, values=choices, state='readonly', width=36)
                      if choices else ttk.Entry(panel, textvariable=variable, width=39))
            widget.grid(row=row, column=1, sticky='ew', pady=4)

        preset = tk.StringVar(value='Choose a preset')
        ttk.Label(connection, text='Provider setup preset').grid(row=0, column=0, sticky='w', padx=(0, 12), pady=4)
        preset_controls = ttk.Frame(connection)
        preset_controls.grid(row=0, column=1, sticky='ew', pady=4)
        ttk.Combobox(preset_controls, textvariable=preset, values=tuple(PROVIDER_PRESETS),
                     state='readonly', width=27).pack(side='left')
        def apply_preset():
            if preset.get() in PROVIDER_PRESETS:
                apply_provider_preset(self.values, self.key, preset.get())
        ttk.Button(preset_controls, text='Apply', command=apply_preset).pack(side='right')
        field(connection, 'model', 'Model ID')
        field(connection, 'base_url', 'API base URL (provider-specific)')
        field(connection, 'api_key_env', 'API key environment variable name')
        ttk.Label(connection, text='API key (blank keeps saved key)').grid(row=4, column=0, sticky='w', padx=(0, 12), pady=4)
        self.key = tk.StringVar()
        ttk.Entry(connection, textvariable=self.key, show='*', width=39).grid(row=4, column=1, sticky='ew', pady=4)
        field(connection, 'api', 'API protocol', ('responses', 'chat_completions'))
        field(connection, 'output_format', 'Structured output format', ('json_schema', 'json_object'))
        field(connection, 'token_parameter', 'Chat Completions token field', ('max_completion_tokens', 'max_tokens'))
        ttk.Label(connection, text='Presets fill connection options and clear the model, reasoning choice and unsaved key.\n'
            'Enter a model ID and key for that provider, then save your settings.\n'
            'Custom/local servers need their own base URL and supported formats.\n'
            'Claude uses its compatibility API: schema enforcement and reasoning controls are unavailable.\n'
            'Variable name: e.g. OPENAI_API_KEY, not the secret key. Leave it blank only if no key is required.\n'
            'Keys go only in .local/.env; an existing process environment key takes precedence.',
            wraplength=570).grid(row=8, column=0, columnspan=2, sticky='w', pady=(12, 0))
        for name, label in (('max_output_tokens', 'Maximum output tokens per call'),
                            ('max_messages', 'Maximum model messages per run (blank = unlimited)'),
                            ('timeout_seconds', 'Call timeout (seconds)'),
                            ('max_tool_rounds', 'Maximum tool rounds per turn'),
                            ('max_tool_calls', 'Maximum tool calls per turn')):
            field(limits, name, label)
        field(limits, 'reasoning_effort', 'Reasoning effort (blank = provider default)',
              ('', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'))
        ttk.Label(limits, text='One user message or automatic tick counts once, including all tool rounds.\n'
            'Blank means no message cap. API calls and token usage are tracked separately.\n'
            'A message counts when its first API call starts, even if it later fails or is cancelled.\n'
            'The last allowed message can finish; the cap then pauses new messages until restart.\n'
            'Reasoning options and output formats depend on the chosen model.',wraplength=570).grid(
            row=6, column=0, columnspan=2, sticky='w', pady=12)
        for name, label in (('active_seconds', 'Active surroundings (seconds)'),
                            ('settlement_seconds', 'Settlement (seconds)'),
                            ('quiet_seconds', 'Quiet surroundings (seconds)'),
                            ('distant_seconds', 'Distant player (seconds)'),
                            ('jitter_percent', 'Timing randomness (%)')):
            field(cadence, name, label, source=config['cadence'])
        field(cadence, 'event_cooldown_seconds', 'Important-event cooldown (seconds)')
        ttk.Label(frame, text='The general prompt and other settings are saved locally in settings.json.').pack(anchor='w', pady=10)
        self.error = tk.StringVar()
        ttk.Label(frame, textvariable=self.error, foreground='#b00020', wraplength=570).pack(anchor='w')
        buttons = ttk.Frame(frame); buttons.pack(fill='x')

        def save():
            try:
                values = {k: v.get() for k, v in self.values.items()}
                values['general_prompt'] = self.general_prompt.get('1.0', 'end-1c')
                saved = save_basics(self.path, values, self.key.get(), voice_key=self.voice_tab.key.get())
            except (ValueError, OSError) as error:
                self.error.set(str(error))
                return
            self.key.set('')
            self.voice_tab.key.set('')
            if on_saved is not None:
                try:
                    on_saved(saved)
                except (ValueError, OSError) as error:
                    self.error.set(f'Settings saved but not applied: {error}')
                    return
            else:
                print('Settings saved for the next mod start. No model call was made.', flush=True)
            self.root.destroy()

        ttk.Button(buttons, text='Save & apply' if on_saved else 'Save settings & close', command=save).pack(side='right')
        ttk.Button(buttons, text='Cancel', command=self.root.destroy).pack(side='right', padx=8)
        def open_file():
            try:
                os.startfile(str(self.path))
            except OSError:
                self.error.set('Could not open settings.json. Open it manually in a text editor.')
        ttk.Button(buttons, text='Open settings.json', command=open_file).pack(side='left')


class StatusWindow:
    def __init__(self, path, stop):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
        self.settings_path = Path(path)
        self.display_settings_path = Path(path).parent / '.local' / 'launcher-display.json'
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry('650x340')
        self.root.minsize(480, 250)
        frame = ttk.Frame(self.root, padding=14); frame.pack(fill='both', expand=True)
        self.heading = ttk.Label(frame, text='Creature mod is running', font=('Segoe UI', 12, 'bold'))
        self.heading.pack(anchor='w')
        ttk.Label(frame, text='Keep this window open while playing. Closing it stops the mod, not the game.', wraplength=590).pack(anchor='w', pady=(6, 10))
        self.log = ScrolledText(frame, height=9, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True)
        buttons = ttk.Frame(frame); buttons.pack(fill='x', pady=(10, 0))
        self.stopping = False
        self.restarting = False
        self.restart = None
        self.settings_window = None
        self.game_launcher = None
        self.game_buttons = []
        self.debug_session = None
        self.debug_task = None
        self.game_controls = ttk.Frame(frame)
        self.game_controls.pack(fill='x', before=buttons, pady=(6, 0))
        self.saved_cut_content = LLMConfig.load(path).reenable_cut_content
        self.cut_content_enabled = tk.BooleanVar(value=self.saved_cut_content)
        self.cut_content_toggle = ttk.Checkbutton(frame, text='Reenable cut content',
            variable=self.cut_content_enabled, command=self.remember_cut_content)
        self.cut_content_toggle.pack(anchor='w', after=self.game_controls, pady=(6, 0))

        def settings():
            if self.stopping:
                return
            try:
                if self.settings_window is not None and self.settings_window.root.winfo_exists():
                    self.settings_window.root.lift()
                else:
                    self.settings_window = SettingsWindow(path, self.root,
                        on_saved=self.apply_settings)
            except (ValueError, OSError) as error:
                self.write(f'ERROR: Cannot open settings: {error}')

        def close():
            if self.stopping:
                return
            self.stopping = True
            self.root.title(f'{WINDOW_TITLE} — Stopping…')
            self.heading.configure(text='Stopping…')
            self.write('Stopping the mod and closing its services...')
            self.stop_button.configure(text='Stopping…', state='disabled')
            self.settings_button.configure(state='disabled')
            self.cut_content_toggle.configure(state='disabled')
            for button in self.game_buttons:button.configure(state='disabled')
            if self.settings_window is not None and self.settings_window.root.winfo_exists():
                self.settings_window.root.attributes('-disabled', True)
            self.root.attributes('-disabled', True)
            stop()

        self.settings_button = ttk.Button(buttons, text='Model, API key & settings', command=settings)
        self.settings_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Stop mod', command=close)
        self.stop_button.pack(side='right')
        self.root.protocol('WM_DELETE_WINDOW', close)

    def apply_settings(self, config):
        if getattr(self, 'restart', None) is not None:
            self.restart(config)
        else:
            self.write(f'Model saved: {config.model}. Restart the mod to apply changes.')

    def set_restarting(self, restarting):
        self.restarting = restarting
        if self.stopping:return
        self.heading.configure(text='Applying settings...' if restarting else 'Creature mod is running')
        for button in (self.settings_button, self.cut_content_toggle):
            button.configure(state='disabled' if restarting else 'normal')
        self.refresh_game_buttons()

    def set_debug_session(self, session):
        import asyncio
        import tkinter as tk
        from tkinter import ttk
        self.debug_session = session
        controls = ttk.LabelFrame(self.game_controls.master, text='Debugging', padding=6)
        controls.pack(fill='x', after=self.cut_content_toggle, pady=(6, 0))
        self.root.geometry('780x500')
        self.root.minsize(650, 400)
        self.debug_enabled = tk.BooleanVar(value=session.enabled)
        self.debug_port = tk.StringVar(value=str(session.port) if session.port else '')
        self.debug_status = tk.StringVar(value='Starting services…')

        def apply():
            if self.stopping or self.debug_task is not None:
                return
            try:
                port = int(self.debug_port.get().strip() or '0') if self.debug_enabled.get() else session.port
                if not 0 <= port <= 65535:
                    raise ValueError()
            except ValueError:
                self.write('ERROR: Debug port must be 1–65535, or blank for an available port.')
                self.debug_enabled.set(session.enabled)
                return
            enabled = self.debug_enabled.get()

            async def change():
                try:
                    await session.configure(enabled, port)
                except (OSError, ValueError) as error:
                    self.write(f'ERROR: {error}')
                finally:
                    self.debug_enabled.set(session.enabled)
                    self.debug_task = None
            self.debug_task = asyncio.create_task(change())

        self.debug_toggle = ttk.Checkbutton(controls, text='Enable debugging', variable=self.debug_enabled, command=apply)
        self.debug_toggle.grid(row=0, column=0, sticky='w')
        ttk.Label(controls, text='Localhost port (blank = automatic)').grid(row=0, column=1, padx=(12, 4))
        self.debug_port_entry = ttk.Entry(controls, textvariable=self.debug_port, width=7)
        self.debug_port_entry.grid(row=0, column=2)
        self.debug_apply = ttk.Button(controls, text='Apply port', command=apply)
        self.debug_apply.grid(row=0, column=3, padx=6)
        self.debug_links = []

        def open_page(page):
            if not self.stopping and session.url:
                try:
                    os.startfile(session.url + page + '/')
                except OSError:
                    self.write('ERROR: Could not open the browser. Copy the debugger URL from the log.')

        for column, (label, page) in enumerate((('Open LLM debugger', 'llm'), ('Open observer', 'observer'))):
            button = ttk.Button(controls, text=label, command=lambda page=page: open_page(page))
            button.grid(row=1, column=column, sticky='w', pady=(6, 0))
            self.debug_links.append(button)
        ttk.Label(controls, textvariable=self.debug_status).grid(row=1, column=2, columnspan=2, sticky='w')

    def refresh_debug(self):
        session = self.debug_session
        if session is None:
            return
        ready = session.runtime is not None and not self.stopping and not self.restarting and self.debug_task is None
        self.debug_toggle.configure(state='normal' if ready else 'disabled')
        self.debug_port_entry.configure(state='normal' if ready else 'disabled')
        self.debug_apply.configure(state='normal' if ready and session.enabled else 'disabled')
        for button in self.debug_links:
            button.configure(state='normal' if ready and session.url else 'disabled')
        if self.debug_task is None:
            self.debug_enabled.set(session.enabled)
        self.debug_status.set('Applying…' if self.debug_task is not None else
                              '' if session.server else 'Off')

    def set_game_launcher(self, launcher):
        import tkinter as tk
        from tkinter import ttk
        from .game_display import RESOLUTIONS
        self.game_launcher=launcher
        self.root.geometry('760x420')
        self.size=tk.StringVar(value=self.load_display_size())
        self.size.trace_add('write',self.remember_display_size)
        ttk.Combobox(self.game_controls,textvariable=self.size,values=RESOLUTIONS,
                     state='readonly',width=12).pack(side='left',padx=(0,8))
        def start(mode):
            self.start_game(mode)
        for label,mode in (('Run B&W windowed','windowed'),
                           ('Run B&W borderless fullscreen','borderless')):
            button=ttk.Button(self.game_controls,text=label,command=lambda mode=mode:start(mode))
            button.pack(side='left',padx=(0,8));self.game_buttons.append(button)
        self.refresh_game_buttons()

    def remember_cut_content(self):
        if self.stopping:
            self.cut_content_enabled.set(self.saved_cut_content)
            return
        enabled = self.cut_content_enabled.get()
        try:
            config = LLMConfig.load(self.settings_path)
            updated = replace(config, reenable_cut_content=enabled)
            updated.save(self.settings_path)
        except (OSError, ValueError) as error:
            self.cut_content_enabled.set(self.saved_cut_content)
            self.write(f'ERROR: Could not save cut content setting. {error}')
            return
        self.saved_cut_content = enabled
        try:
            self.apply_settings(updated)
        except (ValueError, OSError) as error:
            self.write(f'ERROR: Cut content setting saved but not applied: {error}')

    def load_display_size(self):
        from .game_display import RESOLUTIONS
        try:
            size=json.loads(self.display_settings_path.read_text('utf-8'))['window_size']
            if size not in RESOLUTIONS:raise ValueError('Unsupported display size')
            return size
        except FileNotFoundError:
            pass
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.write(f'ERROR: Could not restore the saved display size; using 800x600. {error}')
        return '800x600'

    def remember_display_size(self, *_):
        from .game_display import RESOLUTIONS
        size=self.size.get()
        if size not in RESOLUTIONS:return
        try:
            path=self.display_settings_path
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'window_size':size},indent=2)+'\n',encoding='utf-8')
            temporary.replace(path)
        except OSError as error:
            self.write(f'ERROR: Could not remember the display size for next time. {error}')

    def start_game(self, mode):
        if self.stopping:return
        # Disable both before launch, including while configuring/spawning.
        for button in self.game_buttons:button.configure(state='disabled')
        try:
            self.write(self.game_launcher.start(mode,self.size.get(),self.root.winfo_id()))
        except (OSError,ValueError,RuntimeError) as error:
            self.write('ERROR: '+str(error))
        finally:
            self.refresh_game_buttons()

    def refresh_game_buttons(self):
        error=(self.game_launcher.installation_error if self.game_launcher is not None
               else 'No Black & White installation selected.')
        previous=getattr(self,'game_installation_error',None)
        if error!=previous:
            self.game_installation_error=error
            self.write('ERROR: '+error if error else 'Game installation is available again.')
        disabled=(self.stopping or getattr(self, 'restarting', False) or bool(error)
                  or self.game_launcher is None or self.game_launcher.busy)
        for button in self.game_buttons:
            button.configure(state='disabled' if disabled else 'normal')

    def write(self, text, **kwargs):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        if int(self.log.index('end-1c').split('.')[0]) > 150:
            self.log.delete('1.0', '20.0')
        self.log.see('end'); self.log.configure(state='disabled')

    def pump(self):
        self.refresh_debug()
        if self.game_launcher is not None and not self.stopping:
            try:
                notice=self.game_launcher.poll()
                if notice:self.write(notice)
            except (OSError,ValueError,RuntimeError) as error:
                self.write('ERROR: '+str(error))
            self.refresh_game_buttons()
        self.root.update()

    def close(self):
        self.root.destroy()
