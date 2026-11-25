from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso


class AuditRepository:
    """مخزن ثبت غیرقابل‌حذف و غیرقابل‌تغییر گزارش‌های امنیتی و عملیاتی (Audit Log)."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def record(
        self,
        action: str,
        entity_type: str,
        actor_user_id: Optional[int] = None,
        actor_bale_id: Optional[int] = None,
        entity_id: Optional[str | int] = None,
        before_data: Any = None,
        after_data: Any = None,
        result: str = "SUCCESS",
        ip_address: Optional[str] = None,
        description: Optional[str] = None,
    ) -> int:
        before_json = json.dumps(before_data, ensure_ascii=False, default=str) if before_data is not None else None
        after_json = json.dumps(after_data, ensure_ascii=False, default=str) if after_data is not None else None
        return self.db.execute(
            """
            INSERT INTO audit_logs(
                actor_user_id, actor_bale_id, action, entity_type, entity_id,
                before_data, after_data, result, ip_address, description, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                actor_user_id,
                actor_bale_id,
                action,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                before_json,
                after_json,
                result,
                ip_address,
                description,
                now_iso(),
            ),
        )

    def search(
        self,
        action: Optional[str] = None,
        entity_type: Optional[str] = None,
        actor_user_id: Optional[int] = None,
        result: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if action:
            clauses.append("a.action = ?")
            params.append(action)
        if entity_type:
            clauses.append("a.entity_type = ?")
            params.append(entity_type)
        if actor_user_id is not None:
            clauses.append("a.actor_user_id = ?")
            params.append(actor_user_id)
        if result:
            clauses.append("a.result = ?")
            params.append(result)
        if date_from:
            clauses.append("substr(a.created_at, 1, 10) >= ?")
            params.append(date_from[:10])
        if date_to:
            clauses.append("substr(a.created_at, 1, 10) <= ?")
            params.append(date_to[:10])
        params.append(max(1, min(limit, 1000)))
        sql = f"""
            SELECT a.*, u.full_name AS actor_name, u.role AS actor_role
            FROM audit_logs a
            LEFT JOIN users u ON u.id = a.actor_user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.id DESC
            LIMIT ?;
        """
        return self.db.fetchall(sql, params)
