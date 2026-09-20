# Plan: Thiết lập Chế độ Tự chủ Toàn phần (Autonomous Mode) cho SCP Task Kernel

## 1. Survey Phase (Phase 0)
- Dispatch 3 exploratory agents:
  1. `teamwork_preview_explorer` (Survey 1): Map Task Kernel state machine (`scp/ask_kernel_adapter.py`, `scp/task_kernel.py` or equivalent, state enums, transitions, `HUMAN_REVIEW` trigger conditions, transition guards).
  2. `teamwork_preview_explorer` (Survey 2): Map Capability Authority (`capability_authority.py`, Unified Broker, token issuance/verification, PEP/PDP, permissions like `network`, `fs_write`, `execution`).
  3. `teamwork_preview_spec_miner` (Survey 3): Extract SCP invariants, Zero-Trust requirements, FA-01 to FA-13 constraints, and existing tests/test runners (`tests/T03_capability`, `tests/T04_kernel`, `tools/t00_meta_audit.py`).
- Synthesize findings into `PROJECT.md` at project root with Feature Inventory, Milestone Decomposition, Interface Contracts, and Code Layout.

## 2. Milestone Execution Phase (Phase 1)
- Decompose according to survey findings:
  - Milestone 1: Autonomous State Machine (R1) - Configure or extend Task Kernel / Adapter to allow autonomous progression past HUMAN_REVIEW when automated verification succeeds without violating fail-closed state invariants.
  - Milestone 2: Autonomous Capability Token Minting/Granting (R2) - Secure autonomous capability grant authority following Zero-Trust (ensuring proper cryptographic or authority boundaries, NOT self-granting in worker, adhering to FA-05).
  - Milestone 3: Integration Test & Full Verification (R3) - Integration test demonstrating full lifecycle without manual HUMAN_REVIEW block, full `pytest tests/` pass, and `python tools/t00_meta_audit.py` with 0 regressions.

## 3. Verification & Gating Phase (Phase 2)
- For each milestone: Explorer -> Worker -> Reviewer(s) -> Challenger(s) -> Forensic Auditor.
- Binary veto on any integrity violation.

## 4. Final Handoff & Completion
- Full terminal evidence of integration test, pytest, and t00_meta_audit.py.
- Hand off to Sentinel via `send_message`.
