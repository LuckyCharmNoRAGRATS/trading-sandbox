"""Purged event-time CV; walk-forward for reported out-of-sample scores."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .contracts import validate_events


@dataclass(frozen=True)
class PurgedEmbargoSplit:
    n_splits: int = 4
    embargo: pd.Timedelta = pd.Timedelta("2D")
    walk_forward: bool = True

    def split(self, events: pd.DataFrame):
        validate_events(events)
        if self.n_splits < 2 or self.embargo < pd.Timedelta(0):
            raise ValueError("n_splits >= 2 and nonnegative embargo required")
        if len(events) < self.n_splits + 1:
            raise ValueError("not enough events")
        positions = np.arange(len(events))
        # First block is always training-only in walk-forward mode.
        blocks = np.array_split(positions, self.n_splits + 1 if self.walk_forward else self.n_splits)
        for test_pos in (blocks[1:] if self.walk_forward else blocks):
            start = events.index[test_pos[0]]
            end = events.iloc[test_pos]["event_end"].max()
            candidates = positions[positions < test_pos[0]] if self.walk_forward else positions[~np.isin(positions, test_pos)]
            candidate_events = events.iloc[candidates]
            overlaps = (candidate_events.index <= end) & (candidate_events["event_end"] >= start)
            # Embargo only affects samples after the validation event interval.
            post_embargo = (candidate_events.index > end) & (candidate_events.index <= end + self.embargo)
            train_pos = candidates[~(overlaps | post_embargo)]
            if len(train_pos) == 0:
                raise ValueError("fold has no training events after purge")
            yield train_pos, test_pos
