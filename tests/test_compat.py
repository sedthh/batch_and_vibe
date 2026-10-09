"""Synthetic compatibility checks; no game binaries, CD keys or local settings."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bwv.compat import BUILD_ID, EXE_SHA256, VM_SHA256, REQUIRED_EXPORTS, inspect_install


class CompatibilityTests(unittest.TestCase):
    def inspect(self, hashes, *, machine=0x14C):
        pe=SimpleNamespace(machine=machine,managed=False,exports=lambda:sorted(REQUIRED_EXPORTS))
        with TemporaryDirectory() as directory, patch('bwv.compat.PE.read',return_value=pe), \
                patch('bwv.compat.sha256',side_effect=hashes):
            return inspect_install(Path(directory))

    def test_build_metadata_has_no_binary_hashes_or_recording_date(self):
        data=json.loads((Path(__file__).parents[1]/'src/bwv/builds.json').read_text())
        record=next(b for b in data['builds'] if b['id']==BUILD_ID)
        self.assertNotIn('sha256',json.dumps(record))
        self.assertNotRegex(record['id'],r'\d{4}-\d{2}-\d{2}')

    def test_supported_files_still_match_without_hashes_in_metadata(self):
        result=self.inspect([EXE_SHA256,VM_SHA256])
        self.assertEqual(result['status'],'experimental')
        self.assertEqual(result['build']['id'],BUILD_ID)

    def test_unknown_exe_vm_or_architecture_is_still_rejected(self):
        for hashes in ([('0'*64),VM_SHA256],[EXE_SHA256,('0'*64)]):
            result=self.inspect(hashes)
            self.assertEqual(result['status'],'unsupported')
            self.assertIsNone(result['build'])
        self.assertEqual(self.inspect([EXE_SHA256,VM_SHA256],machine=0x8664)['status'],'unsupported')

    def test_shareable_report_omits_private_fields_even_if_input_is_extended(self):
        from bwv.compat import shareable_report
        source=self.inspect([EXE_SHA256,VM_SHA256])
        source.update(install='synthetic-private-folder',api_key='synthetic-secret',profile_name='SyntheticProfile')
        source['compatibility']['exe']['private_setting']='synthetic-secret'
        result=shareable_report(source)
        self.assertNotIn('synthetic-',json.dumps(result))
        self.assertNotIn('profile',json.dumps(result))
        self.assertNotIn('install',result)
        self.assertEqual(result['build_id'],BUILD_ID)
