# Project: Free API Adapter Bridge & 60 Data Sources Activation

## Architecture
- **Tier 0: Local Knowledge Cache**: `data/knowledge.sqlite3` (`knowledge_records` table, SQLite WAL mode, 0.299ms indexed seek, beating <1.5ms SLA).
- **Tier 1: Dynamic Free API Adapter Bridge**: `scp/data_sources/free_api_bridge.py` (`FreeAPIAdapterBridge`).
  - Catalog: `data/free_api_catalog.json` (>2,120 APIs, 51 categories, 1,010 `auth="No"` prioritized).
  - Domain mapping: 60 domains mapped to corresponding categories.
  - Smart HTTP extraction: `safe_urlopen`, Content-Type inspection, readable text extraction preserving keys and concise values, term relevance gate (`_terms_covered`).
  - Provenance tracking: `api_name`, `api_url`, `timestamp`, `confidence`, `epistemic_tier`.
- **Tier 2: Safe Multi-Source Fallback**:
  - `LiveKnowledgeFetcher.fetch_live()`: Multi-source consensus (Wikipedia, Wikidata, DuckDuckGo, arXiv) with cross-checking.
  - Canonical `wikipedia_client.search_then_summary()`: Language regex validation, rate-limited, SSRF safe.
  - Fail-closed: returns `None` / `found=False` without fabricating fake data (FA-04).
- **Data Source Layer**: 60 Data Source classes in `scp/data_sources/` + `ConversionDataSource` + `WeatherDataSource`.
  - Wire `fetch(intent, entity, **kwargs)` and `query(query_str)` to `FreeAPIAdapterBridge`.
  - Maintain 100% Tabula Rasa: 0 static truth dictionaries in Python code.
  - Dual-Contract Preservation: maintain `deprecated = True` and init warning for backward compatibility with 67/67 M3 deprecation tests, while adding `bridge_connected = True` and functional routing.
  - `health_check()`: catalog readiness + live connectivity, fail-closed on egress deny.
- **Orchestration Layer**: `QuestionRouter` (`scp/runtime/question_router.py`).
  - In `attempt_lookup_fork`: Tier 0 WAL cache (<1.5ms) -> Tier 1 `FreeAPIAdapterBridge` -> Tier 2 Fallback -> LLM generation only when all 1,000+ APIs have no answer.
- **Observability Layer**: `reports/live_system_topology.html`.
  - Update 60 Data Sources from `🟡 UNCONNECTED` to `🟢 RUNNING`.
- **Integrity & Verification**: `tests/T02_contract/test_free_api_bridge.py`, `scripts/run_reality_tests_portable.py`, `tools/t00_meta_audit.py`, `tools/scan_secrets.py`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Free API Catalog Mapping | Map 60 domains to 51 categories in `data/free_api_catalog.json` prioritizing 1,010 auth="No" APIs | M1 | ORIGINAL_REQUEST § R1 |
| 2 | FreeAPIAdapterBridge Core | Dynamic API bridge with safe HTTP fetching, content-type inspection, text extraction, term relevance gate | M1 | ORIGINAL_REQUEST § R1 |
| 3 | Safe Multi-Tier Fallback | Fallback to `LiveKnowledgeFetcher` / Wikipedia RAG / InternetSearch, fail-closed without simulated data (FA-04) | M1 | ORIGINAL_REQUEST § R1 |
| 4 | Provenance Generation | Provenance metadata attachment: URL, timestamp, confidence, api_name, epistemic_tier | M1 | ORIGINAL_REQUEST § R1 |
| 5 | Activate 60 Data Source Classes | Wire `fetch(intent, entity, **kwargs)` and `query(query_str)` to `FreeAPIAdapterBridge` across all 60 classes | M2 | ORIGINAL_REQUEST § R2 |
| 6 | 100% Tabula Rasa Preservation | Ensure 0 static truth dictionaries in Python code across all 60 classes | M2 | ORIGINAL_REQUEST § R2 |
| 7 | Health Check & Egress Invariants | Implement `health_check()` verifying catalog readiness & safe connectivity, failing-closed when egress denied | M2 | ORIGINAL_REQUEST § R2 |
| 8 | QuestionRouter Tiered Routing | Prioritize Cache (<1.5ms) -> FreeAPIAdapterBridge -> Fallback -> LLM only when all APIs have no answer | M3 | ORIGINAL_REQUEST § R3 |
| 9 | SQLite WAL Caching | Store verified answers into `data/knowledge.sqlite3` (`knowledge_records`) with <1.5ms latency | M3 | ORIGINAL_REQUEST § R3 |
| 10| Live System Topology Update | Update `reports/live_system_topology.html` (KPI card, Tab 3, Tab 5 matrix) from `🟡 UNCONNECTED` to `🟢 RUNNING` | M3 | ORIGINAL_REQUEST § R3 |
| 11| Contract Test Suite | `tests/T02_contract/test_free_api_bridge.py` testing domain mappings, cache latency, Tabula Rasa, Anti-Placebo negative tests | M4 | ORIGINAL_REQUEST § R4 |
| 12| Regression & Reality Verification | Maintain 67/67 deprecation tests, 76/76 portable reality tests, 0 T00 regressions, 0 secrets, 4 services 24/7 | M5 | ORIGINAL_REQUEST § R4, R5 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | FreeAPIAdapterBridge Core | `scp/data_sources/free_api_bridge.py`: catalog mapping, HTTP fetching, extraction, provenance, fallback | none | DONE |
| M2 | Activate 60 Data Sources | `scp/data_sources/*.py`: wire `fetch()`, `query()`, `health_check()` to bridge; Dual-Contract deprecation preservation; Tabula Rasa | M1 | IN_PROGRESS |
| M3 | QuestionRouter & Cache & Topology | `scp/runtime/question_router.py`: Tier 0 WAL cache (<1.5ms), Tier 1 Bridge; `reports/live_system_topology.html`: 60 Data Sources -> RUNNING | M1, M2 | PLANNED |
| M4 | Contract Testing & Anti-Placebo Suite | `tests/T02_contract/test_free_api_bridge.py`: 60 domain tests, negative tests (timeout, fake URL, SSRF), cache benchmark | M1, M2, M3 | PLANNED |
| M5 | Full Verification, Reality Tests & Forensic Audit | Run 76/76 reality tests, 67/67 deprecation tests, T00 meta-audit, secret scanner, forensic audit | M1, M2, M3, M4 | PLANNED |

## Interface Contracts
### FreeAPIAdapterBridge ↔ Data Sources / QuestionRouter
- `FreeAPIAdapterBridge.fetch_for_domain(domain: str, intent: str, entity: str, question: str = "") -> dict[str, Any] | None`
  - Returns dict with keys: `found: bool`, `value: str`, `source: str`, `api_name: str`, `api_url: str`, `confidence: float`, `timestamp: str`, `epistemic_tier: str`.
  - Returns `None` if no data found across APIs and fallback fetchers.
- `FreeAPIAdapterBridge.query_domain(domain: str, question_str: str) -> dict[str, Any]`
  - Standardized dict: `{"found": bool, "answer": str | None, "confidence": float, "source": str, "provenance": dict}`.
- `FreeAPIAdapterBridge.health_check() -> bool`
  - Returns `True` if catalog is loaded and network/egress allows connection, `False` otherwise (fail-closed).

### 60 Data Sources Interface
- Each class in `scp/data_sources/`:
  - `fetch(intent: str, entity: str, **kwargs) -> dict[str, Any] | None`
  - `query(question: str) -> dict[str, Any]`
  - `health_check() -> bool`
  - `deprecated: bool = True` (preserved for M3 contract backward compatibility)
  - `bridge_connected: bool = True` (indicates live bridge activation)

### QuestionRouter ↔ SQLite WAL Cache
- Tier 0 lookup: `data/knowledge.sqlite3` -> `knowledge_records` queried by normalized question.
- Indexed seek (0.299ms measured vs <1.5ms SLA). If found and not expired, returns cached answer immediately.
- If cache miss: queries `FreeAPIAdapterBridge`. On success, stores record to `knowledge_records` before returning.

## Code Layout
- `scp/data_sources/free_api_bridge.py`: Core adapter bridge implementation.
- `scp/data_sources/`: 60 Data Source classes.
- `scp/runtime/question_router.py`: Routing logic and cache integration.
- `data/knowledge.sqlite3`: WAL-mode SQLite cache.
- `reports/live_system_topology.html`: Live topology status.
- `tests/T02_contract/test_free_api_bridge.py`: Contract and anti-placebo test suite.
