from __future__ import annotations

import asyncio
import json as json_lib
import logging
from typing import Any, Optional

import httpx
from balethon import Client
from balethon.dispatcher import Chain
from balethon.errors import NotFoundError, RPCError, TooManyRequestsError, UnauthorizedError
from balethon.network import Connection
from balethon.objects import CallbackQuery, Message

from .logging_config import redact_sensitive_text

log = logging.getLogger("school_bot.bale")


class TransientGatewayError(RuntimeError):
    """خطای موقت شبکه یا پاسخ نامعتبر سرور بله که قابل تلاش مجدد کنترل‌شده است."""


class FatalBotAuthError(RuntimeError):
    """خطای قطعی احراز هویت یا توکن نامعتبر که نباید پنهان یا بی‌نهایت تکرار شود."""


class SafeLoggingChain(Chain):
    """جایگزین PrintingChain پیش‌فرض Balethon برای ثبت امن خطاها بدون افشای جزئیات حساس."""

    def __init__(self, name: str = "safe_logging", condition=None) -> None:
        super().__init__(name, condition)

    @Chain.error_handler()
    async def handle_error(self, client, error: Exception) -> None:
        safe_msg = redact_sensitive_text(str(error))
        if isinstance(error, (TransientGatewayError, httpx.ConnectError, httpx.TimeoutException)):
            log.warning("اختلال موقت ارتباط با سرور بله: %s", safe_msg)
        else:
            log.error("خطای ثبت‌شده در چرخه پردازش رویداد ربات: %s", safe_msg, exc_info=True)


class SafeBaleConnection(Connection):
    """اتصال مقاوم به API بله با مدیریت JSONDecodeError، خطاهای شبکه و حذف توکن از لاگ."""

    async def request(
        self,
        method: str,
        service: str,
        data: dict = None,
        json: dict = None,
        files: dict = None,
    ):
        try:
            response = await self.client.request(
                method,
                f"{self.bot_url()}/{service}",
                data=data,
                files=files,
                json=json,
                timeout=self.time_out,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise TransientGatewayError(f"خطای ارتباط شبکه در فراخوانی {service}") from exc

        if response.status_code in {502, 503, 504}:
            raise TransientGatewayError(f"پاسخ موقت نامعتبر درگاه بله (کد {response.status_code}) در {service}")

        if not response.content or not response.text.strip():
            if response.status_code in {401, 403}:
                raise UnauthorizedError(response.status_code, "توکن ربات نامعتبر یا غیرمجاز است.", service, {})
            raise TransientGatewayError(f"پاسخ خالی از سرور بله در {service} (کد {response.status_code})")

        try:
            response_json = response.json()
        except (ValueError, json_lib.JSONDecodeError) as exc:
            if response.status_code == 401:
                raise UnauthorizedError(401, "توکن ربات بله نامعتبر است.", service, {}) from exc
            if response.status_code == 404 and service == "getMe":
                raise FatalBotAuthError("توکن ربات بله توسط سرور بله شناسایی نشد (404 در getMe).") from exc
            raise TransientGatewayError(
                f"پاسخ غیر JSON از سرور بله در {service} (کد وضعیت {response.status_code})"
            ) from exc

        if not isinstance(response_json, dict):
            raise TransientGatewayError(f"ساختار JSON غیرمنتظره در پاسخ {service}")

        if response.status_code != 200 or response_json.get("ok") is False:
            code = response_json.get("error_code") or response.status_code
            params = response_json.get("parameters") or {}
            raise RPCError.create(code, response_json.get("description"), service, params)

        return response_json.get("result")

    async def download_file(self, file_id: str) -> bytes:
        file_path = file_id
        if "/" not in file_id:
            info = await self.request("post", "getFile", json={"file_id": file_id})
            if isinstance(info, dict) and info.get("file_path"):
                file_path = str(info["file_path"])
        try:
            response = await self.client.get(self.file_url(file_path), timeout=self.time_out)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientGatewayError("خطای شبکه هنگام دریافت فایل از بله") from exc
        if response.status_code != 200:
            try:
                payload = response.json()
                desc = payload.get("description")
            except Exception:
                desc = f"HTTP {response.status_code}"
            raise RPCError.create(response.status_code, desc, "downloadFile", {})
        return response.read()


DEFAULT_BALE_COMMANDS = [
    {"command": "start", "description": "شروع و نمایش منوی اصلی سامانه مدرسه"},
    {"command": "help", "description": "فهرست کامل دستورات مجاز نقش کاربری شما"},
    {"command": "card", "description": "مشاهده کارنامه آنلاین ۲۴ ساعته و لینک وب"},
    {"command": "timetable", "description": "مشاهده برنامه هفتگی کلاس یا تدریس"},
    {"command": "locks", "description": "پنل مدیریت قفل گیف و رسانه در گروه"},
    {"command": "calendar", "description": "تقویم آموزشی و تعطیلات رسمی مدرسه"},
]


class SafeSchoolClient(Client):
    """کلاینت ایمن و پایدار Balethon با مدیریت خطای احراز هویت و بازگشت خودکار از قطعی شبکه."""

    def __init__(
        self,
        token: str,
        time_out: int = 25,
        proxy: Optional[str] = None,
        base_url: str = "https://tapi.bale.ai",
    ) -> None:
        super().__init__(
            token=token,
            time_out=time_out,
            proxy=proxy,
            base_url=base_url,
        )
        # جایگزینی زنجیره چاپ خام با زنجیره لاگ امن و جایگزینی Connection مقاوم
        self.chains = [SafeLoggingChain()]
        self.connection = SafeBaleConnection(
            token=token,
            time_out=time_out,
            proxy=proxy,
            base_url=base_url,
        )

    async def initialize(self) -> None:
        self.dispatcher.event_loop = asyncio.get_running_loop()
        self.dispatcher.events_queue = asyncio.Queue()
        await super().initialize()

    async def connect_resilient(self, max_startup_retries: int = 5) -> None:
        await self.connection.start()
        attempt = 0
        while True:
            try:
                self.user = await self.get_me()
                return
            except (UnauthorizedError, FatalBotAuthError) as exc:
                await self.connection.stop()
                raise FatalBotAuthError("احراز هویت ربات بله ناموفق بود. لطفاً BALE_BOT_TOKEN را بررسی کنید.") from exc
            except NotFoundError as exc:
                await self.connection.stop()
                raise FatalBotAuthError("توکن ربات در سرور بله یافت نشد (NotFoundError در getMe).") from exc
            except (TransientGatewayError, RPCError) as exc:
                attempt += 1
                if attempt >= max_startup_retries:
                    await self.connection.stop()
                    raise
                delay = min(2 ** attempt, 20)
                log.warning("تلاش مجدد اتصال اولیه ربات پس از %s ثانیه (%s)", delay, redact_sensitive_text(str(exc)))
                await asyncio.sleep(delay)

    async def set_my_commands(self, commands: Optional[list[dict]] = None) -> Any:
        """ثبت فهرست فرمان‌های پیشنهادی بازو در بله از طریق متد رسمی setMyCommands."""
        cmds = commands if commands is not None else DEFAULT_BALE_COMMANDS
        return await self.execute("post", "setMyCommands", json=True, commands=cmds)

    async def poll_forever(self, stop_event: Optional[asyncio.Event] = None) -> None:
        try:
            await self.delete_webhook()
        except (UnauthorizedError, FatalBotAuthError):
            raise
        except Exception as exc:
            log.warning("حذف وب‌هوک قبلی با هشدار مواجه شد: %s", redact_sensitive_text(str(exc)))

        try:
            await self.set_my_commands(DEFAULT_BALE_COMMANDS)
        except Exception as exc:
            log.debug("ثبت خودکار setMyCommands انجام نشد: %s", redact_sensitive_text(str(exc)))

        await self.initialize()
        last_update_id: Optional[int] = None
        backoff = 1.0

        try:
            while stop_event is None or not stop_event.is_set():
                try:
                    if last_update_id is None:
                        updates = await self.get_updates(timeout=10)
                    else:
                        updates = await self.get_updates(offset=last_update_id + 1, timeout=10)
                    backoff = 1.0
                except (UnauthorizedError, FatalBotAuthError) as exc:
                    raise FatalBotAuthError("توکن ربات در حین دریافت پیام‌ها نامعتبر اعلام شد.") from exc
                except TooManyRequestsError as exc:
                    wait_s = max(float(getattr(exc, "seconds", 2) or 2), 1.0)
                    await asyncio.sleep(min(wait_s, 30.0))
                    continue
                except (TransientGatewayError, httpx.ConnectError, httpx.TimeoutException) as exc:
                    log.warning("وقفه موقت دریافت آپدیت‌ها (%s)؛ تلاش مجدد تا %.1f ثانیه دیگر.", redact_sensitive_text(str(exc)), backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, 30.0)
                    continue
                except Exception as exc:
                    await self.dispatcher.dispatch_event(self, exc)
                    await asyncio.sleep(min(backoff, 10.0))
                    backoff = min(backoff * 1.5, 30.0)
                    continue

                for update in updates or []:
                    if last_update_id is not None and last_update_id >= update.id:
                        continue
                    last_update_id = update.id
                    effective = update.get_effective_update()
                    if effective is not None:
                        await self.dispatcher.dispatch_event(self, effective)
        finally:
            if self.dispatcher.is_started:
                await self.shutdown()


async def safe_answer_callback(callback_query: CallbackQuery, text: str = "انجام شد", show_alert: bool = False) -> None:
    """پاسخ ایمن به CallbackQuery مطابق مستندات بله (سقف ۲۰۰ کاراکتر + پشتیبانی کلاینت‌های قدیمی با شناسه شروع‌شونده با 1)."""
    clean_text = (text or " ")[:195]
    cb_id = str(getattr(callback_query, "id", "") or "")
    if show_alert and cb_id.startswith("1") and getattr(callback_query, "message", None) is not None:
        try:
            await callback_query.message.reply(clean_text)
        except Exception:
            pass
    try:
        await callback_query.answer(text=clean_text, show_alert=show_alert)
    except Exception as exc:
        log.debug("خطای غیربحرانی در پاسخ به callback_query: %s", redact_sensitive_text(str(exc)))


async def send_chunked_message(
    client: Client,
    chat_id: int | str,
    text: str,
    reply_markup: Any = None,
    max_len: int = 3800,
) -> Optional[Message]:
    """ارسال پیام‌های طولانی در چند بخش منظم برای جلوگیری از خطای محدودیت طول پیام بله."""
    content = (text or "").strip() or "-"
    if len(content) <= max_len:
        return await client.send_message(chat_id, content, reply_markup=reply_markup)

    chunks = []
    current = []
    current_len = 0
    for line in content.splitlines(keepends=True):
        if current_len + len(line) > max_len and current:
            chunks.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        chunks.append("".join(current))

    last_msg = None
    for idx, part in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        last_msg = await client.send_message(chat_id, part, reply_markup=markup)
    return last_msg
