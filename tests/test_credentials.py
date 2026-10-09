from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bwv.credentials import credential_environment


class CredentialTests(unittest.TestCase):
    def test_loads_only_selected_key_without_mutating_environment(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            path.write_text('\ufeff# synthetic fixture\nOTHER=unused\nexport TEST_KEY="synthetic-token" # comment\n', encoding='utf-8')
            env = {}
            self.assertEqual(credential_environment('TEST_KEY', path, environ=env), {'TEST_KEY':'synthetic-token'})
            self.assertEqual(env, {})
            self.assertEqual(credential_environment('OTHER', path, environ={}), {'OTHER':'unused'})
            self.assertEqual(credential_environment('ABSENT', path, environ={}), {})

    def test_environment_precedence_and_local_without_credentials(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            self.assertEqual(credential_environment('TEST_KEY', path, environ={}), {})
            path.write_bytes(b'\xff')
            self.assertEqual(credential_environment('TEST_KEY', path, environ={'TEST_KEY':'process-token'}),
                             {'TEST_KEY':'process-token'})
            self.assertEqual(credential_environment(None, path, environ={}), {})

    def test_no_execution_or_interpolation_and_fixed_errors(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            path.write_text('TEST_KEY=${UNEXPANDED}\n')
            self.assertEqual(credential_environment('TEST_KEY', path, environ={}), {'TEST_KEY':'${UNEXPANDED}'})
            for content in ('TEST_KEY="synthetic-secret', 'TEST_KEY=one\nTEST_KEY=two',
                            'TEST_KEY=synthetic secret', 'X'*65537):
                path.write_text(content)
                with self.assertRaises(ValueError) as caught:
                    credential_environment('TEST_KEY', path, environ={})
                message = str(caught.exception)
                self.assertNotIn(directory, message)
                self.assertNotIn('synthetic', message)
