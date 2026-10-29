"""[§15 follow-up / R7 tightening 2026-10-29] Thread-local _system_authority.

R7 CHECKER finding being pinned here: recover_on_boot() used to flip
``kernel._system_authority`` as an INSTANCE attribute, so every concurrent
caller on the SAME kernel object was silently waived from the lease
requirement during the recovery window. Probe 2026-10-29 (before fix):
thread B holding no lease transitioned RUNNING->HUMAN_REVIEW successfully
while thread A held the flag.

Contract (real TaskKernel on real SQLite; FA-04 zero-mock — no
mock/stub/patch/skip):
1. Waive là per-caller (ContextVar qua property _system_authority): thread A
   khai báo system-authority đúng cách recover_on_boot bật thì thread B trên
   CÙNG kernel object vẫn bị StaleLease khi transition trạng thái leased mà
   không có lease (A không lây sang B).
2. Positive 0%-rollback: thread A khai báo vẫn được waive — boot-recovery edge
   RUNNING->HUMAN_REVIEW chạy được trong scope của A.
3. Hai check R7 KHÔNG bị suy yếu dưới concurrency (vẫn TRƯỚC nhánh lease):
   a) kill-check theo loại cạnh: dưới global kill, resume edge
      CHECKPOINTED->QUEUED bị chặn cho CẢ thread B thường LẪN thread A đang
      giữ system-authority (KillSwitchActive).
   b) edge-class authority gate: RECOVERING->QUEUED (controlled->dispatch)
      qua transition() generic bị chặn với thông điệp gate đúng, kể cả khi
      thread khác đang giữ system-authority (InvalidTransition — không phải
      StaleLease, chứng tỏ gate chạy trước nhánh lease).
4. recover_on_boot thật: waive chỉ active trong lúc pass (recovered != []
   trên instance KHÔNG giữ lease nào — chỉ có thể với waive), tắt sạch sau
   pass (không residual trên caller), và baseline lease-lane của thread khác
   sau pass vẫn nguyên vẹn.
"""
from __future__ import annotations

import threading
from pathlib import Path

from scp.task_kernel import (
    InvalidTransition,
    KillSwitchActive,
    StaleLease,
    TaskKernel,
)
from scp.task_kernel_parts.definitions import current_system_authority

_TIMEOUT = 15.0


def _setup_running(kernel: TaskKernel, task_id: str, owner: str = "tl-owner"):
    """Fresh task driven to RUNNING with a live lease (legal intake path)."""
    kernel.create_task(task_id, owner, "tl thread-local probe", "R1")
    for state in ("PLANNING", "READY", "QUEUED"):
        kernel.transition(task_id, state, actor="setup")
    lease = kernel.claim(task_id, "worker-setup", ttl_seconds=300)
    kernel.start(task_id, lease.lease_id)
    return lease


def _run_pair(thread_a_body, thread_b_body) -> tuple[dict, dict]:
    """Run A/B concurrently; A signals readiness, B probes, both join."""
    a_ready = threading.Event()
    b_done = threading.Event()
    state: dict = {"a": {}, "b": {}}

    def a_wrapper():
        try:
            thread_a_body(a_ready, state["a"])
        except BaseException as exc:  # noqa: BLE001 - captured & asserted below
            state["a"]["unhandled"] = exc
        finally:
            a_ready.set()

    def b_wrapper():
        if not a_ready.wait(_TIMEOUT):
            state["b"]["unhandled"] = RuntimeError("thread A never became ready")
            b_done.set()
            return
        try:
            thread_b_body(state["b"])
        except BaseException as exc:  # noqa: BLE001 - captured & asserted below
            state["b"]["unhandled"] = exc
        finally:
            b_done.set()

    ta = threading.Thread(target=a_wrapper, name="tl-scope-a")
    tb = threading.Thread(target=b_wrapper, name="tl-scope-b")
    ta.start()
    tb.start()
    ta.join(_TIMEOUT * 2)
    tb.join(_TIMEOUT * 2)
    assert not ta.is_alive() and not tb.is_alive(), "worker threads must terminate"
    return state["a"], state["b"]


# ---------------------------------------------------------------------------
# (1) + (2) Waive là per-caller; declaring thread giữ nguyên quyền
# ---------------------------------------------------------------------------

def test_system_authority_waive_does_not_leak_across_threads(tmp_path: Path):
    db = tmp_path / "tl-scope.sqlite3"
    setup = TaskKernel(db)
    _setup_running(setup, "tl-a")
    _setup_running(setup, "tl-b")
    setup.close()

    kernel = TaskKernel(db)  # shared by both threads; holds NO bound leases
    try:
        b_done_holder: dict = {"event": threading.Event()}

        def thread_a(a_ready: threading.Event, out: dict) -> None:
            # Bật system-authority đúng cách recover_on_boot đã bật (trước
            # đây là instance attribute — giờ property ghi ContextVar của
            # riêng thread này). Thread A tự hoàn tất boot-recovery edge của
            # mình trước khi báo hiệu.
            kernel._system_authority = True
            try:
                kernel.transition(
                    "tl-a", "HUMAN_REVIEW",
                    actor="boot_recovery", reason="boot_recovery_in_flight",
                )
                out["a_positive"] = "ok"
                a_ready.set()
                if not b_done_holder["event"].wait(_TIMEOUT):
                    out["a_wait_b"] = "timeout"
            finally:
                kernel._system_authority = False

        def thread_b(out: dict) -> None:
            try:
                kernel.transition(
                    "tl-b", "HUMAN_REVIEW",
                    actor="worker-b", reason="no_lease_no_authority",
                )
                out["b_outcome"] = "ok"
            except BaseException as exc:  # noqa: BLE001 - outcome recorded by type
                out["b_outcome"] = type(exc).__name__
                out["b_exc"] = exc
            finally:
                b_done_holder["event"].set()

        a_out, b_out = _run_pair(thread_a, thread_b)

        assert "unhandled" not in a_out, f"declaring thread crashed: {a_out.get('unhandled')!r}"
        assert a_out.get("a_positive") == "ok", f"declaring thread must keep the waive: {a_out}"
        assert "unhandled" not in b_out, f"probing thread crashed: {b_out.get('unhandled')!r}"
        assert b_out.get("b_outcome") == "StaleLease", (
            f"concurrent caller inherited the waive: outcome={b_out.get('b_outcome')!r} "
            f"exc={b_out.get('b_exc')!r}"
        )
        assert kernel.get_task("tl-a")["state"] == "HUMAN_REVIEW"
        assert kernel.get_task("tl-b")["state"] == "RUNNING", (
            "task must stay RUNNING when a lease-less concurrent caller is refused"
        )
    finally:
        kernel.close()


# ---------------------------------------------------------------------------
# (3) Kill-check + edge-class gate vẫn TRƯỚC nhánh lease dưới concurrency
# ---------------------------------------------------------------------------

def test_kill_and_edge_gates_hold_for_concurrent_callers(tmp_path: Path):
    db = tmp_path / "tl-gates.sqlite3"
    setup = TaskKernel(db)
    lease_ka = _setup_running(setup, "tl-ka")
    setup.transition("tl-ka", "CHECKPOINTED", lease_id=lease_ka.lease_id)
    lease_kb = _setup_running(setup, "tl-kb")
    setup.transition("tl-kb", "CHECKPOINTED", lease_id=lease_kb.lease_id)
    _setup_running(setup, "tl-ea")
    setup.transition("tl-ea", "RECOVERING")
    _setup_running(setup, "tl-eb")
    setup.transition("tl-eb", "RECOVERING")
    setup.close()

    kernel = TaskKernel(db)  # shared; no bound leases
    try:
        kernel.set_global_kill(True)
        b_done_kill = threading.Event()

        def thread_a_kill(a_ready: threading.Event, out: dict) -> None:
            kernel._system_authority = True
            try:
                # Authority holder probing a RESUME edge under global kill:
                # phải fail-closed như mọi caller khác (R7-1 không miễn).
                kernel.transition("tl-ka", "QUEUED", actor="boot_recovery", reason="resume")
                out["a_kill"] = "ok"
            except BaseException as exc:  # noqa: BLE001
                out["a_kill"] = type(exc).__name__
                out["a_kill_exc"] = exc
            finally:
                kernel._system_authority = False
                a_ready.set()
                b_done_kill.wait(_TIMEOUT)

        def thread_b_kill(out: dict) -> None:
            try:
                kernel.transition("tl-kb", "QUEUED", actor="worker-b", reason="resume")
                out["b_kill"] = "ok"
            except BaseException as exc:  # noqa: BLE001
                out["b_kill"] = type(exc).__name__
                out["b_kill_exc"] = exc
            finally:
                b_done_kill.set()

        a_out, b_out = _run_pair(thread_a_kill, thread_b_kill)
        assert a_out.get("a_kill") == "KillSwitchActive", (
            f"system authority must not bypass the kill check: {a_out}"
        )
        assert b_out.get("b_kill") == "KillSwitchActive", (
            f"normal caller must stay blocked on resume edges under kill: {b_out}"
        )
        assert kernel.get_task("tl-ka")["state"] == "CHECKPOINTED"
        assert kernel.get_task("tl-kb")["state"] == "CHECKPOINTED"

        kernel.set_global_kill(False)

        # Edge-class gate (kill OFF so the edge gate is the FIRST blocker):
        # controlled->dispatch qua transition() generic bị chặn cho cả hai
        # thread, với thông điệp gate — không phải StaleLease — chứng tỏ
        # gate chạy trước nhánh lease và không bị yếu đi.
        b_done_edge = threading.Event()

        def thread_a_edge(a_ready: threading.Event, out: dict) -> None:
            kernel._system_authority = True
            try:
                kernel.transition("tl-ea", "QUEUED", actor="boot_recovery")
                out["a_edge"] = "ok"
            except BaseException as exc:  # noqa: BLE001
                out["a_edge"] = type(exc).__name__
                out["a_edge_exc"] = exc
            finally:
                kernel._system_authority = False
                a_ready.set()
                b_done_edge.wait(_TIMEOUT)

        def thread_b_edge(out: dict) -> None:
            try:
                kernel.transition("tl-eb", "QUEUED", actor="worker-b")
                out["b_edge"] = "ok"
            except BaseException as exc:  # noqa: BLE001
                out["b_edge"] = type(exc).__name__
                out["b_edge_exc"] = exc
            finally:
                b_done_edge.set()

        a_out2, b_out2 = _run_pair(thread_a_edge, thread_b_edge)
        assert a_out2.get("a_edge") == "InvalidTransition", a_out2
        assert "controlled-state dispatch edge" in str(a_out2.get("a_edge_exc"))
        assert b_out2.get("b_edge") == "InvalidTransition", (
            f"edge gate must fire before the lease branch for every caller: {b_out2}"
        )
        assert "controlled-state dispatch edge" in str(b_out2.get("b_edge_exc"))
        assert kernel.get_task("tl-ea")["state"] == "RECOVERING"
        assert kernel.get_task("tl-eb")["state"] == "RECOVERING"
    finally:
        kernel.set_global_kill(False)
        kernel.close()


# ---------------------------------------------------------------------------
# (4) recover_on_boot thật: waive scoped vào pass, không residual
# ---------------------------------------------------------------------------

def test_recover_on_boot_waive_is_scoped_to_the_pass(tmp_path: Path):
    db = tmp_path / "tl-boot.sqlite3"
    setup = TaskKernel(db)
    _setup_running(setup, "tl-boot-a")
    _setup_running(setup, "tl-boot-b")
    setup.close()

    kernel = TaskKernel(db)  # fresh instance: NO bound leases
    try:
        assert current_system_authority() is False
        report: dict = {}

        def run_boot() -> None:
            report["out"] = kernel.recover_on_boot()
            report["residual_boot_thread"] = current_system_authority()

        boot = threading.Thread(target=run_boot, name="tl-boot")
        boot.start()
        boot.join(_TIMEOUT)
        assert not boot.is_alive(), "recover_on_boot must terminate"

        out = report["out"]
        assert out["corrupted"] == []
        recovered = {entry["task_id"]: entry for entry in out["recovered"]}
        # RUNNING->HUMAN_REVIEW trên instance không giữ lease nào chỉ có thể
        # chạy khi waive boot-recovery đang active trong pass này.
        assert recovered["tl-boot-a"]["to"] == "HUMAN_REVIEW"
        assert recovered["tl-boot-b"]["to"] == "HUMAN_REVIEW"
        assert kernel.get_task("tl-boot-a")["state"] == "HUMAN_REVIEW"
        # Không residual: trên thread vừa boot và trên caller hiện tại.
        assert report["residual_boot_thread"] is False
        assert current_system_authority() is False
        assert kernel._system_authority is False

        # Baseline sau pass: thread khác vẫn phải có lease để chạm trạng thái
        # leased (lane lease không bị mở rộng bởi recovery).
        setup2 = TaskKernel(db)
        _setup_running(setup2, "tl-post")
        setup2.close()
        outcome: dict = {}

        def b_after() -> None:
            try:
                kernel.transition("tl-post", "HUMAN_REVIEW", actor="worker-b")
                outcome["b"] = "ok"
            except BaseException as exc:  # noqa: BLE001
                outcome["b"] = type(exc).__name__

        tb = threading.Thread(target=b_after, name="tl-post-b")
        tb.start()
        tb.join(_TIMEOUT)
        assert not tb.is_alive()
        assert outcome.get("b") == "StaleLease", outcome
        assert kernel.get_task("tl-post")["state"] == "RUNNING"
    finally:
        kernel.close()
