import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Set, Tuple

from telethon import Button, TelegramClient, events
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    InviteHashExpiredError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import Channel, Message, User


# ================= CONFIG =================
API_ID = 20784926
API_HASH = "2884cd0ca1ab0bbdef307767e2e2f1d0"
BOT_TOKEN = "8292152730:AAEJOCpGqXG6U6xxV6qVyIMER0FbgYZiLLo"

ADMIN_ID = 8674344477

USER_SESSION = os.getenv("TG_USER_SESSION", "user_parser")
BOT_SESSION = os.getenv("TG_BOT_SESSION", "bot_controller")

MIN_SUBS = 300
MAX_SUBS = 7000

# Небольшая пауза между запросами. Слишком большое значение резко режет скорость.
DELAY = 0.1
RESOLVE_COOLDOWN_SECONDS = 1.0

proxy = {
    "proxy_type": "http",
    "addr": "168.81.67.74",
    "port": 8000,
    "username": "c3M0j0",
    "password": "cHjdAE",
}

CALLBACK_PARSE = b"parse_yes:"
CALLBACK_SKIP = b"parse_no:"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


# =========================
# Logging
# =========================
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("telegram_parser")


# =========================
# Models
# =========================
@dataclass
class FoundChannel:
    username: str
    url: str
    subs: int
    source: str
    profile_url: Optional[str] = None


@dataclass
class SessionState:
    owner_user_id: Optional[int] = None
    running: bool = False
    started_at: float = 0.0

    message_count: int = 0
    found_count: int = 0

    channel_limit: int = 1000
    chat_limit: int = 1000

    queue: Deque[str] = field(default_factory=deque)
    approved_queue: Deque[str] = field(default_factory=deque)

    seen_channels: Set[str] = field(default_factory=set)
    visited_entities: Set[int] = field(default_factory=set)
    visited_profiles: Set[int] = field(default_factory=set)

    found_channels: Dict[str, FoundChannel] = field(default_factory=dict)

    pending_approval: Set[str] = field(default_factory=set)

    last_resolve_ts: float = 0.0

    def reset_runtime(self) -> None:
        self.running = False
        self.started_at = 0.0
        self.message_count = 0
        self.found_count = 0
        self.queue.clear()
        self.approved_queue.clear()
        self.seen_channels.clear()
        self.visited_entities.clear()
        self.visited_profiles.clear()
        self.found_channels.clear()
        self.pending_approval.clear()
        self.last_resolve_ts = 0.0


# =========================
# Parser
# =========================
class TelegramParserSystem:
    URL_PATTERN = re.compile(r"https?://t\.me/([A-Za-z0-9_+/]+)", re.IGNORECASE)
    MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_]{5,})")

    def __init__(self, user_client: TelegramClient, bot_client: TelegramClient):
        self.user_client = user_client
        self.bot_client = bot_client
        self.state = SessionState()

    # ---------- Utility ----------
    async def _antiban_delay(self) -> None:
        await asyncio.sleep(DELAY)

    async def _resolve_cooldown(self) -> None:
        now = time.time()
        delta = now - self.state.last_resolve_ts
        if delta < RESOLVE_COOLDOWN_SECONDS:
            await asyncio.sleep(RESOLVE_COOLDOWN_SECONDS - delta)
        self.state.last_resolve_ts = time.time()

    @staticmethod
    def _normalize_username(raw: str) -> str:
        value = raw.strip()
        if value.startswith("https://t.me/"):
            value = value.split("https://t.me/", 1)[1]
        if value.startswith("http://t.me/"):
            value = value.split("http://t.me/", 1)[1]
        if value.startswith("@"):
            value = value[1:]
        value = value.split("/")[0].split("?")[0].strip()
        return value.lower()

    @staticmethod
    def _is_trash_username(username: str) -> bool:
        return ("bot" in username.lower()) or (len(username) < 5)

    def _extract_candidates(self, text: Optional[str]) -> Set[str]:
        if not text:
            return set()
        out: Set[str] = set()
        for match in self.URL_PATTERN.findall(text):
            out.add(self._normalize_username(match))
        for mention in self.MENTION_PATTERN.findall(text):
            out.add(self._normalize_username(mention))
        return out

    async def _safe_resolve_entity(self, username: str):
        logger.info("RESOLVE username=%s", username)
        try:
            await self._resolve_cooldown()
            entity = await self.user_client.get_entity(username)
            return entity
        except FloodWaitError as e:
            logger.warning("FLOOD WAIT on resolve username=%s seconds=%s", username, e.seconds)
            if e.seconds < 60:
                await asyncio.sleep(e.seconds)
                return await self._safe_resolve_entity(username)
            return None
        except (UsernameInvalidError, UsernameNotOccupiedError, InviteHashExpiredError):
            logger.info("QUEUE SKIP invalid username=%s", username)
            return None
        except Exception as e:
            logger.exception("Resolve failed username=%s err=%s", username, e)
            return None

    async def _safe_get_subs(self, entity) -> int:
        try:
            full = await self.user_client(GetFullChannelRequest(entity))
            subs = int(getattr(full.full_chat, "participants_count", 0) or 0)
            logger.info("CHANNEL subs username=%s subs=%s", getattr(entity, "username", None), subs)
            return subs
        except FloodWaitError as e:
            logger.warning("FLOOD WAIT on subs seconds=%s", e.seconds)
            if e.seconds < 60:
                await asyncio.sleep(e.seconds)
                return await self._safe_get_subs(entity)
            return 0
        except ChannelPrivateError:
            logger.info("CHANNEL SKIPPED private username=%s", getattr(entity, "username", None))
            return 0
        except Exception as e:
            logger.exception("Failed get subs: %s", e)
            return 0

    async def _send_found_channel(
        self,
        chat_id: int,
        username: str,
        subs: int,
        source: str,
        profile_url: Optional[str] = None,
    ) -> None:
        url = f"https://t.me/{username}"
        if profile_url:
            text = (
                "🔥 КАНАЛ ИЗ ПРОФИЛЯ\n\n"
                f"Канал: {url}\n"
                f"Подписчики: {subs}\n"
                f"Профиль: {profile_url}\n"
                f"Источник: {source}"
            )
        else:
            text = (
                "🔥 НАЙДЕН КАНАЛ\n\n"
                f"Ссылка: {url}\n"
                f"Подписчики: {subs}\n"
                f"Источник: {source}"
            )

        await self.bot_client.send_message(
            chat_id,
            text,
            buttons=[
                [
                    Button.inline("✅ Парсить потом", CALLBACK_PARSE + username.encode()),
                    Button.inline("❌ Не парсить", CALLBACK_SKIP + username.encode()),
                ]
            ],
        )

    def _queue_add(self, username: str, approved: bool = False) -> None:
        if username in self.state.seen_channels:
            logger.info("QUEUE SKIP username=%s reason=seen", username)
            return
        logger.info("QUEUE ADD username=%s approved=%s", username, approved)
        self.state.seen_channels.add(username)
        if approved:
            self.state.approved_queue.append(username)
        else:
            self.state.queue.append(username)

    async def _process_candidate(
        self,
        owner_chat: int,
        username: str,
        source: str,
        profile_url: Optional[str] = None,
    ) -> None:
        username = self._normalize_username(username)
        if self._is_trash_username(username):
            logger.info("CHANNEL SKIPPED username=%s reason=trash_filter", username)
            return

        entity = await self._safe_resolve_entity(username)
        if not entity:
            return

        if not isinstance(entity, Channel):
            return

        subs = await self._safe_get_subs(entity)
        channel_url = f"https://t.me/{username}"

        self._queue_add(username, approved=False)

        if username not in self.state.found_channels:
            self.state.found_channels[username] = FoundChannel(
                username=username,
                url=channel_url,
                subs=subs,
                source=source,
                profile_url=profile_url,
            )

        if MIN_SUBS <= subs <= MAX_SUBS:
            self.state.found_count += 1
            self.state.pending_approval.add(username)
            await self._send_found_channel(
                owner_chat,
                username,
                subs,
                source,
                profile_url=profile_url,
            )
        else:
            logger.info(
                "CHANNEL SKIPPED username=%s reason=subs_filter_only_for_send subs=%s",
                username,
                subs,
            )

    async def _parse_profile(self, owner_chat: int, user: User) -> None:
        if not user or not user.id:
            return
        if user.id in self.state.visited_profiles:
            return

        self.state.visited_profiles.add(user.id)
        logger.info("ENTITY id=%s depth=profile", user.id)

        profile_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"

        try:
            full_user = await self.user_client(GetFullUserRequest(user))
        except FloodWaitError as e:
            logger.warning("FLOOD WAIT on profile user_id=%s seconds=%s", user.id, e.seconds)
            if e.seconds < 60:
                await asyncio.sleep(e.seconds)
                return await self._parse_profile(owner_chat, user)
            return
        except Exception as e:
            logger.exception("Failed profile user_id=%s err=%s", user.id, e)
            return

        bio = (getattr(full_user.full_user, "about", None) or "").strip()
        for candidate in self._extract_candidates(bio):
            logger.info("BIO LINK FOUND user_id=%s username=%s", user.id, candidate)
            await self._process_candidate(owner_chat, candidate, source="bio", profile_url=profile_url)
            await self._antiban_delay()

        personal_channel_id = getattr(full_user.full_user, "personal_channel_id", None)
        if personal_channel_id:
            logger.info("PROFILE HAS ATTACHED CHANNEL user_id=%s channel_id=%s", user.id, personal_channel_id)
            try:
                entity = await self.user_client.get_entity(personal_channel_id)
                if isinstance(entity, Channel) and entity.username:
                    await self._process_candidate(
                        owner_chat,
                        entity.username,
                        source="attached",
                        profile_url=profile_url,
                    )
            except Exception as e:
                logger.exception("Attached channel resolve failed user_id=%s err=%s", user.id, e)

    async def _parse_messages(self, owner_chat: int, entity, limit: int, source: str) -> None:
        async for msg in self.user_client.iter_messages(entity, limit=limit):
            if not self.state.running:
                return
            if not isinstance(msg, Message):
                continue

            self.state.message_count += 1
            text = msg.raw_text or ""

            for candidate in self._extract_candidates(text):
                await self._process_candidate(owner_chat, candidate, source=source)
                await self._antiban_delay()

            sender = await msg.get_sender()
            if isinstance(sender, Channel) and sender.username:
                await self._process_candidate(owner_chat, sender.username, source="comment")
            elif isinstance(sender, User):
                await self._parse_profile(owner_chat, sender)

            await self._antiban_delay()

    async def _parse_channel(self, owner_chat: int, username: str) -> None:
        entity = await self._safe_resolve_entity(username)
        if not entity:
            return

        ent_id = getattr(entity, "id", None)
        if ent_id and ent_id in self.state.visited_entities:
            logger.info("ENTITY id=%s depth=channel skipped=visited", ent_id)
            return

        if ent_id:
            self.state.visited_entities.add(ent_id)
        logger.info("ENTITY id=%s depth=channel", ent_id)

        await self._parse_messages(owner_chat, entity, self.state.channel_limit, source="message")

        linked_chat_id = getattr(entity, "linked_chat_id", None)
        if linked_chat_id:
            try:
                linked = await self.user_client.get_entity(linked_chat_id)
                linked_id = getattr(linked, "id", None)
                if linked_id and linked_id not in self.state.visited_entities:
                    self.state.visited_entities.add(linked_id)
                    logger.info("ENTITY id=%s depth=chat", linked_id)
                    await self._parse_messages(owner_chat, linked, self.state.chat_limit, source="comment")
            except Exception as e:
                logger.exception("Failed linked chat parsing username=%s err=%s", username, e)

    async def run(self, owner_chat: int) -> None:
        self.state.running = True
        self.state.started_at = time.time()

        await self.bot_client.send_message(owner_chat, "🚀 Парсинг запущен. Этап 1: стартовые каналы.")

        while self.state.queue and self.state.running:
            username = self.state.queue.pop()  # DFS
            await self._parse_channel(owner_chat, username)

        if not self.state.running:
            return

        await self.bot_client.send_message(
            owner_chat,
            "✅ Этап 1 завершён.\n"
            "Теперь доступны каналы, отмеченные кнопкой '✅ Парсить потом'.\n"
            "Если хотите продолжить — нажимайте на кнопки у найденных каналов.",
        )

        while self.state.approved_queue and self.state.running:
            username = self.state.approved_queue.pop()  # DFS for approved
            await self._parse_channel(owner_chat, username)

        await self.finish(owner_chat)

    async def run_approved_only(self, owner_chat: int) -> None:
        self.state.running = True
        if not self.state.started_at:
            self.state.started_at = time.time()

        await self.bot_client.send_message(owner_chat, "🚀 Этап 2 запущен: парсинг одобренных каналов.")
        while self.state.approved_queue and self.state.running:
            username = self.state.approved_queue.pop()  # DFS
            await self._parse_channel(owner_chat, username)
        await self.finish(owner_chat)

    async def stop(self, owner_chat: int) -> None:
        self.state.running = False
        await self.bot_client.send_message(owner_chat, "⛔ Остановлено пользователем.")

    async def progress_text(self) -> str:
        elapsed = int(time.time() - self.state.started_at) if self.state.started_at else 0
        return (
            f"⏱ Время: {elapsed} сек\n"
            f"📨 Сообщений: {self.state.message_count}\n"
            f"🔥 Каналов: {self.state.found_count}"
        )

    async def finish(self, owner_chat: int) -> None:
        self.state.running = False

        payload = {
            "processed": self.state.message_count,
            "found": self.state.found_count,
            "channels": [
                {
                    "username": c.username,
                    "url": c.url,
                    "subs": c.subs,
                    "source": c.source,
                    "profile_url": c.profile_url,
                }
                for c in self.state.found_channels.values()
            ],
        }

        result_file = "parser3_results.json"
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        await self.bot_client.send_message(owner_chat, "🏁 Парсинг завершён.")
        await self.bot_client.send_message(owner_chat, await self.progress_text())
        await self.bot_client.send_file(owner_chat, result_file, caption="📦 JSON результат")


# =========================
# Input parsing
# =========================
def parse_user_payload(text: str) -> Tuple[int, int, List[str]]:
    channel_limit = 1000
    chat_limit = 1000
    channels: List[str] = []

    for line in [x.strip() for x in text.splitlines() if x.strip()]:
        if line.startswith("channel_limit="):
            channel_limit = int(line.split("=", 1)[1].strip())
            continue
        if line.startswith("chat_limit="):
            chat_limit = int(line.split("=", 1)[1].strip())
            continue

        normalized = TelegramParserSystem._normalize_username(line)
        if normalized:
            channels.append(normalized)

    return channel_limit, chat_limit, channels


# =========================
# Main bot wiring
# =========================
async def main() -> None:
    user_client = TelegramClient(USER_SESSION, API_ID, API_HASH, proxy=proxy)
    bot_client = TelegramClient(BOT_SESSION, API_ID, API_HASH, proxy=proxy)

    await user_client.start()
    await bot_client.start(bot_token=BOT_TOKEN)

    system = TelegramParserSystem(user_client=user_client, bot_client=bot_client)

    @bot_client.on(events.NewMessage(pattern=r"/start"))
    async def on_start(event):
        if event.sender_id != ADMIN_ID:
            return
        system.state.owner_user_id = event.sender_id
        await event.respond(
            "🤖 Telegram Parser готов.\n"
            "Отправьте данные:\n"
            "channel_limit=1000\n"
            "chat_limit=1000\n"
            "https://t.me/channel1\n"
            "https://t.me/channel2\n\n"
            "Команды:\n"
            "🚀 Старт\n"
            "⛔ Стоп\n"
            "📊 Прогресс"
        )

    @bot_client.on(events.NewMessage(pattern=r"📊 Прогресс"))
    async def on_progress(event):
        if event.sender_id != ADMIN_ID:
            return
        await event.respond(await system.progress_text())

    @bot_client.on(events.NewMessage(pattern=r"⛔ Стоп"))
    async def on_stop(event):
        if event.sender_id != ADMIN_ID:
            return
        await system.stop(event.chat_id)

    @bot_client.on(events.NewMessage(pattern=r"🚀 Старт"))
    async def on_run(event):
        if event.sender_id != ADMIN_ID:
            return
        if system.state.running:
            await event.respond("⚠️ Уже запущено.")
            return
        if not system.state.queue and not system.state.approved_queue:
            await event.respond("⚠️ Сначала отправьте входные каналы текстом.")
            return
        if system.state.queue:
            asyncio.create_task(system.run(event.chat_id))
        else:
            asyncio.create_task(system.run_approved_only(event.chat_id))

    @bot_client.on(events.NewMessage)
    async def on_payload(event):
        if event.sender_id != ADMIN_ID:
            return
        text = (event.raw_text or "").strip()
        if text.startswith("/") or text in {"🚀 Старт", "⛔ Стоп", "📊 Прогресс"}:
            return

        try:
            channel_limit, chat_limit, channels = parse_user_payload(text)
            if not channels:
                return

            system.state.channel_limit = channel_limit
            system.state.chat_limit = chat_limit

            for channel in channels:
                system._queue_add(channel, approved=False)

            await event.respond(
                "✅ Данные приняты.\n"
                f"channel_limit={channel_limit}, chat_limit={chat_limit}, каналов={len(channels)}\n"
                "Нажмите '🚀 Старт'."
            )
        except Exception as e:
            await event.respond(f"❌ Ошибка входных данных: {e}")

    @bot_client.on(events.CallbackQuery)
    async def on_callback(event):
        if event.sender_id != ADMIN_ID:
            return
        data = event.data or b""
        if data.startswith(CALLBACK_PARSE):
            username = data.split(b":", 1)[1].decode().strip().lower()
            if username in system.state.pending_approval:
                system._queue_add(username, approved=True)
                if not system.state.running and not system.state.queue:
                    asyncio.create_task(system.run_approved_only(event.chat_id))
            await event.answer("Добавлено в очередь этапа 2")
        elif data.startswith(CALLBACK_SKIP):
            username = data.split(b":", 1)[1].decode().strip().lower()
            system.state.pending_approval.discard(username)
            await event.answer("Пропущено")

    logger.info("Bot started")
    await bot_client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
