from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from database.repositories.audit import AuditRepository


class AuditService:
    def __init__(self, db: DatabaseManager) -> None:
        self.repo = AuditRepository(db)

    def log(
        self,
        action: str,
        entity_type: str,
        actor: Optional[Dict[str, Any]] = None,
        actor_bale_id: Optional[int] = None,
        entity_id: Optional[str | int] = None,
        before_data: Any = None,
        after_data: Any = None,
        result: str = "SUCCESS",
        ip_address: Optional[str] = None,
        description: Optional[str] = None,
    ) -> int:
        actor_id = actor["id"] if actor and "id" in actor else None
        bale_id = actor_bale_id or (actor.get("bale_user_id") if actor else None)
        # حذف هش رمز عبور در صورت وجود در داده‌های ثبت‌شده
        if isinstance(before_data, dict):
            before_data = {k: v for k, v in before_data.items() if k != "web_password_hash"}
        if isinstance(after_data, dict):
            after_data = {k: v for k, v in after_data.items() if k != "web_password_hash"}
        return self.repo.record(
            action=action,
            entity_type=entity_type,
            actor_user_id=actor_id,
            actor_bale_id=bale_id,
            entity_id=entity_id,
            before_data=before_data,
            after_data=after_data,
            result=result,
            ip_address=ip_address,
            description=description,
        )

    def search_logs(
        self,
        action: Optional[str] = None,
        entity_type: Optional[str] = None,
        actor_user_id: Optional[int] = None,
        result: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self.repo.search(
            action=action,
            entity_type=entity_type,
            actor_user_id=actor_user_id,
            result=result,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
