"""WRE v6 chaos matrix — transport fault injection through the PATH-shim fake ssh.

One test per matrix cell (saFUZZ-fuzzing.md §3.2): death mode × phase × E1-E6.
Both controller links are injected through the same shim:
  - run_one_shot    -> client.call_request  (subprocess.run)
  - run_session_call -> client.Session      (subprocess.Popen, loop mode)

Phase-exact write/read counters additionally use the scripted FakePopen
(seam 2) where the OS-level pipe race is genuinely nondeterministic
(write-into-dying-child); those cells are labeled seam2.

Fast cells run in milliseconds; the two real-time >deadline cells
(spin / delay-over-deadline) and the stderr-flood cell carry explicit
timeout markers. Zero network, zero real hosts.
"""

from __future__ import annotations

import json
import time

import pytest

from v6.controller import client as rpc

from .conftest import (expect_bounded, expect_connects, expect_error, expect_ok,
                       expect_payload_never_in_argv, expect_peer_requests,
                       run_one_shot, run_session_call)

pytestmark = pytest.mark.timeout(150)


# ---------- sanity: the shim speaks the protocol on both links ----------

class TestShimSanity:
    def test_one_shot_ok(self, chaos_shim, set_mode, fake_target):
        set_mode("ok")
        out = run_one_shot(fake_target)
        call = expect_ok(out)
        assert call.data["echo"] == "host.info"
        expect_peer_requests(chaos_shim, 1)
        expect_connects(chaos_shim, 1, 1)
        expect_payload_never_in_argv(chaos_shim)

    def test_session_ok_two_calls_one_connection(self, chaos_shim, set_mode, fake_target):
        set_mode("ok")
        sess = rpc.Session(fake_target)
        try:
            expect_ok(run_session_call(sess, "host.info"))
            expect_ok(run_session_call(sess, "host.capabilities"))
        finally:
            sess.close()
        expect_peer_requests(chaos_shim, 2)
        expect_connects(chaos_shim, 1, 1)  # loop mode amortizes the connection


# ---------- D1 断连 (peer death / RST) ----------

class TestDisconnect:
    def test_connect_phase_one_shot(self, chaos_shim, set_mode, fake_target):
        """ssh itself fails (exit 255): a structured error, bounded — never a hang."""
        set_mode("connect_refused")
        out = run_one_shot(fake_target, transport_timeout_s=10, watchdog_s=20)
        # current surface: empty stdout classifies as protocol; see report obs-1
        expect_error(out, rpc.RpcProtocolError, match="did not return JSON")
        expect_bounded(out, 0.0, 15.0)

    def test_connect_phase_session(self, chaos_shim, set_mode, fake_target):
        """Session spawn where the peer dies instantly: transport family, bounded,
        at most ONE automatic reconnect (write-phase retry) — then give up."""
        set_mode("connect_refused")
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, watchdog_s=20)
            expect_error(out, rpc.RpcTransportError, exact=False)
            expect_bounded(out, 0.0, 15.0)
        finally:
            sess.close()
        expect_connects(chaos_shim, 1, 2)  # linear, never a storm

    def test_write_phase_reconnect_exactly_once_seam2(self, fake_target, popen_patcher):
        """E3: provable non-delivery -> ONE reconnect + resend; peer executes the
        request exactly once across both connections (seam 2 = deterministic
        write break; QC-WRE R2 semantics)."""
        ok = json.dumps({"ok": True, "data": {"re": 1}}) + "\n"
        factory = popen_patcher([{"broken_writes": 1}, {"stdout_lines": [ok]}])
        sess = rpc.Session(fake_target)
        try:
            call = sess.call("process.start", {"exe": "C:/x"})
            assert call.ok is True
        finally:
            sess.close()
        assert len(factory.spawned) == 2  # exactly one reconnect
        delivered = sum(len(p.stdin.writes) for p in factory.spawned)
        assert delivered == 1  # the peer saw the non-idempotent action ONCE

    def test_post_write_pre_read_no_auto_resend(self, chaos_shim, set_mode, fake_target):
        """E4 (R2 key regression): the peer read the request then died —
        the bridge must NOT auto-resend; the caller retries deliberately."""
        set_mode("disconnect_on_read", fail_first_n=1)
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, "process.start", {"exe": "C:/x"}, watchdog_s=20)
            expect_error(out, rpc.RpcTransportError, match="closed by peer")
            expect_peer_requests(chaos_shim, 1)  # NO duplicate execution
            # E6: the deliberate manual retry succeeds on a fresh connection
            set_mode("ok")
            expect_ok(run_session_call(sess, "host.info"))
        finally:
            sess.close()
        expect_peer_requests(chaos_shim, 2)  # 1 failed attempt + 1 manual retry
        expect_connects(chaos_shim, 2, 2)

    def test_read_phase_bounded(self, chaos_shim, set_mode, fake_target):
        set_mode("disconnect_on_read", fail_first_n=1, exit_code=0)
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, watchdog_s=20)
            expect_error(out, rpc.RpcTransportError, match="closed by peer")
            expect_bounded(out, 0.0, 15.0)  # E5: no hang
        finally:
            sess.close()

    def test_idle_hold_self_heals(self, chaos_shim, set_mode, fake_target):
        """E6: the peer dies while the session idles; the next call transparently
        reopens and succeeds."""
        set_mode("exit_after_n", n=1, exit_code=0)
        sess = rpc.Session(fake_target)
        try:
            expect_ok(run_session_call(sess, "host.info"))
            # wait until the peer's death is observable (bounded, deterministic outcome)
            deadline = time.monotonic() + 10
            while sess._proc is not None and sess._proc.poll() is None:
                assert time.monotonic() < deadline, "shim did not exit after n requests"
                time.sleep(0.05)
            expect_ok(run_session_call(sess, "host.capabilities"))
        finally:
            sess.close()
        expect_peer_requests(chaos_shim, 2)
        expect_connects(chaos_shim, 2, 2)


# ---------- D8 延迟 (delay) ----------

class TestDelay:
    def test_within_deadline_one_shot(self, chaos_shim, set_mode, fake_target):
        set_mode("delay", delay_s=0.6)
        out = run_one_shot(fake_target, transport_timeout_s=10, watchdog_s=20)
        expect_ok(out)
        expect_bounded(out, 0.3, 15.0)  # slow but successful

    def test_within_deadline_session(self, chaos_shim, set_mode, fake_target):
        set_mode("delay", delay_s=0.4)
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, timeout_ms=30_000, watchdog_s=20)
            expect_ok(out)
            expect_bounded(out, 0.2, 30.0)
        finally:
            sess.close()

    def test_over_deadline_one_shot(self, chaos_shim, set_mode, fake_target):
        """E2 real-time: a 4s response against a 1s transport deadline dies
        inside the deadline envelope — never the 600s default, never a hang."""
        set_mode("delay", delay_s=4.0)
        out = run_one_shot(fake_target, transport_timeout_s=1, watchdog_s=20)
        expect_error(out, rpc.RpcTransportError, match="timed out")
        expect_bounded(out, 0.8, 10.0)

    @pytest.mark.timeout(120)
    def test_over_deadline_session_realtime(self, chaos_shim, set_mode, fake_target):
        """E2/E5 on the Popen link with REAL wall-clock: peer spins forever,
        the session read deadline (timeoutMs + 30s margin) must reap it and
        drop the session; E6 afterwards. This is THE slow cell (~31s)."""
        set_mode("spin")
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, timeout_ms=1_000, watchdog_s=70)
            expect_error(out, rpc.RpcTransportError, match="read timed out")
            expect_bounded(out, 29.0, 45.0)
            assert sess._proc is None  # the wedged ssh was discarded
            set_mode("ok")
            expect_ok(run_session_call(sess, timeout_ms=30_000, watchdog_s=20))
        finally:
            sess.close()


# ---------- D2 截断帧 (truncated frame) ----------

class TestTruncatedFrame:
    def test_one_shot(self, chaos_shim, set_mode, fake_target):
        set_mode("truncate")
        out = run_one_shot(fake_target, watchdog_s=20)
        expect_error(out, rpc.RpcProtocolError)  # protocol class, NOT transport

    def test_post_write_session_then_heal(self, chaos_shim, set_mode, fake_target):
        set_mode("truncate", fail_first_n=1)
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, watchdog_s=20)
            expect_error(out, rpc.RpcProtocolError)
            expect_bounded(out, 0.0, 15.0)  # E5
            expect_ok(run_session_call(sess))  # E6: dead proc reopened
        finally:
            sess.close()


# ---------- D3 垃圾字节 (garbage / BOM / banner) ----------

class TestGarbageBytes:
    def test_one_shot_garbage(self, chaos_shim, set_mode, fake_target):
        set_mode("garbage")
        out = run_one_shot(fake_target, watchdog_s=20)
        expect_error(out, rpc.RpcProtocolError)

    def test_one_shot_bom_tolerated(self, chaos_shim, set_mode, fake_target):
        set_mode("bom_ok")
        expect_ok(run_one_shot(fake_target, watchdog_s=20))

    def test_one_shot_banner_noise_tolerated(self, chaos_shim, set_mode, fake_target):
        """Multi-line stdout with banner noise: the multi-candidate parse finds
        the JSON line (documented one-shot tolerance)."""
        set_mode("banner_then_ok")
        expect_ok(run_one_shot(fake_target, watchdog_s=20))

    def test_session_garbage_line_protocol_error_session_survives(
            self, chaos_shim, set_mode, fake_target):
        """A garbage frame costs exactly one call (E1 protocol); the session
        itself stays aligned and the next call succeeds on the same connection."""
        set_mode("garbage")
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, watchdog_s=20)
            expect_error(out, rpc.RpcProtocolError)
            call = expect_ok(run_session_call(sess, "host.capabilities"))
            assert call.response.get("id") == call.request.get("id")
        finally:
            sess.close()
        expect_peer_requests(chaos_shim, 2)
        expect_connects(chaos_shim, 1, 1)  # no reconnect needed


# ---------- 重复帧 (duplicate frame) — frame alignment ----------

class TestDuplicateFrame:
    def test_one_shot_duplicate_tolerated(self, chaos_shim, set_mode, fake_target):
        set_mode("duplicate")
        expect_ok(run_one_shot(fake_target, watchdog_s=20))

    def test_session_duplicate_must_not_misalign(self, chaos_shim, set_mode, fake_target):
        """THE frame-alignment cell: a duplicated response frame must never be
        mistaken for the NEXT call's response. The duplicate is provably stale
        (its id belongs to request 1), so call 2 must return request 2's own
        frame (id match + ordinal 2)."""
        set_mode("duplicate")
        sess = rpc.Session(fake_target)
        try:
            call1 = expect_ok(run_session_call(sess, "host.info"))
            assert call1.response.get("id") == call1.request.get("id")
            call2 = expect_ok(run_session_call(sess, "host.capabilities"))
            assert call2.response.get("id") == call2.request.get("id"), (
                f"frame misalignment: call2 got a stale frame with id "
                f"{call2.response.get('id')!r} (expected {call2.request.get('id')!r})")
            assert call2.data.get("ordinal") == 2
            # and the connection stayed usable afterwards
            call3 = expect_ok(run_session_call(sess, "host.info"))
            assert call3.response.get("id") == call3.request.get("id")
        finally:
            sess.close()
        expect_peer_requests(chaos_shim, 3)
        expect_connects(chaos_shim, 1, 1)  # staleness absorbed, no reconnect


# ---------- slow drip ----------

class TestSlowDrip:
    def test_within_deadline_one_shot(self, chaos_shim, set_mode, fake_target):
        set_mode("slow_drip", chunk_bytes=4, chunk_delay_s=0.02)
        out = run_one_shot(fake_target, transport_timeout_s=10, watchdog_s=20)
        expect_ok(out)
        expect_bounded(out, 0.3, 15.0)

    def test_past_deadline_one_shot(self, chaos_shim, set_mode, fake_target):
        """E2+E1: a byte-at-a-time drip that would need ~6.5s dies against the
        2s transport deadline, inside the envelope."""
        set_mode("slow_drip", chunk_bytes=1, chunk_delay_s=0.05)
        out = run_one_shot(fake_target, transport_timeout_s=2, watchdog_s=20)
        expect_error(out, rpc.RpcTransportError, match="timed out")
        expect_bounded(out, 1.5, 10.0)


# ---------- D9 提前 EOF (early EOF) ----------

class TestEarlyEof:
    def test_one_shot_eof_before_response(self, chaos_shim, set_mode, fake_target):
        set_mode("disconnect_on_read", exit_code=0)  # clean EOF, no response
        out = run_one_shot(fake_target, watchdog_s=20)
        expect_error(out, rpc.RpcProtocolError)  # empty stdout -> protocol class

    def test_session_eof_post_write(self, chaos_shim, set_mode, fake_target):
        set_mode("disconnect_on_read", exit_code=0)
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, watchdog_s=20)
            expect_error(out, rpc.RpcTransportError, match="closed by peer")
            expect_peer_requests(chaos_shim, 1)  # E4: no auto-resend
        finally:
            sess.close()


# ---------- D12 stderr 洪泛 (stderr flood, stdout fine) ----------

class TestStderrFlood:
    def test_one_shot_flood_tolerated(self, chaos_shim, set_mode, fake_target):
        """subprocess.run drains both pipes concurrently: even a 256 KiB flood
        cannot block the exchange."""
        set_mode("stderr_flood", flood_kb=256)
        out = run_one_shot(fake_target, transport_timeout_s=30, watchdog_s=40)
        expect_ok(out)
        expect_payload_never_in_argv(chaos_shim)

    def test_session_small_flood(self, chaos_shim, set_mode, fake_target):
        set_mode("stderr_flood", flood_kb=4)
        sess = rpc.Session(fake_target)
        try:
            expect_ok(run_session_call(sess, watchdog_s=20))
        finally:
            sess.close()

    @pytest.mark.timeout(120)
    def test_session_large_flood_must_not_deadlock(self, chaos_shim, set_mode, fake_target):
        """A >pipe-buffer stderr flood mid-call: the peer blocks writing stderr
        unless SOMETHING drains it. The call must still succeed well inside the
        31s read deadline — an undrained stderr would wedge the peer until the
        deadline killed it."""
        set_mode("stderr_flood", flood_kb=256)
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, timeout_ms=1_000, watchdog_s=60)
            expect_ok(out)
            assert out.elapsed_s < 25.0, (
                f"took {out.elapsed_s:.1f}s — stderr starvation (peer blocked on a "
                "full pipe until the read deadline reaped it)")
            expect_ok(run_session_call(sess, timeout_ms=1_000, watchdog_s=60))
        finally:
            sess.close()


# ---------- D11 磁盘满 / structured peer error passthrough ----------

class TestDiskFull:
    def test_one_shot_error_class_passthrough(self, chaos_shim, set_mode, fake_target):
        set_mode("disk_full")
        out = run_one_shot(fake_target, watchdog_s=20)
        assert out.returned and out.error is None
        assert out.call.ok is False
        assert out.call.error_class == "disk-full"
        assert "No space left" in out.call.message

    def test_session_error_class_passthrough(self, chaos_shim, set_mode, fake_target):
        set_mode("disk_full")
        sess = rpc.Session(fake_target)
        try:
            out = run_session_call(sess, "file.writeText",
                                   {"path": "C:/x", "text": "y"}, watchdog_s=20)
            assert out.returned and out.error is None
            assert out.call.ok is False and out.call.error_class == "disk-full"
        finally:
            sess.close()


# ---------- 重连风暴 (reconnect storm) ----------

class TestReconnectStorm:
    def test_read_fail_storm_is_linear(self, chaos_shim, set_mode, fake_target):
        """N consecutive calls against an always-dying peer: exactly N connects
        (one per call), N peer requests (no auto-resend), N structured errors,
        bounded total time — never exponential, never silent."""
        set_mode("storm_read_fail")
        sess = rpc.Session(fake_target)
        n = 5
        t0 = time.monotonic()
        try:
            for _ in range(n):
                out = run_session_call(sess, watchdog_s=20)
                expect_error(out, rpc.RpcTransportError, match="closed by peer")
        finally:
            sess.close()
        elapsed = time.monotonic() - t0
        expect_connects(chaos_shim, n, n)      # linear bound
        expect_peer_requests(chaos_shim, n)    # no duplication under failure
        assert elapsed < 30.0

    def test_write_fail_storm_is_linear_seam2(self, fake_target, popen_patcher):
        """Write-phase storm: every write breaks. Call 1 = attempt + exactly 1
        reconnect (2 spawns); each later call fails on the half-dead proc and
        reconnects once more (+1 spawn). 4 calls -> 5 spawns, zero delivered
        writes — linear, never a retry loop."""
        factory = popen_patcher([{"broken_writes": 100} for _ in range(5)])
        sess = rpc.Session(fake_target)
        try:
            for _ in range(4):
                with pytest.raises(rpc.RpcTransportWriteError):
                    sess.call("host.info")
        finally:
            sess.close()
        assert 4 <= len(factory.spawned) <= 2 * 4  # linear bound
        assert sum(len(p.stdin.writes) for p in factory.spawned) == 0
