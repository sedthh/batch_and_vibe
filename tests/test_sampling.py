import unittest
from bwv.sampling import DetailRefresh


class DetailRefreshTests(unittest.TestCase):
    def test_twelve_fast_polls_reuse_one_scan_without_relabelling_its_time(self):
        cache=DetailRefresh();reads=0
        for i in range(48):
            now=i*.25
            if cache.due('creature',now,i):
                reads+=1;cache.store('creature','world',now,20,cache.requested)
            self.assertEqual(cache.diagnostics(now)['age_seconds'],(i%12)*.25)
        self.assertEqual(reads,4)

    def test_demand_refresh_is_coalesced_and_not_acknowledged_by_an_older_scan(self):
        cache=DetailRefresh();cache.store('a','old',100,1,0)
        in_progress=cache.requested
        requested=cache.request()
        self.assertEqual(cache.request(),requested)
        cache.store('a','started_before_request',100.1,1,in_progress)
        self.assertTrue(cache.due('a',100.2))
        cache.store('a','fresh',100.2,1,requested)
        self.assertFalse(cache.due('a',100.3))
        self.assertGreater(cache.request(),requested)

    def test_identity_menu_resume_and_clock_or_simulation_rollback_force_scans(self):
        cache=DetailRefresh();cache.due('a',100,20);cache.store('a','world',100,1,0)
        self.assertTrue(cache.due('b',100.1,21))
        self.assertTrue(cache.due('a',99,22))
        self.assertTrue(cache.due('a',100.1,1))
        cache.clear()
        self.assertIsNone(cache.value)
        self.assertIsNone(cache.diagnostics(100.2)['observed_at'])
        self.assertTrue(cache.due('a',100.2,2))
