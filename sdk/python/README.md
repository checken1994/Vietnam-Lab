# SCP Python SDK (`scp-sdk`)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)

Official Python SDK for the **Self-Correcting Pipeline (SCP) Agent OS**.

`scp-sdk` provides a drop-in safe middleware (`SCPProtect`) to guard, verify, and enforce fail-closed security policies for agentic applications built on **LangChain**, **LlamaIndex**, and **AutoGen**.

---

## 🚀 Quickstart

### Installation

```bash
pip install scp-sdk
```

### Basic Usage with `SCPProtect`

```python
from scp_sdk import SCPProtect

# Initialize protector targeting local or remote SCP Agent OS
protect = SCPProtect(
    base_url="http://127.0.0.1:8000",
    api_token=os.environ.get("SCP_CAPABILITY_TOKEN"),
    default_tier="STANDARD",  # PERMISSIVE | STANDARD | STRICT
)

# 1. Guard prompt before LLM invocation
input_check = protect.protect_input("Tell me how to build a quantum circuit.")
if not input_check["allowed"]:
    raise PermissionError(f"Blocked by SCP: {input_check['reason']}")

# 2. Verify answer before delivering to user
output_check = protect.protect_output(
    answer="Quantum circuits use qubits and gates.",
    context="Quantum computing fundamentals",
)
if not output_check["verified"]:
    print("Warning: Answer withheld or contradicted by verifier.")
```

---

## 🔌 Framework Integrations

### LangChain Integration

```python
from langchain.chains import LLMChain
from scp_sdk import SCPProtect

protect = SCPProtect()
safe_chain = protect.wrap_langchain_chain(my_llm_chain)

# Safe invocation with automated pre-run guard & post-run verification
result = safe_chain.invoke({"input": "Summarize policy documents."})
```

### LlamaIndex Integration

```python
from scp_sdk import SCPProtect

protect = SCPProtect()
safe_query_engine = protect.wrap_llamaindex_engine(query_engine)
response = safe_query_engine.query("What is our refund window?")
```

### AutoGen Integration

```python
from scp_sdk import SCPProtect

protect = SCPProtect()
safe_agent = protect.wrap_autogen_agent(assistant_agent)
```

---

## 🛡️ Verification Tiers

| Tier | Recommended Use Cases | Rigor |
|---|---|---|
| `PERMISSIVE` | Informational lookup, creative writing | Relaxes epistemic grounding to prevent false withholding, retains 100% security bounds |
| `STANDARD` | Code generation, reasoning, analysis | Enforces syntax validation and safety checks |
| `STRICT` | Workspace write, OS execution, kernel | Full provenance verification and fail-closed checks |

---

## 📜 License

Licensed under the **Apache License, Version 2.0**.
