import asyncio
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
DELAY = 0.15
RESOLVE_COOLDOWN_SECONDS = 0.08
PROFILE_WORKERS = 18
PROFILE_BATCH_SIZE = 20

proxy = {
    "proxy_type": "http",
    "addr": "168.81.67.74",
    "port": 8000,
    "username": "c3M0j0",
    "password": "cHjdAE",
}

CALLBACK_PARSE = b"parse_yes:"
CALLBACK_SKIP = b"parse_no:"
CALLBACK_STAGE2_YES = b"stage2_yes"
CALLBACK_STAGE2_NO = b"stage2_no"

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
    channel_id: int
    username: str
    url: str
    subs: int
    source: str
    profile_url: str = ""


@dataclass
class SessionState:
    owner_user_id: Optional[int] = None
    running: bool = False
    started_at: float = 0.0

    message_count: int = 0
    found_count: int = 0
    profiles_checked: int = 0
    profiles_success: int = 0
    profiles_failed: int = 0
    unique_profiles_processed: int = 0
    duplicate_profiles_skipped: int = 0
    profiles_without_username_processed: int = 0
    current_stage: str = "IDLE"
    channel_processed_current: int = 0
    chat_processed_current: int = 0

    channel_limit: int = 1000
    chat_limit: int = 1000

    queue: Deque[str] = field(default_factory=deque)
    approved_queue: Deque[str] = field(default_factory=deque)

    visited_entities: Set[int] = field(default_factory=set)
    visited_profiles: Set[Tuple[int, Optional[int]]] = field(default_factory=set)

    found_channels_all: Dict[int, FoundChannel] = field(default_factory=dict)
    found_channels_filtered: Dict[int, FoundChannel] = field(default_factory=dict)
    processed_usernames: Set[str] = field(default_factory=set)
    queue_set: Set[str] = field(default_factory=set)
    queued_channel_ids: Set[int] = field(default_factory=set)

    pending_approval: Dict[int, str] = field(default_factory=dict)
    stage2_total_channels: int = 0
    stage2_processed_channels: int = 0

    last_resolve_ts: float = 0.0

    def reset_runtime(self) -> None:
        self.running = False
        self.started_at = 0.0
        self.message_count = 0
        self.found_count = 0
        self.profiles_checked = 0
        self.profiles_success = 0
        self.profiles_failed = 0
        self.unique_profiles_processed = 0
        self.duplicate_profiles_skipped = 0
        self.profiles_without_username_processed = 0
        self.current_stage = "IDLE"
        self.channel_processed_current = 0
        self.chat_processed_current = 0
        self.queue.clear()
        self.approved_queue.clear()
        self.visited_entities.clear()
        self.visited_profiles.clear()
        self.found_channels_all.clear()
        self.found_channels_filtered.clear()
        self.processed_usernames.clear()
        self.queue_set.clear()
        self.queued_channel_ids.clear()
        self.pending_approval.clear()
        self.stage2_total_channels = 0
        self.stage2_processed_channels = 0
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
        self.profile_semaphore = asyncio.Semaphore(PROFILE_WORKERS)
        self.inflight_profiles: Set[Tuple[int, Optional[int]]] = set()
        self.stop_requested = False
        self.pending_tasks: Set[asyncio.Task] = set()
        self.awaiting_stage2_confirmation = False

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
        lowered = username.lower()
        if lowered.startswith("+") or lowered.startswith("joinchat"):
            return True
        return "bot" in lowered

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
        except ValueError:
            logger.info("QUEUE SKIP unresolved username=%s", username)
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

    def _queue_add(self, username: str, approved: bool = False, channel_id: Optional[int] = None) -> None:
        normalized = self._normalize_username(username)
        if normalized in self.state.queue_set:
            return
        if channel_id and channel_id in self.state.queued_channel_ids:
            return
        self.state.queue_set.add(normalized)
        if channel_id:
            self.state.queued_channel_ids.add(channel_id)
        logger.info("QUEUE ADD username=%s approved=%s", username, approved)
        if approved:
            self.state.approved_queue.append(normalized)
        else:
            self.state.queue.append(normalized)

    async def _process_candidate(
        self,
        owner_chat: int,
        username: str,
        source: str,
        profile_url: Optional[str] = None,
    ) -> bool:
        username = self._normalize_username(username)

        if self._is_trash_username(username):
            return False
        entity = await self._safe_resolve_entity(username)
        if not entity:
            return False

        if not isinstance(entity, Channel):
            return False

        subs = await self._safe_get_subs(entity)
        channel_id = int(getattr(entity, "id", 0) or 0)
        normalized_username = self._normalize_username(getattr(entity, "username", None) or username)
        channel_url = f"https://t.me/{normalized_username}"
        normalized_profile_url = profile_url or f"tg://user?id={owner_chat}"

        self._queue_add(normalized_username, approved=False, channel_id=channel_id)

        is_new_channel = channel_id not in self.state.found_channels_all
        if is_new_channel:
            self.state.found_channels_all[channel_id] = FoundChannel(
                channel_id=channel_id,
                username=normalized_username,
                url=channel_url,
                subs=subs,
                source=source,
                profile_url=normalized_profile_url,
            )

        if MIN_SUBS <= subs <= MAX_SUBS:
            if channel_id not in self.state.found_channels_filtered:
                self.state.found_channels_filtered[channel_id] = self.state.found_channels_all[channel_id]
                self.state.found_count = len(self.state.found_channels_filtered)
                self.state.pending_approval[channel_id] = normalized_username
                await self._send_found_channel(
                    owner_chat,
                    normalized_username,
                    subs,
                    source,
                    profile_url=normalized_profile_url,
                )
        else:
            logger.info(
                "CHANNEL SKIPPED username=%s reason=subs_filter_only_for_send subs=%s",
                normalized_username,
                subs,
            )
        return is_new_channel

    async def _parse_profile(self, owner_chat: int, user: User, source_channel_id: Optional[int]) -> None:
        if not user or not user.id:
            logger.info("PROFILE SKIPPED reason=invalid_user")
            return
        logger.info("PROFILE START user_id=%s", user.id)

        profile_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"

        full_user = None
        for attempt in range(1, 3):
            try:
                full_user = await self.user_client(GetFullUserRequest(user))
                break
            except FloodWaitError as e:
                logger.warning(
                    "FLOOD WAIT on profile user_id=%s seconds=%s attempt=%s",
                    user.id,
                    e.seconds,
                    attempt,
                )
                if e.seconds < 60:
                    await asyncio.sleep(e.seconds)
                    continue
                self.state.profiles_failed += 1
                logger.info("PROFILE FAIL user_id=%s reason=flood_wait_long", user.id)
                return
            except Exception as e:
                logger.info("PROFILE FAIL user_id=%s reason=exception attempt=%s", user.id, attempt)
                logger.exception("Failed profile user_id=%s err=%s", user.id, e)
                if attempt >= 2:
                    self.state.profiles_failed += 1
                    return

        self.state.visited_profiles.add((user.id, source_channel_id))
        self.state.profiles_success += 1
        logger.info("PROFILE SUCCESS user_id=%s", user.id)
        logger.info("ENTITY id=%s depth=profile", user.id)

        bio = (getattr(full_user.full_user, "about", None) or "").strip()
        if bio:
            for candidate in self._extract_candidates(bio):
                logger.info("BIO LINK FOUND user_id=%s username=%s", user.id, candidate)
                await self._process_candidate(owner_chat, candidate, source="bio", profile_url=profile_url)

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


    def _reserve_profile(self, user_id: int, source_channel_id: Optional[int]) -> bool:
        profile_key = (user_id, source_channel_id)
        if profile_key in self.state.visited_profiles or profile_key in self.inflight_profiles:
            self.state.duplicate_profiles_skipped += 1
            return False
        self.inflight_profiles.add(profile_key)
        return True

    async def _parse_profile_task(self, owner_chat: int, user: User, source_channel_id: Optional[int]) -> None:
        try:
            async with self.profile_semaphore:
                await self._parse_profile(owner_chat, user, source_channel_id)
        finally:
            if user and user.id:
                self.inflight_profiles.discard((user.id, source_channel_id))

    async def _flush_profile_batch(self, profile_tasks: Optional[List[asyncio.Task]] = None) -> None:
        if profile_tasks is None:
            return
        if not profile_tasks:
            return
        await asyncio.gather(*profile_tasks, return_exceptions=True)
        profile_tasks.clear()

    def _track_task(self, task: asyncio.Task) -> None:
        self.pending_tasks.add(task)
        task.add_done_callback(self.pending_tasks.discard)

    async def _resolve_sender(self, msg: Message):
        sender = None
        try:
            sender = await msg.get_sender()
        except Exception as e:
            logger.info("PROFILE SKIPPED reason=get_sender_error err=%s", e)

        if sender is not None:
            return sender

        sender_id = getattr(msg, "sender_id", None)
        if sender_id:
            logger.info("PROFILE RETRY sender_id=%s message_id=%s", sender_id, getattr(msg, "id", None))
            try:
                return await self.user_client.get_entity(sender_id)
            except Exception:
                pass

        input_sender = None
        try:
            input_sender = await msg.get_input_sender()
        except Exception:
            input_sender = None
        if input_sender:
            logger.info("PROFILE RETRY input_sender message_id=%s", getattr(msg, "id", None))
            try:
                return await self.user_client.get_entity(input_sender)
            except Exception:
                pass
        return None

    async def _handle_sender_entity(self, owner_chat: int, msg: Message, sender) -> bool:
        if isinstance(sender, Channel) and sender.username:
            sender_profile_url = f"tg://user?id={getattr(msg, 'sender_id', 0)}"
            await self._process_candidate(owner_chat, sender.username, source="comment", profile_url=sender_profile_url)
            return True
        if isinstance(sender, Channel):
            logger.info("PROFILE SKIPPED reason=channel_sender_without_username id=%s", sender.id)
            await self._parse_channel_entity(owner_chat, sender, source="anonymous_comment_channel")
            return True
        if isinstance(sender, User):
            return False
        return False

    async def _parse_messages(self, owner_chat: int, entity, limit: int, source: str) -> None:
        source_channel_id = getattr(entity, "id", None)
        processed = 0
        retry_messages: List[Message] = []
        retry_seen_ids: Set[int] = set()
        seen_in_batch: Set[int] = set()
        profile_tasks: List[asyncio.Task] = []
        no_new_profiles_streak = 0
        window_start_found_count = len(self.state.found_channels_all)
        try:
            async for msg in self.user_client.iter_messages(entity, limit=None):
                if not self.state.running or self.stop_requested:
                    break
                processed += 1
                if source == "comment":
                    self.state.chat_processed_current = processed
                else:
                    self.state.channel_processed_current = processed
                if processed >= limit:
                    break
                if not isinstance(msg, Message):
                    continue

                self.state.message_count += 1
                text = msg.raw_text or ""

                for candidate in self._extract_candidates(text):
                    if self.stop_requested:
                        break
                    await self._process_candidate(owner_chat, candidate, source=source)
                if self.stop_requested:
                    break

                new_profile_in_message = False
                sender_id = getattr(msg, "sender_id", None)
                if sender_id:
                    if sender_id in seen_in_batch:
                        self.state.duplicate_profiles_skipped += 1
                        no_new_profiles_streak += 1
                        if no_new_profiles_streak >= 300 and processed > 300:
                            break
                        continue
                    seen_in_batch.add(sender_id)
                    if len(seen_in_batch) > 1000:
                        seen_in_batch.clear()

                sender = await self._resolve_sender(msg)
                if isinstance(sender, User):
                    is_deleted = bool(getattr(sender, "deleted", False))
                    if getattr(sender, "bot", False) or is_deleted:
                        self.state.duplicate_profiles_skipped += 1
                    elif self._reserve_profile(sender.id, source_channel_id):
                        self.state.profiles_checked += 1
                        self.state.unique_profiles_processed += 1
                        if not getattr(sender, "username", None):
                            self.state.profiles_without_username_processed += 1
                        if self.stop_requested:
                            break
                        task = asyncio.create_task(self._parse_profile_task(owner_chat, sender, source_channel_id))
                        self._track_task(task)
                        profile_tasks.append(task)
                        new_profile_in_message = True
                        if len(profile_tasks) >= PROFILE_BATCH_SIZE:
                            await self._flush_profile_batch(profile_tasks)
                else:
                    handled = await self._handle_sender_entity(owner_chat, msg, sender)
                    if not handled:
                        msg_id = getattr(msg, "id", None)
                        if msg_id and msg_id not in retry_seen_ids:
                            retry_seen_ids.add(msg_id)
                            retry_messages.append(msg)
                            logger.info("PROFILE RETRY QUEUE ADD message_id=%s", msg_id)
                        else:
                            self.state.profiles_checked += 1
                            self.state.profiles_failed += 1
                            logger.info("PROFILE FAIL message_id=%s reason=sender_unavailable", msg.id)

                if new_profile_in_message:
                    no_new_profiles_streak = 0
                else:
                    no_new_profiles_streak += 1
                    if no_new_profiles_streak >= 300 and processed > 300:
                        break
                if source == "comment" and processed % 200 == 0:
                    new_channels_in_window = len(self.state.found_channels_all) - window_start_found_count
                    if new_channels_in_window < 2:
                        logger.info(
                            "CHAT EARLY EXIT entity_id=%s processed=%s new_channels=%s",
                            source_channel_id,
                            processed,
                            new_channels_in_window,
                        )
                        break
                    window_start_found_count = len(self.state.found_channels_all)
        except ChannelPrivateError:
            logger.info(
                "MESSAGES SKIPPED private source=%s entity_id=%s",
                source,
                getattr(entity, "id", None),
            )
        except Exception as e:
            logger.exception(
                "MESSAGES PARSE FAILED source=%s entity_id=%s err=%s",
                source,
                getattr(entity, "id", None),
                e,
            )

        for msg in retry_messages:
            if not self.state.running or self.stop_requested:
                break
            msg_id = getattr(msg, "id", None)
            logger.info("PROFILE RETRY QUEUE PROCESS message_id=%s", msg_id)
            sender = await self._resolve_sender(msg)
            if isinstance(sender, User):
                is_deleted = bool(getattr(sender, "deleted", False))
                if getattr(sender, "bot", False) or is_deleted:
                    self.state.duplicate_profiles_skipped += 1
                elif self._reserve_profile(sender.id, source_channel_id):
                    self.state.profiles_checked += 1
                    self.state.unique_profiles_processed += 1
                    if not getattr(sender, "username", None):
                        self.state.profiles_without_username_processed += 1
                    if self.stop_requested:
                        break
                    task = asyncio.create_task(self._parse_profile_task(owner_chat, sender, source_channel_id))
                    self._track_task(task)
                    profile_tasks.append(task)
                    if len(profile_tasks) >= PROFILE_BATCH_SIZE:
                        await self._flush_profile_batch(profile_tasks)
                continue

            handled = await self._handle_sender_entity(owner_chat, msg, sender)
            if not handled:
                self.state.profiles_checked += 1
                self.state.profiles_failed += 1
                logger.info("PROFILE FAIL message_id=%s reason=sender_unavailable_after_retry", msg_id)

        await self._flush_profile_batch(profile_tasks)

    async def _parse_channel_entity(self, owner_chat: int, entity, source: str = "message") -> None:
        if self.stop_requested:
            return
        ent_id = getattr(entity, "id", None)
        if ent_id and ent_id in self.state.visited_entities:
            logger.info("ENTITY id=%s depth=channel skipped=visited", ent_id)
            return

        if ent_id:
            self.state.visited_entities.add(ent_id)
        logger.info("ENTITY id=%s depth=channel", ent_id)
        self.state.current_stage = "CHANNEL"
        self.state.channel_processed_current = 0
        await self._parse_messages(owner_chat, entity, self.state.channel_limit, source=source)

    async def _resolve_linked_chat(self, entity):
        linked_chat_id = getattr(entity, "linked_chat_id", None)
        if not linked_chat_id:
            try:
                full = await self.user_client(GetFullChannelRequest(entity))
                linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
            except Exception:
                linked_chat_id = None
        if not linked_chat_id:
            return None
        try:
            return await self.user_client.get_entity(linked_chat_id)
        except Exception:
            return None

    async def _parse_chat_entity(self, owner_chat: int, entity) -> None:
        if self.stop_requested:
            return
        ent_id = getattr(entity, "id", None)
        if ent_id and ent_id in self.state.visited_entities:
            logger.info("ENTITY id=%s depth=chat skipped=visited", ent_id)
            return
        if ent_id:
            self.state.visited_entities.add(ent_id)
        logger.info("ENTITY id=%s depth=chat", ent_id)
        self.state.current_stage = "CHAT"
        self.state.chat_processed_current = 0
        await self._parse_messages(owner_chat, entity, self.state.chat_limit, source="comment")

    async def _parse_channel(self, owner_chat: int, username: str) -> None:
        if self.stop_requested:
            return
        entity = await self._safe_resolve_entity(username)
        if not entity:
            return
        await self._parse_channel_entity(owner_chat, entity, source="message")

    @staticmethod
    def _format_url_list(usernames: List[str]) -> List[str]:
        return [f"{i}. https://t.me/{username}" for i, username in enumerate(usernames, start=1)]

    async def _export_results(self, owner_chat: int) -> None:
        logger.info("EXPORT START")

        found_filtered_sorted = sorted(
            self.state.found_channels_filtered.values(),
            key=lambda ch: (-int(ch.subs or 0), ch.username),
        )
        found_all_sorted = sorted(
            self.state.found_channels_all.values(),
            key=lambda ch: (-int(ch.subs or 0), ch.username),
        )

        remaining_candidates = list(self.state.queue) + list(self.state.approved_queue) + list(self.state.pending_approval.values())
        remaining_usernames: List[str] = []
        remaining_seen: Set[str] = set()
        for username in remaining_candidates:
            normalized = self._normalize_username(username)
            if normalized in self.state.processed_usernames:
                continue
            if normalized in remaining_seen:
                continue
            remaining_seen.add(normalized)
            remaining_usernames.append(normalized)

        result_file = "result.txt"
        lines: List[str] = []
        lines.append("===== НАЙДЕННЫЕ КАНАЛЫ (300–7000) =====")
        lines.append("")
        lines.extend(self._format_url_list([c.username for c in found_filtered_sorted]))
        lines.append("")
        lines.append(f"Всего: {len(found_filtered_sorted)}")
        lines.append("")
        lines.append("")
        lines.append("===== ВСЕ НАЙДЕННЫЕ КАНАЛЫ (БЕЗ ФИЛЬТРА) =====")
        lines.append("")
        for i, channel in enumerate(found_all_sorted, start=1):
            lines.append(f"{i}. {channel.url} (subs: {channel.subs})")
        lines.append("")
        lines.append(f"Всего: {len(found_all_sorted)}")
        lines.append("")
        lines.append("")
        lines.append("===== НЕ ОБРАБОТАННЫЕ КАНАЛЫ =====")
        lines.append("")
        lines.extend(self._format_url_list(remaining_usernames))
        lines.append("")
        lines.append(f"Всего: {len(remaining_usernames)}")
        lines.append("")

        with open(result_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info("EXPORT DONE")
        await self.bot_client.send_file(owner_chat, result_file, caption="📄 Результаты на момент остановки")
        logger.info("FILE SENT")

    async def run(self, owner_chat: int) -> None:
        self.state.running = True
        self.stop_requested = False
        self.state.started_at = time.time()

        await self.bot_client.send_message(owner_chat, "🚀 Парсинг запущен. Этап 1: стартовые каналы.")

        while self.state.queue and self.state.running:
            if self.stop_requested:
                break
            username = self.state.queue.pop()  # DFS
            self.state.queue_set.discard(username)
            self.state.processed_usernames.add(username)
            entity = await self._safe_resolve_entity(username)
            if not entity:
                continue
            await self._parse_channel_entity(owner_chat, entity, source="message")
            if self.stop_requested:
                break
            linked = await self._resolve_linked_chat(entity)
            if linked:
                await self._parse_chat_entity(owner_chat, linked)
            if self.stop_requested:
                break

        if not self.stop_requested:
            if self.state.approved_queue:
                self.awaiting_stage2_confirmation = True
                self.state.running = False
                await self.bot_client.send_message(
                    owner_chat,
                    "Этап 1 завершён.\nПродолжить парсинг выбранных каналов?",
                    buttons=[
                        [Button.inline("✅ Да", CALLBACK_STAGE2_YES), Button.inline("❌ Нет", CALLBACK_STAGE2_NO)]
                    ],
                )
                return
            await self.bot_client.send_message(owner_chat, "✅ Этап 1 завершён. Выбранных каналов для этапа 2 нет.")

        await self.finish(owner_chat)

    async def run_approved_only(self, owner_chat: int) -> None:
        self.state.running = True
        self.stop_requested = False
        self.state.visited_entities.clear()
        self.state.message_count = 0
        self.state.profiles_checked = 0
        self.state.profiles_success = 0
        self.state.profiles_failed = 0
        self.state.duplicate_profiles_skipped = 0
        self.state.unique_profiles_processed = 0
        self.state.channel_processed_current = 0
        self.state.chat_processed_current = 0
        self.state.current_stage = "STAGE 2"
        self.state.stage2_total_channels = len(self.state.approved_queue)
        self.state.stage2_processed_channels = 0
        if not self.state.started_at:
            self.state.started_at = time.time()

        await self.bot_client.send_message(owner_chat, "🚀 Этап 2 запущен: парсинг одобренных каналов.")
        while self.state.approved_queue and self.state.running:
            if self.stop_requested:
                break
            username = self.state.approved_queue.pop()  # DFS
            self.state.queue_set.discard(username)
            self.state.processed_usernames.add(username)
            self.state.stage2_processed_channels += 1
            entity = await self._safe_resolve_entity(username)
            if not entity:
                continue
            await self._parse_channel_entity(owner_chat, entity, source="message")
            if self.stop_requested:
                break
            linked = await self._resolve_linked_chat(entity)
            if linked:
                await self._parse_chat_entity(owner_chat, linked)
            if self.stop_requested:
                break
        await self.finish(owner_chat)

    async def stop(self, owner_chat: int) -> None:
        if self.stop_requested:
            return
        logger.info("STOP REQUESTED")
        self.stop_requested = True
        await self.bot_client.send_message(owner_chat, "⛔ Останавливаю...")

    async def progress_text(self) -> str:
        elapsed = int(time.time() - self.state.started_at) if self.state.started_at else 0
        stage2_extra = ""
        if self.state.current_stage == "STAGE 2":
            total = self.state.stage2_total_channels
            processed = self.state.stage2_processed_channels
            left = max(0, total - processed)
            stage2_extra = (
                "\n\n📊 ЭТАП 2\n"
                f"Каналов в очереди: {total}\n"
                f"Обработано: {processed}\n"
                f"Осталось: {left}"
            )
        return (
            f"⏱ Время: {elapsed} сек\n"
            f"📨 Сообщений: {self.state.message_count}\n"
            f"🔥 Каналов: {self.state.found_count}\n"
            f"🧭 Этап: {self.state.current_stage}\n"
            f"📡 Канал: {self.state.channel_processed_current}/{self.state.channel_limit}\n"
            f"💬 Чат: {self.state.chat_processed_current}/{self.state.chat_limit}\n"
            f"👤 Профили проверено: {self.state.profiles_checked}\n"
            f"✅ Профили успешно: {self.state.profiles_success}\n"
            f"❌ Профили ошибок: {self.state.profiles_failed}\n"
            f"🆕 Уникальные профили: {self.state.unique_profiles_processed}\n"
            f"♻️ Дубликаты пропущены: {self.state.duplicate_profiles_skipped}\n"
            f"🪪 Без username обработано: {self.state.profiles_without_username_processed}"
            f"{stage2_extra}"
        )

    async def finish(self, owner_chat: int) -> None:
        self.state.running = False

        await self._flush_profile_batch()
        if self.pending_tasks:
            await asyncio.gather(*list(self.pending_tasks), return_exceptions=True)
            self.pending_tasks.clear()

        await self._export_results(owner_chat)
        if self.stop_requested:
            await self.bot_client.send_message(owner_chat, "⛔ Парсинг корректно остановлен.")
        else:
            await self.bot_client.send_message(owner_chat, "🏁 Парсинг завершён.")
        await self.bot_client.send_message(owner_chat, await self.progress_text())


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
            if username in system.state.pending_approval.values():
                system._queue_add(username, approved=True)
            await event.answer("Добавлено в очередь этапа 2")
        elif data.startswith(CALLBACK_SKIP):
            username = data.split(b":", 1)[1].decode().strip().lower()
            for channel_id, pending_username in list(system.state.pending_approval.items()):
                if pending_username == username:
                    del system.state.pending_approval[channel_id]
            await event.answer("Пропущено")
        elif data == CALLBACK_STAGE2_YES:
            system.awaiting_stage2_confirmation = False
            system.state.queue.clear()
            await event.answer("Запускаю этап 2")
            if system.state.approved_queue and not system.state.running:
                asyncio.create_task(system.run_approved_only(event.chat_id))
        elif data == CALLBACK_STAGE2_NO:
            system.awaiting_stage2_confirmation = False
            await event.answer("Завершаю")
            if not system.state.running:
                await system.finish(event.chat_id)

    logger.info("Bot started")
    await bot_client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
