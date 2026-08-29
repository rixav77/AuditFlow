"""Long-term memory store — mem0's three stores in one SQLite file.

- memories   : the memory rows (semantic/episodic/procedural)
- entities   : entity -> memory links (mem0's entity store)
- memory_log : every ADD/UPDATE/DELETE/NOOP op (mem0's history store)
- recent_messages : per-session ring buffer (mem0's last-10 pronoun context)

Search: SQLite FTS5 (BM25) with graceful LIKE fallback. No vector DB, no new
dependencies — deterministic and replayable at our scale.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("data/memory/memory.db")

STOP = set(
    "a an the is are was were of to in on for with and or if it its this that be as at by from "
    "what which who whom how when where why not no yes do does did done can could should would "
    "there their they them we you your our i me my".split()
)


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class MemoryStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.db_path)
        self.con.row_factory = sqlite3.Row
        self._fts = self._init_schema()

    def _init_schema(self) -> bool:
        c = self.con
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                kind TEXT NOT NULL,
                attribution TEXT NOT NULL,
                scope TEXT,
                hash TEXT NOT NULL,
                keywords TEXT NOT NULL,
                source_refs TEXT NOT NULL,
                grounded INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entities (
                entity TEXT NOT NULL,
                memory_id INTEGER NOT NULL REFERENCES memories(id),
                PRIMARY KEY (entity, memory_id)
            );
            CREATE TABLE IF NOT EXISTS memory_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                op TEXT NOT NULL,
                memory_id INTEGER,
                detail TEXT
            );
            CREATE TABLE IF NOT EXISTS recent_messages (
                session TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                PRIMARY KEY (session, seq)
            );
            """
        )
        fts = True
        try:
            c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5"
                "(text, keywords, content='memories', content_rowid='id')"
            )
            for trig in (
                "CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN "
                "INSERT INTO memories_fts(rowid, text, keywords) "
                "VALUES (new.id, new.text, new.keywords); END;",
                "CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE OF text, keywords ON memories"
                " BEGIN "
                "INSERT INTO memories_fts(memories_fts, rowid, text, keywords) "
                "VALUES ('delete', old.id, old.text, old.keywords); "
                "INSERT INTO memories_fts(rowid, text, keywords) "
                "VALUES (new.id, new.text, new.keywords); END;",
                "CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN "
                "INSERT INTO memories_fts(memories_fts, rowid, text, keywords) "
                "VALUES ('delete', old.id, old.text, old.keywords); END;",
            ):
                c.execute(trig)
        except sqlite3.OperationalError:
            fts = False  # FTS5 unavailable -> LIKE fallback
        c.commit()
        return fts

    def log(self, op: str, memory_id: int | None = None, detail: str = "") -> None:
        self.con.execute(
            "INSERT INTO memory_log (ts, op, memory_id, detail) VALUES (?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), op, memory_id, detail),
        )
        self.con.commit()

    # -- session ring buffer ------------------------------------------------
    def push_message(self, session: str, role: str, content: str, keep: int = 10) -> None:
        cur = self.con.execute(
            "SELECT COALESCE(MAX(seq),0) FROM recent_messages WHERE session=?", (session,)
        ).fetchone()[0]
        self.con.execute(
            "INSERT INTO recent_messages (session, seq, role, content) VALUES (?,?,?,?)",
            (session, cur + 1, role, content[:4000]),
        )
        self.con.commit()
        self.con.execute(
            """DELETE FROM recent_messages WHERE session=? AND seq<= (SELECT seq FROM """
            """recent_messages WHERE session=? ORDER BY seq DESC LIMIT 1 OFFSET ?)""",
            (session, session, keep),
        )
        self.con.commit()

    def last_messages(self, session: str, n: int = 10) -> list[dict]:
        rows = self.con.execute(
            "SELECT role, content FROM recent_messages WHERE session=? ORDER BY seq DESC LIMIT ?",
            (session, n),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # -- dedup --------------------------------------------------------------
    def find_duplicate(self, text: str) -> sqlite3.Row | None:
        h = hashlib.sha256(text.strip().lower().encode()).hexdigest()
        row = self.con.execute(
            "SELECT * FROM memories WHERE hash=? AND status='active'", (h,)
        ).fetchone()
        if row:
            return row
        tt = tokens(text)
        for r in self.con.execute("SELECT * FROM memories WHERE status='active'"):
            if jaccard(tt, tokens(r["text"])) >= 0.8:
                return r
        return None

    # -- core ops -----------------------------------------------------------
    def add(
        self,
        text: str,
        kind: str,
        attribution: str = "agent",
        scope: str | None = None,
        source_refs: list[str] | None = None,
        grounded: bool = False,
    ) -> tuple[int, str]:
        """Returns (memory_id, op); op in {ADD, UPDATE} (near-dup refreshes)."""
        dup = self.find_duplicate(text)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        kw = json.dumps(sorted(tokens(text)))
        refs = json.dumps(source_refs or [])
        if dup:
            self.con.execute(
                "UPDATE memories SET text=?, kind=?, attribution=?, scope=?, source_refs=?,"
                " grounded=?, updated_at=? WHERE id=?",
                (text, kind, attribution, scope, refs, int(grounded), now, dup["id"]),
            )
            self.con.commit()
            self._sync_entities(dup["id"], text)
            self.log("UPDATE", dup["id"], "near-duplicate refreshed")
            return dup["id"], "UPDATE"
        cur = self.con.execute(
            "INSERT INTO memories (text, kind, attribution, scope, hash, keywords,"
            " source_refs, grounded, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                text,
                kind,
                attribution,
                scope,
                hashlib.sha256(text.strip().lower().encode()).hexdigest(),
                kw,
                refs,
                int(grounded),
                now,
                now,
            ),
        )
        self.con.commit()
        mid = cur.lastrowid
        self._sync_entities(mid, text)
        self.log("ADD", mid)
        return mid, "ADD"

    def delete(self, memory_id: int, reason: str = "") -> bool:
        row = self.con.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row or row["status"] != "active":
            self.log("NOOP", None, f"delete: unknown/inactive id {memory_id}")
            return False
        self.con.execute("UPDATE memories SET status='deleted' WHERE id=?", (memory_id,))
        self.con.commit()
        self.log("DELETE", memory_id, reason)
        return True

    def _sync_entities(self, memory_id: int, text: str) -> None:
        from memory.retrieve import extract_entities

        self.con.execute("DELETE FROM entities WHERE memory_id=?", (memory_id,))
        for ent in extract_entities(text):
            self.con.execute(
                "INSERT OR IGNORE INTO entities (entity, memory_id) VALUES (?,?)",
                (ent, memory_id),
            )
        self.con.commit()

    # -- queries ------------------------------------------------------------
    def all_active(self, scope: str | None = None, kind: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM memories WHERE status='active'"
        args: list = []
        if scope:
            q += " AND (scope IS NULL OR scope=?)"
            args.append(scope)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        return list(self.con.execute(q + " ORDER BY updated_at DESC", args))

    def get(self, memory_id: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()

    def stats(self) -> dict:
        n = self.con.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
        by_kind = {
            r[0]: r[1]
            for r in self.con.execute(
                "SELECT kind, COUNT(*) FROM memories WHERE status='active' GROUP BY kind"
            )
        }
        grounded = self.con.execute(
            "SELECT COUNT(*) FROM memories WHERE status='active' AND grounded=1"
        ).fetchone()[0]
        ops = self.con.execute("SELECT COUNT(*) FROM memory_log").fetchone()[0]
        return {"active": n, "by_kind": by_kind, "grounded": grounded, "ops_logged": ops}

    def close(self) -> None:
        self.con.close()
