"""Voice settings form; no recording, model loading or network access on save."""
from .voice import HOTKEYS, VoiceSettings


PRESETS = {
    'Local Whisper': {'provider': 'local', 'model': 'base'},
    'OpenAI transcription': {'provider': 'api', 'model': 'gpt-transcribe',
        'base_url': 'https://api.openai.com/v1', 'api_key_env': 'OPENAI_API_KEY'},
    'Groq Whisper (fast)': {'provider': 'api', 'model': 'whisper-large-v3-turbo',
        'base_url': 'https://api.groq.com/openai/v1', 'api_key_env': 'GROQ_API_KEY'},
    'Groq Whisper (accuracy)': {'provider': 'api', 'model': 'whisper-large-v3',
        'base_url': 'https://api.groq.com/openai/v1', 'api_key_env': 'GROQ_API_KEY'},
    'Together AI': {'provider': 'api', 'model': 'openai/whisper-large-v3',
        'base_url': 'https://api.together.ai/v1', 'api_key_env': 'TOGETHER_API_KEY'},
    'Custom compatible API': {'provider': 'api', 'model': '',
        'base_url': '', 'api_key_env': 'VOICE_API_KEY'},
    'Local compatible server': {'provider': 'api', 'model': 'whisper-1',
        'base_url': 'http://127.0.0.1:8000/v1', 'api_key_env': ''},
}


def voice_form_values(values):
    result = {}
    for name in VoiceSettings.__dataclass_fields__:
        if 'voice_' + name not in values: continue
        value = values['voice_' + name]
        if name in ('enabled', 'auto_send'):
            if type(value) is not bool: raise ValueError('Invalid voice checkbox value.')
        elif name in ('max_recording_seconds', 'timeout_seconds'):
            try: value = int(value)
            except (TypeError, ValueError):
                raise ValueError('Voice durations must be whole seconds.') from None
        elif name in ('max_requests', 'max_audio_minutes'):
            value = value.strip()
            try: value = (int(value) if name == 'max_requests' else float(value)) if value else None
            except ValueError:
                raise ValueError('Enter a whole request count or an audio limit in minutes; leave blank for unlimited.') from None
        else:
            value = value.strip()
            if name == 'api_key_env': value = value or None
        result[name] = value
    return result


def apply_voice_preset(values, key, name):
    """Change only connection fields; keep language, input preferences and quotas."""
    for field, value in PRESETS[name].items(): values['voice_' + field].set(value)
    key.set('')


def microphone_names(sd):
    hosts = sd.query_hostapis()
    return tuple(dict.fromkeys(d['name'] + ', ' + hosts[d['hostapi']]['name']
        for d in sd.query_devices() if d['max_input_channels'] > 0))


class VoiceTab:
    def __init__(self, tabs, config, values, *, limits_panel):
        import tkinter as tk
        from tkinter import ttk
        panel = ttk.Frame(tabs, padding=12); tabs.add(panel, text='Voice')
        self.key = tk.StringVar()
        for name, value in config.items():
            variable = tk.BooleanVar if type(value) is bool else tk.StringVar
            values['voice_' + name] = variable(value='' if value is None else value)

        def field(name, label, choices=None, parent=panel):
            row = parent.grid_size()[1]
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=3)
            options = dict(textvariable=values['voice_' + name], width=36)
            widget = (ttk.Combobox(parent, **options, values=choices, state='readonly')
                      if choices else ttk.Entry(parent, **options))
            widget.grid(row=row, column=1, sticky='ew', pady=3)
            return widget

        ttk.Checkbutton(panel, text='Enable push-to-talk', variable=values['voice_enabled']).grid(
            row=0, column=0, columnspan=2, sticky='w')
        preset = tk.StringVar(value='Choose a preset')
        controls = ttk.Frame(panel); controls.grid(row=1, column=0, columnspan=2, sticky='ew', pady=6)
        ttk.Combobox(controls, textvariable=preset, values=tuple(PRESETS), state='readonly', width=32).pack(side='left')
        ttk.Button(controls, text='Apply preset', command=lambda:
            apply_voice_preset(values, self.key, preset.get()) if preset.get() in PRESETS else None).pack(side='left', padx=8)
        field('provider', 'Transcription method', ('local', 'api'))
        field('model', 'Speech model ID / local folder (blank = off)')
        api_fields = [field('base_url', 'API base URL (API method only)'),
                      field('api_key_env', 'Voice API key variable name')]
        row = panel.grid_size()[1]
        ttk.Label(panel, text='Voice API key (blank keeps saved key)').grid(row=row, column=0, sticky='w', pady=3)
        key_entry = ttk.Entry(panel, textvariable=self.key, show='*', width=36)
        key_entry.grid(row=row, column=1, sticky='ew'); api_fields.append(key_entry)
        field('hotkey', 'Hold to speak; release to transcribe', tuple(HOTKEYS))
        field('language', 'Language code (blank = auto, en = English)')
        row = panel.grid_size()[1]
        ttk.Label(panel, text='Microphone (blank = Windows default)').grid(row=row, column=0, sticky='w', pady=3)
        mic = ttk.Frame(panel); mic.grid(row=row, column=1, sticky='ew')
        devices = ttk.Combobox(mic, textvariable=values['voice_microphone'], width=25)
        devices.pack(side='left', fill='x', expand=True)
        status = tk.StringVar()
        def refresh():
            try:
                import sounddevice as sd
                devices.configure(values=('', *microphone_names(sd)))
                status.set('Microphones listed. Blank uses the Windows default input.')
            except Exception:
                status.set('Cannot list microphones. Run Setup-Mod and check Windows microphone access.')
        ttk.Button(mic, text='Refresh', command=refresh).pack(side='left', padx=(4, 0))
        field('max_recording_seconds', 'Maximum recording (seconds)')
        api_fields.append(field('timeout_seconds', 'API transcription timeout (seconds)'))
        quotas = ttk.LabelFrame(limits_panel, text='Voice API limits per run (optional)', padding=8)
        quotas.pack(fill='x', pady=(12, 0))
        api_fields.extend((field('max_requests', 'Maximum transcription requests', parent=quotas),
                           field('max_audio_minutes', 'Maximum total audio (minutes)', parent=quotas)))
        ttk.Label(quotas, text='Blank = unlimited. Separate from chat limits; local Whisper is not capped.\n'
            'Attempts count even if they fail. Restarting or Save & apply resets usage.',
            wraplength=570).grid(row=quotas.grid_size()[1], column=0, columnspan=2, sticky='w')
        def method_changed(*_):
            state = 'normal' if values['voice_provider'].get() == 'api' else 'disabled'
            for widget in api_fields: widget.configure(state=state)
        values['voice_provider'].trace_add('write', method_changed)
        method_changed()
        ttk.Checkbutton(panel, text='Send transcript immediately (uncheck to review before sending)',
            variable=values['voice_auto_send']).grid(row=panel.grid_size()[1], column=0, columnspan=2, sticky='w', pady=6)
        ttk.Label(panel, text='API mode sends audio to its provider. Audio is not saved. Escape cancels.\n'
            'Local Whisper needs the voice-local extra and downloads its model on first use.\n'
            'See docs/usage.md. Saving applies changes without a game restart.',
            wraplength=570).grid(row=panel.grid_size()[1], column=0, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Label(panel, textvariable=status, wraplength=570).grid(row=panel.grid_size()[1], column=0, columnspan=2, sticky='w')
