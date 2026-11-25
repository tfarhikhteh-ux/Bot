from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso, now_tehran


class AnnouncementRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, announcement_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone(
            """
            SELECT a.*, u.full_name AS author_name,
                   (SELECT COUNT(*) FROM message_queue mq WHERE mq.announcement_id = a.id AND mq.status = 'SENT') AS sent_count,
                   (SELECT COUNT(*) FROM message_queue mq WHERE mq.announcement_id = a.id AND mq.status = 'FAILED') AS failed_count,
                   (SELECT COUNT(*) FROM message_queue mq WHERE mq.announcement_id = a.id AND mq.status = 'PENDING') AS pending_count
            FROM announcements a
            JOIN users u ON u.id = a.created_by
            WHERE a.id = ? AND a.status != 'DELETED';
            """,
            (announcement_id,),
        )

    def create(
        self,
        title: str,
        body: str,
        target_type: str,
        created_by: int,
        target_value: Optional[str] = None,
        scheduled_at: Optional[str] = None,
        status: str = "QUEUED",
    ) -> Dict[str, Any]:
        ts = now_iso()
        aid = self.db.execute(
            """
            INSERT INTO announcements(
                title, body, target_type, target_value, scheduled_at,
                status, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (title.strip(), body.strip(), target_type, target_value, scheduled_at, status, created_by, ts, ts),
        )
        return self.get_by_id(aid)

    def update(self, announcement_id: int, title: str, body: str, scheduled_at: Optional[str] = None) -> Optional[Dict[str, Any]]:
        self.db.execute(
            """
            UPDATE announcements
            SET title = ?, body = ?, scheduled_at = COALESCE(?, scheduled_at), updated_at = ?
            WHERE id = ? AND status IN ('DRAFT', 'QUEUED');
            """,
            (title.strip(), body.strip(), scheduled_at, now_iso(), announcement_id),
        )
        return self.get_by_id(announcement_id)

    def soft_delete(self, announcement_id: int) -> None:
        ts = now_iso()
        with self.db.transaction() as conn:
            conn.execute("UPDATE announcements SET status = 'DELETED', updated_at = ? WHERE id = ?;", (ts, announcement_id))
            conn.execute(
                "DELETE FROM message_queue WHERE announcement_id = ? AND status = 'PENDING';",
                (announcement_id,),
            )

    def list_announcements(
        self,
        for_role: Optional[str] = None,
        for_class_ids: Optional[List[int]] = None,
        include_management: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if include_management:
            return self.db.fetchall(
                """
                SELECT a.*, u.full_name AS author_name,
                       (SELECT COUNT(*) FROM message_queue mq WHERE mq.announcement_id = a.id AND mq.status = 'SENT') AS sent_count,
                       (SELECT COUNT(*) FROM message_queue mq WHERE mq.announcement_id = a.id AND mq.status = 'FAILED') AS failed_count,
                       (SELECT COUNT(*) FROM message_queue mq WHERE mq.announcement_id = a.id AND mq.status = 'PENDING') AS pending_count
                FROM announcements a
                JOIN users u ON u.id = a.created_by
                WHERE a.status != 'DELETED'
                ORDER BY a.id DESC LIMIT ?;
                """,
                (limit,),
            )

        clauses = ["a.status IN ('SENT', 'PARTIAL', 'SENDING', 'QUEUED')", "(a.scheduled_at IS NULL OR a.scheduled_at <= ?)"]
        params: List[Any] = [now_iso()]
        vis = ["a.target_type = 'ALL'"]
        if for_role:
            vis.append("(a.target_type = 'ROLE' AND a.target_value = ?)")
            params.append(for_role)
            if for_role == "TEACHER":
                vis.append("a.target_type = 'TEACHERS'")
            if for_role == "PARENT":
                vis.append("a.target_type = 'PARENTS'")
        if for_class_ids:
            placeholders = ",".join("?" for _ in for_class_ids)
            vis.append(f"(a.target_type = 'CLASS' AND a.target_value IN ({placeholders}))")
            params.extend(str(cid) for cid in for_class_ids)
        clauses.append("(" + " OR ".join(vis) + ")")
        params.append(limit)
        return self.db.fetchall(
            f"""
            SELECT a.*, u.full_name AS author_name
            FROM announcements a
            JOIN users u ON u.id = a.created_by
            WHERE {' AND '.join(clauses)}
            ORDER BY a.id DESC LIMIT ?;
            """,
            params,
        )

    def due_scheduled_announcements(self) -> List[Dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT * FROM announcements
            WHERE status = 'QUEUED'
              AND (scheduled_at IS NULL OR scheduled_at <= ?)
              AND NOT EXISTS (SELECT 1 FROM message_queue mq WHERE mq.announcement_id = announcements.id)
            ORDER BY id ASC;
            """,
            (now_iso(),),
        )

    def enqueue_messages(
        self,
        recipients: List[Dict[str, Any]],
        text: str,
        message_type: str = "NOTIFICATION",
        announcement_id: Optional[int] = None,
        max_attempts: int = 3,
    ) -> int:
        if not recipients:
            return 0
        ts = now_iso()
        rows = [
            (
                announcement_id,
                int(r["chat_id"]),
                r.get("user_id"),
                message_type,
                text,
                max_attempts,
                ts,
                ts,
            )
            for r in recipients
            if r.get("chat_id")
        ]
        return self.db.executemany(
            """
            INSERT INTO message_queue(
                announcement_id, recipient_chat_id, recipient_user_id,
                message_type, text, status, attempts, max_attempts, next_retry_at, created_at
            ) VALUES (?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?);
            """,
            rows,
        )

    def fetch_pending_queue_batch(self, batch_size: int = 15) -> List[Dict[str, Any]]:
        ts = now_iso()
        return self.db.fetchall(
            """
            SELECT * FROM message_queue
            WHERE status = 'PENDING' AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY id ASC
            LIMIT ?;
            """,
            (ts, batch_size),
        )

    def mark_queue_sent(self, queue_id: int) -> None:
        ts = now_iso()
        self.db.execute(
            "UPDATE message_queue SET status = 'SENT', attempts = attempts + 1, sent_at = ?, last_error = NULL WHERE id = ?;",
            (ts, queue_id),
        )

    def mark_queue_failure(self, queue_id: int, error_message: str) -> None:
        row = self.db.fetchone("SELECT attempts, max_attempts FROM message_queue WHERE id = ?;", (queue_id,))
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        max_att = int(row["max_attempts"])
        if attempts >= max_att:
            self.db.execute(
                "UPDATE message_queue SET status = 'FAILED', attempts = ?, last_error = ? WHERE id = ?;",
                (attempts, error_message[:400], queue_id),
            )
        else:
            next_dt = (now_tehran() + timedelta(seconds=30 * attempts)).strftime("%Y-%m-%dT%H:%M:%S")
            self.db.execute(
                "UPDATE message_queue SET attempts = ?, next_retry_at = ?, last_error = ? WHERE id = ?;",
                (attempts, next_dt, error_message[:400], queue_id),
            )

    def retry_failed_for_announcement(self, announcement_id: int) -> int:
        ts = now_iso()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE message_queue
                SET status = 'PENDING', attempts = 0, next_retry_at = ?, last_error = NULL
                WHERE announcement_id = ? AND status = 'FAILED';
                """,
                (ts, announcement_id),
            )
            if cur.rowcount > 0:
                conn.execute("UPDATE announcements SET status = 'SENDING', updated_at = ? WHERE id = ?;", (ts, announcement_id))
            return int(cur.rowcount)

    def refresh_announcement_status(self, announcement_id: int) -> None:
        stats = self.db.fetchone(
            """
            SELECT
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS p,
                SUM(CASE WHEN status = 'SENT' THEN 1 ELSE 0 END) AS s,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS f
            FROM message_queue WHERE announcement_id = ?;
            """,
            (announcement_id,),
        )
        if not stats:
            return
        p, s, f = int(stats["p"] or 0), int(stats["s"] or 0), int(stats["f"] or 0)
        if p > 0:
            new_st = "SENDING"
        elif s > 0 and f == 0:
            new_st = "SENT"
        elif s > 0 and f > 0:
            new_st = "PARTIAL"
        elif f > 0 and s == 0:
            new_st = "FAILED"
        else:
            return
        ts = now_iso()
        self.db.execute(
            "UPDATE announcements SET status = ?, sent_at = COALESCE(sent_at, ?), updated_at = ? WHERE id = ?;",
            (new_st, ts if new_st in {"SENT", "PARTIAL"} else None, ts, announcement_id),
        )
