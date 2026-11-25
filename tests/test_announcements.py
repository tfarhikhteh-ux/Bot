from __future__ import annotations

import asyncio


def test_announcements_queue_partial_failure_and_retry(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    t1, t2 = actors["teacher1"], actors["teacher2"]

    ann = svc.notifications.create_announcement(
        actor=mgr,
        title="جلسه شورای دبیران",
        body="جلسه روز دوشنبه ساعت ۱۲ برگزار می‌شود.",
        target_type="TEACHERS",
    )
    assert ann["status"] == "SENDING"

    delivered_to = []

    async def flaky_sender(chat_id: int, text: str):
        if chat_id == t2["bale_user_id"]:
            raise RuntimeError("شبیه‌سازی خطای شبکه برای یک کاربر")
        delivered_to.append(chat_id)

    res1 = asyncio.run(svc.notifications.dispatch_queue_batch(flaky_sender, batch_size=10, rate_delay_seconds=0.0))
    assert res1["sent"] >= 1
    assert res1["failed"] == 1
    assert t1["bale_user_id"] in delivered_to
    # خطای یک کاربر مانع ارسال به سایر دبیران نشد

    # شبیه‌سازی اتمام تمام تلاش‌ها و سپس تلاش مجدد دستی
    svc.db.execute("UPDATE message_queue SET status = 'FAILED', attempts = 3 WHERE recipient_chat_id = ?;", (t2["bale_user_id"],))
    svc.notifications.announcements.refresh_announcement_status(ann["id"])
    assert svc.notifications.announcements.get_by_id(ann["id"])["status"] == "PARTIAL"

    retried = svc.notifications.announcements.retry_failed_for_announcement(ann["id"])
    assert retried == 1

    async def healthy_sender(chat_id: int, text: str):
        delivered_to.append(chat_id)

    res2 = asyncio.run(svc.notifications.dispatch_queue_batch(healthy_sender, batch_size=10, rate_delay_seconds=0.0))
    assert res2["sent"] == 1
    assert svc.notifications.announcements.get_by_id(ann["id"])["status"] == "SENT"
