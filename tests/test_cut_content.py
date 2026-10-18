"""Recovered content uses invented settings, bridge replies and native transport."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from bwv.action_probe import COMMAND, configure_cut_content
from bwv.conversation import CreatureSpeech
from bwv.conversation import reply_contract
from bwv.expressions import EXPRESSION_CONTRACT, EXPRESSION_GESTURES, RECOVERED_GESTURES, expression_from_speech
from bwv.llm import LLMConfig
from bwv.supervisor import sync_cut_content
from bwv.launcher_ui import save_basics, StatusWindow
from test_launcher_ui import form


class CutContentTests(unittest.TestCase):
    def test_main_window_toggle_saves_only_its_setting_and_reports_failure(self):
        import tkinter as tk
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            config=LLMConfig('fixture',prompts={'general':'Keep these instructions.'})
            config.save(path)
            # A previously opened settings form must not overwrite the main toggle.
            values=form(config);values.pop('reenable_cut_content')
            window=StatusWindow.__new__(StatusWindow)
            window.settings_path=path;window.stopping=False;window.saved_cut_content=True
            window.cut_content_enabled=tk.BooleanVar(tk.Tcl(),value=False)
            window.write=Mock()
            window.remember_cut_content()
            self.assertFalse(LLMConfig.load(path).reenable_cut_content)
            self.assertEqual(LLMConfig.load(path).prompts,config.prompts)
            self.assertIn('Restart',window.write.call_args.args[0])
            self.assertFalse(save_basics(path,values).reenable_cut_content)
            before=path.read_bytes()
            window.cut_content_enabled.set(True)
            with patch.object(LLMConfig,'save',side_effect=OSError('fixture failure')):
                window.remember_cut_content()
            self.assertFalse(window.cut_content_enabled.get())
            self.assertEqual(path.read_bytes(),before)
            self.assertIn('ERROR:',window.write.call_args.args[0])
            window.stopping=True;window.cut_content_enabled.set(True)
            with patch.object(LLMConfig,'save') as save:
                window.remember_cut_content()
                save.assert_not_called()
            self.assertFalse(window.cut_content_enabled.get())

    def test_default_and_persisted_checkbox(self):
        self.assertTrue(LLMConfig.defaults().reenable_cut_content)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text('{"model":"fixture"}')
            config=LLMConfig.load(path)
            self.assertTrue(config.reenable_cut_content)
            for enabled in (False,True):
                values=form(config);values['reenable_cut_content']=enabled
                self.assertEqual(save_basics(path,values).reenable_cut_content,enabled)
                self.assertEqual(LLMConfig.load(path).reenable_cut_content,enabled)
        for value in ('false',0,1,None):
            with self.assertRaises(ValueError):LLMConfig('fixture',reenable_cut_content=value)

    def test_all_five_emojis_remain_documented_but_can_be_skipped(self):
        self.assertEqual(len(RECOVERED_GESTURES),5)
        dispatcher=Mock()
        for emoji,action in EXPRESSION_GESTURES.items():
            self.assertIn(f'{emoji} {action}:',EXPRESSION_CONTRACT)
            self.assertIn(f'{emoji} {action}:',reply_contract())
            self.assertEqual(expression_from_speech(emoji)['candidate'],action)
            if action not in RECOVERED_GESTURES:continue
            speech=CreatureSpeech(reenable_cut_content=False)
            self.assertIsNone(speech.publish('Hello '+emoji,1,{'candidate':None},
                source='god_question',dispatcher=dispatcher))
            self.assertEqual(speech.snapshot(1)['current']['text'],'Hello')
            self.assertEqual(expression_from_speech(emoji+' 👋',reenable_cut_content=False)['candidate'],'wave')
        dispatcher.dispatch.assert_not_called()
        dispatcher.dispatch_expression.assert_not_called()

    def test_configuration_packet_never_contains_a_physical_action(self):
        state={'source_age_ms':0,'action_probe':{'epoch':4,'generation':8,'offered_at_uptime_ms':123,
            'supported':True,'transport_ready':True,'cut_content':{'protocol':13,'enabled':False}}}
        with patch('bwv.action_probe.snapshot',return_value=state),patch('bwv.action_probe.submit',return_value=True) as send:
            for enabled in (False,True):
                configure_cut_content(123,enabled)
                packet=COMMAND.unpack(send.call_args.args[1])
                self.assertEqual(packet,(0x42575041,13,4,8,123,0,int(enabled)))
            send.reset_mock();state['source_age_ms']=2000
            with self.assertRaises(ValueError):configure_cut_content(123,True)
            send.assert_not_called()
            state['source_age_ms']=0;state['action_probe']['cut_content']={}
            with self.assertRaisesRegex(ValueError,'Install'):configure_cut_content(123,True)
            send.assert_not_called()


class SettingSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_uses_saved_checkbox_for_gesture_dispatch(self):
        from bwv.qa_live import LiveDummyRuntime
        with TemporaryDirectory() as directory:
            runtime=LiveDummyRuntime(state_dir=directory,
                settings=LLMConfig('fixture',reenable_cut_content=False))
            self.assertFalse(runtime.broker.speech.reenable_cut_content)
            await runtime.broker.close()

    async def test_no_extra_pipe_work_when_setting_matches_or_game_absent(self):
        notices=Mock();frame={'diagnostics':{'action_probe':{'cut_content':{'protocol':13,'enabled':True}}}}
        with patch('bwv.action_probe.configure_cut_content') as configure:
            for _ in range(100):await sync_cut_content(123,frame,True,notices)
            await sync_cut_content(None,frame,False,notices)
            configure.assert_not_called()
            await sync_cut_content(123,frame,False,notices)
            configure.assert_called_once_with(123,False)

    async def test_configuration_error_is_reported_and_recoverable(self):
        notices=Mock();frame={'diagnostics':{'action_probe':{'cut_content':{'enabled':False}}}}
        with patch('bwv.action_probe.configure_cut_content',side_effect=[OSError('fixture'),None]) as configure:
            await sync_cut_content(123,frame,True,notices)
            self.assertTrue(notices.show.call_args.kwargs['error'])
            await sync_cut_content(123,frame,True,notices)
            self.assertEqual(configure.call_count,2)
            self.assertEqual(notices.show.call_args.args,('cut_content','Recovered content enabled.'))
