from __future__ import annotations

import io

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, Message

from keyboards.admin import BTN_REPORTS_MGMT
from keyboards.manager import reports_mgmt_inline
from utils.helpers import safe_answer_callback, send_chunked_message

from . import BotContext, cb_prefix


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & equals(BTN_REPORTS_MGMT))
    async def reports_menu(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "VIEW_REPORTS_ALL", "REPORT")
            await message.reply("📈 سامانه گزارش‌های مدیریتی و خروجی‌های رسمی (اکسل XLSX / CSV / PDF):", reply_markup=reports_mgmt_inline())

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("rep:"))
    async def report_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "VIEW_REPORTS_ALL", "REPORT")
            parts = callback_query.data.split(":")
            action = parts[1]
            chat_id = callback_query.message.chat.id

            if action == "overview":
                ov = ctx.services.reports.get_dashboard_overview(actor)
                s = ov["attendance_summary"]
                text = (
                    "📊 گزارش جامع مدیریتی مدرسه\n"
                    f"{'━' * 24}\n"
                    f"👥 کل کاربران فعال: {ov['total_users']}\n"
                    f"🎓 دانش‌آموزان: {ov['students_count']} | 👨‍🏫 دبیران: {ov['teachers_count']} | 👪 والدین: {ov['parents_count']}\n"
                    f"🏫 کلاس‌های فعال: {ov['classes_count']} | 📥 ثبت‌نام در انتظار: {ov['pending_registrations']}\n\n"
                    f"📋 آمار کل حضور و غیاب:\n"
                    f"   • حاضر: {s['PRESENT']} | غایب: {s['ABSENT']} | تأخیر: {s['LATE']}\n"
                    f"   • موجه: {s['EXCUSED_ABSENT']} | غیرموجه: {s['UNEXCUSED_ABSENT']}"
                )
                await safe_answer_callback(callback_query, "خلاصه آمار آماده شد.")
                await send_chunked_message(client, chat_id, text)
            elif action == "academic":
                ov = ctx.services.reports.get_dashboard_overview(actor)
                lines = ["🏆 معدل وزنی کلاس‌ها:"]
                for c in ov["class_gpas"]:
                    lines.append(f"• کلاس {c['class_name']} (پایه {c['grade_level']}): معدل {c['gpa']:.2f} ({c['student_count']} دانش‌آموز)")
                lines.append("\n👨‍🏫 عملکرد ثبت دبیران:")
                for t in ov["teacher_performance"]:
                    lines.append(
                        f"• {t['full_name']}: {t['assigned_courses']} درس | "
                        f"{t['attendance_recorded']} ثبت حضور | {t['grades_recorded']} ثبت نمره"
                    )
                await safe_answer_callback(callback_query, "گزارش عملکرد آموزشی")
                await send_chunked_message(client, chat_id, "\n".join(lines))
            elif action in {"xlsx", "csv", "pdf"}:
                cat = parts[2]
                file_bytes = ctx.services.reports.export_report(actor, category=cat, fmt=action)
                bio = io.BytesIO(file_bytes)
                bio.name = f"school_report_{cat.lower()}.{action}"
                await safe_answer_callback(callback_query, f"فایل {action.upper()} آماده شد.")
                await client.send_document(chat_id, bio, caption=f"📄 خروجی رسمی گزارش {cat} ({action.upper()})")
            elif action == "changes":
                role_ch = ctx.services.reports.get_change_reports(actor, "ROLE_CHANGES", limit=5)
                grd_ch = ctx.services.reports.get_change_reports(actor, "GRADE_CHANGES", limit=5)
                tt_ch = ctx.services.reports.get_change_reports(actor, "TIMETABLE_CHANGES", limit=5)
                lines = [
                    "🕵️ خلاصه آخرین تغییرات حساس سامانه:",
                    f"▫️ تغییرات نقش اخیر: {len(role_ch)} مورد",
                ]
                for r in role_ch:
                    lines.append(f"   • {r.get('description') or r['action']}")
                lines.append(f"▫️ تغییرات نمره اخیر: {len(grd_ch)} مورد")
                for g in grd_ch:
                    lines.append(f"   • [{g['action']}] {g['student_name']} ({g['subject_name']}): {g['old_score']} -> {g['new_score']}")
                lines.append(f"▫️ تغییرات برنامه هفتگی اخیر: {len(tt_ch)} مورد")
                for t in tt_ch:
                    lines.append(f"   • [{t['action']}] کلاس {t.get('class_name') or t['class_id']}: {t.get('new_value') or 'پاک‌شدن'}")
                await safe_answer_callback(callback_query, "گزارش تغییرات آماده شد.")
                await send_chunked_message(client, chat_id, "\n".join(lines))

        await ctx.run_safe_callback(client, callback_query, _run)
