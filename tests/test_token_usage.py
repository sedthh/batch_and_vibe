import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bwv.token_usage import TokenUsage, normalize_usage


class TokenUsageTests(unittest.TestCase):
    def test_reasoning_is_an_output_subset_for_both_protocols(self):
        for key in ('output_tokens_details','completion_tokens_details'):
            usage=normalize_usage({'input_tokens':10,'output_tokens':8,key:{'reasoning_tokens':5}})
            self.assertEqual(usage,{'input_tokens':10,'output_tokens':8,'total_tokens':18,
                'reasoning_tokens':5,'non_reasoning_output_tokens':3})
            for invalid in (None,True,-1,9,'5'):
                self.assertNotIn('reasoning_tokens',normalize_usage({'output_tokens':8,key:{'reasoning_tokens':invalid}}))
        self.assertEqual(normalize_usage({'completion_tokens_details':{'reasoning_tokens':0}}),{'reasoning_tokens':0})

    def test_legacy_totals_keep_history_but_mark_thinking_unknown(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'usage.json'
            path.write_text(json.dumps({'version':1,'total':dict(requests=2,input_tokens=12,
                output_tokens=8,total_tokens=20,responses_with_complete_usage=2)}))
            ledger=TokenUsage(path)
            ledger.begin_request();ledger.record({'prompt_tokens':3,'completion_tokens':4,
                'completion_tokens_details':{'reasoning_tokens':2}})
            stats=TokenUsage(path).snapshot()['total']
            self.assertEqual(stats['total_tokens'],27)
            self.assertEqual(stats['reasoning_tokens'],2)
            self.assertEqual(stats['non_reasoning_output_tokens'],2)
            self.assertEqual(stats['requests_with_unknown_reasoning_usage'],2)
            self.assertEqual(json.loads(path.read_text())['version'],3)

    def test_cache_reads_and_writes_are_reported_input_subsets_not_added_to_totals(self):
        for detail in ('input_tokens_details','prompt_tokens_details'):
            ledger=TokenUsage();ledger.begin_request()
            ledger.record({'input_tokens':100,'output_tokens':8,detail:{'cached_tokens':60,'cache_write_tokens':20}})
            counts=ledger.snapshot()['session']
            self.assertEqual(counts['cached_input_tokens'],60)
            self.assertEqual(counts['cache_write_input_tokens'],20)
            self.assertEqual(counts['total_tokens'],108)
            self.assertEqual(counts['requests_with_unknown_cache_read_usage'],0)
            for invalid in (None,True,-1,101,'60'):
                self.assertNotIn('cached_input_tokens',normalize_usage({'input_tokens':100,detail:{'cached_tokens':invalid}}))
        self.assertNotIn('cached_input_tokens',normalize_usage({'input_tokens':100}))

    def test_v2_migration_preserves_totals_and_marks_older_cache_usage_unknown(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'usage.json'
            old=TokenUsage();old.begin_request();old.record({'input_tokens':10,'output_tokens':5})
            legacy={k:v for k,v in old.total.items() if 'cache' not in k}
            path.write_text(json.dumps({'version':2,'total':legacy}))
            ledger=TokenUsage(path)
            self.assertIsNone(ledger.storage_error)
            ledger.begin_request();ledger.record({'input_tokens':10,'output_tokens':5,'input_tokens_details':{'cached_tokens':8}})
            restored=TokenUsage(path).snapshot()['total']
            self.assertEqual(restored['input_tokens'],20)
            self.assertEqual(restored['cached_input_tokens'],8)
            self.assertEqual(restored['requests_with_unknown_cache_read_usage'],1)
            self.assertEqual(restored['requests_with_unknown_cache_write_usage'],2)

    def test_reset_persists_and_new_usage_starts_from_zero(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'usage.json';ledger=TokenUsage(path)
            ledger.begin_request();ledger.record({'input_tokens':1,'output_tokens':5,
                'output_tokens_details':{'reasoning_tokens':4}})
            ledger.reset()
            self.assertTrue(all(v==0 for v in ledger.snapshot()['session'].values()))
            self.assertTrue(all(v==0 for v in TokenUsage(path).snapshot()['total'].values()))
            ledger.begin_request();ledger.record({'input_tokens':2,'output_tokens':3})
            self.assertEqual(ledger.snapshot()['total']['requests_with_unknown_reasoning_usage'],1)

    def test_failed_reset_preserves_previous_counters_and_file(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'usage.json';ledger=TokenUsage(path)
            ledger.begin_request();ledger.record({'input_tokens':2,'output_tokens':1})
            before=path.read_text()
            with patch('bwv.token_usage.tempfile.NamedTemporaryFile',side_effect=PermissionError()):
                with self.assertRaises(ValueError):ledger.reset()
            self.assertEqual(path.read_text(),before)
            self.assertEqual(ledger.snapshot()['total']['requests'],1)

    def test_normalize_aliases_without_double_counting_or_inventing_missing_usage(self):
        self.assertEqual(normalize_usage({'input_tokens':10,'prompt_tokens':99,
            'completion_tokens':4}),{'input_tokens':10,'output_tokens':4,'total_tokens':14})
        for bad in (None,[],{'input_tokens':True,'output_tokens':-1,'total_tokens':'9'}):
            self.assertEqual(normalize_usage(bad),{})
        self.assertEqual(normalize_usage({'input_tokens':7}),{'input_tokens':7})

    def test_rounds_persist_numeric_totals_across_restart_and_mark_unknown_requests(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'usage.json';ledger=TokenUsage(path)
            ledger.begin_request();ledger.record({'input_tokens':20,'output_tokens':5})
            ledger.begin_request();ledger.record({'prompt_tokens':25,'completion_tokens':6})
            ledger.begin_request()  # Timeout/cancellation: billing cannot be inferred.
            ledger.begin_request();ledger.record(None)  # Local server omitted usage.
            ledger.begin_request();ledger.record({'input_tokens':3})
            restored=TokenUsage(path);stats=restored.snapshot()
            self.assertEqual(stats['total']['input_tokens'],48)
            self.assertEqual(stats['total']['output_tokens'],11)
            self.assertEqual(stats['total']['requests_with_unknown_usage'],3)
            self.assertEqual(stats['session']['requests'],0)
            self.assertEqual(set(json.loads(path.read_text())),{'version','total'})
            self.assertNotIn(directory,path.read_text())

    def test_corrupt_file_preserved_and_fixed_error_does_not_leak_its_contents(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'usage.json';path.write_text('synthetic-private-content')
            ledger=TokenUsage(path);ledger.begin_request()
            ledger.record({'input_tokens':2,'output_tokens':1})
            self.assertEqual(path.read_text(),'synthetic-private-content')
            self.assertEqual(ledger.snapshot()['storage_error'],'usage_totals_unavailable')
            self.assertNotIn('synthetic-private-content',json.dumps(ledger.snapshot()))
