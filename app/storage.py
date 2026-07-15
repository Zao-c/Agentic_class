import json
import hashlib
import re
import sqlite3
import statistics
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas import AgentState, RunStatus, utc_now


class ClosingSQLiteConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3.Connection and always release the handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Store:
    """Small SQLite repository with request/session isolation and JSON export."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=30,
            factory=ClosingSQLiteConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            answer TEXT,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, user_id);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, sequence),
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            helpful INTEGER NOT NULL,
            comment TEXT,
            tags_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS bad_cases (
            bad_case_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_path TEXT,
            document_type TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            token_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(document_id)
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
        CREATE TABLE IF NOT EXISTS escalations (
            escalation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS learning_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            outcome TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(source_run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_learning_user ON learning_records(user_id, created_at);
        CREATE TABLE IF NOT EXISTS knowledge_points (
            knowledge_point_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            source_query TEXT NOT NULL,
            question_template TEXT NOT NULL,
            criteria_json TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            version TEXT NOT NULL,
            review_status TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS exercises (
            exercise_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            question TEXT NOT NULL,
            criteria_json TEXT NOT NULL,
            citation_json TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            source_run_id TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            graded_at TEXT,
            FOREIGN KEY(knowledge_point_id) REFERENCES knowledge_points(knowledge_point_id),
            FOREIGN KEY(source_run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_exercises_user ON exercises(user_id, created_at);
        CREATE TABLE IF NOT EXISTS exercise_attempts (
            attempt_id TEXT PRIMARY KEY,
            exercise_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            answer TEXT NOT NULL,
            score REAL NOT NULL,
            matched_json TEXT NOT NULL,
            missing_json TEXT NOT NULL,
            feedback TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(exercise_id) REFERENCES exercises(exercise_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS mastery (
            user_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            mastery_score REAL NOT NULL,
            last_score REAL NOT NULL,
            mastery_status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, knowledge_point_id),
            FOREIGN KEY(knowledge_point_id) REFERENCES knowledge_points(knowledge_point_id)
        );
        CREATE TABLE IF NOT EXISTS alarm_codes (
            alarm_id TEXT PRIMARY KEY,
            equipment_brand TEXT NOT NULL,
            equipment_models_json TEXT NOT NULL,
            controller_versions_json TEXT NOT NULL,
            code TEXT NOT NULL,
            title TEXT NOT NULL,
            meaning TEXT NOT NULL,
            likely_causes_json TEXT NOT NULL,
            safe_checks_json TEXT NOT NULL,
            forbidden_actions_json TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            source_title TEXT NOT NULL,
            source_locator TEXT,
            source_excerpt TEXT,
            version TEXT NOT NULL,
            effective_date TEXT,
            review_status TEXT NOT NULL,
            access_scope TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_alarm_code ON alarm_codes(code, equipment_brand, is_active);
        CREATE TABLE IF NOT EXISTS diagnostic_states (
            run_id TEXT PRIMARY KEY,
            equipment TEXT NOT NULL,
            error_code TEXT NOT NULL,
            lookup_status TEXT NOT NULL,
            hypotheses_json TEXT NOT NULL,
            next_action TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS bad_case_assertions (
            bad_case_id TEXT PRIMARY KEY,
            expected_status TEXT,
            expected_task_type TEXT,
            expected_risk_level TEXT,
            answer_must_contain_json TEXT NOT NULL,
            answer_must_not_contain_json TEXT NOT NULL,
            require_citations INTEGER,
            review_note TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(bad_case_id) REFERENCES bad_cases(bad_case_id)
        );
        CREATE TABLE IF NOT EXISTS regression_cases (
            regression_case_id TEXT PRIMARY KEY,
            bad_case_id TEXT NOT NULL UNIQUE,
            package_json TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(bad_case_id) REFERENCES bad_cases(bad_case_id)
        );
        """
        with self._lock, self._connect() as connection:
            connection.executescript(schema)

    def create_run(self, state: AgentState) -> None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO runs
                (run_id, request_id, session_id, user_id, task_type, status, message,
                 answer, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state.run_id,
                    state.request_id,
                    state.session_id,
                    state.user_id,
                    state.task_type.value,
                    state.final_status.value,
                    state.original_message,
                    state.answer,
                    state.model_dump_json(),
                    now,
                    now,
                ),
            )
        self.append_event(state.run_id, "run.created", {"status": state.final_status.value})

    def save_state(self, state: AgentState) -> None:
        state.updated_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE runs SET task_type=?, status=?, answer=?, state_json=?, updated_at=?
                WHERE run_id=?""",
                (
                    state.task_type.value,
                    state.final_status.value,
                    state.answer,
                    state.model_dump_json(),
                    state.updated_at,
                    state.run_id,
                ),
            )

    def append_event(self, run_id: str, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            connection.execute(
                "INSERT INTO events(run_id, sequence, event_type, data_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, sequence, event_type, json.dumps(data, ensure_ascii=False), created_at),
            )
        return {
            "sequence": sequence,
            "event_type": event_type,
            "data": data,
            "created_at": created_at,
        }

    def get_events(self, run_id: str, after: int = 0) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, event_type, data_json, created_at FROM events WHERE run_id=? AND sequence>? ORDER BY sequence",
                (run_id, after),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "data": json.loads(row["data_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_state(self, run_id: str) -> Optional[AgentState]:
        with self._connect() as connection:
            row = connection.execute("SELECT state_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return AgentState.model_validate_json(row["state_json"]) if row else None

    def get_state_by_request(self, request_id: str) -> Optional[AgentState]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM runs WHERE request_id=?", (request_id,)
            ).fetchone()
        return AgentState.model_validate_json(row["state_json"]) if row else None

    def session_context(
        self,
        session_id: str,
        user_id: str,
        limit: int = 6,
        before_run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            if before_run_id:
                rows = connection.execute(
                    """SELECT message, answer, task_type, status FROM runs
                    WHERE session_id=? AND user_id=?
                      AND created_at < (SELECT created_at FROM runs WHERE run_id=?)
                      AND status NOT IN ('queued', 'running')
                    ORDER BY created_at DESC LIMIT ?""",
                    (session_id, user_id, before_run_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT message, answer, task_type, status FROM runs
                    WHERE session_id=? AND user_id=? ORDER BY created_at DESC LIMIT ?""",
                    (session_id, user_id, limit),
                ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def save_feedback(self, run_id: str, user_id: str, feedback: Dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO feedback(run_id, user_id, rating, helpful, comment, tags_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET rating=excluded.rating, helpful=excluded.helpful,
                    comment=excluded.comment, tags_json=excluded.tags_json""",
                (
                    run_id,
                    user_id,
                    feedback["rating"],
                    int(feedback["helpful"]),
                    feedback.get("comment"),
                    json.dumps(feedback.get("tags", []), ensure_ascii=False),
                    utc_now(),
                ),
            )

    def create_bad_case(self, bad_case_id: str, state: AgentState, reason: str, tags: List[str]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO bad_cases
                (bad_case_id, run_id, request_id, tags_json, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    bad_case_id,
                    state.run_id,
                    state.request_id,
                    json.dumps(tags, ensure_ascii=False),
                    reason,
                    utc_now(),
                ),
            )

    def list_bad_cases(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM bad_cases ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = self._redact_value(json.loads(item.pop("tags_json")))
            item["reason"] = self._redact_text(item["reason"])
            result.append(item)
        return result

    def bad_case_detail(self, bad_case_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT b.*, r.state_json, r.message, r.answer,
                f.rating, f.helpful, f.comment, f.tags_json AS feedback_tags_json
                FROM bad_cases b
                JOIN runs r ON r.run_id=b.run_id
                LEFT JOIN feedback f ON f.run_id=b.run_id
                WHERE b.bad_case_id=?""",
                (bad_case_id,),
            ).fetchone()
            assertion = connection.execute(
                "SELECT * FROM bad_case_assertions WHERE bad_case_id=?", (bad_case_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = self._redact_value(json.loads(item.pop("tags_json")))
        item["reason"] = self._redact_text(item["reason"])
        feedback_tags = item.pop("feedback_tags_json")
        item["feedback"] = (
            {
                "rating": item.pop("rating"),
                "helpful": bool(item.pop("helpful")),
                "comment": item.pop("comment"),
                "tags": json.loads(feedback_tags),
            }
            if feedback_tags is not None
            else None
        )
        if feedback_tags is None:
            item.pop("rating")
            item.pop("helpful")
            item.pop("comment")
        state = AgentState.model_validate_json(item.pop("state_json"))
        item["trace"] = self.trace(state)
        item["message"] = self._redact_text(item["message"])
        if item.get("answer"):
            item["answer"] = self._redact_text(item["answer"])
        item["assertions"] = (
            self._redact_value(self._decode_assertion(assertion)) if assertion else None
        )
        return item

    @staticmethod
    def _decode_assertion(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["answer_must_contain"] = json.loads(item.pop("answer_must_contain_json"))
        item["answer_must_not_contain"] = json.loads(
            item.pop("answer_must_not_contain_json")
        )
        if item["require_citations"] is not None:
            item["require_citations"] = bool(item["require_citations"])
        return item

    def save_bad_case_assertions(
        self, bad_case_id: str, assertions: Dict[str, Any], status: str
    ) -> Dict[str, Any]:
        updated_at = utc_now()
        with self._lock, self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM bad_cases WHERE bad_case_id=?", (bad_case_id,)
            ).fetchone()
            if not exists:
                raise KeyError(bad_case_id)
            connection.execute(
                """INSERT INTO bad_case_assertions (
                bad_case_id, expected_status, expected_task_type, expected_risk_level,
                answer_must_contain_json, answer_must_not_contain_json,
                require_citations, review_note, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bad_case_id) DO UPDATE SET
                expected_status=excluded.expected_status,
                expected_task_type=excluded.expected_task_type,
                expected_risk_level=excluded.expected_risk_level,
                answer_must_contain_json=excluded.answer_must_contain_json,
                answer_must_not_contain_json=excluded.answer_must_not_contain_json,
                require_citations=excluded.require_citations,
                review_note=excluded.review_note, updated_at=excluded.updated_at""",
                (
                    bad_case_id,
                    assertions.get("expected_status"),
                    assertions.get("expected_task_type"),
                    assertions.get("expected_risk_level"),
                    json.dumps(assertions.get("answer_must_contain", []), ensure_ascii=False),
                    json.dumps(assertions.get("answer_must_not_contain", []), ensure_ascii=False),
                    (
                        int(assertions["require_citations"])
                        if assertions.get("require_citations") is not None
                        else None
                    ),
                    assertions.get("review_note"),
                    updated_at,
                ),
            )
            connection.execute(
                "UPDATE bad_cases SET status=? WHERE bad_case_id=?", (status, bad_case_id)
            )
        return {"bad_case_id": bad_case_id, "status": status, "updated_at": updated_at}

    def prior_session_messages(self, run_id: str, limit: int = 6) -> List[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT previous.message FROM runs current
                JOIN runs previous
                  ON previous.session_id=current.session_id
                 AND previous.user_id=current.user_id
                 AND previous.created_at < current.created_at
                WHERE current.run_id=? AND previous.status NOT IN ('queued', 'running')
                ORDER BY previous.created_at DESC LIMIT ?""",
                (run_id, limit),
            ).fetchall()
        return [self._redact_text(row["message"]) for row in reversed(rows)]

    def save_regression_case(
        self, regression_case_id: str, bad_case_id: str, package: Dict[str, Any]
    ) -> Dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO regression_cases
                (regression_case_id, bad_case_id, package_json, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(bad_case_id) DO UPDATE SET
                package_json=excluded.package_json, is_active=1, updated_at=excluded.updated_at""",
                (
                    regression_case_id,
                    bad_case_id,
                    json.dumps(package, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT regression_case_id FROM regression_cases WHERE bad_case_id=?",
                (bad_case_id,),
            ).fetchone()
        return {
            "regression_case_id": row["regression_case_id"],
            "bad_case_id": bad_case_id,
            "status": "active",
        }

    def list_regression_cases(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT regression_case_id, bad_case_id, package_json, created_at, updated_at
                FROM regression_cases WHERE is_active=1 ORDER BY created_at LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "regression_case_id": row["regression_case_id"],
                "bad_case_id": row["bad_case_id"],
                "package": json.loads(row["package_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def create_escalation(self, escalation_id: str, run_id: str, risk_level: str, reason: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO escalations(escalation_id, run_id, risk_level, reason, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (escalation_id, run_id, risk_level, reason, utc_now()),
            )

    def record_learning(self, user_id: str, topic: str, outcome: str, run_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO learning_records(user_id, topic, outcome, source_run_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, topic[:300], outcome, run_id, utc_now()),
            )

    def upsert_knowledge_points(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        created = 0
        updated = 0
        now = utc_now()
        with self._lock, self._connect() as connection:
            for raw in records:
                record = dict(raw)
                point_id = record["knowledge_point_id"]
                canonical = json.dumps(record, ensure_ascii=False, sort_keys=True)
                content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                exists = connection.execute(
                    "SELECT 1 FROM knowledge_points WHERE knowledge_point_id=?", (point_id,)
                ).fetchone()
                connection.execute(
                    """INSERT INTO knowledge_points (
                    knowledge_point_id, name, aliases_json, category, description,
                    source_query, question_template, criteria_json, difficulty, version,
                    review_status, content_hash, is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(knowledge_point_id) DO UPDATE SET
                    name=excluded.name, aliases_json=excluded.aliases_json,
                    category=excluded.category, description=excluded.description,
                    source_query=excluded.source_query,
                    question_template=excluded.question_template,
                    criteria_json=excluded.criteria_json, difficulty=excluded.difficulty,
                    version=excluded.version, review_status=excluded.review_status,
                    content_hash=excluded.content_hash, is_active=excluded.is_active,
                    updated_at=excluded.updated_at""",
                    (
                        point_id,
                        record["name"],
                        json.dumps(record.get("aliases", []), ensure_ascii=False),
                        record.get("category", "operation_programming"),
                        record["description"],
                        record["source_query"],
                        record["question_template"],
                        json.dumps(record["criteria"], ensure_ascii=False),
                        record.get("difficulty", "basic"),
                        record.get("version", "1"),
                        record.get("review_status", "source_verified"),
                        content_hash,
                        int(record.get("is_active", True)),
                        now,
                        now,
                    ),
                )
                if exists:
                    updated += 1
                else:
                    created += 1
        return {"created": created, "updated": updated}

    @staticmethod
    def _decode_knowledge_point(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["aliases"] = json.loads(item.pop("aliases_json"))
        item["criteria"] = json.loads(item.pop("criteria_json"))
        item["is_active"] = bool(item["is_active"])
        return item

    def list_knowledge_points(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        where = "" if include_inactive else "WHERE is_active=1"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_points %s ORDER BY category, name" % where
            ).fetchall()
        return [self._decode_knowledge_point(row) for row in rows]

    def get_knowledge_point(self, knowledge_point_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_points WHERE knowledge_point_id=? AND is_active=1",
                (knowledge_point_id,),
            ).fetchone()
        return self._decode_knowledge_point(row) if row else None

    def create_exercise(self, exercise: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO exercises (
                exercise_id, user_id, knowledge_point_id, question, criteria_json,
                citation_json, difficulty, source_run_id, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                (
                    exercise["exercise_id"],
                    exercise["user_id"],
                    exercise["knowledge_point_id"],
                    exercise["question"],
                    json.dumps(exercise["criteria"], ensure_ascii=False),
                    json.dumps(exercise["citation"], ensure_ascii=False),
                    exercise["difficulty"],
                    exercise.get("source_run_id"),
                    utc_now(),
                ),
            )
        return self.get_exercise(exercise["exercise_id"], include_private=True)

    @staticmethod
    def _decode_exercise(row: sqlite3.Row, include_private: bool) -> Dict[str, Any]:
        item = dict(row)
        item["citation"] = json.loads(item.pop("citation_json"))
        criteria = json.loads(item.pop("criteria_json"))
        if include_private:
            item["criteria"] = criteria
        return item

    def get_exercise(
        self, exercise_id: str, include_private: bool = False
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT e.*, k.name AS knowledge_point_name
                FROM exercises e JOIN knowledge_points k
                ON k.knowledge_point_id=e.knowledge_point_id
                WHERE e.exercise_id=?""",
                (exercise_id,),
            ).fetchone()
        return self._decode_exercise(row, include_private) if row else None

    def list_exercises(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT e.*, k.name AS knowledge_point_name
                FROM exercises e JOIN knowledge_points k
                ON k.knowledge_point_id=e.knowledge_point_id
                WHERE e.user_id=? ORDER BY e.created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [self._decode_exercise(row, False) for row in rows]

    def save_exercise_attempt(self, attempt: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            exercise = connection.execute(
                "SELECT * FROM exercises WHERE exercise_id=?", (attempt["exercise_id"],)
            ).fetchone()
            if not exercise:
                raise KeyError(attempt["exercise_id"])
            if exercise["status"] != "open":
                raise ValueError("该练习已经提交，不能重复批改")
            point_id = exercise["knowledge_point_id"]
            current = connection.execute(
                "SELECT * FROM mastery WHERE user_id=? AND knowledge_point_id=?",
                (attempt["user_id"], point_id),
            ).fetchone()
            previous_attempts = int(current["attempts"]) if current else 0
            previous_score = float(current["mastery_score"]) if current else 0.0
            mastery_score = round(
                (previous_score * previous_attempts + float(attempt["score"]))
                / (previous_attempts + 1),
                2,
            )
            mastery_status = (
                "proficient" if mastery_score >= 80 else "developing" if mastery_score >= 50 else "needs_review"
            )
            connection.execute(
                """INSERT INTO exercise_attempts (
                attempt_id, exercise_id, user_id, answer, score, matched_json,
                missing_json, feedback, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt["attempt_id"], attempt["exercise_id"], attempt["user_id"],
                    attempt["answer"], attempt["score"],
                    json.dumps(attempt["matched_points"], ensure_ascii=False),
                    json.dumps(attempt["missing_points"], ensure_ascii=False),
                    attempt["feedback"], now,
                ),
            )
            connection.execute(
                "UPDATE exercises SET status='graded', graded_at=? WHERE exercise_id=?",
                (now, attempt["exercise_id"]),
            )
            connection.execute(
                """INSERT INTO mastery (
                user_id, knowledge_point_id, attempts, mastery_score, last_score,
                mastery_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, knowledge_point_id) DO UPDATE SET
                attempts=excluded.attempts, mastery_score=excluded.mastery_score,
                last_score=excluded.last_score, mastery_status=excluded.mastery_status,
                updated_at=excluded.updated_at""",
                (
                    attempt["user_id"], point_id, previous_attempts + 1,
                    mastery_score, attempt["score"], mastery_status, now,
                ),
            )
        return {
            **attempt,
            "knowledge_point_id": point_id,
            "mastery": {
                "attempts": previous_attempts + 1,
                "score": mastery_score,
                "status": mastery_status,
                "updated_at": now,
            },
        }

    def student_progress(self, user_id: str) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT k.knowledge_point_id, k.name, k.category, k.difficulty,
                COALESCE(m.attempts, 0) AS attempts, m.mastery_score,
                m.last_score, COALESCE(m.mastery_status, 'not_started') AS mastery_status,
                m.updated_at
                FROM knowledge_points k LEFT JOIN mastery m
                  ON m.knowledge_point_id=k.knowledge_point_id AND m.user_id=?
                WHERE k.is_active=1
                ORDER BY CASE COALESCE(m.mastery_status, 'not_started')
                    WHEN 'needs_review' THEN 0 WHEN 'developing' THEN 1
                    WHEN 'not_started' THEN 2 ELSE 3 END,
                    COALESCE(m.mastery_score, 0), k.name""",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def aggregate_mastery(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT k.knowledge_point_id, k.name,
                COUNT(m.user_id) AS assessed_students,
                ROUND(AVG(m.mastery_score), 2) AS average_mastery,
                SUM(CASE WHEN m.mastery_status='needs_review' THEN 1 ELSE 0 END) AS needs_review,
                SUM(CASE WHEN m.mastery_status='developing' THEN 1 ELSE 0 END) AS developing,
                SUM(CASE WHEN m.mastery_status='proficient' THEN 1 ELSE 0 END) AS proficient
                FROM knowledge_points k LEFT JOIN mastery m
                  ON m.knowledge_point_id=k.knowledge_point_id
                WHERE k.is_active=1 GROUP BY k.knowledge_point_id, k.name
                ORDER BY average_mastery, k.name"""
            ).fetchall()
        return [dict(row) for row in rows]

    def learning_records(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT topic, outcome, source_run_id, created_at FROM learning_records
                WHERE user_id=? ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_learning_records(self, user_id: str) -> int:
        with self._lock, self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM learning_records WHERE user_id=?", (user_id,)
            ).rowcount
            deleted += connection.execute(
                "DELETE FROM exercise_attempts WHERE user_id=?", (user_id,)
            ).rowcount
            deleted += connection.execute(
                "DELETE FROM exercises WHERE user_id=?", (user_id,)
            ).rowcount
            deleted += connection.execute(
                "DELETE FROM mastery WHERE user_id=?", (user_id,)
            ).rowcount
            return deleted

    def aggregate_learning(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT topic, outcome, COUNT(*) AS interactions
                FROM learning_records GROUP BY topic, outcome ORDER BY interactions DESC LIMIT 100"""
            ).fetchall()
        return [dict(row) for row in rows]

    def trace(self, state: AgentState) -> Dict[str, Any]:
        state_snapshot = state.model_dump(mode="json")
        anonymous_user_id = hashlib.sha256(state.user_id.encode("utf-8")).hexdigest()[:16]
        state_snapshot["user_id"] = anonymous_user_id
        state_snapshot["original_message"] = self._redact_text(state.original_message)
        if state_snapshot.get("answer"):
            state_snapshot["answer"] = self._redact_text(state_snapshot["answer"])
        trace = {
            "schema_version": "2.0.0",
            "request_id": state.request_id,
            "run_id": state.run_id,
            "session_id": state.session_id,
            "anonymous_user_id": anonymous_user_id,
            "task_type": state.task_type.value,
            "status": state.final_status.value,
            "normalized_query": self._redact_text(state.normalized_query),
            "state": state_snapshot,
            "events": self.get_events(state.run_id),
            "exported_at": utc_now(),
        }
        return self._redact_value(trace)

    @staticmethod
    def _redact_text(value: str) -> str:
        value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[PHONE_REDACTED]", value)
        value = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL_REDACTED]", value)
        return value[:8000]

    @classmethod
    def _redact_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._redact_text(value)
        if isinstance(value, list):
            return [cls._redact_value(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._redact_value(item) for key, item in value.items()}
        return value

    def operational_metrics(self, hours: int = 24) -> Dict[str, Any]:
        threshold = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._connect() as connection:
            run_rows = connection.execute(
                """SELECT status, task_type, created_at, updated_at FROM runs
                WHERE created_at>=?""",
                (threshold,),
            ).fetchall()
            event_rows = connection.execute(
                """SELECT event_type, data_json FROM events WHERE created_at>=?""", (threshold,)
            ).fetchall()
            feedback_rows = connection.execute(
                "SELECT rating, helpful FROM feedback WHERE created_at>=?", (threshold,)
            ).fetchall()
            bad_case_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM bad_cases WHERE created_at>=? GROUP BY status",
                (threshold,),
            ).fetchall()

        by_status: Dict[str, int] = {}
        by_task: Dict[str, int] = {}
        durations = []
        for row in run_rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            by_task[row["task_type"]] = by_task.get(row["task_type"], 0) + 1
            started = datetime.fromisoformat(row["created_at"])
            finished = datetime.fromisoformat(row["updated_at"])
            durations.append(max(0.0, (finished - started).total_seconds() * 1000))

        tool_calls = tool_errors = tool_timeouts = safety_events = 0
        safety_by_risk: Dict[str, int] = {}
        for row in event_rows:
            data = json.loads(row["data_json"])
            if row["event_type"] == "tool.finished":
                tool_calls += 1
                if data.get("status") == "error":
                    tool_errors += 1
                    if (data.get("error") or {}).get("code") == "TOOL_TIMEOUT":
                        tool_timeouts += 1
            elif row["event_type"] in {"safety.checked", "teacher.escalated"}:
                safety_events += 1
                risk = data.get("risk_level")
                if risk:
                    safety_by_risk[risk] = safety_by_risk.get(risk, 0) + 1

        ordered = sorted(durations)
        p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1)) if ordered else 0
        return {
            "window_hours": hours,
            "generated_at": utc_now(),
            "runs": {
                "total": len(run_rows),
                "by_status": by_status,
                "by_task_type": by_task,
                "latency_p50_ms": round(statistics.median(durations), 2) if durations else None,
                "latency_p95_ms": round(ordered[p95_index], 2) if durations else None,
            },
            "tools": {
                "calls": tool_calls,
                "errors": tool_errors,
                "timeouts": tool_timeouts,
                "error_rate": round(tool_errors / tool_calls, 4) if tool_calls else 0.0,
            },
            "safety": {"events": safety_events, "by_risk_level": safety_by_risk},
            "feedback": {
                "count": len(feedback_rows),
                "helpful_rate": (
                    round(sum(row["helpful"] for row in feedback_rows) / len(feedback_rows), 4)
                    if feedback_rows
                    else None
                ),
                "average_rating": (
                    round(statistics.mean(row["rating"] for row in feedback_rows), 2)
                    if feedback_rows
                    else None
                ),
            },
            "bad_cases": {row["status"]: row["count"] for row in bad_case_rows},
        }

    def count_chunks(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    def document_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE content_hash=?", (content_hash,)
            ).fetchone()
        return dict(row) if row else None

    def add_document(
        self,
        document_id: str,
        title: str,
        source_path: Optional[str],
        document_type: str,
        metadata: Dict[str, Any],
        content_hash: str,
        chunks: List[Dict[str, Any]],
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO documents
                (document_id, title, source_path, document_type, metadata_json, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id,
                    title,
                    source_path,
                    document_type,
                    json.dumps(metadata, ensure_ascii=False),
                    content_hash,
                    utc_now(),
                ),
            )
            for chunk in chunks:
                connection.execute(
                    """INSERT INTO chunks
                    (chunk_id, document_id, chunk_index, content, token_json, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        chunk["chunk_id"],
                        document_id,
                        chunk["chunk_index"],
                        chunk["content"],
                        json.dumps(chunk["tokens"], ensure_ascii=False),
                        json.dumps(chunk["metadata"], ensure_ascii=False),
                    ),
                )

    def active_chunks(self, access_scopes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT c.chunk_id, c.document_id, c.chunk_index, c.content,
                c.token_json, c.metadata_json, d.title, d.document_type, d.source_path
                FROM chunks c JOIN documents d ON d.document_id=c.document_id
                WHERE d.is_active=1"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tokens"] = json.loads(item.pop("token_json"))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            scope = item["metadata"].get("access_scope", "public")
            if access_scopes is None or scope in access_scopes or scope == "public":
                result.append(item)
        return result

    def list_documents(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT document_id, title, source_path, document_type, metadata_json,
                content_hash, is_active, created_at FROM documents ORDER BY created_at DESC"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def upsert_alarm_codes(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        created = updated = 0
        with self._lock, self._connect() as connection:
            for record in records:
                exists = connection.execute(
                    "SELECT 1 FROM alarm_codes WHERE alarm_id=?", (record["alarm_id"],)
                ).fetchone()
                now = utc_now()
                connection.execute(
                    """INSERT INTO alarm_codes (
                    alarm_id, equipment_brand, equipment_models_json,
                    controller_versions_json, code, title, meaning,
                    likely_causes_json, safe_checks_json, forbidden_actions_json,
                    risk_level, source_title, source_locator, source_excerpt,
                    version, effective_date, review_status, access_scope,
                    content_hash, is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(alarm_id) DO UPDATE SET
                    equipment_brand=excluded.equipment_brand,
                    equipment_models_json=excluded.equipment_models_json,
                    controller_versions_json=excluded.controller_versions_json,
                    code=excluded.code, title=excluded.title, meaning=excluded.meaning,
                    likely_causes_json=excluded.likely_causes_json,
                    safe_checks_json=excluded.safe_checks_json,
                    forbidden_actions_json=excluded.forbidden_actions_json,
                    risk_level=excluded.risk_level, source_title=excluded.source_title,
                    source_locator=excluded.source_locator,
                    source_excerpt=excluded.source_excerpt, version=excluded.version,
                    effective_date=excluded.effective_date,
                    review_status=excluded.review_status,
                    access_scope=excluded.access_scope,
                    content_hash=excluded.content_hash, is_active=excluded.is_active,
                    updated_at=excluded.updated_at""",
                    (
                        record["alarm_id"],
                        record["equipment_brand"],
                        json.dumps(record["equipment_models"], ensure_ascii=False),
                        json.dumps(record["controller_versions"], ensure_ascii=False),
                        record["code"],
                        record["title"],
                        record["meaning"],
                        json.dumps(record["likely_causes"], ensure_ascii=False),
                        json.dumps(record["safe_checks"], ensure_ascii=False),
                        json.dumps(record["forbidden_actions"], ensure_ascii=False),
                        record["risk_level"],
                        record["source_title"],
                        record.get("source_locator"),
                        record.get("source_excerpt"),
                        record["version"],
                        record.get("effective_date"),
                        record["review_status"],
                        record["access_scope"],
                        record["content_hash"],
                        int(record["is_active"]),
                        now,
                        now,
                    ),
                )
                if exists:
                    updated += 1
                else:
                    created += 1
        return {"created": created, "updated": updated, "total": len(records)}

    @staticmethod
    def _decode_alarm_row(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        for target, source in (
            ("equipment_models", "equipment_models_json"),
            ("controller_versions", "controller_versions_json"),
            ("likely_causes", "likely_causes_json"),
            ("safe_checks", "safe_checks_json"),
            ("forbidden_actions", "forbidden_actions_json"),
        ):
            item[target] = json.loads(item.pop(source))
        item["is_active"] = bool(item["is_active"])
        return item

    def alarm_codes_by_code(self, code: str) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM alarm_codes WHERE code=? AND is_active=1 ORDER BY equipment_brand, title",
                (code,),
            ).fetchall()
        return [self._decode_alarm_row(row) for row in rows]

    def list_alarm_codes(self, code: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            if code:
                rows = connection.execute(
                    "SELECT * FROM alarm_codes WHERE code=? ORDER BY equipment_brand, title LIMIT ?",
                    (code, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM alarm_codes ORDER BY equipment_brand, code LIMIT ?", (limit,)
                ).fetchall()
        return [self._decode_alarm_row(row) for row in rows]

    def count_alarm_codes(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM alarm_codes WHERE is_active=1").fetchone()[0]
            )

    def record_diagnostic_state(
        self,
        run_id: str,
        equipment: str,
        error_code: str,
        lookup_status: str,
        hypotheses: List[str],
        next_action: str,
    ) -> Dict[str, Any]:
        updated_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO diagnostic_states
                (run_id, equipment, error_code, lookup_status, hypotheses_json, next_action, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                equipment=excluded.equipment, error_code=excluded.error_code,
                lookup_status=excluded.lookup_status,
                hypotheses_json=excluded.hypotheses_json,
                next_action=excluded.next_action, updated_at=excluded.updated_at""",
                (
                    run_id,
                    equipment,
                    error_code,
                    lookup_status,
                    json.dumps(hypotheses, ensure_ascii=False),
                    next_action,
                    updated_at,
                ),
            )
        return {
            "run_id": run_id,
            "lookup_status": lookup_status,
            "next_action": next_action,
            "updated_at": updated_at,
        }

    def run_status(self, run_id: str) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return row["status"] if row else None
