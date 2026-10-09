import threading
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from bwv.overlay_windows import Windows, OverlayWindow, hotkey_loop


class HotkeyThreadTests(TestCase):
    def test_collapsed_reply_receives_scroll_clicks_without_activation(self):
        win=object.__new__(Windows);win.get_long=Mock(return_value=0)
        win.set_long=Mock()
        win.own_style(1,False,readable=True)
        style=win.set_long.call_args.args[-1]
        self.assertTrue(style & 0x08000000)
        self.assertFalse(style & 0x20)
        win.own_style(1,False)
        self.assertTrue(win.set_long.call_args.args[-1] & 0x20)
        win.own_style(1,True,readable=True)
        self.assertFalse(win.set_long.call_args.args[-1] & (0x08000000|0x20))

    def test_scene_lock_hides_and_disables_without_forgetting_open_input(self):
        window=object.__new__(OverlayWindow)
        window.model=Mock();window.model.can_send.return_value=False
        window.model.can_show.return_value=False
        window.win=Mock();window.hwnd=2;window.game=1
        window.win.u.GetForegroundWindow.return_value=2
        window.win.pid.return_value=123;window.model.game_pid=123
        window.expanded=True;window.restore_input_focus=False
        window.want_hotkey=threading.Event();window.entry=Mock();window.send=Mock()
        window.diagnostics=Mock()
        window.render()
        self.assertTrue(window.expanded)
        self.assertTrue(window.restore_input_focus)
        window.entry.delete.assert_not_called()
        window.entry.configure.assert_called_with(state='disabled')
        window.win.u.ShowWindow.assert_called_with(2,0)

    def test_read_gap_keeps_input_and_banner_visible_but_send_disabled(self):
        window=object.__new__(OverlayWindow)
        window.model=Mock();window.model.can_send.return_value=False
        window.model.can_show.return_value=True
        window.model.chat_title='Chat with creature';window.model.is_dummy=True
        window.model.banner_text.return_value='Still here.'
        window.win=Mock();window.hwnd=2;window.game=1
        window.win.u.GetForegroundWindow.return_value=2
        window.win.rect.return_value=(0,0,800,600)
        window.expanded=True;window.restore_input_focus=False
        window.pending=False;window.notice='';window.hotkey_failed=False
        window.heading=Mock();window.indicator=Mock();window.message=Mock()
        window.message_box=Mock();window.message_scroll=Mock()
        window.message_text='Still here.';window.form=Mock();window.root=Mock()
        window.form.winfo_reqheight.return_value=28
        window.geometry=None;window.want_hotkey=threading.Event()
        window.entry=Mock();window.send=Mock();window.diagnostics=Mock()
        window.render()
        window.win.u.ShowWindow.assert_not_called()
        window.entry.configure.assert_called_with(state='normal')
        window.send.configure.assert_called_with(state='disabled')
        window.entry.delete.assert_not_called()
        window.diagnostics.assert_called_with(True)
        # Full long replies survive both modes; routine polling must not reset
        # the scroll position while the user is reading the same message.
        reply='A long reply. '*80+'Last line.'
        window.model.banner_text.return_value=reply
        window.expanded=False;window.render()
        window.message.insert.assert_called_with('1.0',reply)
        self.assertEqual(window.message_text,reply)
        window.message_scroll.pack.assert_called_with(side='right',fill='y',before=window.message)
        window.win.own_style.assert_called_with(2,False,readable=True)
        window.message.yview_moveto.reset_mock()
        window.expanded=True;window.render();window.render()
        window.message.yview_moveto.assert_not_called()
        window.message_scroll.pack.assert_called_with(side='right',fill='y',before=window.message)

    def test_scene_gap_preserves_draft_but_new_recipient_clears_it(self):
        window=object.__new__(OverlayWindow)
        window.closed=False;window.deadline=float('inf')
        window.state_lock=threading.Lock();window.latest_state=None
        window.events=queue.SimpleQueue();window.target_identity=('session','creature')
        window.model=SimpleNamespace(identity=None,game_pid=123)
        window.entry=Mock();window.root=Mock();window.render=Mock()
        window.win=Mock();window.win.pid.return_value=123
        window.game=1;window.expanded=False
        window.tick()  # creature identity temporarily hidden in a scene
        window.model.identity=('session','creature')
        window.tick()  # returning to the same recipient keeps the draft
        window.entry.delete.assert_not_called()
        window.model.identity=('other-session','creature')
        window.tick()
        window.entry.delete.assert_called_once_with(0,'end')

    def test_thread_queue_delivery_unregisters_when_game_loses_focus(self):
        user32=Mock();stop=threading.Event();wanted=threading.Event()
        wanted.set();events=[];delivered=False
        def peek(pointer,*args):
            nonlocal delivered
            if delivered:return False
            delivered=True
            pointer._obj.message=0x0312
            pointer._obj.wParam=Windows.HOTKEY
            return True
        def emit(kind,value):
            events.append((kind,value))
            if kind=='toggle':wanted.clear()
            elif kind=='hotkey_state' and value==(False,False):stop.set()
        user32.PeekMessageW.side_effect=peek
        hotkey_loop(user32,stop,wanted,emit)
        self.assertEqual(events,[('hotkey_state',(True,False)),('toggle',None),
                                 ('hotkey_state',(False,False))])
        user32.RegisterHotKey.assert_called_once_with(None,Windows.HOTKEY,0x4002,0x0D)
        user32.UnregisterHotKey.assert_called_once_with(None,Windows.HOTKEY)

    def test_close_unregisters_and_conflict_does_not_deliver_toggle(self):
        for registered in (True,False):
            with self.subTest(registered=registered):
                user32=Mock();stop=threading.Event();wanted=threading.Event()
                wanted.set();events=[]
                user32.RegisterHotKey.return_value=registered
                user32.PeekMessageW.return_value=False
                def emit(kind,value):
                    events.append((kind,value));stop.set()
                hotkey_loop(user32,stop,wanted,emit)
                self.assertEqual(events,[('hotkey_state',(registered,not registered))])
                self.assertEqual(user32.UnregisterHotKey.call_count,int(registered))
