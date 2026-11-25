import unittest

import pandas as pd

from ai.triple_barrier import TripleBarrierLabeler


class BarrierTests(unittest.TestCase):
    def test_directional_barriers(self):
        cases = [(-1, 99, 0), (-1, 98, 1), (-1, 101, -1),
                 (1, 101, 0), (1, 102, 1), (1, 99, -1)]
        for side, end, label in cases:
            with self.subTest(side=side, end=end):
                bars = pd.DataFrame({"close": [100, end], "signal": [side, 0]})
                result = TripleBarrierLabeler(max_hold_bars=1).apply_barriers(bars)
                self.assertEqual(result.loc[0, "target_label"], label)

    def test_unfinished_trade_has_no_training_label(self):
        bars = pd.DataFrame({"close": [100, 100], "signal": [1, 0]})
        result = TripleBarrierLabeler(max_hold_bars=2).apply_barriers(bars)
        self.assertTrue(pd.isna(result.loc[0, "target_label"]))

    def test_observed_hit_counts_before_end_of_history(self):
        bars = pd.DataFrame({"close": [100, 98], "signal": [-1, 0]})
        result = TripleBarrierLabeler(max_hold_bars=2).apply_barriers(bars)
        self.assertEqual(result.loc[0, "target_label"], 1)
