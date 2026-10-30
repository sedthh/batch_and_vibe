import hashlib
import unittest
from unittest.mock import Mock

from bwv.action_probe import COMMAND
from bwv.voice import VoiceSettings
from bwv.vr import CaptionMirror,caption_packet,caption_pages,creature_pages,voice_settings


def state():
    return {'pid':123,'session':1,'source_age_ms':10,'action_probe':{
        'conditions_allow_action':True,'epoch':1,'generation':3,'offered_at_uptime_ms':100,
        'conversation_actions':{'creature_handle':200,'mental_handle':300},
        'vr_caption':{'version':15,'busy':False,'queued':False}}}


def payload(now=100,text='Hello there.'):
    creature=hashlib.sha256(b'123:1:200:300').hexdigest()[:24]
    return {'runtime':{'mode':'live_dummy_preview'},'source':{'observation':{
        'session':'123:1','creature_id':creature,'observed_at':now,'connected':True}},
        'current':{'question_unavailable':None,'speech':{'last_message':{
            'text':text,'created_at':100}}}}


class VrTests(unittest.TestCase):
    def test_name_prefix_repeats_on_every_page_and_cannot_add_engine_commands(self):
        pages=creature_pages('Hello '*100,'Bongo$C123\\C4')
        self.assertGreater(len(pages),1)
        for page in pages:
            self.assertTrue(page.startswith('Bongo C123 C4: '))
            self.assertLessEqual(len(page),240)
            self.assertNotIn('$',page);self.assertNotIn('\\',page)
        self.assertEqual(creature_pages('Hello.',None),['Creature: Hello.'])

    def test_notice_waits_for_reply_and_neither_is_ever_refreshed(self):
        native=state();native['action_probe']['vr_caption']['notices']=True
        sender=Mock(return_value=True);clock=[100]
        mirror=CaptionMirror(reader=lambda pid:native,sender=sender,clock=lambda:clock[0])
        mirror.update(payload())
        mirror.queue_voice_notice('Heard: Cast water.',mirror.model.identity)
        native['action_probe']['vr_caption']['busy']=True
        for now in (102,105,109):
            clock[0]=now;mirror.update(payload(now=now))
        self.assertEqual(sender.call_count,1)
        native['action_probe']['vr_caption']['busy']=False
        clock[0]=111;mirror.update(payload(now=111))
        self.assertEqual(sender.call_args.args[1][40:].decode('utf-16-le').rstrip('\0'),'[Heard: Cast water.]')
        self.assertEqual(COMMAND.unpack(sender.call_args.args[1][:40])[-2],1)
        for now in (113,120,140):
            clock[0]=now;mirror.update(payload(now=now))
        self.assertEqual(sender.call_count,2)

    def test_repeated_errors_are_deduplicated_and_stale_notices_expire(self):
        native=state();native['action_probe']['vr_caption'].update(notices=True,busy=True)
        sender=Mock(return_value=True);clock=[100]
        mirror=CaptionMirror(reader=lambda pid:native,sender=sender,clock=lambda:clock[0])
        mirror.update(payload(text=''));identity=mirror.model.identity
        mirror.queue_voice_notice('Voice: No speech recognized.',identity)
        clock[0]=105;mirror.queue_voice_notice('Voice: No speech recognized.',identity)
        native['action_probe']['vr_caption']['busy']=False
        clock[0]=131;mirror.update(payload(now=131,text=''));sender.assert_not_called()
        mirror.queue_voice_notice('Voice error: Microphone unavailable.',identity)
        clock[0]=132;mirror.update(payload(now=132,text=''))
        self.assertEqual(sender.call_count,1)
        clock[0]=135;mirror.queue_voice_notice('Voice error: Microphone unavailable.',identity)
        mirror.update(payload(now=135,text=''));self.assertEqual(sender.call_count,1)

    def test_old_identity_and_scene_change_discard_notice(self):
        native=state();native['action_probe']['vr_caption']['notices']=True
        sender=Mock();clock=[100]
        mirror=CaptionMirror(reader=lambda pid:native,sender=sender,clock=lambda:clock[0])
        mirror.queue_voice_notice('Heard: Hello.',('other','creature'))
        mirror.update(payload(text=''));sender.assert_not_called()
        mirror.queue_voice_notice('Heard: Hello.',mirror.model.identity)
        hidden=payload(text='');hidden['source']['observation']['cutscene']=True;mirror.update(hidden)
        clock[0]=102;mirror.update(payload(now=102,text=''));sender.assert_not_called()

    def test_notice_requires_new_capability_and_rejects_empty_text(self):
        native=state()
        with self.assertRaises(ValueError):caption_packet(native,'Hello.',notice=True)
        native['action_probe']['vr_caption']['notices']=True
        self.assertEqual(COMMAND.unpack(caption_packet(native,'Hello.',notice=True)[:40])[-2],1)
        with self.assertRaises(ValueError):caption_packet(native,'',notice=True)

    def test_long_notice_is_shortened_to_one_packet(self):
        native=state();native['action_probe']['vr_caption']['notices']=True
        sender=Mock();mirror=CaptionMirror(reader=lambda pid:native,sender=sender,clock=lambda:100)
        data=payload(text='');mirror.model.update(data,100)
        mirror.queue_voice_notice('Heard: '+'hello '*400,mirror.model.identity);mirror.update(data)
        text=sender.call_args.args[1][40:].decode('utf-16-le').rstrip('\0')
        self.assertLessEqual(len(text),240);self.assertTrue(text.endswith('\u2026]'))

    def test_vr_requires_its_overlay_worker(self):
        import io
        from contextlib import redirect_stderr
        from bwv.launcher import arguments
        self.assertTrue(arguments(['--vr']).vr)
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            arguments(['--vr','--no-overlay'])

    def test_vr_preserves_all_voice_preferences(self):
        original=VoiceSettings(hotkey='Ctrl+F11',auto_send=False)
        adjusted=voice_settings(original)
        self.assertEqual(adjusted.hotkey,'Ctrl+F11')
        self.assertFalse(adjusted.auto_send)
        self.assertTrue(adjusted.push_to_talk)
        self.assertEqual(adjusted,original)
        self.assertTrue(original.push_to_talk)
        self.assertFalse(adjusted.enabled)
        self.assertEqual(original.hotkey,'Ctrl+F11')
        self.assertFalse(original.auto_send)

    def test_caption_pages_are_bounded_unicode_and_no_engine_markup(self):
        pages=caption_pages(('Hello café 🐮 50% <test>\n'+'x'*300)*4)
        self.assertGreater(len(pages),4)
        for page in pages:
            self.assertLessEqual(len(page.encode('utf-16-le')),480)
            packet=caption_packet(state(),page)
            self.assertEqual(len(packet),552)
            self.assertNotIn('%',page)
            self.assertNotIn('<',page)
        self.assertIn('café',' '.join(pages))

    def test_packet_binds_epoch_creature_and_offer(self):
        packet=caption_packet(state(),'Hello.')
        self.assertEqual(COMMAND.unpack(packet[:40]),(0x42575041,15,1,3,100,0,200))
        for text in ('','x'*256,'a\0b','bad%','🦊','\uf8ff','\x80'):
            with self.assertRaises(ValueError):caption_packet(state(),text)
        old=state();old['action_probe']['vr_caption']={}
        with self.assertRaises(ValueError):caption_packet(old,'Hello.')

    def test_same_reply_is_sent_once_and_new_creature_never_receives_old_text(self):
        native=state();sender=Mock(return_value=True)
        mirror=CaptionMirror(reader=lambda pid:native,sender=sender,clock=lambda:100)
        mirror.update(payload());mirror.update(payload())
        self.assertEqual(sender.call_count,1)
        mirror=CaptionMirror(reader=lambda pid:native,sender=sender,clock=lambda:100)
        native['action_probe']['conversation_actions']['mental_handle']=999
        mirror.update(payload())
        self.assertEqual(sender.call_count,1)
        self.assertEqual(mirror.status,'context_changed')

    def test_native_dialogue_waits_and_scene_change_drops_pending_pages(self):
        native=state();native['action_probe']['vr_caption']['busy']=True
        sender=Mock(return_value=True);clock=[100]
        mirror=CaptionMirror(reader=lambda pid:native,sender=sender,clock=lambda:clock[0])
        mirror.update(payload());sender.assert_not_called()
        hidden=payload();hidden['source']['observation']['cutscene']=True
        mirror.update(hidden)
        native['action_probe']['vr_caption']['busy']=False;clock[0]=101
        mirror.update(payload(now=101));sender.assert_not_called()

    def test_uncertain_delivery_is_not_retried(self):
        sender=Mock(side_effect=TimeoutError('no acknowledgement'));clock=[100]
        mirror=CaptionMirror(reader=lambda pid:state(),sender=sender,clock=lambda:clock[0])
        with self.assertRaises(TimeoutError):mirror.update(payload())
        clock[0]=103;mirror.update(payload(now=103))
        self.assertEqual(sender.call_count,1)

    def test_stale_native_sample_is_not_used(self):
        for change in ({'source_age_ms':1501},{'source_age_ms':-1},{'source_age_ms':None},{'pid':456}):
            native=state();native.update(change);sender=Mock()
            mirror=CaptionMirror(reader=lambda pid:native,sender=sender,clock=lambda:100)
            mirror.update(payload());sender.assert_not_called()


if __name__=='__main__':unittest.main()
