import asyncio
import heapq
import logging
import os
import re
import time
import json
from collections import defaultdict, deque
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
DELAY = 0.03
RESOLVE_COOLDOWN_SECONDS = 0.35
PROFILE_WORKERS = 70
CANDIDATE_WORKERS = 15
MAX_CANDIDATE_RETRIES = 3
MAX_PROFILE_RETRIES = 5
MAX_DEPTH = 5
RESOLVE_FAIL_INVALID_THRESHOLD = 10
RESOLVE_TIMEOUT_SECONDS = 8
MIN_DELAY = 1.2

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
PENDING_APPROVAL_FILE = "pending_approval.json"
APPROVED_QUEUE_FILE = "approved_queue.json"


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
class ProfileRetry:
    user: User
    source_channel_id: Optional[int]
    attempt: int
    not_before_ts: float
    depth: int


@dataclass
class SessionState:
    owner_user_id: Optional[int] = None
    running: bool = False
    started_at: float = 0.0

    message_count: int = 0
    found_count: int = 0
    found_filtered_count: int = 0
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

    main_queue: List[Tuple[float, int, str, str, Optional[str], int, int]] = field(default_factory=list)
    in_queue: Set[str] = field(default_factory=set)
    approved_queue: Deque[int] = field(default_factory=deque)
    retry_profiles: Deque[ProfileRetry] = field(default_factory=deque)

    visited_entities: Set[int] = field(default_factory=set)
    visited_profiles: Dict[int, float] = field(default_factory=dict)
    channel_parse_queue: Deque[Tuple[int, int]] = field(default_factory=deque)
    channel_parse_in_queue: Set[int] = field(default_factory=set)

    found_channels_all: Dict[int, FoundChannel] = field(default_factory=dict)
    found_channels_filtered: Dict[int, FoundChannel] = field(default_factory=dict)
    username_state: Dict[str, str] = field(default_factory=dict)
    queued_channel_ids: Set[int] = field(default_factory=set)
    approved_set: Set[int] = field(default_factory=set)

    pending_approval: Dict[int, str] = field(default_factory=dict)
    stage2_total_channels: int = 0
    stage2_processed_channels: int = 0

    last_resolve_ts: float = 0.0
    last_subs_ts: float = 0.0

    def reset_runtime(self) -> None:
        self.running = False
        self.started_at = 0.0
        self.message_count = 0
        self.found_count = 0
        self.found_filtered_count = 0
        self.profiles_checked = 0
        self.profiles_success = 0
        self.profiles_failed = 0
        self.unique_profiles_processed = 0
        self.duplicate_profiles_skipped = 0
        self.profiles_without_username_processed = 0
        self.current_stage = "IDLE"
        self.channel_processed_current = 0
        self.chat_processed_current = 0
        self.main_queue.clear()
        self.in_queue.clear()
        self.approved_queue.clear()
        self.retry_profiles.clear()
        self.visited_entities.clear()
        self.visited_profiles.clear()
        self.channel_parse_queue.clear()
        self.channel_parse_in_queue.clear()
        self.found_channels_all.clear()
        self.found_channels_filtered.clear()
        self.username_state.clear()
        self.queued_channel_ids.clear()
        self.approved_set.clear()
        self.pending_approval.clear()
        self.stage2_total_channels = 0
        self.stage2_processed_channels = 0
        self.last_resolve_ts = 0.0
        self.last_subs_ts = 0.0


class RateLimiter:
    def __init__(self):
        self.intervals = {
            "resolve": 1.0,
            "full_channel": 1.5,
            "full_user": 0.5,
        }
        self.next_allowed: Dict[str, float] = defaultdict(float)
        self.lock = asyncio.Lock()
        self.penalty_multiplier = 1.0

    async def acquire(self, key: str) -> None:
        async with self.lock:
            now = time.monotonic()
            next_ts = self.next_allowed.get(key, 0.0)
            wait = max(0.0, next_ts - now)
            slot = max(now, next_ts)
            self.next_allowed[key] = slot + self.intervals.get(key, 1.0) * self.penalty_multiplier
        if wait > 0:
            await asyncio.sleep(wait)

    def report_flood(self, seconds: int) -> None:
        if seconds >= 20:
            self.penalty_multiplier = min(5.0, self.penalty_multiplier * 1.25)
        else:
            self.penalty_multiplier = min(3.0, self.penalty_multiplier * 1.1)

    def report_success(self) -> None:
        self.penalty_multiplier = max(1.0, self.penalty_multiplier * 0.995)


# =========================
# Parser
# =========================
class TelegramParserSystem:
    URL_PATTERN = re.compile(r"(?:https?://)?t\.me/([^\s<>\")]+)", re.IGNORECASE)
    TG_RESOLVE_PATTERN = re.compile(r"tg://resolve\?domain=([A-Za-z0-9_]{4,})", re.IGNORECASE)
    MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_]{5,})")

    def __init__(self, user_client: TelegramClient, bot_client: TelegramClient):
        self.user_client = user_client
        self.bot_client = bot_client
        self.state = SessionState()
        self.candidate_semaphore = asyncio.Semaphore(CANDIDATE_WORKERS)
        self.resolve_semaphore = asyncio.Semaphore(1)
        self.channel_semaphore = asyncio.Semaphore(1)
        self.user_semaphore = asyncio.Semaphore(1)
        self.subs_semaphore = asyncio.Semaphore(1)
        self.profile_semaphore = asyncio.Semaphore(PROFILE_WORKERS)
        self.rate_limiter = RateLimiter()
        self.inflight_profiles: Set[int] = set()
        self.resolving_now: Set[str] = set()
        self.resolve_cache: Dict[str, Tuple[object, float]] = {}
        self.subs_cache: Dict[str, int] = {}
        self.seen_usernames: Set[str] = set()
        self.resolve_failures: Dict[str, int] = defaultdict(int)
        self.invalid_usernames: Set[str] = set()
        self.profile_retry_count: Dict[int, int] = defaultdict(int)
        self.global_pause_until: float = 0.0
        self.last_request_time: float = 0.0
        self.request_lock = asyncio.Lock()
        self.stop_requested = False
        self.pending_tasks: Set[asyncio.Task] = set()
        self.profile_retry_task: Optional[asyncio.Task] = None
        self.awaiting_stage2_confirmation = False
        self._load_stage2_state()

    async def _before_api_request(self) -> None:
        if time.time() < self.global_pause_until:
            await asyncio.sleep(self.global_pause_until - time.time())
        async with self.request_lock:
            now = time.time()
            sleep_time = self.last_request_time + MIN_DELAY - now
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            self.last_request_time = time.time()

    async def _limited_get_entity(self, target):
        while True:
            await self.rate_limiter.acquire("resolve")
            await self._before_api_request()
            async with self.resolve_semaphore:
                try:
                    logger.info("START RESOLVE username=%s", target)
                    entity = await asyncio.wait_for(
                        self.user_client.get_entity(target),
                        timeout=RESOLVE_TIMEOUT_SECONDS,
                    )
                    self.rate_limiter.report_success()
                    await asyncio.sleep(0.5)
                    return entity
                except asyncio.TimeoutError:
                    logger.warning("RESOLVE TIMEOUT target=%s", target)
                    await asyncio.sleep(0.5)
                    return None
                except FloodWaitError as e:
                    self.rate_limiter.report_flood(e.seconds)
                    if e.seconds > 60:
                        self.global_pause_until = max(self.global_pause_until, time.time() + float(e.seconds))
                        logger.warning("GLOBAL PAUSE %ss", e.seconds)
                        await asyncio.sleep(0.5)
                        return ("DEFERRED", e.seconds)
                    if e.seconds < 20:
                        await asyncio.sleep(e.seconds)
                        continue
                    await asyncio.sleep(0.5)
                    return ("DEFERRED", e.seconds)
                except Exception as e:
                    logger.warning("RESOLVE ERROR target=%s err=%s", target, e)
                    await asyncio.sleep(0.5)
                    return None

    async def _limited_get_full_channel(self, entity):
        while True:
            await self.rate_limiter.acquire("full_channel")
            await self._before_api_request()
            async with self.channel_semaphore:
                try:
                    full = await self.user_client(GetFullChannelRequest(entity))
                    self.rate_limiter.report_success()
                    return full
                except FloodWaitError as e:
                    self.rate_limiter.report_flood(e.seconds)
                    if e.seconds > 60:
                        self.global_pause_until = max(self.global_pause_until, time.time() + float(e.seconds))
                        logger.warning("GLOBAL PAUSE %ss", e.seconds)
                        return ("DEFERRED", e.seconds)
                    if e.seconds < 20:
                        await asyncio.sleep(e.seconds)
                        continue
                    return ("DEFERRED", e.seconds)

    async def _limited_get_full_user(self, user):
        while True:
            await self.rate_limiter.acquire("full_user")
            await self._before_api_request()
            async with self.user_semaphore:
                try:
                    full = await self.user_client(GetFullUserRequest(user))
                    self.rate_limiter.report_success()
                    return full
                except FloodWaitError as e:
                    self.rate_limiter.report_flood(e.seconds)
                    if e.seconds > 60:
                        self.global_pause_until = max(self.global_pause_until, time.time() + float(e.seconds))
                        logger.warning("GLOBAL PAUSE %ss", e.seconds)
                        return ("DEFERRED", e.seconds)
                    if e.seconds < 20:
                        await asyncio.sleep(e.seconds)
                        continue
                    return ("DEFERRED", e.seconds)

    # ---------- Utility ----------
    async def _antiban_delay(self) -> None:
        await asyncio.sleep(DELAY)

    async def _resolve_cooldown(self) -> None:
        now = time.time()
        delta = now - self.state.last_resolve_ts
        if delta < RESOLVE_COOLDOWN_SECONDS:
            await asyncio.sleep(RESOLVE_COOLDOWN_SECONDS - delta)
        self.state.last_resolve_ts = time.time()

    async def _subs_cooldown(self) -> None:
        now = time.time()
        delta = now - self.state.last_subs_ts
        if delta < 0.3:
            logger.info("SUBS REQUEST throttled")
            await asyncio.sleep(0.3 - delta)
        self.state.last_subs_ts = time.time()

    def _save_stage2_state(self) -> None:
        pending_data = {str(k): v for k, v in self.state.pending_approval.items()}
        approved_data = list(self.state.approved_queue)
        with open(PENDING_APPROVAL_FILE, "w", encoding="utf-8") as f:
            json.dump(pending_data, f, ensure_ascii=False, indent=2)
        with open(APPROVED_QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(approved_data, f, ensure_ascii=False, indent=2)

    def _load_stage2_state(self) -> None:
        pending: Dict[int, str] = {}
        approved: Deque[int] = deque()
        if os.path.exists(PENDING_APPROVAL_FILE):
            try:
                with open(PENDING_APPROVAL_FILE, "r", encoding="utf-8") as f:
                    raw_pending = json.load(f)
                if isinstance(raw_pending, dict):
                    for key, value in raw_pending.items():
                        if str(key).isdigit() and isinstance(value, str):
                            pending[int(key)] = value
            except Exception:
                pending = {}
        if os.path.exists(APPROVED_QUEUE_FILE):
            try:
                with open(APPROVED_QUEUE_FILE, "r", encoding="utf-8") as f:
                    raw_approved = json.load(f)
                if isinstance(raw_approved, list):
                    for value in raw_approved:
                        if isinstance(value, int):
                            approved.append(value)
            except Exception:
                approved = deque()
        self.state.pending_approval = pending
        self.state.approved_queue = approved
        self.state.approved_set = set(approved)

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

    def _extract_username_from_tme_path(self, raw_path: str) -> str:
        path = (raw_path or "").strip()
        if not path:
            return ""
        path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
        if not path:
            return ""
        parts = [x for x in path.split("/") if x]
        if not parts:
            return ""
        head = parts[0].lower()
        if head in {"joinchat", "+", "addstickers", "share", "iv", "proxy", "login", "confirmphone"}:
            return ""
        if head == "s":
            if len(parts) < 2:
                return ""
            return self._normalize_username(parts[1])
        if head in {"c", "b"}:
            return ""
        return self._normalize_username(parts[0])

    @staticmethod
    def _is_trash_username(username: str) -> bool:
        lowered = username.lower()
        if lowered.startswith("+") or lowered.startswith("joinchat"):
            return True
        return False

    def _extract_candidates(self, text: Optional[str]) -> Set[str]:
        if not text:
            return set()
        out: Set[str] = set()
        for match in self.URL_PATTERN.findall(text):
            extracted = self._extract_username_from_tme_path(match)
            if extracted:
                out.add(extracted)
        for match in self.TG_RESOLVE_PATTERN.findall(text):
            out.add(self._normalize_username(match))
        for mention in self.MENTION_PATTERN.findall(text):
            out.add(self._normalize_username(mention))
        out = {x for x in out if x and not self._is_trash_username(x)}
        return out

    def _extract_candidates_from_message(self, msg: Message) -> Set[str]:
        out = set(self._extract_candidates(msg.raw_text or ""))

        entities = getattr(msg, "entities", None) or []
        for entity in entities:
            for attr in ("url",):
                value = getattr(entity, attr, None)
                if isinstance(value, str):
                    out.update(self._extract_candidates(value))
            user_obj = getattr(entity, "user_id", None)
            if isinstance(user_obj, User) and getattr(user_obj, "username", None):
                out.add(self._normalize_username(user_obj.username))

        reply_markup = getattr(msg, "reply_markup", None)
        rows = getattr(reply_markup, "rows", None) or []
        for row in rows:
            buttons = getattr(row, "buttons", None) or []
            for button in buttons:
                button_url = getattr(button, "url", None)
                if isinstance(button_url, str):
                    out.update(self._extract_candidates(button_url))

        return {x for x in out if x and not self._is_trash_username(x)}

    async def _safe_resolve_entity(self, username: str):
        username = self._normalize_username(username)
        if username in self.invalid_usernames:
            logger.info("RESOLVE SKIP invalid_cached username=%s", username)
            return None
        cached_item = self.resolve_cache.get(username)
        if cached_item:
            cached_entity, cached_ts = cached_item
            if cached_entity == "FLOOD_BLOCK":
                if time.time() <= cached_ts:
                    return "DEFERRED"
                self.resolve_cache.pop(username, None)
            elif time.time() - cached_ts <= 60:
                return cached_entity
            else:
                self.resolve_cache.pop(username, None)
        if username in self.resolving_now:
            logger.info("RESOLVE SKIPPED duplicate username=%s", username)
            return "IN_PROGRESS"
        self.resolving_now.add(username)
        try:
            await self._resolve_cooldown()
            entity = await self._limited_get_entity(username)
            if isinstance(entity, tuple) and entity and entity[0] == "DEFERRED":
                delay_seconds = min(60.0, float(entity[1]))
                self.resolve_failures[username] += 1
                if self.resolve_failures[username] >= RESOLVE_FAIL_INVALID_THRESHOLD:
                    self.invalid_usernames.add(username)
                    logger.info("RESOLVE MARK INVALID username=%s reason=deferred_limit", username)
                    logger.info("END RESOLVE username=%s status=INVALID", username)
                    return None
                self.resolve_cache[username] = ("FLOOD_BLOCK", time.time() + delay_seconds)
                logger.info("END RESOLVE username=%s status=DEFERRED", username)
                return "DEFERRED"
            if not entity:
                self.resolve_failures[username] += 1
                if self.resolve_failures[username] >= RESOLVE_FAIL_INVALID_THRESHOLD:
                    self.invalid_usernames.add(username)
                    logger.info("RESOLVE MARK INVALID username=%s", username)
                logger.info("END RESOLVE username=%s status=NONE", username)
                return None
            self.resolve_failures.pop(username, None)
            self.resolve_cache[username] = (entity, time.time())
            logger.info("END RESOLVE username=%s status=OK", username)
            return entity
        except FloodWaitError as e:
            logger.warning("FLOOD WAIT on resolve username=%s seconds=%s", username, e.seconds)
            if e.seconds < 20:
                await asyncio.sleep(e.seconds)
                return await self._safe_resolve_entity(username)
            delay_seconds = min(60.0, float(e.seconds))
            self.resolve_failures[username] += 1
            if self.resolve_failures[username] >= RESOLVE_FAIL_INVALID_THRESHOLD:
                self.invalid_usernames.add(username)
                logger.info("RESOLVE MARK INVALID username=%s reason=flood_deferred_limit", username)
                logger.info("END RESOLVE username=%s status=INVALID", username)
                return None
            self.resolve_cache[username] = ("FLOOD_BLOCK", time.time() + delay_seconds)
            logger.info("END RESOLVE username=%s status=DEFERRED", username)
            return "DEFERRED"
        except (UsernameInvalidError, UsernameNotOccupiedError, InviteHashExpiredError):
            logger.info("QUEUE SKIP invalid username=%s", username)
            self.invalid_usernames.add(username)
            logger.info("END RESOLVE username=%s status=INVALID", username)
            return None
        except ValueError:
            logger.info("QUEUE SKIP unresolved username=%s", username)
            self.resolve_failures[username] += 1
            if self.resolve_failures[username] >= RESOLVE_FAIL_INVALID_THRESHOLD:
                self.invalid_usernames.add(username)
            logger.info("END RESOLVE username=%s status=VALUE_ERROR", username)
            return None
        except Exception as e:
            logger.exception("Resolve failed username=%s err=%s", username, e)
            self.resolve_failures[username] += 1
            if self.resolve_failures[username] >= RESOLVE_FAIL_INVALID_THRESHOLD:
                self.invalid_usernames.add(username)
            logger.info("END RESOLVE username=%s status=ERROR", username)
            return None
        finally:
            self.resolving_now.discard(username)

    async def _safe_get_subs(self, entity) -> int:
        try:
            cache_key = self._normalize_username(getattr(entity, "username", None) or str(getattr(entity, "id", "")))
            if cache_key in self.subs_cache:
                return self.subs_cache[cache_key]
            await self._subs_cooldown()
            async with self.subs_semaphore:
                full = await self._limited_get_full_channel(entity)
                if isinstance(full, tuple) and full and full[0] == "DEFERRED":
                    return -1
                subs = int(getattr(full.full_chat, "participants_count", 0) or 0)
            self.subs_cache[cache_key] = subs
            logger.info("CHANNEL subs username=%s subs=%s", getattr(entity, "username", None), subs)
            await asyncio.sleep(0.5)
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
        channel_id: Optional[int] = None,
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

        callback_value = str(channel_id) if channel_id else username
        await self.bot_client.send_message(
            chat_id,
            text,
            buttons=[
                [
                    Button.inline("✅ Парсить потом", CALLBACK_PARSE + callback_value.encode()),
                    Button.inline("❌ Не парсить", CALLBACK_SKIP + callback_value.encode()),
                ]
            ],
        )

    def _source_priority(self, source: str, attempt: int) -> int:
        if attempt > 0:
            return 3
        if source in {"message", "comment"}:
            return 0
        if source in {"attached", "profile"}:
            return 1
        if source == "bio":
            return 2
        return 3

    def _queue_add(
        self,
        username: str,
        approved: bool = False,
        channel_id: Optional[int] = None,
        depth: int = 0,
        source: str = "message",
        profile_url: Optional[str] = None,
    ) -> None:
        normalized = self._normalize_username(username)
        if approved and channel_id and channel_id in self.state.approved_set:
            return
        if channel_id and channel_id in self.state.queued_channel_ids and not profile_url:
            return
        if channel_id:
            self.state.queued_channel_ids.add(channel_id)
        if approved:
            if channel_id:
                self.state.approved_set.add(channel_id)
                self.state.approved_queue.append(channel_id)
                self._save_stage2_state()
        else:
            self._enqueue_main_candidate(
                normalized,
                source=source,
                profile_url=profile_url,
                attempt=0,
                is_retry=False,
                depth=depth,
            )

    def _enqueue_main_candidate(
        self,
        username: str,
        source: str,
        profile_url: Optional[str],
        attempt: int = 0,
        is_retry: bool = False,
        depth: int = 0,
    ) -> None:
        normalized = self._normalize_username(username)
        if not is_retry:
            if normalized in self.seen_usernames and source not in {"bio", "attached"}:
                return
            self.seen_usernames.add(normalized)
        if depth > MAX_DEPTH:
            return
        cached_item = self.resolve_cache.get(normalized)
        if cached_item and not profile_url:
            cached_entity, cached_ts = cached_item
            if cached_entity == "FLOOD_BLOCK":
                now_ts = time.time()
                if now_ts <= cached_ts:
                    run_at = cached_ts + 1.0
                    priority = self._source_priority(source, attempt)
                    heapq.heappush(
                        self.state.main_queue,
                        (run_at, priority, normalized, source, profile_url, attempt, depth),
                    )
                    self.state.in_queue.add(normalized)
                    if normalized not in self.state.username_state:
                        self.state.username_state[normalized] = "NEW"
                    return
                self.resolve_cache.pop(normalized, None)
            elif time.time() - cached_ts <= 60:
                return
        state = self.state.username_state.get(normalized, "NEW")
        if state == "IN_PROGRESS":
            return
        if state == "DONE" and not profile_url:
            return
        if is_retry and state in {"DONE", "IN_PROGRESS"}:
            return
        if normalized in self.state.in_queue and source not in {"bio", "attached"}:
            return
        if is_retry and len(self.state.main_queue) > 1000:
            logger.warning("Retry queue overflow, skipping username=%s", normalized)
            return
        if source == "bio":
            run_at = time.time() - 1
        elif source == "attached":
            run_at = time.time()
        else:
            run_at = time.time() + (min(60.0, float(2 ** max(1, attempt))) if is_retry else 0.0)
        priority = self._source_priority(source, attempt)
        heapq.heappush(self.state.main_queue, (run_at, priority, normalized, source, profile_url, attempt, depth))
        self.state.in_queue.add(normalized)
        if normalized not in self.state.username_state:
            self.state.username_state[normalized] = "NEW"

    async def _drain_main_queue(self, owner_chat: int, batch_size: int = 50) -> None:
        now = time.time()
        scheduled = 0
        while self.state.main_queue and scheduled < batch_size:
            run_at, priority, username, source, profile_url, attempt, depth = self.state.main_queue[0]
            if run_at > now:
                break
            heapq.heappop(self.state.main_queue)
            self.state.in_queue.discard(username)
            task = asyncio.create_task(
                self._process_candidate_task(
                    owner_chat=owner_chat,
                    username=username,
                    source=source,
                    profile_url=profile_url,
                    attempt=attempt,
                    depth=depth,
                )
            )
            self._track_task(task)
            scheduled += 1

    def _schedule_channel_for_parsing(self, channel_id: int, depth: int) -> None:
        if depth > MAX_DEPTH:
            return
        if channel_id in self.state.channel_parse_in_queue or channel_id in self.state.visited_entities:
            return
        self.state.channel_parse_queue.appendleft((channel_id, depth))
        self.state.channel_parse_in_queue.add(channel_id)

    async def _drain_channel_parse_queue(self, owner_chat: int, batch_size: int = 300) -> None:
        processed = 0
        while self.state.channel_parse_queue and processed < batch_size and not self.stop_requested:
            channel_id, depth = self.state.channel_parse_queue.popleft()
            self.state.channel_parse_in_queue.discard(channel_id)
            if depth > MAX_DEPTH:
                continue
            try:
                entity = await self._limited_get_entity(channel_id)
            except Exception:
                entity = None
            if not entity or not isinstance(entity, Channel):
                continue
            task = asyncio.create_task(self._parse_channel_with_linked(owner_chat, entity, depth))
            self._track_task(task)
            processed += 1

    async def _parse_channel_with_linked(self, owner_chat: int, entity, depth: int) -> None:
        if self.stop_requested:
            return
        linked = await self._resolve_linked_chat(entity)
        if linked:
            await self._parse_chat_entity(owner_chat, linked, depth=depth + 1)
            return
        if isinstance(entity, Channel) and bool(getattr(entity, "megagroup", False)):
            logger.info("ENTITY id=%s depth=chat fallback=self_megagroup", getattr(entity, "id", None))
            await self._parse_chat_entity(owner_chat, entity, depth=depth + 1)
            return
        logger.info(
            "ENTITY id=%s depth=chat skipped=no_linked_chat",
            getattr(entity, "id", None),
        )

    async def _process_candidate_task(
        self,
        owner_chat: int,
        username: str,
        source: str,
        profile_url: Optional[str],
        attempt: int = 0,
        depth: int = 0,
    ) -> None:
        if self.stop_requested:
            return
        async with self.candidate_semaphore:
            if self.stop_requested:
                return
            await self._process_candidate(
                owner_chat=owner_chat,
                username=username,
                source=source,
                profile_url=profile_url,
                attempt=attempt,
                depth=depth,
            )

    def _schedule_candidate_processing(
        self,
        owner_chat: int,
        username: str,
        source: str,
        profile_url: Optional[str] = None,
        attempt: int = 0,
        depth: int = 0,
    ) -> None:
        if self.stop_requested:
            return
        normalized = self._normalize_username(username)
        self._enqueue_main_candidate(
            username=normalized,
            source=source,
            profile_url=profile_url,
            attempt=attempt,
            is_retry=attempt > 0,
            depth=depth,
        )

    async def _process_candidate(
        self,
        owner_chat: int,
        username: str,
        source: str,
        profile_url: Optional[str] = None,
        attempt: int = 0,
        depth: int = 0,
    ) -> bool:
        username = self._normalize_username(username)

        if self._is_trash_username(username):
            return False
        current_state = self.state.username_state.get(username, "NEW")
        if current_state == "DONE":
            if not profile_url:
                return False
            self.state.username_state[username] = "NEW"
        if current_state == "IN_PROGRESS":
            return False
        self.state.username_state[username] = "IN_PROGRESS"
        entity = await self._safe_resolve_entity(username)
        if entity == "IN_PROGRESS":
            self.state.username_state[username] = "NEW"
            if attempt < MAX_CANDIDATE_RETRIES:
                self._enqueue_main_candidate(
                    username=username,
                    source=source,
                    profile_url=profile_url,
                    attempt=attempt + 1,
                    is_retry=True,
                    depth=depth,
                )
            return False
        if entity == "DEFERRED":
            self.state.username_state[username] = "NEW"
            if attempt < MAX_CANDIDATE_RETRIES:
                self._enqueue_main_candidate(
                    username=username,
                    source=source,
                    profile_url=profile_url,
                    attempt=attempt + 1,
                    is_retry=True,
                    depth=depth,
                )
            else:
                self.state.username_state[username] = "FAILED"
            return False
        if not entity and username.isdigit():
            try:
                fallback_entity = await self._limited_get_entity(int(username))
                if isinstance(fallback_entity, tuple) and fallback_entity and fallback_entity[0] == "DEFERRED":
                    fallback_entity = None
                if fallback_entity:
                    entity = fallback_entity
                    logger.info("RESOLVE FALLBACK BY CHANNEL_ID target=%s status=OK", username)
            except Exception as e:
                logger.info("RESOLVE FALLBACK BY CHANNEL_ID target=%s status=FAIL err=%s", username, e)

        if not entity:
            self.state.username_state[username] = "FAILED"
            if attempt < MAX_CANDIDATE_RETRIES:
                self._enqueue_main_candidate(
                    username=username,
                    source=source,
                    profile_url=profile_url,
                    attempt=attempt + 1,
                    is_retry=True,
                    depth=depth,
                )
            return False

        if not isinstance(entity, Channel):
            self.state.username_state[username] = "FAILED"
            return False
        if getattr(entity, "bot", False):
            self.state.username_state[username] = "FAILED"
            return False

        channel_id = int(getattr(entity, "id", 0) or 0)
        normalized_username = self._normalize_username(getattr(entity, "username", None) or username)
        if not normalized_username:
            self.state.username_state[username] = "FAILED"
            return False
        if profile_url and "tg://user" in profile_url.lower():
            self.state.username_state[username] = "FAILED"
            return False
        channel_url = f"https://t.me/{normalized_username}"
        normalized_profile_url = profile_url or ""

        subs = await self._safe_get_subs(entity)
        if subs < 0:
            self.state.username_state[username] = "NEW"
            if attempt < MAX_CANDIDATE_RETRIES:
                self._enqueue_main_candidate(
                    username=username,
                    source=source,
                    profile_url=profile_url,
                    attempt=attempt + 1,
                    is_retry=True,
                    depth=depth,
                )
            return False
        if subs < 50:
            self.state.username_state[username] = "FAILED"
            return False
        self.state.username_state[username] = "DONE"

        self._queue_add(
            normalized_username,
            approved=False,
            channel_id=channel_id,
            depth=depth + 1,
            source=source,
            profile_url=normalized_profile_url,
        )

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
            self.state.found_count = len(self.state.found_channels_all)

        if MIN_SUBS <= subs <= MAX_SUBS:
            if channel_id not in self.state.found_channels_filtered:
                self.state.found_channels_filtered[channel_id] = self.state.found_channels_all[channel_id]
                self.state.found_filtered_count = len(self.state.found_channels_filtered)
                if source != "seed":
                    self.state.pending_approval[channel_id] = normalized_username
                    self._save_stage2_state()
                    logger.info("SEND CHANNEL username=%s subs=%s", normalized_username, subs)
                    await self._send_found_channel(
                        owner_chat,
                        normalized_username,
                        subs,
                        source,
                        channel_id=channel_id,
                        profile_url=normalized_profile_url,
                    )
        else:
            logger.info(
                "CHANNEL OUTSIDE FILTER username=%s subs=%s filter=[%s,%s]",
                normalized_username,
                subs,
                MIN_SUBS,
                MAX_SUBS,
            )
        self._schedule_channel_for_parsing(channel_id, depth + 1)
        return is_new_channel

    def _enqueue_profile_retry(self, user: User, source_channel_id: Optional[int], attempt: int, depth: int) -> None:
        user_id = getattr(user, "id", None)
        if not user_id:
            return
        if self.profile_retry_count[user_id] >= 1:
            return
        self.profile_retry_count[user_id] += 1
        if attempt > MAX_PROFILE_RETRIES:
            logger.info("PROFILE RETRY DROP user_id=%s reason=max_retries", getattr(user, "id", None))
            return
        wait_seconds = min(60.0, float(2 ** max(1, attempt)))
        self.state.retry_profiles.append(
            ProfileRetry(
                user=user,
                source_channel_id=source_channel_id,
                attempt=attempt,
                not_before_ts=time.time() + wait_seconds,
                depth=depth,
            )
        )
        logger.info("PROFILE RETRY ENQUEUE user_id=%s attempt=%s", getattr(user, "id", None), attempt)

    async def _drain_profile_retries(self, owner_chat: int) -> None:
        if not self.state.retry_profiles:
            return
        now = time.time()
        retries_total = len(self.state.retry_profiles)
        for _ in range(retries_total):
            retry_item = self.state.retry_profiles.popleft()
            if retry_item.not_before_ts > now:
                self.state.retry_profiles.append(retry_item)
                continue
            self.state.profiles_checked += 1
            self.state.unique_profiles_processed += 1
            if not getattr(retry_item.user, "username", None):
                self.state.profiles_without_username_processed += 1
            task = asyncio.create_task(
                self._parse_profile_task(
                    owner_chat,
                    retry_item.user,
                    retry_item.source_channel_id,
                    attempt=retry_item.attempt,
                    depth=retry_item.depth,
                    high_priority=True,
                )
            )
            self._track_task(task)

    @staticmethod
    def _extract_gift_sender_ids(full_user) -> List[int]:
        sender_ids: Set[int] = set()

        def add_sender(value) -> None:
            try:
                sender_id = int(value)
            except Exception:
                return
            if sender_id > 0:
                sender_ids.add(sender_id)

        def from_peer(peer) -> None:
            if peer is None:
                return
            add_sender(getattr(peer, "user_id", 0))
            add_sender(getattr(peer, "channel_id", 0))

        full_user_obj = getattr(full_user, "full_user", None)
        candidate_collections = []
        for source in (full_user_obj, full_user):
            if not source:
                continue
            for attr in ("gifts", "saved_gifts", "profile_gifts", "gift_items"):
                items = getattr(source, attr, None)
                if items:
                    candidate_collections.append(items)

        for collection in candidate_collections:
            values = collection if isinstance(collection, (list, tuple, set)) else [collection]
            for item in values:
                if getattr(item, "anonymous", False) or getattr(item, "hidden", False):
                    continue
                add_sender(getattr(item, "sender_id", 0))
                add_sender(getattr(item, "from_id", 0))
                add_sender(getattr(item, "user_id", 0))
                from_peer(getattr(item, "peer", None))
                from_peer(getattr(item, "from_peer", None))

        return list(sender_ids)

    async def _parse_profile(
        self,
        owner_chat: int,
        user: User,
        source_channel_id: Optional[int],
        depth: int = 0,
    ) -> bool:
        if not user or not user.id:
            logger.info("PROFILE SKIPPED reason=invalid_user")
            return True
        logger.info("PROFILE START user_id=%s", user.id)

        profile_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"

        full_user = None
        for attempt in range(1, 3):
            try:
                full_user = await self._limited_get_full_user(user)
                if isinstance(full_user, tuple) and full_user and full_user[0] == "DEFERRED":
                    self._enqueue_profile_retry(user, source_channel_id, attempt, depth)
                    return False
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
                return False
            except Exception as e:
                logger.info("PROFILE FAIL user_id=%s reason=exception attempt=%s", user.id, attempt)
                logger.exception("Failed profile user_id=%s err=%s", user.id, e)
                if attempt >= 2:
                    self.state.profiles_failed += 1
                    return False

        self.state.visited_profiles[user.id] = time.time()
        self.state.profiles_success += 1
        logger.info("PROFILE SUCCESS user_id=%s", user.id)
        logger.info("ENTITY id=%s depth=profile", user.id)

        bio = (getattr(full_user.full_user, "about", None) or "").strip()
        if bio:
            for candidate in self._extract_candidates(bio):
                logger.info("BIO LINK FOUND user_id=%s username=%s", user.id, candidate)
                self._schedule_candidate_processing(
                    owner_chat,
                    candidate,
                    source="bio",
                    profile_url=profile_url,
                    depth=depth + 1,
                )

        gift_sender_ids = self._extract_gift_sender_ids(full_user)
        if gift_sender_ids:
            for gift_sender_id in gift_sender_ids:
                try:
                    sender_entity = await self._limited_get_entity(gift_sender_id)
                except Exception:
                    sender_entity = None
                if isinstance(sender_entity, User) and self._reserve_profile(sender_entity.id, source_channel_id):
                    self.state.profiles_checked += 1
                    self.state.unique_profiles_processed += 1
                    if not getattr(sender_entity, "username", None):
                        self.state.profiles_without_username_processed += 1
                    task = asyncio.create_task(
                        self._parse_profile_task(
                            owner_chat,
                            sender_entity,
                            source_channel_id,
                            attempt=0,
                            depth=depth + 1,
                        )
                    )
                    self._track_task(task)

        personal_channel_id = getattr(full_user.full_user, "personal_channel_id", None)
        if personal_channel_id:
            logger.info("PROFILE HAS ATTACHED CHANNEL user_id=%s channel_id=%s", user.id, personal_channel_id)
            try:
                entity = await self._limited_get_entity(personal_channel_id)
                if isinstance(entity, Channel):
                    channel_id = int(getattr(entity, "id", 0) or 0)
                    attached_username = self._normalize_username(getattr(entity, "username", None) or "")
                    subs = await self._safe_get_subs(entity)
                    if subs > 0 and channel_id and channel_id not in self.state.found_channels_all:
                        channel_url = f"https://t.me/{attached_username}" if attached_username else f"channel_id:{channel_id}"
                        self.state.found_channels_all[channel_id] = FoundChannel(
                            channel_id=channel_id,
                            username=attached_username or str(channel_id),
                            url=channel_url,
                            subs=subs,
                            source="attached",
                            profile_url=profile_url,
                        )
                        self.state.found_count = len(self.state.found_channels_all)
                        if MIN_SUBS <= subs <= MAX_SUBS:
                            self.state.found_channels_filtered[channel_id] = self.state.found_channels_all[channel_id]
                            self.state.found_filtered_count = len(self.state.found_channels_filtered)
                    if attached_username:
                        self._schedule_candidate_processing(
                            owner_chat,
                            attached_username,
                            source="attached",
                            profile_url=profile_url,
                            depth=depth + 1,
                        )
                    else:
                        logger.info(
                            "ATTACHED CHANNEL WITHOUT USERNAME user_id=%s channel_id=%s counted=yes",
                            user.id,
                            channel_id,
                        )
                    self._schedule_channel_for_parsing(channel_id, depth + 1)
            except Exception as e:
                logger.exception("Attached channel resolve failed user_id=%s err=%s", user.id, e)
        await asyncio.sleep(0.5)
        return True

    def _reserve_profile(self, user_id: int, source_channel_id: Optional[int]) -> bool:
        if user_id in self.state.visited_profiles:
            return False
        if user_id in self.inflight_profiles:
            return False
        self.inflight_profiles.add(user_id)
        return True

    async def _parse_profile_task(
        self,
        owner_chat: int,
        user: User,
        source_channel_id: Optional[int],
        attempt: int = 0,
        depth: int = 0,
        high_priority: bool = False,
    ) -> None:
        try:
            if self.stop_requested:
                return
            if high_priority:
                success = await self._parse_profile(owner_chat, user, source_channel_id, depth=depth)
            else:
                async with self.profile_semaphore:
                    if self.stop_requested:
                        return
                    success = await self._parse_profile(owner_chat, user, source_channel_id, depth=depth)
            if not success:
                self._enqueue_profile_retry(user, source_channel_id, attempt + 1, depth)
        finally:
            if user and user.id:
                self.inflight_profiles.discard(user.id)

    def _track_task(self, task: asyncio.Task) -> None:
        self.pending_tasks.add(task)
        task.add_done_callback(self.pending_tasks.discard)

    async def _profile_retry_loop(self, owner_chat: int) -> None:
        while self.state.running and not self.stop_requested:
            await self._drain_profile_retries(owner_chat)
            await asyncio.sleep(0.2)

    async def _resolve_sender(self, msg: Message):
        sender = None
        try:
            sender = await msg.get_sender()
        except Exception as e:
            logger.info("PROFILE SKIPPED reason=get_sender_error err=%s", e)
            try:
                await asyncio.sleep(0.1)
                sender = await msg.get_sender()
            except Exception:
                sender = None

        if sender is not None:
            return sender

        sender_id = getattr(msg, "sender_id", None)
        if sender_id:
            logger.info("PROFILE RETRY sender_id=%s message_id=%s", sender_id, getattr(msg, "id", None))
            try:
                return await self._limited_get_entity(sender_id)
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
                return await self._limited_get_entity(input_sender)
            except Exception:
                pass
        return None

    async def _handle_sender_entity(self, owner_chat: int, msg: Message, sender, depth: int = 0) -> bool:
        if isinstance(sender, Channel) and sender.username:
            sender_profile_url = f"tg://user?id={getattr(msg, 'sender_id', 0)}"
            self._schedule_candidate_processing(
                owner_chat,
                sender.username,
                source="comment",
                profile_url=sender_profile_url,
                depth=depth + 1,
            )
            self._schedule_channel_for_parsing(sender.id, depth + 1)
            return True
        if isinstance(sender, Channel):
            logger.info("PROFILE SKIPPED reason=channel_sender_without_username id=%s", sender.id)
            self._schedule_channel_for_parsing(sender.id, depth + 1)
            return True
        if isinstance(sender, User):
            return False
        return False

    async def _parse_messages(self, owner_chat: int, entity, limit: int, source: str, depth: int = 0) -> None:
        source_channel_id = getattr(entity, "id", None)
        processed = 0
        retry_messages: List[Message] = []
        retry_seen_ids: Set[int] = set()
        no_new_profiles_streak = 0
        try:
            async for msg in self.user_client.iter_messages(entity, limit=limit):
                if not self.state.running or self.stop_requested:
                    break
                processed += 1
                await asyncio.sleep(0.3)
                if source == "comment":
                    self.state.chat_processed_current = processed
                else:
                    self.state.channel_processed_current = processed
                if not isinstance(msg, Message):
                    continue

                self.state.message_count += 1
                sender = await self._resolve_sender(msg)
                new_profile_in_message = False
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
                        task = asyncio.create_task(
                            self._parse_profile_task(owner_chat, sender, source_channel_id, attempt=0, depth=depth + 1)
                        )
                        self._track_task(task)
                        new_profile_in_message = True
                else:
                    handled = await self._handle_sender_entity(owner_chat, msg, sender, depth=depth)
                    if not handled:
                        msg_id = getattr(msg, "id", None)
                        if msg_id and msg_id not in retry_seen_ids:
                            retry_seen_ids.add(msg_id)
                            retry_messages.append(msg)
                        else:
                            self.state.profiles_checked += 1
                            self.state.profiles_failed += 1
                            logger.info("PROFILE FAIL message_id=%s reason=sender_unavailable", msg.id)

                for candidate in self._extract_candidates_from_message(msg):
                    if self.stop_requested:
                        break
                    self._schedule_candidate_processing(owner_chat, candidate, source=source, depth=depth + 1)
                if self.stop_requested:
                    break
                if processed % 2 == 0:
                    await self._drain_profile_retries(owner_chat)
                if processed % 20 == 0:
                    await self._drain_main_queue(owner_chat)
                    await self._drain_profile_retries(owner_chat)
                while len(self.pending_tasks) > 200:
                    await asyncio.sleep(0.02)

                if new_profile_in_message:
                    no_new_profiles_streak = 0
                else:
                    no_new_profiles_streak += 1
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

        for attempt_idx in range(3):
            new_retry = []
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
                        task = asyncio.create_task(
                            self._parse_profile_task(owner_chat, sender, source_channel_id, attempt=0, depth=depth + 1)
                        )
                        self._track_task(task)
                    continue

                handled = await self._handle_sender_entity(owner_chat, msg, sender, depth=depth)
                if not handled:
                    sender_id = getattr(msg, "sender_id", None)
                    if sender_id:
                        try:
                            forced_entity = await self._limited_get_entity(sender_id)
                            if isinstance(forced_entity, User) and self._reserve_profile(
                                forced_entity.id, source_channel_id
                            ):
                                self.state.profiles_checked += 1
                                self.state.unique_profiles_processed += 1
                                if not getattr(forced_entity, "username", None):
                                    self.state.profiles_without_username_processed += 1
                                task = asyncio.create_task(
                                    self._parse_profile_task(
                                        owner_chat,
                                        forced_entity,
                                        source_channel_id,
                                        attempt=0,
                                        depth=depth + 1,
                                    )
                                )
                                self._track_task(task)
                                continue
                        except Exception:
                            pass
                    new_retry.append(msg)
                    if attempt_idx == 2:
                        self.state.profiles_checked += 1
                        self.state.profiles_failed += 1
                        logger.info("PROFILE FAIL message_id=%s reason=sender_unavailable_after_retry", msg_id)
            retry_messages = new_retry
            if not retry_messages:
                break
        await self._drain_main_queue(owner_chat)
        await self._drain_profile_retries(owner_chat)

    async def _parse_channel_entity(self, owner_chat: int, entity, source: str = "message", depth: int = 0) -> None:
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
        await self._parse_messages(owner_chat, entity, self.state.channel_limit, source=source, depth=depth)

    async def _resolve_linked_chat(self, entity):
        linked_chat_id = getattr(entity, "linked_chat_id", None)
        if not linked_chat_id:
            try:
                full = await self._limited_get_full_channel(entity)
                if isinstance(full, tuple) and full and full[0] == "DEFERRED":
                    return None
                linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
            except Exception:
                linked_chat_id = None
        if not linked_chat_id:
            return None
        try:
            linked = await self._limited_get_entity(linked_chat_id)
            if isinstance(linked, tuple) and linked and linked[0] == "DEFERRED":
                return None
            return linked
        except Exception:
            return None

    async def _parse_chat_entity(self, owner_chat: int, entity, depth: int = 0) -> None:
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
        await self._parse_messages(owner_chat, entity, self.state.chat_limit, source="comment", depth=depth)

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

        approved_channel_ids = set(self.state.approved_queue)
        approved_usernames = [
            username
            for channel_id, username in self.state.pending_approval.items()
            if channel_id in approved_channel_ids
        ]
        queue_usernames = [item[2] for item in self.state.main_queue]
        remaining_candidates = queue_usernames + approved_usernames + list(self.state.pending_approval.values())
        remaining_usernames: List[str] = []
        remaining_seen: Set[str] = set()
        for username in remaining_candidates:
            normalized = self._normalize_username(username)
            if self.state.username_state.get(normalized) == "DONE":
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
        self.seen_usernames.clear()
        self.state.started_at = time.time()
        self.profile_retry_task = None

        await self.bot_client.send_message(owner_chat, "🚀 Парсинг запущен. Этап 1: стартовые каналы.")

        while (
            self.state.main_queue
            or self.pending_tasks
            or self.state.channel_parse_queue
            or self.state.retry_profiles
        ) and self.state.running:
            if self.stop_requested:
                break
            await self._drain_main_queue(owner_chat, batch_size=100)
            await self._drain_profile_retries(owner_chat)
            await self._drain_channel_parse_queue(owner_chat, batch_size=300)
            if len(self.pending_tasks) > 200:
                await asyncio.sleep(0.3)
            if self.state.main_queue and not self.pending_tasks and not self.state.channel_parse_queue:
                next_run_at = self.state.main_queue[0][0]
                now = time.time()
                if next_run_at > now:
                    await asyncio.sleep(min(0.5, next_run_at - now))
            if not self.state.main_queue and not self.state.channel_parse_queue and self.pending_tasks:
                await asyncio.sleep(0.1)

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
        self.seen_usernames.clear()
        self.profile_retry_task = None
        self.state.visited_entities.clear()
        self.state.queued_channel_ids.clear()
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
            channel_id = self.state.approved_queue.popleft()
            self.state.approved_set.discard(channel_id)
            self.state.stage2_processed_channels += 1
            self.state.pending_approval.pop(channel_id, None)
            self._save_stage2_state()
            try:
                entity = await self._limited_get_entity(channel_id)
            except Exception:
                entity = None
            if not entity:
                continue
            username = self._normalize_username(getattr(entity, "username", None) or "")
            if username:
                self.state.username_state[username] = "DONE"
            task = asyncio.create_task(self._parse_channel_with_linked(owner_chat, entity, depth=0))
            self._track_task(task)
            if self.stop_requested:
                break
            await self._drain_main_queue(owner_chat)
            await self._drain_profile_retries(owner_chat)
            while len(self.pending_tasks) > 120:
                await asyncio.sleep(0.05)
        self.state.pending_approval.clear()
        self._save_stage2_state()
        await self.finish(owner_chat)

    async def stop(self, owner_chat: int) -> None:
        if self.stop_requested:
            return
        logger.info("STOP REQUESTED")
        self.stop_requested = True
        await asyncio.sleep(0.5)
        self.resolve_cache.clear()
        logger.info("STOP: awaiting %s tasks", len(self.pending_tasks))
        if self.pending_tasks:
            await asyncio.gather(*list(self.pending_tasks), return_exceptions=True)
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
            f"🔥 Каналов (всего): {self.state.found_count}\n"
            f"🎯 Каналов (300–7000): {self.state.found_filtered_count}\n"
            f"📦 Очередь: {len(self.state.main_queue)}\n"
            f"🟡 В работе: {sum(1 for v in self.state.username_state.values() if v == 'IN_PROGRESS')}\n"
            f"✅ Done: {sum(1 for v in self.state.username_state.values() if v == 'DONE')}\n"
            f"❌ Failed: {sum(1 for v in self.state.username_state.values() if v == 'FAILED')}\n"
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
        if self.profile_retry_task:
            self.profile_retry_task.cancel()
            await asyncio.gather(self.profile_retry_task, return_exceptions=True)
            self.profile_retry_task = None

        for _ in range(3):
            await self._drain_main_queue(owner_chat)
            await self._drain_profile_retries(owner_chat)
            await self._drain_channel_parse_queue(owner_chat)
            if self.pending_tasks:
                await asyncio.gather(*list(self.pending_tasks), return_exceptions=True)
                self.pending_tasks.clear()
            if not self.state.main_queue and not self.state.retry_profiles and not self.state.channel_parse_queue:
                break
        await asyncio.sleep(1)
        while self.pending_tasks:
            await asyncio.gather(*list(self.pending_tasks), return_exceptions=True)
            self.pending_tasks.clear()
            await asyncio.sleep(0.5)

        await self._export_results(owner_chat)
        if self.stop_requested:
            await self.bot_client.send_message(owner_chat, "⛔ Парсинг корректно остановлен.")
        else:
            await self.bot_client.send_message(owner_chat, "🏁 Парсинг завершён.")
        await self.bot_client.send_message(owner_chat, await self.progress_text())
        self.state.reset_runtime()
        self.seen_usernames.clear()
        self.resolving_now.clear()
        self.resolve_cache.clear()
        self._save_stage2_state()


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
        if not system.state.main_queue and not system.state.approved_queue:
            await event.respond("⚠️ Сначала отправьте входные каналы текстом.")
            return
        if system.state.main_queue:
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
        lowered = text.lower()
        if any(word in lowered for word in ["старт", "stop", "стоп", "progress", "прогресс"]):
            return
        if any(emoji in text for emoji in ["🚀", "⛔", "📊", "✅", "❌"]):
            return
        if len(text) < 5:
            return
        if "t.me" not in lowered and "@" not in text:
            return

        try:
            channel_limit, chat_limit, channels = parse_user_payload(text)
            if not channels:
                return

            system.state.channel_limit = channel_limit
            system.state.chat_limit = chat_limit

            for channel in channels:
                system._queue_add(channel, approved=False, source="seed")

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
            token = data.split(b":", 1)[1].decode(errors="ignore").strip().lower()
            approved = False
            if token.isdigit():
                channel_id = int(token)
                username = system.state.pending_approval.get(channel_id)
                if username:
                    system._queue_add(username, approved=True, channel_id=channel_id)
                    approved = True
            await event.answer("Добавлено в очередь этапа 2" if approved else "Канал уже обработан")
        elif data.startswith(CALLBACK_SKIP):
            token = data.split(b":", 1)[1].decode(errors="ignore").strip().lower()
            removed = False
            if token.isdigit():
                removed = system.state.pending_approval.pop(int(token), None) is not None
            elif token:
                for channel_id, pending_username in list(system.state.pending_approval.items()):
                    if pending_username == token:
                        del system.state.pending_approval[channel_id]
                        removed = True
                        break
            if removed:
                system._save_stage2_state()
            await event.answer("Пропущено" if removed else "Уже обработано")
        elif data == CALLBACK_STAGE2_YES:
            system.awaiting_stage2_confirmation = False
            system.state.main_queue.clear()
            system.state.in_queue.clear()
            system.state.channel_parse_queue.clear()
            system.state.channel_parse_in_queue.clear()
            await event.answer("Запускаю этап 2")
            if system.state.approved_queue:
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
