import contextlib
import io
import json
import unittest
from unittest.mock import AsyncMock, patch

from bwv.cli import main
from bwv.qa import run


class DebugStartupTests(unittest.TestCase):
    def test_cli_forwards_debug_opt_in_for_each_live_server(self):
        for command, args, target in (
            ('qa', ['123'], 'bwv.qa.run'),
            ('qa-live-dummy', ['synthetic-url'], 'bwv.qa_live.run'),
            ('chat', ['synthetic-url'], 'bwv.qa_live.run_llm'),
        ):
            for enabled in (False, True):
                argv = ['bwv', command, *args, *(['--debug'] if enabled else [])]
                with patch('sys.argv', argv), patch(target, return_value={}) as launch:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(), 0)
                    self.assertIs(launch.call_args.kwargs['debug'], enabled)

    def test_startup_labels_dashboard_only_when_enabled_and_always_logs_api(self):
        for enabled in (False, True):
            output = io.StringIO()
            with patch('bwv.qa.collect', new_callable=AsyncMock), contextlib.redirect_stdout(output):
                run(123, seconds=1, debug=enabled)
            lines = output.getvalue().splitlines()
            startup = json.loads(lines[-1])
            self.assertTrue(startup['api_url'].startswith('http://127.0.0.1:'))
            self.assertEqual(startup['qa_url'], startup['api_url'] if enabled else None)
            self.assertEqual(any(line.startswith('Debug inspector: ') for line in lines), enabled)
