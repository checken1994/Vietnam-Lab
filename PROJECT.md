# Project: AutoFix Autonomy, Iterative Refinement & Architectural Cleanup

## Architecture
- **AutoFix Subsystem**: `scp/autofix/` (`subsystem_reviewer.py`, `llm_fix.py`, `llm_fix_parts/`, `global_symbol_index.py`, `shadow_snapshot.py`, `post_fix_verify.py`)
- **Core Evolution & Quarantine**: `scp/core/code_evolution_agent.py`, `pending_fixes/`
- **Data Sources & Catalog**: `scp/data_sources/`, `data/free_api_catalog.json`, `scp/runtime/experts/`, `scp/runtime/domain_data_fork.py`
- **Live Search & Knowledge**: `scp/web_control/internet_search.py`, `scp/core/wikipedia_client.py`, `scp/data_sources/live_knowledge.py`
- **24/7 Services & Reporting**: Ports 8000, 3030, 8081, 3000; `tools/generate_visual_reports.py`, `scripts/ops/generate_visual_reports_schedule.ps1`
- **Integrity & Governance**: `tools/t00_meta_audit.py`, `.agents/AGENTS.md` (FA-01 to FA-13), `spec/protected_invariants.yaml`

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---|---|---|---|
| 1 | R1. AutoFix Autonomy in FULL_PRODUCTION | Implement `SubsystemIndependentReviewer` in `scp/autofix/subsystem_reviewer.py` to auto-review, shadow-test, and auto-merge safe patches without human intervention; deduplicate & prune `pending_fixes/` queue. | M2 | ORIGINAL_REQUEST § R1 |
| 2 | R2. Multi-Turn Iterative Refinement Loop | Connect WHY-Gate and DriftGuard critique feedback into 3-round iterative prompt loop in AutoFix; maintain 9/9 tests in `test_autofix_iterative_refinement.py`. | M2 | ORIGINAL_REQUEST § R2 |
| 3 | R3. Global AST Symbol Index & Provenance | Maintain `data/global_symbol_index.json` and Symbol Provenance Injection in prompt construction to prevent hallucinated deletions of dynamic symbols (`_validate_openrouter_base_url`, `_SIMPLE_BUG_TYPES`). | M2 | ORIGINAL_REQUEST § R3 |
| 4 | R4. Architectural Deprecation (61 Data Sources & 26 Feeds) | Deprecate 61 empty Data Source classes in `scp/data_sources/` and 26 dead news feeds in `data/free_api_catalog.json`; retain `ConversionDataSource`; route experts to `LiveKnowledgeFetcher` / `InternetSearch`; preserve 100% test NodeIDs without placebo assertions; keep T00 Meta-Audit 0 regressions. | M3 | ORIGINAL_REQUEST § R4 |
| 5 | R5. 24/7 Services & Hourly Reporting | Verify all 4 core services (Ports 8000, 3030, 8081, 3000) are healthy and listening; verify 3 Windows tasks; verify hourly visual reporting to GitHub `main`; preserve 100% Tabula Rasa. | M4 | ORIGINAL_REQUEST § R5 |
| 6 | System Verification & Forensic Integrity Audit | Run full regression across affected test suites; execute independent forensic audit verifying zero cheating, zero placebo assertions, and database/hardware boundaries. | M5 | Acceptance Criteria § 1-4 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| M1 | Survey & Architectural Assessment | Full survey of pending_fixes, AutoFix, symbol index, data sources, 24/7 services | none | DONE |
| M2 | AutoFix Autonomy & Refinement Loop | Build SubsystemIndependentReviewer, integrate with AutoFixEngine, prune pending_fixes, connect 3-round refinement loop and symbol provenance injection | M1 | DONE |
| M3 | Architectural Deprecation | Controlled deprecation of 61 data sources and 26 news feeds, rewire experts, preserve test nodeids, maintain T00 0 regressions | M2 | IN_PROGRESS |
| M4 | 24/7 Services & Live Reporting | Verify live ports, verify hourly reporting pipeline, verify Tabula Rasa and WAL persistence | M3 | PLANNED |
| M5 | Final E2E Reality & Forensic Audit | Full pytest suite on affected targets, T00 meta-audit, forensic integrity audit, Sentinel handoff | M4 | PLANNED |

## Interface Contracts
### SubsystemIndependentReviewer ↔ AutoFixEngine
- `SubsystemIndependentReviewer.review_pending_fixes(pending_dir: Path, capability_level: CapabilityLevel) -> ReviewSummary`
- Under `CapabilityLevel.FULL_PRODUCTION`, reviewer extracts candidate patches, tests them via `shadow_apply_and_compare`, verifies safety against DriftGuard and WHY-Gate, and applies safe patches or triggers iterative refinement loop.
- `_queue_pending_fix` must check deduplication key (e.g. hash of file + target diff) to prevent accumulating 900+ redundant files.

### GlobalSymbolIndex ↔ AutoFix Prompts
- `GlobalSymbolIndex.build_provenance_prompt_section(file_path: str, bug: Any) -> str`
- When bug type is `PossiblyUndefinedName` (F821) and symbol matches dynamic wiring, returns explicit instructions with source module and non-deletion constraint.

### Data Sources ↔ LiveKnowledgeFetcher / InternetSearch
- Domain experts in `scp/runtime/experts/` must query `LiveKnowledgeFetcher` (Wikipedia RAG) or `InternetSearch` instead of calling deprecated static data source dictionaries.
- Deprecated classes in `scp/data_sources/` provide graceful deprecation warnings and route to live knowledge without breaking existing imports.

## Code Layout
- `scp/autofix/`: AutoFix engine, subsystem reviewer, AST symbol index, shadow snapshot.
- `scp/core/code_evolution_agent.py`: Evolution loop and pending fix queueing.
- `pending_fixes/`: Queue directory for patches under review.
- `scp/data_sources/`: Data sources (ConversionDataSource retained; empty sources deprecated).
- `data/free_api_catalog.json`: API catalog (dead news feeds removed/marked deprecated).
- `tests/T07_learning/`: AutoFix and symbol index tests.
- `tools/t00_meta_audit.py`: Mandatory pre-commit integrity gate.
