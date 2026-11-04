"""Creature speech form. Remote connections are used only by explicit preview/playback."""
import queue
import threading
from .speech import SpeechSettings
from .speech_audio import SpeechPlayer,windows_voices


PRESETS={
    'Windows voices (offline)':dict(provider='windows',voice='',model='',instructions=''),
    'Piper server (offline models)':dict(provider='piper',base_url='http://localhost:5000',
        api_key_env='',model='',voice='',instructions=''),
    'OpenAI':dict(provider='openai',base_url='https://api.openai.com/v1',
        api_key_env='OPENAI_API_KEY',model='gpt-4o-mini-tts',voice='coral',instructions=''),
    'ElevenLabs':dict(provider='elevenlabs',base_url='https://api.elevenlabs.io/v1',
        api_key_env='ELEVENLABS_API_KEY',model='eleven_multilingual_v2',voice='',instructions=''),
    'Custom OpenAI-compatible':dict(provider='openai',base_url='',api_key_env='SPEECH_API_KEY',
        model='',voice='',instructions=''),
    'Local OpenAI-compatible':dict(provider='openai',base_url='http://localhost:8000/v1',
        api_key_env='',model='',voice='',instructions=''),
}


def speech_form_values(values):
    result={}
    for name in SpeechSettings.__dataclass_fields__:
        key='speech_'+name
        if key not in values:continue
        value=values[key]
        try:
            if name in ('enabled','feedback_protection'):
                if type(value) is not bool:raise ValueError()
            elif name in ('speaker_id','max_requests','max_characters'):
                value=int(value) if str(value).strip() else None
            elif name in ('volume','timeout_seconds'):value=int(value)
            elif name=='speed':value=float(value)
            else:
                value=value.strip()
                if name=='api_key_env':value=value or None
        except (ValueError,TypeError,AttributeError):raise ValueError('Invalid Creature speech setting: '+name) from None
        result[name]=value
    return result


def apply_speech_preset(values,key,name):
    for field,value in PRESETS[name].items():values['speech_'+field].set(value)
    # Provider speed ranges differ; switching presets starts at normal speed.
    values['speech_speed'].set('1');values['speech_speaker_id'].set('');key.set('')


class SpeechTab:
    def __init__(self,tabs,config,values,settings_path,*,limits_panel):
        import tkinter as tk
        from tkinter import ttk
        panel=ttk.Frame(tabs,padding=12);tabs.add(panel,text='Creature speech')
        root=panel.winfo_toplevel();self.key=tk.StringVar();self.preview=None
        status=tk.StringVar();self.closed=False
        for name,value in config.items():
            variable=tk.BooleanVar if type(value) is bool else tk.StringVar
            values['speech_'+name]=variable(value='' if value is None else value)
        ttk.Checkbutton(panel,text='Enable creature speech (AI-generated voice)',variable=values['speech_enabled']).pack(anchor='w')
        controls=ttk.Frame(panel);controls.pack(fill='x',pady=5)
        preset=tk.StringVar(value='Choose a preset')
        ttk.Combobox(controls,textvariable=preset,values=tuple(PRESETS),state='readonly',width=29).pack(side='left')
        ttk.Button(controls,text='Apply preset',command=lambda:apply_speech_preset(values,self.key,preset.get())
                   if preset.get() in PRESETS else None).pack(side='left',padx=6)
        form=ttk.Frame(panel);form.pack(fill='x');form.columnconfigure(1,weight=1)
        def field(name,label,choices=None,*,parent=form,editable=False):
            row=parent.grid_size()[1]
            ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=(0,10),pady=2)
            options=dict(textvariable=values['speech_'+name],width=36)
            widget=(ttk.Combobox(parent,**options,values=choices,state='normal' if editable else 'readonly')
                if choices is not None else ttk.Entry(parent,**options))
            widget.grid(row=row,column=1,sticky='ew',pady=2);return widget
        field('provider','Speech provider',('windows','piper','openai','elevenlabs'))
        voice=field('voice','Voice name / ID (blank = Windows/Piper default)',('',),editable=True)
        model=field('model','Model ID (API providers)')
        base=field('base_url','Server / API base URL')
        env=field('api_key_env','API key variable (blank for unauthenticated local)')
        row=form.grid_size()[1]
        ttk.Label(form,text='API key (blank keeps saved key)').grid(row=row,column=0,sticky='w')
        secret=ttk.Entry(form,textvariable=self.key,show='*',width=36);secret.grid(row=row,column=1,sticky='ew')
        field('speed','Speed (1 = normal; ElevenLabs 0.7-1.2)')
        field('volume','Volume (0-100)')
        output=field('output_device','Output device (blank = Windows default)',('',),editable=True)
        instructions=field('instructions','Voice direction (compatible API models only)')
        speaker=field('speaker_id','Piper speaker ID (optional)')
        quotas=ttk.LabelFrame(limits_panel,text='Creature speech limits per run (optional)',padding=8)
        quotas.pack(fill='x',pady=(12,0))
        field('timeout_seconds','Synthesis timeout (seconds)',parent=quotas)
        requests=field('max_requests','Maximum synthesis requests',parent=quotas)
        characters=field('max_characters','Maximum total characters',parent=quotas)
        ttk.Label(quotas,text='Blank = unlimited. Windows voices are not capped. Failed attempts count.\n'
            'Restarting or Save & apply resets usage. Each Test voice preview has its own limits.',wraplength=570
            ).grid(row=quotas.grid_size()[1],column=0,columnspan=2,sticky='w')
        def provider_changed(*_):
            p=values['speech_provider'].get()
            for widget,enabled in ((model,p in ('openai','elevenlabs')),(base,p!='windows'),
                    (env,p in ('openai','elevenlabs')),(secret,p in ('openai','elevenlabs')),
                    (instructions,p=='openai'),(speaker,p=='piper'),(requests,p!='windows'),(characters,p!='windows')):
                widget.configure(state='normal' if enabled else 'disabled')
        values['speech_provider'].trace_add('write',provider_changed);provider_changed()
        ttk.Checkbutton(panel,text='Microphone feedback protection (pause listening during creature speech)',
            variable=values['speech_feedback_protection']).pack(anchor='w',pady=(6,0))
        ttk.Label(panel,text='Turn protection off with headphones to listen while the creature speaks.\n'
            'Speech works in desktop and VR. Only new creature lines are spoken; notices stay silent.',wraplength=620).pack(anchor='w')
        buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=5)
        results=queue.SimpleQueue()
        def refresh():
            refresh_button.configure(state='disabled')
            status.set('Looking for installed voices and output devices...')
            def work():
                try:
                    names=windows_voices()
                    import sounddevice as sd
                    hosts=sd.query_hostapis()
                    devices=tuple(dict.fromkeys(d['name']+', '+hosts[d['hostapi']]['name']
                        for d in sd.query_devices() if d['max_output_channels']>0))
                    results.put((names,devices))
                except Exception:results.put(None)
            threading.Thread(target=work,daemon=True).start()
        def stop():
            if self.preview:self.preview.cancel()
        def preview():
            if self.preview and self.preview.busy:return
            try:
                data=speech_form_values({k:v.get() for k,v in values.items()})
                data['enabled']=True;c=SpeechSettings(**data)
                from .speech_audio import Synthesizer
                supplied={c.api_key_env:self.key.get().strip()} if c.api_key_env and self.key.get().strip() else None
                self.preview=SpeechPlayer(c,settings_path,synthesizer=Synthesizer(c,settings_path,environ=supplied))
                self.preview.start('Hello! I am ready to explore the island with you.')
                status.set('Previewing creature speech...')
            except ValueError as error:status.set(str(error))
        refresh_button=ttk.Button(buttons,text='Refresh Windows voices / devices',command=refresh)
        refresh_button.pack(side='left')
        ttk.Button(buttons,text='Test voice',command=preview).pack(side='left',padx=5)
        ttk.Button(buttons,text='Stop preview',command=stop).pack(side='left')
        ttk.Label(panel,text='API presets send creature text to the provider and may incur costs, including Test voice\n'
            'and dummy chat. Set optional usage caps in Limits. Audio is not saved.\n'
            'Windows voices need no download. Piper needs a separately installed local server; see docs/speech.md.',
            wraplength=620).pack(anchor='w')
        ttk.Label(panel,textvariable=status,wraplength=620).pack(anchor='w',pady=4)
        def poll():
            if self.closed:return
            try:
                result=results.get_nowait()
                refresh_button.configure(state='normal')
                if result is None:status.set('Cannot list voices/devices. Check Windows speech and audio support.')
                else:
                    voice.configure(values=('',*result[0]));output.configure(values=('',*result[1]))
                    status.set('Installed Windows voices and output devices listed.')
            except queue.Empty:pass
            if self.preview and not self.preview.busy:
                status.set(self.preview.error or 'Preview finished.');self.preview=None
            root.after(100,poll)
        def close(event):
            if event.widget is root:
                self.closed=True
                if self.preview:self.preview.close()
        root.bind('<Destroy>',close,add='+');root.after(100,poll)
