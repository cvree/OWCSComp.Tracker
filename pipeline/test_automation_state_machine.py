#!/usr/bin/env python3
"""
test_automation_state_machine.py — the automation state graph is legal and
strict where it must be. No network. Run: python3 pipeline/test_automation_state_machine.py
"""
from __future__ import annotations
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import state_machine as sm  # noqa: E402


class TestStateMachine(unittest.TestCase):
    def test_all_states_have_transition_entries(self):
        for s in sm.ALL_STATES:
            self.assertIn(s, sm.TRANSITIONS)

    def test_happy_path_is_walkable(self):
        path = [
            sm.DISCOVERED, sm.SCHEDULED, sm.AWAITING_BROADCAST, sm.RECORDING,
            sm.ARCHIVED, sm.DOWNLOADED, sm.SEGMENTING, sm.PROCESSING,
            sm.APPROVED, sm.PUBLISHED,
        ]
        for a, b in zip(path, path[1:]):
            self.assertTrue(sm.can_transition(a, b), f"{a} -> {b} should be legal")

    def test_cannot_skip_review_to_publish(self):
        # Processing must not jump straight to PUBLISHED (review/approve gate).
        self.assertFalse(sm.can_transition(sm.PROCESSING, sm.PUBLISHED))
        self.assertFalse(sm.can_transition(sm.DISCOVERED, sm.PUBLISHED))

    def test_terminal_states_have_no_exits(self):
        for s in sm.TERMINAL_STATES:
            self.assertEqual(sm.TRANSITIONS[s], frozenset())
            self.assertTrue(sm.is_terminal(s))

    def test_anything_active_can_fail(self):
        for s in sm.ALL_STATES:
            if s in sm.TERMINAL_STATES:
                continue
            self.assertTrue(sm.can_transition(s, sm.FAILED), f"{s} should be able to FAIL")

    def test_failure_lifecycle(self):
        self.assertTrue(sm.can_transition(sm.FAILED, sm.RETRY_SCHEDULED))
        self.assertTrue(sm.can_transition(sm.FAILED, sm.FAILED_PERMANENT))
        self.assertTrue(sm.can_transition(sm.RETRY_SCHEDULED, sm.PROCESSING))

    def test_noop_transition_allowed(self):
        self.assertTrue(sm.can_transition(sm.PROCESSING, sm.PROCESSING))

    def test_assert_transition_rejects_unknown_and_illegal(self):
        with self.assertRaises(ValueError):
            sm.assert_transition("NOPE", sm.SCHEDULED)
        with self.assertRaises(ValueError):
            sm.assert_transition(sm.DISCOVERED, sm.PUBLISHED)

    def test_claimable_excludes_terminal(self):
        self.assertFalse(sm.CLAIMABLE_STATES & sm.TERMINAL_STATES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
