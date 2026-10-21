"""Push-to-talk UI state, driven by the overlay's existing timer."""
from .voice import HOTKEYS, VoiceInput


class VoiceInputMeter:
    """Cover the disabled input with status and one reusable volume rectangle."""
    def __init__(self, parent, font):
        import tkinter as tk
        self.canvas = tk.Canvas(parent, bg='#3b3b3b', highlightthickness=0,
            borderwidth=1, relief='sunken', width=1, height=1, takefocus=False)
        self.bar = self.canvas.create_rectangle(0, 0, 0, 0, fill='#426b5b', outline='')
        self.label = self.canvas.create_text(6, 0, text='', anchor='w', fill='#f1f3f5', font=font)
        self.visible = False
        self.drawn = None

    def render(self, status, level=0.0):
        if not status:
            if self.visible: self.canvas.place_forget()
            self.visible = False; self.drawn = None
            return
        if not self.visible:
            self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
            self.visible = True
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        fill = round(width * max(0.0, min(1.0, level)))
        state = (status, width, height, fill)
        if state == self.drawn: return
        self.canvas.coords(self.bar, 0, 0, fill, height)
        self.canvas.coords(self.label, 6, height / 2)
        if self.drawn is None or status != self.drawn[0]:
            self.canvas.itemconfigure(self.label, text='[' + status + ']')
        self.drawn = state


class VoiceOverlay:
    def __init__(self, window, config, settings_path, *, service=None):
        self.window, self.config = window, config
        self.service = service or VoiceInput(config, settings_path, window.emit)
        self.job = None
        self.active = False
        self.status = ''
        self.was_down = False

    def focused(self):
        w = self.window
        return bool(w.game and w.win.u.GetForegroundWindow() in (w.game, w.hwnd))

    def valid(self):
        return bool(self.job and not self.job.cancelled.is_set() and self.focused()
            and self.window.model.can_show() and self.window.model.identity == self.job.identity)

    def cancel(self):
        if self.active:
            self.service.cancel()
            self.active = False
            self.status = ''
            self.window.notice = '[Voice input cancelled.]'

    def tick(self):
        w = self.window
        down = all(w.win.u.GetAsyncKeyState(key) & 0x8000 for key in HOTKEYS[self.config.hotkey])
        pressed = down and not self.was_down
        self.was_down = down
        if self.active:
            if not self.valid() or w.win.u.GetAsyncKeyState(0x1B) & 0x8000:
                self.cancel()
            elif not down:
                self.service.release()
        elif pressed and self.focused() and w.model.can_show():
            if w.pending or self.service.busy or not w.model.can_send():
                w.notice = '[Voice is waiting for chat. Try again when the current reply finishes.]'
            elif w.entry.get().strip():
                w.notice = '[Send or clear your typed message before recording.]'
            else:
                self.status = 'Opening microphone'
                self.active = True
                w.notice = ''
                self.job = self.service.start(w.model.identity)
                if self.job is None:
                    self.active = False; self.status = ''
                else:
                    w.open_input(focus=False)

    def render_input(self):
        meter = getattr(self.window, 'voice_meter', None)
        if meter is None: return
        level = (self.job.level if self.active and self.status == 'Listening' and self.job
            and not self.job.released.is_set() and not self.job.cancelled.is_set() else 0.0)
        meter.render(self.status if self.active else '', level)

    def handle(self, kind, payload):
        job, value = payload
        if job is not self.job: return
        if kind == 'voice_done':
            self.active = False; self.status = ''
            return
        if not self.active: return
        if not self.valid():
            self.cancel()
            return
        w = self.window
        if kind == 'voice_status':
            self.status = value
        elif kind == 'voice_error':
            self.active = False; self.status = ''
            w.notice = '[ERROR: ' + value + ']'
        elif kind == 'voice_text':
            self.active = False; self.status = ''
            w.entry.configure(state='normal')
            w.entry.delete(0, 'end'); w.entry.insert(0, value)
            w.entry.icursor(0); w.entry.xview_moveto(0)
            if self.config.auto_send:
                w.submit()
                w.open_input(focus=False)
            else:
                w.notice = '[Transcript ready. Review it, then press Enter to send.]'
                w.open_input()

    def close(self):
        self.service.close()
