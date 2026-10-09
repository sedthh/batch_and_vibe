from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from bwv.control import ControlGate, ControlObservation
from bwv.control_live import (PauseSample, CameraControlSample, read_pause, with_pause,
    read_camera_control, with_camera_control, camera_ownership,
    GAME_FLAGS, SCRIPT_POINTER, CAMERA_RELEASE, CAMERA_ENABLED, CAMERA_OWNER)
from bwv.training_live import GAME_POINTER
from test_training_live import MemoryFixture


class ControlMemory(MemoryFixture):
    def __init__(self):
        super().__init__()
        self.script = 0x6000000
        self.put(self.game + GAME_FLAGS, 0x03810020)
        self.put(self.game + SCRIPT_POINTER, self.script)
        for i, (address, name) in enumerate(((self.game, b'.?AVGGame@@'),
                                            (self.script, b'.?AVGScript@@')), start=3):
            table = 0x5000000 + i * 0x1000
            self.put(address, table)
            self.put(table - 4, table + 0x100)
            self.put(table + 0x10c, table + 0x200)
            self.bytes(table + 0x208, name.ljust(64, b'\0'))
        self.camera(1, 1, 0)

    def camera(self, release, enabled, owner):
        for offset, value in ((CAMERA_RELEASE, release), (CAMERA_ENABLED, enabled),
                              (CAMERA_OWNER, owner)):
            self.put(self.script + offset, value)


class LiveControlTests(TestCase):
    def test_pause_reads_flag_without_requiring_fresh_vm_snapshot(self):
        memory = ControlMemory()
        with patch('bwv.training_live.snapshot', side_effect=AssertionError('No VM read needed')):
            self.assertFalse(read_pause(memory.reader).paused)
            memory.put(memory.game + GAME_FLAGS, 0x03810024)
            self.assertTrue(read_pause(memory.reader).paused)
            memory.put(memory.game + GAME_FLAGS, 0x03810020)
            self.assertFalse(read_pause(memory.reader).paused)

    def test_pause_never_refreshes_stale_game_observations(self):
        old = ControlObservation('1234:4', 10, 0, connected=True, supported=False)
        updated = with_pause(old, PauseSample(1234, 10, False), 10)
        self.assertEqual(updated.observed_at, 0)
        self.assertEqual(updated.sequence, 10)
        status = ControlGate(resume_delay=0).update(updated, 10)
        self.assertFalse(status.allowed)
        self.assertIn('stale', status.reasons)
        self.assertIn('unsupported', status.reasons)

    def test_old_or_other_process_pause_data_remains_unknown(self):
        observation = ControlObservation('1234:4', 1, 1)
        for signal in (PauseSample(4567, 1, False), PauseSample(1234, 0, False),
                       PauseSample(1234, 2, False)):
            self.assertIsNone(with_pause(observation, signal, 1).paused)

    def test_pause_change_during_read_is_rejected(self):
        memory = ControlMemory()
        original = memory.read
        count = 0
        def changing(address, size):
            nonlocal count
            if address == memory.game + GAME_FLAGS:
                count += 1
                return bytes([4 if count % 2 else 0])
            return original(address, size)
        memory.reader.read = changing
        with self.assertRaises(ValueError):
            read_pause(memory.reader)

    def test_game_replacement_during_read_is_rejected(self):
        memory = ControlMemory()
        original = memory.reader.word
        count = 0
        def changing(address):
            nonlocal count
            if address == GAME_POINTER:
                count += 1
                return memory.game if count % 2 else memory.game + 0x100
            return original(address)
        memory.reader.word = changing
        with self.assertRaises(ValueError):
            read_pause(memory.reader)

    def test_scripted_camera_ownership_and_release(self):
        memory = ControlMemory()
        self.assertFalse(read_camera_control(memory.reader).script_owned)
        memory.camera(0, 0, 48)
        active = read_camera_control(memory.reader)
        self.assertTrue(active.script_owned)
        self.assertEqual(active.task_number, 48)
        memory.camera(1, 1, 0)
        self.assertFalse(read_camera_control(memory.reader).script_owned)

    def test_inconsistent_camera_fields_stay_unknown(self):
        for values in ((0, 1, 48), (1, 0, 48), (0, 0, 0), (1, 1, 48), (0, 0, 0xFFFFFFFF)):
            self.assertEqual(camera_ownership(*values), (None, None))

    def test_camera_lock_can_deny_but_release_cannot_grant_permission(self):
        observation = ControlObservation('1234:4', 1, 1, connected=True)
        active = with_camera_control(observation, CameraControlSample(1234, 1, True, 48), 1)
        self.assertTrue(active.cutscene)
        self.assertFalse(ControlGate(resume_delay=0).update(active, 1).allowed)
        released = with_camera_control(observation, CameraControlSample(1234, 1, False), 1)
        self.assertIsNone(released.cutscene)
        self.assertIsNone(released.player_has_control)
        self.assertIsNone(released.script_owns_creature)
        self.assertFalse(released.supported)
        existing = replace(observation, cutscene=True)
        self.assertTrue(with_camera_control(existing, CameraControlSample(1234, 1, False), 1).cutscene)

    def test_stale_or_other_process_camera_owner_is_not_applied(self):
        observation = ControlObservation('1234:4', 1, 1)
        for signal in (CameraControlSample(4567, 1, True, 48),
                       CameraControlSample(1234, 0, True, 48)):
            self.assertIsNone(with_camera_control(observation, signal, 1).cutscene)
