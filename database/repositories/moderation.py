from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso

LOCK_COLUMNS = (
    "lock_all",
    "lock_gif",
    "lock_sticker",
    "lock_photo",
    "lock_video",
    "lock_voice",
    "lock_audio",
    "lock_document",
    "lock_link",
    "lock_forward",
    "lock_contact",
    "lock_location",
)


class GroupLockRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_locks(self, chat_id: int) -> Dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM group_locks WHERE chat_id = ?;", (int(chat_id),))
        if row:
            return dict(row)
        base: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "chat_title": None,
            "class_id": None,
            "updated_by": None,
            "updated_at": None,
        }
        for col in LOCK_COLUMNS:
            base[col] = 0
        return base

    def upsert_locks(
        self,
        chat_id: int,
        chat_title: Optional[str] = None,
        class_id: Optional[int] = None,
        clear_class_id: bool = False,
        updated_by: Optional[int] = None,
        **flags: int,
    ) -> Dict[str, Any]:
        current = self.get_locks(int(chat_id))
        merged = {col: int(bool(flags[col])) if col in flags else int(current.get(col, 0)) for col in LOCK_COLUMNS}
        title = chat_title if chat_title is not None else current.get("chat_title")
        if clear_class_id:
            final_class_id = None
        elif class_id is not None:
            final_class_id = int(class_id)
        else:
            final_class_id = current.get("class_id")
        ts = now_iso()

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO group_locks(
                    chat_id, chat_title, class_id,
                    lock_all, lock_gif, lock_sticker, lock_photo, lock_video,
                    lock_voice, lock_audio, lock_document, lock_link, lock_forward,
                    lock_contact, lock_location, updated_by, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_title = COALESCE(excluded.chat_title, group_locks.chat_title),
                    class_id = excluded.class_id,
                    lock_all = excluded.lock_all,
                    lock_gif = excluded.lock_gif,
                    lock_sticker = excluded.lock_sticker,
                    lock_photo = excluded.lock_photo,
                    lock_video = excluded.lock_video,
                    lock_voice = excluded.lock_voice,
                    lock_audio = excluded.lock_audio,
                    lock_document = excluded.lock_document,
                    lock_link = excluded.lock_link,
                    lock_forward = excluded.lock_forward,
                    lock_contact = excluded.lock_contact,
                    lock_location = excluded.lock_location,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at;
                """,
                (
                    int(chat_id),
                    title,
                    final_class_id,
                    merged["lock_all"],
                    merged["lock_gif"],
                    merged["lock_sticker"],
                    merged["lock_photo"],
                    merged["lock_video"],
                    merged["lock_voice"],
                    merged["lock_audio"],
                    merged["lock_document"],
                    merged["lock_link"],
                    merged["lock_forward"],
                    merged["lock_contact"],
                    merged["lock_location"],
                    updated_by,
                    ts,
                ),
            )
        return self.get_locks(int(chat_id))

    def list_all_group_locks(self) -> List[Dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM group_locks ORDER BY updated_at DESC;")
        return [dict(r) for r in rows]
