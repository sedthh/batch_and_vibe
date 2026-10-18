import threading
from unittest import TestCase
from unittest.mock import Mock, patch

from bwv.overlay import OverlayModel, ReplyReveal
from bwv.overlay_windows import OverlayWindow


class ReplyRevealTests(TestCase):
    def test_bursts_preserve_words_and_exact_unicode_text(self):
        reveal=ReplyReveal()
        text=('I found the village. Let\'s help them! Café, 森林 and 🐒.\n' * 5).rstrip()
        first=reveal.reveal(text,('creature',1),100)
        self.assertGreaterEqual(len(first),32)
        self.assertLess(len(first),64)
        self.assertLess(len(first),len(text))
        self.assertTrue(first[-1].isspace())
        self.assertEqual(reveal.reveal(text,('creature',1),100.2),first)
        second=reveal.reveal(text,('creature',1),100.3)
        self.assertTrue(second.startswith(first));self.assertGreater(len(second),len(first))
        self.assertEqual(reveal.reveal(text,('creature',1),103),text)

    def test_long_reply_finishes_quickly_and_repeated_polls_do_not_restart(self):
        reveal=ReplyReveal();text=('A thought about the island. ' * 120)[:3000]
        reveal.reveal(text,1,0)
        for i in range(1,87):result=reveal.reveal(text,1,i/10)
        self.assertEqual(result,text)
        ends=reveal.ends
        for i in range(600):self.assertEqual(reveal.reveal(text,1,9+i/10),text)
        self.assertIs(reveal.ends,ends)
        self.assertLessEqual(len(ends),24)

    def test_new_recipient_or_same_text_with_new_timestamp_starts_fresh(self):
        reveal=ReplyReveal();text='Hello from the village. '*20
        first=reveal.reveal(text,('first',1),100)
        self.assertEqual(reveal.reveal(text,('first',1),110),text)
        self.assertEqual(reveal.reveal(text,('first',2),111),first)
        self.assertEqual(reveal.reveal(text,('second',2),112),first)
        self.assertEqual(reveal.reveal('A different answer. '*20,('second',2),113),
                         ReplyReveal().reveal('A different answer. '*20,('second',2),113))

    def test_empty_short_and_unbroken_replies_are_complete_without_truncation(self):
        reveal=ReplyReveal()
        for text in ('','Hello!','One long word: '+'🐒'*400):
            self.assertEqual(reveal.reveal(text,1,100),text)
        reveal.clear()
        self.assertEqual(reveal.text,'');self.assertEqual(reveal.ends,())


class OverlayRevealTests(TestCase):
    def setUp(self):
        self.now=100
        clock=patch('bwv.overlay_windows.time.monotonic',side_effect=lambda:self.now)
        clock.start();self.addCleanup(clock.stop)
        w=self.window=object.__new__(OverlayWindow)
        w.model=OverlayModel(identity=('session','creature'),game_pid=123,scene_hidden=False,
                             available=True,received_at=100,observed_at=100)
        w.win=Mock();w.hwnd=2;w.game=1
        w.win.u.GetForegroundWindow.return_value=1
        w.win.rect.return_value=(0,0,800,600)
        w.expanded=False;w.restore_input_focus=False
        w.pending=False;w.notice='';w.hotkey_failed=False
        w.heading=Mock();w.indicator=Mock();w.message=Mock()
        w.message.yview.return_value=(0,1)
        w.message_box=Mock();w.message_scroll=Mock()
        w.message_text='';w.form=Mock();w.root=Mock()
        w.form.winfo_reqheight.return_value=28
        w.reply_reveal=ReplyReveal();w._message_key=None
        w.geometry=None;w.want_hotkey=threading.Event()
        w.entry=Mock();w.send=Mock();w.diagnostics=Mock()
        self.reply='The village needs some wood. I can help by bringing it a tree. '*20
        w.model.accept_message({'text':self.reply,'created_at':100})
        self.reply=w.model.last_message

    def test_chunks_append_without_relayout_or_resetting_reader_scroll(self):
        w=self.window;w.render();first=w.message_text
        self.assertTrue(self.reply.startswith(first));self.assertNotEqual(first,self.reply)
        self.assertEqual(w.model.last_message,self.reply)
        w.message.reset_mock();w.message.yview.return_value=(0,.5)
        self.now+=.4;w.render()
        w.message.delete.assert_not_called();w.message.yview_moveto.assert_not_called()
        w.message.see.assert_not_called()
        w.message.insert.assert_called_once_with('end-1c',w.message_text[len(first):])
        w.heading.configure.assert_called_once();w.message_box.pack.assert_called_once()
        # Opening/closing the input does not restart the reply or reset scrolling.
        current=w.message_text;w.expanded=True;w.render();w.expanded=False;w.render()
        self.assertEqual(w.message_text,current);w.message.yview_moveto.assert_not_called()
        self.now+=9;w.render();self.assertEqual(w.message_text,self.reply)
        w.message.reset_mock();w.heading.reset_mock();w.message_box.reset_mock()
        for _ in range(600):self.now+=.1;w.render()
        w.message.configure.assert_not_called();w.message.insert.assert_not_called()
        w.heading.configure.assert_not_called();w.message_box.pack.assert_not_called()

    def test_follow_bottom_and_replacement_does_not_append_old_reply(self):
        w=self.window;w.render();self.now+=.4;w.render()
        w.message.see.assert_called_with('end')
        w.message.reset_mock()
        w.model.accept_message({'text':'A new thought. '*25,'created_at':101})
        self.now=101;w.render()
        w.message.delete.assert_called_once_with('1.0','end')
        self.assertTrue(w.message_text.startswith('A new thought.'))
        self.assertNotIn('village',w.message_text)

    def test_errors_and_wait_notices_are_immediate_and_clearing_stays_cleared(self):
        w=self.window;w.render()
        error='[ERROR: '+('The model is unavailable. '*10)+']'
        w.model.accept_message({'text':error,'created_at':101})
        self.now=101;w.render();self.assertEqual(w.message_text,error)
        w.notice='[Waiting to send your message; retrying shortly.]'
        w.render();self.assertEqual(w.message_text,w.notice)
        w.notice='';w.model.last_message='';w.model.speech=''
        w.render();self.now+=10;w.render();self.assertEqual(w.message_text,'')
        self.assertEqual(w.reply_reveal.text,'')

    def test_command_acknowledgements_bypass_reveal_including_long_names(self):
        w=self.window;w.render()
        with patch.object(w.reply_reveal,'reveal',wraps=w.reply_reveal.reveal) as reveal:
            for name in ('The creature','Pebble','A creature with a particularly long and elaborate name'):
                self.now+=1
                text='['+name+' is obeying your command]'
                w.model.accept_message({'text':text,'created_at':self.now,
                                        'source':'acknowledgement'})
                w.render()
                self.assertEqual(w.message_text,text)
            reveal.assert_not_called()

    def test_scene_lock_hides_reply_and_resume_catches_up_without_replay(self):
        w=self.window;w.render();w.model.scene_hidden=True
        self.now+=1;w.render();w.diagnostics.assert_called_with(False)
        self.now+=8;w.model.scene_hidden=False;w.render()
        self.assertEqual(w.message_text,self.reply)
