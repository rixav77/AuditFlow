"""Agent memory: long-term store (SQLite+FTS5), grounded ingestion, mem0-style retrieval.

Design contract (docs/CONTEXT_MEMORY.md):
- Memory NEVER feeds verdicts — the deterministic engine is untouched.
- Any financial fact inside a memory must cite record IDs verifiable against
  the batch DB (reuses the citations Layer-A discipline) or it is dropped.
- Everything is replayable: ops are logged, text is deterministic where possible.
"""
