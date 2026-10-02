"""Negative controls (non-idling proof, saFUZZ-fuzzing.md §2.1.4 / §3.2).

The chaos harness is worthless if its assertions cannot go red. Each test
plants a DELIBERATELY WRONG expectation against a real injected fault and
proves the harness catches it (pytest.raises(AssertionError)). If any of these
ever passes silently, the harness is broken — attribution class 4 (channel).
"""

from __future__ import annotations

import pytest

from v6.controller import client as rpc

from .conftest import (expect_bounded, expect_error, expect_ok,
                       expect_peer_requests, run_one_shot, run_session_call)

pytestmark = pytest.mark.timeout(90)


class TestNegativeControl:
    def test_expect_success_on_permanent_hang_must_go_red(
            self, chaos_shim, set_mode, fake_target):
        """Canonical control: shim wedged forever. Asserting SUCCESS must fail —
        the harness produces a bounded structured error instead of passing."""
        set_mode("hang_forever")
        out = run_one_shot(fake_target, transport_timeout_s=1, watchdog_s=20)
        # reality: bounded transport error (this proves the deadline works)...
        expect_error(out, rpc.RpcTransportError, match="timed out")
        expect_bounded(out, 0.8, 10.0)
        # ...and the planted WRONG expectation (success) is caught red-handed
        with pytest.raises(AssertionError):
            expect_ok(out)

    def test_wrong_error_class_must_go_red(self, chaos_shim, set_mode, fake_target):
        """Truncated frame -> protocol error. Claiming it was a TRANSPORT error
        (wrong class) must be caught: the E1 assertion pins exact classes."""
        set_mode("truncate")
        out = run_one_shot(fake_target, watchdog_s=20)
        expect_error(out, rpc.RpcProtocolError)  # reality
        with pytest.raises(AssertionError):
            expect_error(out, rpc.RpcTransportError)  # planted wrong class

    def test_impossible_time_bound_must_go_red(self, chaos_shim, set_mode, fake_target):
        """A 0.6s injected delay cannot finish in 0.1s: the E2 wall-clock
        assertion must reject an impossibly tight bound (proves it measures)."""
        set_mode("delay", delay_s=0.6)
        out = run_one_shot(fake_target, transport_timeout_s=10, watchdog_s=20)
        expect_ok(out)
        expect_bounded(out, 0.3, 15.0)  # reality
        with pytest.raises(AssertionError):
            expect_bounded(out, 0.0, 0.1)  # planted impossible bound

    def test_wrong_peer_count_must_go_red(self, chaos_shim, set_mode, fake_target):
        """E3/E4's teeth: after ONE request the peer counter says 1 — asserting
        any other number (e.g. a phantom resend) must fail."""
        set_mode("ok")
        sess = rpc.Session(fake_target)
        try:
            expect_ok(run_session_call(sess, "host.info"))
        finally:
            sess.close()
        expect_peer_requests(chaos_shim, 1)  # reality
        with pytest.raises(AssertionError):
            expect_peer_requests(chaos_shim, 2)  # planted phantom duplicate
