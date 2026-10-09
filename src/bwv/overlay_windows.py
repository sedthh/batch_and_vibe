"""Original Win32/Tk overlay; only our own window is restyled.

The external game is inspected through window geometry and PID only. All chat
goes through the existing loopback broker. No injection, input simulation,
game-window subclassing, process memory access or game files are involved.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

from .overlay import OverlayModel, overlay_bounds


class Windows:
    HOTKEY=0x4257
    def __init__(self):
        if os.name!='nt':
            raise OSError('The game overlay requires Windows')
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.enum_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        signatures={
            'EnumWindows':([self.enum_type,wintypes.LPARAM],wintypes.BOOL),
            'GetWindowThreadProcessId':([wintypes.HWND,ctypes.POINTER(wintypes.DWORD)],wintypes.DWORD),
            'GetClientRect':([wintypes.HWND,ctypes.POINTER(wintypes.RECT)],wintypes.BOOL),
            'ClientToScreen':([wintypes.HWND,ctypes.POINTER(wintypes.POINT)],wintypes.BOOL),
            'GetForegroundWindow':([],wintypes.HWND),
            'GetAncestor':([wintypes.HWND,wintypes.UINT],wintypes.HWND),
            'IsWindowVisible':([wintypes.HWND],wintypes.BOOL),
            'IsIconic':([wintypes.HWND],wintypes.BOOL),
            'IsWindow':([wintypes.HWND],wintypes.BOOL),
            'SetForegroundWindow':([wintypes.HWND],wintypes.BOOL),
            'SetWindowPos':([wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT],wintypes.BOOL),
            'ShowWindow':([wintypes.HWND,ctypes.c_int],wintypes.BOOL),
            'RegisterHotKey':([wintypes.HWND,ctypes.c_int,wintypes.UINT,wintypes.UINT],wintypes.BOOL),
            'UnregisterHotKey':([wintypes.HWND,ctypes.c_int],wintypes.BOOL),
            'PeekMessageW':([ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT],wintypes.BOOL),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.u,name);fn.argtypes=args;fn.restype=result
        pointer_suffix='PtrW' if ctypes.sizeof(ctypes.c_void_p)==8 else 'W'
        self.get_long=getattr(self.u,'GetWindowLong'+pointer_suffix)
        self.set_long=getattr(self.u,'SetWindowLong'+pointer_suffix)
        self.get_long.argtypes=[wintypes.HWND,ctypes.c_int];self.get_long.restype=ctypes.c_ssize_t
        self.set_long.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t];self.set_long.restype=ctypes.c_ssize_t
        try:
            self.u.SetProcessDpiAwarenessContext.argtypes=[ctypes.c_void_p]
            self.u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            pass

    def pid(self, hwnd):
        pid=wintypes.DWORD()
        self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        return pid.value

    def rect(self, hwnd):
        if not self.u.IsWindow(hwnd) or not self.u.IsWindowVisible(hwnd) or self.u.IsIconic(hwnd):
            return None
        rect=wintypes.RECT(); origin=wintypes.POINT(0,0)
        if not self.u.GetClientRect(hwnd,ctypes.byref(rect)) or not self.u.ClientToScreen(hwnd,ctypes.byref(origin)):
            return None
        return (origin.x,origin.y,origin.x+rect.right,origin.y+rect.bottom)

    def game_window(self, pid):
        if not pid:
            return None
        matches=[]
        @self.enum_type
        def each(hwnd,unused):
            if self.pid(hwnd)==pid:
                rect=self.rect(hwnd)
                if rect and overlay_bounds(rect):
                    matches.append(((rect[2]-rect[0])*(rect[3]-rect[1]),hwnd))
            return True
        self.u.EnumWindows(each,0)
        return max(matches)[1] if matches else None

    def own_style(self, hwnd, expanded, *, readable=False):
        style=self.get_long(hwnd,-20)|0x80|0x80000  # tool window, layered
        style &= ~0x40000  # no taskbar button
        if expanded:
            style &= ~(0x08000000|0x20)
        else:
            style |= 0x08000000  # scrolling must not activate the input/game menu
            if readable:style &= ~0x20
            else:style |= 0x20  # empty collapsed banner remains click-through
        self.set_long(hwnd,-20,style)


def hotkey_loop(user32, stop, wanted, emit):
    """Own thread queue: never enter Tk's native window procedure via ctypes.

    RegisterHotKey(NULL, ...) delivers WM_HOTKEY to this thread. Forward only
    ordinary Python queue events; all Tk work stays in its regular event loop.
    """
    registered=False;reported=None;next_attempt=0
    message=wintypes.MSG()
    try:
        while not stop.is_set():
            enabled=wanted.is_set()
            if enabled and not registered and time.monotonic()>=next_attempt:
                registered=bool(user32.RegisterHotKey(None,Windows.HOTKEY,0x4002,0x0D))
                next_attempt=time.monotonic()+1
            elif not enabled and registered:
                user32.UnregisterHotKey(None,Windows.HOTKEY);registered=False
                next_attempt=0
            state=(registered,enabled and not registered)
            if state!=reported:
                emit('hotkey_state',state);reported=state
            while user32.PeekMessageW(ctypes.byref(message),ctypes.c_void_p(-1),0x0312,0x0312,1):
                if (message.message==0x0312 and message.wParam==Windows.HOTKEY
                        and registered and wanted.is_set()):
                    emit('toggle',None)
            stop.wait(.025)
    finally:
        if registered:user32.UnregisterHotKey(None,Windows.HOTKEY)


class OverlayWindow:
    def __init__(self, client, *, seconds=43200, diagnostics_file=None):
        import tkinter as tk
        if type(seconds) is not int or not 1<=seconds<=43200:
            raise ValueError('Invalid overlay duration')
        from .overlay import ChatInputHistory
        self.win=Windows();self.client=client;self.model=OverlayModel()
        self.input_history=ChatInputHistory()
        self.stop=threading.Event();self.events=queue.SimpleQueue()
        self.want_hotkey=threading.Event();self.hotkey_events=0
        self.state_lock=threading.Lock();self.latest_state=None
        self.expanded=False;self.pending=False;self.closed=False;self.game=None
        self.notice=''
        self.restore_input_focus=False
        self.hotkey=False;self.hotkey_failed=False;self.geometry=None
        self.deadline=time.monotonic()+seconds
        self.diagnostics_file=Path(diagnostics_file) if diagnostics_file else None
        self.last_diagnostic=None;self.target_identity=None
        self.root=tk.Tk();self.root.withdraw();self.root.title('Black & White — Creature chat')
        self.root.overrideredirect(True);self.root.attributes('-topmost',True)
        self.root.attributes('-alpha',.82);self.root.configure(bg='#292929')
        font=('Segoe UI',9)
        self.header=tk.Frame(self.root,bg='#292929')
        self.header.pack(side='top',fill='x',padx=8,pady=3)
        self.indicator=tk.Label(self.header,text='',width=2,anchor='e',
            bg='#292929',fg='#dedede',font=('Segoe UI',9,'bold'))
        self.indicator.pack(side='right')
        self.heading=tk.Label(self.header,text='Chat with creature · Ctrl+Enter',anchor='w',
            bg='#292929',fg='#f1d3a0',font=('Segoe UI',9,'bold'))
        self.heading.pack(side='left',fill='x',expand=True)
        self.message_box=tk.Frame(self.root,bg='#292929')
        self.message=tk.Text(self.message_box,height=3,wrap='word',state='disabled',
            relief='flat',borderwidth=0,highlightthickness=0,takefocus=False,
            bg='#292929',fg='#f1f3f5',font=font)
        self.message_scroll=tk.Scrollbar(self.message_box,orient='vertical',
            command=self.message.yview,takefocus=False)
        self.message.configure(yscrollcommand=self.message_scroll.set)
        self.message.pack(side='left',fill='both',expand=True)
        self.message_text=''
        self.form=tk.Frame(self.root,bg='#292929')
        self.entry=tk.Entry(self.form,font=font,bg='#3b3b3b',fg='white',insertbackground='white')
        self.entry.pack(side='left',fill='x',expand=True)
        self.send=tk.Button(self.form,text='Send',command=self.submit,font=font)
        self.send.pack(side='left',padx=(6,0))
        tk.Button(self.form,text='Close',command=self.collapse,font=font).pack(side='left',padx=(6,0))
        self.entry.configure(validate='key',validatecommand=(self.root.register(lambda value:len(value)<=2000),'%P'))
        self.entry.bind('<Return>',lambda event:self.submit())
        self.entry.bind('<Up>',lambda event:self.browse_input(-1))
        self.entry.bind('<Down>',lambda event:self.browse_input(1))
        self.root.bind('<Escape>',lambda event:self.collapse())
        self.root.bind('<Control-Return>',lambda event:'break')
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.update_idletasks()
        self.hwnd=self.win.u.GetAncestor(self.root.winfo_id(),2)
        self.win.own_style(self.hwnd,False)
        self.hotkey_thread=threading.Thread(target=hotkey_loop,
            args=(self.win.u,self.stop,self.want_hotkey,self.emit),daemon=True)
        self.hotkey_thread.start()
        threading.Thread(target=self.poll_service,daemon=True).start()
        self.root.after(50,self.tick)

    def emit(self, kind, value):
        if kind in ('state','disconnected'):
            with self.state_lock:self.latest_state=(kind,value)
        else:
            # At most one submitted question; its terminal result is never
            # evicted by polling frames while the window's event loop is busy.
            self.events.put((kind,value))

    def poll_service(self):
        while not self.stop.is_set():
            try:self.emit('state',self.client.state())
            except (OSError,ValueError,KeyError):self.emit('disconnected',None)
            self.stop.wait(.4)

    def toggle(self):
        # A queued hotkey can arrive just after availability changes.
        if not self.model.can_show():return
        foreground=self.win.u.GetForegroundWindow()
        if foreground not in (self.game,self.hwnd):return
        if self.expanded:
            self.collapse();return
        self.expanded=True
        self.win.own_style(self.hwnd,True)
        self.win.u.ShowWindow(self.hwnd,5)
        self.win.u.SetForegroundWindow(self.hwnd)
        self.root.after_idle(self.entry.focus_set)
        self.render()

    def collapse(self):
        self.restore_input_focus=False
        focused=self.win.u.GetForegroundWindow()==self.hwnd
        self.expanded=False;self.win.own_style(self.hwnd,False)
        if focused and self.game and self.win.pid(self.game)==self.model.game_pid:
            self.win.u.SetForegroundWindow(self.game)
        self.render()
        return 'break'

    def submit(self):
        if self.pending or not self.model.can_send():return 'break'
        text=self.entry.get().strip()
        if not text:return 'break'
        self.input_history.submitted(text)
        self.pending=True;self.notice=''
        identity=self.model.identity
        def send():
            try:self.emit('reply',(identity,self.client.ask(text,identity)))
            except (OSError,ValueError):
                # Fetch the error from this request, rather than a stale poll.
                try:self.emit('failure_state',self.client.state())
                except (OSError,ValueError):self.emit('failure_state',None)
                self.emit('failed',identity)
        threading.Thread(target=send,daemon=True).start()
        self.render()
        return 'break'

    def browse_input(self, direction):
        if self.pending:return 'break'
        text=self.input_history.move(direction,self.entry.get())
        self.entry.delete(0,'end');self.entry.insert(0,text);self.entry.icursor('end')
        return 'break'

    def render(self):
        available=self.model.can_send()
        if not self.model.can_show():
            focused=self.win.u.GetForegroundWindow()==self.hwnd
            # Keep the draft and open-input intent across a scene lock.
            if focused and self.expanded:self.restore_input_focus=True
            self.win.own_style(self.hwnd,False)
            self.want_hotkey.clear()
            self.entry.configure(state='disabled');self.send.configure(state='disabled')
            self.win.u.ShowWindow(self.hwnd,0)
            self.geometry=None
            if focused and self.game and self.win.pid(self.game)==self.model.game_pid:
                self.win.u.SetForegroundWindow(self.game)
            self.diagnostics(False)
            return
        speech=self.model.banner_text(pending=self.pending,notice=self.notice)
        title=self.model.chat_title+' · Ctrl+Enter'
        if self.model.is_dummy:
            title += ' · Dummy'
        if self.hotkey_failed:title='Ctrl+Enter is already in use'
        self.heading.configure(text=title)
        self.indicator.configure(text=self.model.call_indicator(pending=self.pending),
            fg='#58d878')
        compact_speech=speech
        if compact_speech!=self.message_text:
            self.message.configure(state='normal');self.message.delete('1.0','end')
            self.message.insert('1.0',compact_speech);self.message.configure(state='disabled')
            self.message.yview_moveto(0)
            self.message_text=compact_speech
        if speech:
            self.message_box.pack(fill='both',expand=True,padx=8,pady=(0,2))
        else:self.message_box.pack_forget()
        # Replies stay scrollable even after the input form closes.
        if speech:self.message_scroll.pack(side='right',fill='y',before=self.message)
        else:self.message_scroll.pack_forget()
        if self.expanded:
            self.form.pack(fill='x',side='bottom',padx=8,pady=(0,5))
        else:
            self.form.pack_forget()
        self.entry.configure(state='normal' if not self.pending else 'disabled')
        self.send.configure(state='normal' if available and not self.pending else 'disabled')
        rect=self.win.rect(self.game) if self.game else None
        # Add exactly the input form's requested height (including its padding)
        # so opening it does not steal a pixel from the reply/scrollbar viewport.
        input_height=self.form.winfo_reqheight()+5 if self.expanded else None
        bounds=overlay_bounds(rect,self.expanded,bool(speech),input_height=input_height) if rect else None
        foreground=self.win.u.GetForegroundWindow()
        visible=bool(bounds and foreground in (self.game,self.hwnd))
        if visible:
            self.win.own_style(self.hwnd,self.expanded,readable=bool(speech))
            x,y,width,height=bounds
            if self.geometry is None or self.geometry[2:]!=(width,height):
                self.root.geometry(f'{width}x{height}')
            # Map through Tk once without activation, then place in physical
            # screen coordinates. This also supports monitors left of primary.
            if self.root.state()=='withdrawn':
                self.root.deiconify();self.win.own_style(self.hwnd,self.expanded,readable=bool(speech))
            self.win.u.SetWindowPos(self.hwnd,ctypes.c_void_p(-1),x,y,width,height,0x10|0x40)
            self.geometry=bounds
            if self.expanded and self.restore_input_focus:
                self.win.u.SetForegroundWindow(self.hwnd)
                self.root.after_idle(self.entry.focus_set)
                self.restore_input_focus=False
        else:
            self.win.u.ShowWindow(self.hwnd,0)
            self.geometry=None
        if visible:self.want_hotkey.set()
        else:self.want_hotkey.clear()
        self.diagnostics(visible)

    def diagnostics(self, visible):
        if not self.diagnostics_file:return
        status={'game_pid':self.model.game_pid,'game_window':self.game,'overlay_window':self.hwnd,
            'visible':visible,'expanded':self.expanded,'hotkey_registered':self.hotkey,
            'hotkey_conflict':self.hotkey_failed,'geometry':self.geometry,
            'hotkey_events':self.hotkey_events,
            'question_available':self.model.can_send(),'request_pending':self.pending,
            'draft_length':len(self.entry.get()),
            'llm_call_visible':visible and bool(self.model.call_indicator(pending=self.pending)),
            'call_indicator_state':self.model.call_state(pending=self.pending),
            'thinking_visible':visible and self.model.is_thinking(),
            'click_through':not self.expanded and not bool(self.model.banner_text(pending=self.pending,notice=self.notice)),
            'speech_visible':visible and bool(self.model.banner_text(pending=self.pending,notice=self.notice)),
            'game_actions':0}
        serial=json.dumps(status)
        if serial!=self.last_diagnostic:
            self.diagnostics_file.write_text(serial,encoding='utf-8');self.last_diagnostic=serial

    def tick(self):
        if self.closed:return
        if time.monotonic()>=self.deadline:
            self.close();return
        with self.state_lock:
            state=self.latest_state;self.latest_state=None
        if state:
            if state[0]=='state':self.model.update(state[1])
            else:self.model.read_gap()
        for _ in range(32):
            try:kind,value=self.events.get_nowait()
            except queue.Empty:break
            if kind=='shutdown':
                self.close();return
            elif kind=='hotkey_state':
                self.hotkey,self.hotkey_failed=value
            elif kind=='failure_state':
                if value:self.model.update(value)
                else:self.model.provider_error='chat_transport_failed'
            elif kind=='toggle':
                self.hotkey_events+=1
                self.toggle()
            elif kind in ('reply','failed'):
                self.pending=False
                identity=value[0] if kind=='reply' else value
                if identity==self.model.identity and self.model.can_send():
                    if kind=='reply':
                        self.entry.configure(state='normal');self.entry.delete(0,'end')
                        self.notice=''
                        message=value[1].get('last_message')
                        if message is not None:
                            try:self.model.accept_message(message)
                            except (KeyError,TypeError,ValueError):pass
                    else:self.notice=self.model.send_failure_notice()
        # A scene lock or short read gap can hide identity. Keep the unsent
        # entry locally until the same creature returns; a verified different
        # recipient still clears it. Nothing is sent automatically on resume.
        if self.model.identity is not None and self.target_identity!=self.model.identity:
            if self.target_identity is not None:
                self.expanded=False;self.restore_input_focus=False
            self.entry.configure(state='normal');self.entry.delete(0,'end')
            self.target_identity=self.model.identity
        if not self.game or self.win.pid(self.game)!=self.model.game_pid or not self.win.rect(self.game):
            self.game=self.win.game_window(self.model.game_pid)
        if self.expanded and self.model.can_send() and self.win.u.GetForegroundWindow() not in (0,self.game,self.hwnd):
            self.expanded=False;self.win.own_style(self.hwnd,False)
            self.restore_input_focus=False
        self.render()
        self.root.after(100,self.tick)

    def close(self):
        if self.closed:return
        self.closed=True;self.stop.set()
        self.hotkey_thread.join(.5)
        self.root.destroy()


def parent_shutdown(stream, emit):
    """Only an explicitly launcher-owned overlay watches this dedicated pipe."""
    try:
        stream.readline()
    finally:
        emit('shutdown', None)


def run_window(client, *, seconds=43200, diagnostics_file=None, stop_on_stdin=False):
    window=OverlayWindow(client,seconds=seconds,diagnostics_file=diagnostics_file)
    if stop_on_stdin:
        threading.Thread(target=parent_shutdown,args=(sys.stdin,window.emit),daemon=True).start()
    try:window.root.mainloop()
    finally:window.close()
    return {'stopped':True,'game_actions':0}
