import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


DEFAULT_DB_PATH = os.path.expanduser(
    os.environ.get("CLARK_MEMORY_DB", "~/clark-semantic-memory/clark_memory.db")
)
EMBED_DIM = 128


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", "-", str(text or "").strip().lower())
    return text.strip("-") or "item"


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Zа-яА-Я0-9_]+", str(text or "").lower())


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _hash_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    tokens = _tokenize(text) or [str(text or "")]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for idx in range(0, len(digest), 4):
            bucket = int.from_bytes(digest[idx:idx + 4], "big") % dim
            vec[bucket] += 1.0
    return _normalize(vec)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


@dataclass
class SemanticEntry:
    kind: str
    scope: str
    subject: str
    statement: str
    canonical_key: str
    tags: list[str]
    confidence: float = 0.9
    provenance: str = "manual"
    details: dict | None = None


class ClarkSemanticMemory:
    """
    Standalone semantic memory inspired by the repo's CLARK framing:
    1. Seed memories by lexical/semantic similarity
    2. Propagate confidence over the semantic graph
    3. Retrieve top candidates with an A*-style frontier expansion
    4. Reinforce retrieved durable memories
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = os.path.expanduser(db_path)
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def close(self):
        self.conn.close()

    def _init_db(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS semantic_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            scope TEXT NOT NULL,
            subject TEXT NOT NULL,
            statement TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            embedding_json TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.9,
            access_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            provenance TEXT NOT NULL DEFAULT 'manual',
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_reinforced_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS semantic_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            target_key TEXT NOT NULL,
            relation TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            UNIQUE(source_key, target_key, relation)
        );

        CREATE TABLE IF NOT EXISTS retrieval_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_text TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            score REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        self.conn.commit()

    def _default_key(self, kind: str, scope: str, subject: str, statement: str) -> str:
        topic = "-".join(_tokenize(statement)[:6]) or hashlib.md5(statement.encode()).hexdigest()[:8]
        return f"{_slugify(kind)}:{_slugify(scope)}:{_slugify(subject)}:{_slugify(topic)}"

    def _node_text(self, entry: SemanticEntry) -> str:
        return " ".join([entry.kind, entry.scope, entry.subject, entry.statement, " ".join(entry.tags)]).strip()

    def remember(self, entry: SemanticEntry) -> dict:
        key = entry.canonical_key or self._default_key(entry.kind, entry.scope, entry.subject, entry.statement)
        tags = sorted({tag.strip().lower() for tag in entry.tags if tag.strip()})
        embedding = _hash_embedding(self._node_text(entry))
        timestamp = _now()
        self.conn.execute("""
            INSERT INTO semantic_nodes(
                canonical_key, kind, scope, subject, statement, tags_json, embedding_json,
                confidence, provenance, details_json, created_at, updated_at, last_reinforced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                kind=excluded.kind,
                scope=excluded.scope,
                subject=excluded.subject,
                statement=excluded.statement,
                tags_json=excluded.tags_json,
                embedding_json=excluded.embedding_json,
                confidence=excluded.confidence,
                provenance=excluded.provenance,
                details_json=excluded.details_json,
                status='active',
                updated_at=excluded.updated_at,
                last_reinforced_at=excluded.last_reinforced_at
        """, (
            key,
            entry.kind,
            entry.scope,
            entry.subject,
            entry.statement,
            json.dumps(tags, ensure_ascii=False),
            json.dumps(embedding),
            float(entry.confidence),
            entry.provenance,
            json.dumps(entry.details or {}, ensure_ascii=False, sort_keys=True),
            timestamp,
            timestamp,
            timestamp,
        ))
        self._refresh_auto_edges_for(key)
        self.conn.commit()
        return {"status": "ok", "canonical_key": key, "kind": entry.kind}

    def remember_project(self, statement: str, project: str, canonical_key: str = "", tags: list[str] | None = None):
        return self.remember(SemanticEntry(
            kind="project",
            scope=project,
            subject=project,
            statement=statement,
            canonical_key=canonical_key,
            tags=(tags or []) + ["project"],
        ))

    def remember_tool(self, statement: str, tool_name: str, canonical_key: str = "", tags: list[str] | None = None):
        return self.remember(SemanticEntry(
            kind="tool",
            scope="tooling",
            subject=tool_name,
            statement=statement,
            canonical_key=canonical_key,
            tags=(tags or []) + ["tool", _slugify(tool_name)],
        ))

    def remember_rule(self, statement: str, rule_name: str, canonical_key: str = "", tags: list[str] | None = None):
        return self.remember(SemanticEntry(
            kind="rule",
            scope="rules",
            subject=rule_name,
            statement=statement,
            canonical_key=canonical_key,
            tags=(tags or []) + ["rule", _slugify(rule_name)],
        ))

    def add_edge(self, source_key: str, target_key: str, relation: str, weight: float = 1.0):
        timestamp = _now()
        self.conn.execute("""
            INSERT OR REPLACE INTO semantic_edges(source_key, target_key, relation, weight, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (source_key, target_key, relation, float(weight), timestamp))
        self.conn.commit()

    def _iter_nodes(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM semantic_nodes WHERE status='active'").fetchall()

    def _refresh_auto_edges_for(self, canonical_key: str):
        node = self.conn.execute(
            "SELECT canonical_key, kind, scope, subject, tags_json FROM semantic_nodes WHERE canonical_key=?",
            (canonical_key,),
        ).fetchone()
        if not node:
            return
        tags = set(json.loads(node["tags_json"]))
        peers = self.conn.execute(
            "SELECT canonical_key, kind, scope, subject, tags_json FROM semantic_nodes WHERE canonical_key != ? AND status='active'",
            (canonical_key,),
        ).fetchall()
        for peer in peers:
            weight = 0.0
            relation = None
            peer_tags = set(json.loads(peer["tags_json"]))
            if node["scope"] == peer["scope"]:
                weight += 0.45
                relation = relation or "same_scope"
            if node["subject"] == peer["subject"]:
                weight += 0.30
                relation = relation or "same_subject"
            overlap = len(tags & peer_tags)
            if overlap:
                weight += min(0.25, overlap * 0.08)
                relation = relation or "tag_overlap"
            if node["kind"] == peer["kind"]:
                weight += 0.08
                relation = relation or "same_kind"
            if weight > 0:
                self.conn.execute("""
                    INSERT OR REPLACE INTO semantic_edges(source_key, target_key, relation, weight, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (canonical_key, peer["canonical_key"], relation or "related", round(weight, 4), _now()))
                self.conn.execute("""
                    INSERT OR REPLACE INTO semantic_edges(source_key, target_key, relation, weight, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (peer["canonical_key"], canonical_key, relation or "related", round(weight, 4), _now()))

    def _keyword_bonus(self, query: str, row: sqlite3.Row) -> float:
        q_tokens = set(_tokenize(query))
        searchable = " ".join([row["kind"], row["scope"], row["subject"], row["statement"], row["tags_json"]]).lower()
        hits = sum(1 for token in q_tokens if token in searchable)
        return min(0.4, hits * 0.08)

    def _build_graph(self) -> dict[str, list[tuple[str, float]]]:
        graph = defaultdict(list)
        for edge in self.conn.execute("SELECT source_key, target_key, weight FROM semantic_edges").fetchall():
            graph[edge["source_key"]].append((edge["target_key"], float(edge["weight"])))
        return graph

    def _value_iteration(self, seeds: dict[str, float], steps: int = 4, damping: float = 0.65) -> dict[str, float]:
        graph = self._build_graph()
        base = {row["canonical_key"]: float(row["confidence"]) for row in self._iter_nodes()}
        values = dict(base)
        for key, score in seeds.items():
            values[key] = max(values.get(key, 0.0), score)

        for _ in range(steps):
            updated = dict(values)
            for key, current in values.items():
                neighbors = graph.get(key, [])
                if not neighbors:
                    continue
                propagated = sum(values.get(target, 0.0) * weight for target, weight in neighbors) / max(len(neighbors), 1)
                updated[key] = damping * base.get(key, current) + (1 - damping) * propagated
            values = updated
        return values

    def _astar_rank(self, query: str, propagated: dict[str, float], limit: int) -> list[dict]:
        rows = {row["canonical_key"]: row for row in self._iter_nodes()}
        q_vec = _hash_embedding(query)
        graph = self._build_graph()

        seeds = []
        for key, row in rows.items():
            embedding = json.loads(row["embedding_json"])
            cosine = _cosine(q_vec, embedding)
            lexical = self._keyword_bonus(query, row)
            seed_score = (cosine * 0.55) + (propagated.get(key, 0.0) * 0.35) + lexical
            if seed_score > 0.12:
                seeds.append((key, seed_score, cosine, lexical))

        frontier = sorted(seeds, key=lambda item: item[1], reverse=True)
        visited = set()
        ranked = []
        while frontier and len(ranked) < limit * 3:
            key, score, cosine, lexical = frontier.pop(0)
            if key in visited:
                continue
            visited.add(key)
            row = rows[key]
            ranked.append({
                "canonical_key": key,
                "kind": row["kind"],
                "scope": row["scope"],
                "subject": row["subject"],
                "statement": row["statement"],
                "confidence": row["confidence"],
                "tags": json.loads(row["tags_json"]),
                "cosine_sim": round(cosine, 4),
                "propagated_confidence": round(propagated.get(key, 0.0), 4),
                "score": round(score, 4),
                "layer": "semantic",
            })
            for neighbor_key, weight in graph.get(key, []):
                if neighbor_key in visited or neighbor_key not in rows:
                    continue
                neighbor = rows[neighbor_key]
                n_vec = json.loads(neighbor["embedding_json"])
                n_cos = _cosine(q_vec, n_vec)
                n_lex = self._keyword_bonus(query, neighbor)
                heuristic = (n_cos * 0.5) + (propagated.get(neighbor_key, 0.0) * 0.3) + (weight * 0.2) + n_lex
                frontier.append((neighbor_key, heuristic, n_cos, n_lex))
            frontier.sort(key=lambda item: item[1], reverse=True)
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:limit]

    def _reinforce(self, query: str, results: Iterable[dict]):
        for item in results:
            self.conn.execute("""
                UPDATE semantic_nodes
                SET access_count = access_count + 1,
                    confidence = MIN(1.0, confidence + 0.03),
                    last_reinforced_at = ?,
                    updated_at = ?
                WHERE canonical_key = ?
            """, (_now(), _now(), item["canonical_key"]))
            self.conn.execute("""
                INSERT INTO retrieval_events(query_text, canonical_key, score, created_at)
                VALUES (?, ?, ?, ?)
            """, (query, item["canonical_key"], float(item["score"]), _now()))
        self.conn.commit()

    def query(self, query: str, limit: int = 8) -> dict:
        rows = self._iter_nodes()
        q_vec = _hash_embedding(query)
        seed_scores = {}
        for row in rows:
            embedding = json.loads(row["embedding_json"])
            cosine = _cosine(q_vec, embedding)
            lexical = self._keyword_bonus(query, row)
            score = (cosine * 0.6) + lexical
            if score > 0.12:
                seed_scores[row["canonical_key"]] = score
        propagated = self._value_iteration(seed_scores)
        ranked = self._astar_rank(query, propagated, limit)
        self._reinforce(query, ranked)
        return {
            "query": query,
            "results": ranked,
            "count": len(ranked),
        }

    def session_context(self, limit_per_kind: int = 5) -> str:
        rows = self.list_entries()
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["kind"]].append(row)
        blocks = []
        for label, kind in [("Project", "project"), ("Tools", "tool"), ("Rules", "rule"), ("Context", "context")]:
            entries = grouped.get(kind, [])[:limit_per_kind]
            if entries:
                lines = [f"  - {entry['subject']}: {entry['statement']}" for entry in entries]
                blocks.append(f"[{label}]\n" + "\n".join(lines))
        return "\n\n".join(blocks) if blocks else "(no semantic context)"

    def list_entries(self, kind: str | None = None) -> list[dict]:
        if kind:
            rows = self.conn.execute(
                "SELECT * FROM semantic_nodes WHERE status='active' AND kind=? ORDER BY confidence DESC, updated_at DESC",
                (kind,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM semantic_nodes WHERE status='active' ORDER BY kind, confidence DESC, updated_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item["tags_json"])
            item["details"] = json.loads(item["details_json"])
            result.append(item)
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Clark semantic memory")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    remember_project = sub.add_parser("remember-project")
    remember_project.add_argument("project")
    remember_project.add_argument("statement")
    remember_project.add_argument("--key", default="")

    remember_tool = sub.add_parser("remember-tool")
    remember_tool.add_argument("tool_name")
    remember_tool.add_argument("statement")
    remember_tool.add_argument("--key", default="")

    remember_rule = sub.add_parser("remember-rule")
    remember_rule.add_argument("rule_name")
    remember_rule.add_argument("statement")
    remember_rule.add_argument("--key", default="")

    query = sub.add_parser("query")
    query.add_argument("text")
    query.add_argument("--limit", type=int, default=8)

    context = sub.add_parser("context")

    listing = sub.add_parser("list")
    listing.add_argument("--kind", default=None)

    return parser
