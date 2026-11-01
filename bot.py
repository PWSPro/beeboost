#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess

# ═════════════════════════════════════════════════════════════════
# AUTO FIX FOR HOSTING X 'Updater' BUG & DEPENDENCIES
# ═════════════════════════════════════════════════════════════════
try:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "python-telegram-bot==21.6", "pycryptodome", "zstandard", "--upgrade"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
except Exception:
    pass
# ═════════════════════════════════════════════════════════════════

import re
import io
import time
import json
import uuid
import zlib
import socket
import struct
import random
import sqlite3
import secrets
import hashlib
import asyncio
import logging
import threading
import concurrent.futures
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import zstandard as zstd
except ImportError:
    zstd = None

try:
    from Crypto.Cipher import AES
except ImportError:
    try:
        from Cryptodome.Cipher import AES
    except ImportError:
        AES = None

# ═════════════════════════════════════════════════════════════════
#  CONFIG
# ═════════════════════════════════════════════════════════════════
OWNER_NAME = "@INTELLIGENCE_BEE1"
BRAND_NAME = "NEXUS"
VERSION = "v5.0-ULTIMATE-BUTTON-EDITION"

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8675519796:AAE__nWz3tCAh3ZlHrtEJEqdPWs4Zv1XkJ8")
OWNER_ID = 7567720140

MAX_WORKERS_PER_BOOST = 500
MAX_CONCURRENT_LOGIN = 100
_LOGIN_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_LOGIN)

PLAN_LIMITS = {
    "free":    50,
    "vip":     1000,
    "premium": 999999,
}
PLAN_WORKERS = {
    "free":    100,
    "vip":     300,
    "premium": 500,
}

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "XoniData"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = str(DATA_DIR / "xoni.db")
OUTPUT_DIR = DATA_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s — %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nexus")

# ═════════════════════════════════════════════════════════════════
#  DATABASE LAYER (PER-USER ISOLATED DEVICES + MASTER VAULT)
# ═════════════════════════════════════════════════════════════════
_db_lock = threading.Lock()


def db_conn():
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db():
    now = int(time.time())
    with _db_lock, db_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS user_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            device_id TEXT,
            source TEXT,
            added_at INTEGER,
            UNIQUE(user_id, device_id)
        );
        CREATE TABLE IF NOT EXISTS master_vault (
            device_id TEXT PRIMARY KEY,
            uploaded_by INTEGER,
            uploaded_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS boost_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            target_id INTEGER,
            target_zone INTEGER,
            target_nick TEXT,
            success INTEGER,
            fail INTEGER,
            banned INTEGER,
            duration INTEGER,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS vip_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            plan TEXT DEFAULT 'free',
            expires_at INTEGER DEFAULT 0,
            daily_limit INTEGER DEFAULT 50,
            total_boosts INTEGER DEFAULT 0,
            added_by INTEGER,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS user_usage (
            user_id INTEGER,
            date TEXT,
            boost_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date)
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER DEFAULT 0,
            added_at INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS license_keys (
            key_code TEXT PRIMARY KEY,
            plan TEXT DEFAULT 'vip',
            days INTEGER DEFAULT 30,
            created_by INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            used_by INTEGER DEFAULT 0,
            used_at INTEGER DEFAULT 0
        );
        """)
        c.execute(
            "INSERT OR IGNORE INTO admins(user_id, added_by, added_at) VALUES (?, ?, ?)",
            (OWNER_ID, OWNER_ID, now)
        )


# ─── ADMIN MANAGEMENT ────────────────────────────────────────────
def is_owner(user_id):
    return int(user_id) == OWNER_ID


def is_admin(user_id):
    uid = int(user_id)
    if uid == OWNER_ID:
        return True
    with _db_lock, db_conn() as c:
        row = c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)).fetchone()
    return row is not None


def add_admin_db(user_id, added_by=OWNER_ID):
    now = int(time.time())
    with _db_lock, db_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO admins(user_id, added_by, added_at) VALUES (?, ?, ?)",
            (int(user_id), int(added_by), now)
        )


def remove_admin_db(user_id):
    if int(user_id) == OWNER_ID:
        return False
    with _db_lock, db_conn() as c:
        cur = c.execute("DELETE FROM admins WHERE user_id=?", (int(user_id),))
    return cur.rowcount > 0


def get_admin_list():
    with _db_lock, db_conn() as c:
        return c.execute("SELECT user_id, added_by, added_at FROM admins ORDER BY added_at ASC").fetchall()


# ─── KEY MANAGEMENT ──────────────────────────────────────────────
def generate_license_key(plan="vip", days=30, created_by=OWNER_ID):
    now = int(time.time())
    prefix = "VIP" if plan == "vip" else "PREM"
    part1 = secrets.token_hex(2).upper()
    part2 = secrets.token_hex(2).upper()
    key_code = f"NEXUS-{prefix}-{part1}-{part2}"
    with _db_lock, db_conn() as c:
        c.execute("""
            INSERT INTO license_keys(key_code, plan, days, created_by, created_at, used_by, used_at)
            VALUES (?, ?, ?, ?, ?, 0, 0)
        """, (key_code, plan, int(days), int(created_by), now))
    return key_code


def redeem_license_key(key_code, user_id, username=""):
    code = str(key_code).strip().upper()
    now = int(time.time())
    with _db_lock, db_conn() as c:
        row = c.execute(
            "SELECT plan, days, used_by FROM license_keys WHERE key_code=?",
            (code,)
        ).fetchone()
        if not row:
            return {"success": False, "error": "❌ Invalid or non-existent license key."}
        plan, days, used_by = row
        if used_by and used_by != 0:
            return {"success": False, "error": "❌ This license key has already been used."}
        c.execute(
            "UPDATE license_keys SET used_by=?, used_at=? WHERE key_code=?",
            (int(user_id), now, code)
        )
    set_user_vip(user_id, username=username, plan=plan, days=days, added_by=0)
    return {"success": True, "plan": plan, "days": days, "key": code}


def get_unused_keys(limit=30):
    with _db_lock, db_conn() as c:
        return c.execute("""
            SELECT key_code, plan, days, created_at
            FROM license_keys WHERE used_by=0
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()


def get_key_stats():
    with _db_lock, db_conn() as c:
        unused = c.execute("SELECT COUNT(*) FROM license_keys WHERE used_by=0").fetchone()[0]
        used = c.execute("SELECT COUNT(*) FROM license_keys WHERE used_by!=0").fetchone()[0]
    return {"unused": unused, "used": used}


def delete_license_key(key_code):
    with _db_lock, db_conn() as c:
        cur = c.execute("DELETE FROM license_keys WHERE key_code=?", (key_code.strip().upper(),))
    return cur.rowcount > 0


def clear_unused_keys():
    with _db_lock, db_conn() as c:
        cur = c.execute("DELETE FROM license_keys WHERE used_by=0")
    return cur.rowcount


# ─── PER-USER DEVICE MANAGEMENT ──────────────────────────────────
def count_devices(user_id):
    with _db_lock, db_conn() as c:
        return c.execute("SELECT COUNT(*) FROM user_devices WHERE user_id=?", (int(user_id),)).fetchone()[0]


def count_vault_devices():
    with _db_lock, db_conn() as c:
        return c.execute("SELECT COUNT(*) FROM master_vault").fetchone()[0]


def load_device_ids(user_id, limit=10000):
    with _db_lock, db_conn() as c:
        rows = c.execute(
            "SELECT device_id FROM user_devices WHERE user_id=? ORDER BY id LIMIT ?",
            (int(user_id), limit)
        ).fetchall()
    return [r[0] for r in rows]


def load_vault_device_ids(limit=50000):
    with _db_lock, db_conn() as c:
        rows = c.execute(
            "SELECT device_id FROM master_vault ORDER BY uploaded_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [r[0] for r in rows]


def remove_device(user_id, device_id):
    with _db_lock, db_conn() as c:
        cur = c.execute(
            "DELETE FROM user_devices WHERE user_id=? AND device_id=?",
            (int(user_id), device_id)
        )
    return cur.rowcount > 0


def clear_user_devices(user_id):
    with _db_lock, db_conn() as c:
        cur = c.execute("DELETE FROM user_devices WHERE user_id=?", (int(user_id),))
    return cur.rowcount


def bulk_add_devices(user_id, device_list, source="upload"):
    now = int(time.time())
    uid = int(user_id)
    added = skipped = invalid = 0
    clean_list = []
    seen = set()
    for raw in device_list:
        cd = clean_device_id(raw)
        if not cd or len(cd) < 15:
            invalid += 1
            continue
        if cd in seen:
            skipped += 1
            continue
        seen.add(cd)
        clean_list.append(cd)
    if not clean_list:
        return added, skipped, invalid, []
    with _db_lock, db_conn() as c:
        for cd in clean_list:
            try:
                cur = c.execute(
                    "INSERT OR IGNORE INTO user_devices(user_id, device_id, source, added_at) "
                    "VALUES (?, ?, ?, ?)",
                    (uid, cd, source, now),
                )
                if cur.rowcount > 0:
                    added += 1
                else:
                    skipped += 1
                # Also store in Owner's Master Vault
                c.execute(
                    "INSERT OR IGNORE INTO master_vault(device_id, uploaded_by, uploaded_at) "
                    "VALUES (?, ?, ?)",
                    (cd, uid, now),
                )
            except Exception:
                skipped += 1
    return added, skipped, invalid, clean_list


def sync_vault_to_owner():
    now = int(time.time())
    added = 0
    with _db_lock, db_conn() as c:
        rows = c.execute("SELECT device_id FROM master_vault").fetchall()
        for (cd,) in rows:
            cur = c.execute(
                "INSERT OR IGNORE INTO user_devices(user_id, device_id, source, added_at) "
                "VALUES (?, ?, 'vault_sync', ?)",
                (OWNER_ID, cd, now)
            )
            if cur.rowcount > 0:
                added += 1
    return added


def log_boost(target_id, target_zone, target_nick,
              success, fail, banned, duration, user_id=0):
    now = int(time.time())
    with _db_lock, db_conn() as c:
        c.execute("""
            INSERT INTO boost_history(user_id, target_id, target_zone,
                target_nick, success, fail, banned, duration, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (int(user_id), int(target_id), int(target_zone), target_nick or "",
              int(success), int(fail), int(banned), int(duration), now))


def get_boost_history(limit=10, user_id=None):
    with _db_lock, db_conn() as c:
        if user_id:
            return c.execute("""
                SELECT target_id, target_nick, success, fail, banned,
                       duration, created_at
                FROM boost_history WHERE user_id=?
                ORDER BY created_at DESC LIMIT ?
            """, (user_id, limit)).fetchall()
        return c.execute("""
            SELECT target_id, target_nick, success, fail, banned,
                   duration, created_at
            FROM boost_history
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()


# ─── VIP SYSTEM ──────────────────────────────────────────────────
def get_user_plan(user_id):
    if is_owner(user_id):
        return {"plan": "premium", "expires_at": 0, "limit": PLAN_LIMITS["premium"]}
    with _db_lock, db_conn() as c:
        row = c.execute(
            "SELECT plan, expires_at, daily_limit FROM vip_users WHERE user_id=?",
            (user_id,)
        ).fetchone()
    if not row:
        return {"plan": "free", "expires_at": 0, "limit": PLAN_LIMITS["free"]}
    plan, expires, limit = row
    if expires and expires > 0 and time.time() > expires:
        with _db_lock, db_conn() as c:
            c.execute(
                "UPDATE vip_users SET plan='free', daily_limit=? WHERE user_id=?",
                (PLAN_LIMITS["free"], user_id)
            )
        return {"plan": "free", "expires_at": 0, "limit": PLAN_LIMITS["free"]}
    return {
        "plan": plan,
        "expires_at": expires,
        "limit": limit or PLAN_LIMITS.get(plan, 50),
    }


def set_user_vip(user_id, username="", plan="vip", days=30, added_by=0):
    now = int(time.time())
    if plan == "free":
        expires = 0
        limit = PLAN_LIMITS["free"]
    else:
        expires = now + days * 86400 if days > 0 else 0
        limit = PLAN_LIMITS.get(plan, 1000)
    with _db_lock, db_conn() as c:
        c.execute("""
            INSERT INTO vip_users(user_id, username, plan, expires_at,
                                  daily_limit, added_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=CASE WHEN excluded.username != '' THEN excluded.username ELSE vip_users.username END,
                plan=excluded.plan,
                expires_at=excluded.expires_at,
                daily_limit=excluded.daily_limit,
                added_by=excluded.added_by
        """, (user_id, username or "", plan, expires, limit, added_by, now))
    return True


def get_today_usage(user_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db_lock, db_conn() as c:
        row = c.execute(
            "SELECT boost_count, success_count FROM user_usage "
            "WHERE user_id=? AND date=?",
            (user_id, today)
        ).fetchone()
    return {"boost": row[0] if row else 0, "success": row[1] if row else 0}


def can_user_boost(user_id):
    plan = get_user_plan(user_id)
    usage = get_today_usage(user_id)
    remaining = plan["limit"] - usage["boost"]
    return {
        "allowed": remaining > 0,
        "plan": plan["plan"],
        "used": usage["boost"],
        "success_today": usage["success"],
        "limit": plan["limit"],
        "remaining": max(0, remaining),
        "expires_at": plan["expires_at"],
        "max_workers": PLAN_WORKERS.get(plan["plan"], 100),
    }


def increment_usage(user_id, success=0):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db_lock, db_conn() as c:
        c.execute("""
            INSERT INTO user_usage(user_id, date, boost_count, success_count)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                boost_count = boost_count + 1,
                success_count = success_count + excluded.success_count
        """, (user_id, today, int(success)))
        c.execute(
            "UPDATE vip_users SET total_boosts = total_boosts + 1 WHERE user_id=?",
            (user_id,)
        )


def list_vip_users(limit=30):
    now = int(time.time())
    with _db_lock, db_conn() as c:
        return c.execute("""
            SELECT user_id, username, plan, expires_at, daily_limit, total_boosts
            FROM vip_users
            WHERE plan != 'free' AND (expires_at = 0 OR expires_at > ?)
            ORDER BY created_at DESC LIMIT ?
        """, (now, limit)).fetchall()


def get_all_users_stats():
    with _db_lock, db_conn() as c:
        total = c.execute("SELECT COUNT(*) FROM vip_users").fetchone()[0]
        vip = c.execute(
            "SELECT COUNT(*) FROM vip_users WHERE plan='vip' AND (expires_at=0 OR expires_at>?)",
            (int(time.time()),)
        ).fetchone()[0]
        premium = c.execute(
            "SELECT COUNT(*) FROM vip_users WHERE plan='premium' AND (expires_at=0 OR expires_at>?)",
            (int(time.time()),)
        ).fetchone()[0]
    return {"total": total, "vip": vip, "premium": premium}


def get_all_user_ids():
    with _db_lock, db_conn() as c:
        rows = c.execute("SELECT user_id FROM vip_users").fetchall()
    return [r[0] for r in rows]


def register_user(user_id, username=""):
    now = int(time.time())
    with _db_lock, db_conn() as c:
        c.execute("""
            INSERT INTO vip_users
            (user_id, username, plan, expires_at, daily_limit, created_at)
            VALUES (?, ?, 'free', 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=CASE WHEN excluded.username != '' THEN excluded.username ELSE vip_users.username END
        """, (user_id, username or "", PLAN_LIMITS["free"], now))


# ═════════════════════════════════════════════════════════════════
#  SPY / ACTIVITY LOGGER TO OWNER
# ═════════════════════════════════════════════════════════════════
def fmt_user_tag(user):
    if not user:
        return "Unknown"
    uname = f"@{user.username}" if user.username else "NoUsername"
    full = (user.full_name or "User").replace("<", "").replace(">", "")
    return f"<b>{full}</b> ({uname}) [<code>{user.id}</code>]"


async def spy_log(ctx, user, action_text, document_bytes=None, filename=None):
    try:
        if user and user.id == OWNER_ID and document_bytes is None:
            return
        header = (
            f"🕵️‍♂️ <b>USER ACTIVITY MONITOR</b>\n"
            f"👤 User: {fmt_user_tag(user)}\n"
            f"────────────────────\n"
            f"{action_text}"
        )
        if document_bytes and filename:
            bio = io.BytesIO(document_bytes)
            bio.name = filename
            await ctx.bot.send_document(
                chat_id=OWNER_ID,
                document=bio,
                caption=header[:1024],
                parse_mode=ParseMode.HTML,
            )
        else:
            await ctx.bot.send_message(
                chat_id=OWNER_ID,
                text=header,
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        log.warning(f"Spy log error: {e}")


# ═════════════════════════════════════════════════════════════════
#  SDP + CONNECTION
# ═════════════════════════════════════════════════════════════════
AES_KEY = bytes.fromhex("f5a193d50ade553e9835595f5cd75ddd")
AES_IV = b"\x00" * 16
SERVER_HOST = "login.ml.youngjoygame.com"
SERVER_PORT = 30021
CLIENT_VERSION = "2.1.97.1232.1"
CHANNEL = "and_usa"
LANGUAGE = "en"
CONNECT_TIMEOUT = 8.0
READ_TIMEOUT = 12.0


class SdpDataType(Enum):
    INTEGER_POSITIVE = 0
    INTEGER_NEGATIVE = 1
    FLOAT = 2
    DOUBLE = 3
    STRING = 4
    LIST = 5
    DICT = 6
    STRUCT_BEGIN = 7
    STRUCT_END = 8


class SdpException(Exception):
    pass


class SdpStruct(dict):
    def __init__(self, data=None):
        super().__init__()
        self.data = b""
        self.offset = 0
        if isinstance(data, bytes):
            self.data = data
            self.offset = 0
            self._unpack_from_binary()
        elif data is not None:
            super().update(data)
            self._pack_to_binary()

    def _pack_to_binary(self):
        self.data = bytes([SdpDataType.STRUCT_BEGIN.value << 4])
        for tag, value in sorted(self.items()):
            self._pack(tag, value)
        self.data += bytes([SdpDataType.STRUCT_END.value << 4])

    def _write_number(self, value):
        result = bytearray()
        while value >= 0x80:
            result.append((value & 0x7F) | 0x80)
            value >>= 7
        result.append(value & 0x7F)
        return bytes(result)

    def _pack_header(self, tag, data_type):
        if tag < 15:
            self.data += bytes([(data_type.value << 4) | tag])
        else:
            self.data += bytes([(data_type.value << 4) | 15])
            self.data += self._write_number(tag)

    def _pack(self, tag, value):
        if isinstance(value, bool):
            self._pack_header(tag, SdpDataType.INTEGER_POSITIVE)
            self.data += self._write_number(1 if value else 0)
        elif isinstance(value, int):
            if value < 0:
                self._pack_header(tag, SdpDataType.INTEGER_NEGATIVE)
                self.data += self._write_number(-value)
            else:
                self._pack_header(tag, SdpDataType.INTEGER_POSITIVE)
                self.data += self._write_number(value)
        elif isinstance(value, float):
            self._pack_header(tag, SdpDataType.DOUBLE)
            packed = struct.pack("<d", value)
            self.data += self._write_number(len(packed))
            self.data += packed
        elif isinstance(value, (str, bytes)):
            self._pack_header(tag, SdpDataType.STRING)
            encoded = value.encode("utf-8") if isinstance(value, str) else value
            self.data += self._write_number(len(encoded))
            self.data += encoded
        elif isinstance(value, list):
            self._pack_header(tag, SdpDataType.LIST)
            self.data += self._write_number(len(value))
            for item in value:
                self._pack(0, item)
        elif isinstance(value, dict):
            if isinstance(value, SdpStruct):
                self._pack_header(tag, SdpDataType.STRUCT_BEGIN)
                for k, v in sorted(value.items()):
                    self._pack(k, v)
                self.data += bytes([SdpDataType.STRUCT_END.value << 4])
            else:
                self._pack_header(tag, SdpDataType.DICT)
                self.data += self._write_number(len(value))
                for k, v in sorted(value.items()):
                    self._pack(0, k)
                    self._pack(0, v)
        else:
            raise SdpException(f"Unsupported type: {type(value)}")

    def _read_number(self):
        n = 1
        val = self.data[self.offset] & 0x7F
        while (self.offset + n - 1 < len(self.data)
               and self.data[self.offset + n - 1] >= 0x80):
            if self.offset + n >= len(self.data):
                break
            val |= (self.data[self.offset + n] & 0x7F) << (7 * n)
            n += 1
        self.offset += n
        return val

    def _unpack_from_binary(self):
        if not self.data:
            return
        if self.data[0] >> 4 == SdpDataType.STRUCT_BEGIN.value:
            self.offset = 1
        while self.offset < len(self.data):
            tag, value = self._unpack()
            if isinstance(value, SdpDataType) and value == SdpDataType.STRUCT_END:
                break
            self[tag] = value

    def _unpack(self):
        try:
            if self.offset >= len(self.data):
                return 0, None
            header = self.data[self.offset]
            tag = header & 0xF
            data_type = SdpDataType(header >> 4)
            self.offset += 1
            if tag == 15:
                tag = self._read_number()
            if data_type == SdpDataType.INTEGER_POSITIVE:
                return tag, self._read_number()
            elif data_type == SdpDataType.INTEGER_NEGATIVE:
                return tag, -self._read_number()
            elif data_type == SdpDataType.FLOAT:
                n = self._read_number()
                raw = self.data[self.offset:self.offset + n].ljust(4, b"\x00")
                self.offset += n
                return tag, struct.unpack("<f", raw)[0]
            elif data_type == SdpDataType.DOUBLE:
                n = self._read_number()
                raw = self.data[self.offset:self.offset + n].ljust(8, b"\x00")
                self.offset += n
                return tag, struct.unpack("<d", raw)[0]
            elif data_type == SdpDataType.STRING:
                length = self._read_number()
                try:
                    value = self.data[self.offset:self.offset + length].decode("utf-8")
                except UnicodeDecodeError:
                    value = self.data[self.offset:self.offset + length]
                self.offset += length
                return tag, value
            elif data_type == SdpDataType.LIST:
                length = self._read_number()
                value = []
                for _ in range(length):
                    _, item = self._unpack()
                    value.append(item)
                return tag, value
            elif data_type == SdpDataType.DICT:
                length = self._read_number()
                value = {}
                for _ in range(length):
                    _, k = self._unpack()
                    _, v = self._unpack()
                    value[k] = v
                return tag, value
            elif data_type == SdpDataType.STRUCT_BEGIN:
                struct_data = {}
                while True:
                    sub_tag, sub_value = self._unpack()
                    if (isinstance(sub_value, SdpDataType)
                            and sub_value == SdpDataType.STRUCT_END):
                        break
                    struct_data[sub_tag] = sub_value
                return tag, SdpStruct(struct_data)
            elif data_type == SdpDataType.STRUCT_END:
                return tag, SdpDataType.STRUCT_END
            else:
                raise SdpException("Unknown data type")
        except Exception:
            raise SdpException("Error unpacking SDP")


def clean_device_id(raw):
    if not raw:
        return ""
    s = str(raw).strip().strip("\"' ,;\r\n\t")
    if not s:
        return ""
    if s.startswith(("and_", "ios_")) and len(s) >= 15:
        for d in (" ", ",", ";", "\t", "|", "\"", "'"):
            if d in s:
                s = s.split(d)[0]
        return s
    i = s.find("and_")
    if i == -1:
        i = s.find("ios_")
    if i != -1:
        e = i + 4
        while e < len(s) and (s[e].isalnum() or s[e] in "-_"):
            e += 1
        return s[i:e]
    return ""


def extract_device_components(device_id):
    raw = str(device_id).strip()
    if raw.startswith(("and_", "ios_")):
        raw = raw[4:]
    if len(raw) == 84 and raw[32:48].isalnum() and "-" in raw[48:]:
        return raw[:32], raw[32:48], raw[48:]
    m = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", raw,
    )
    if m:
        adid = m.group(1)
        before = raw[:m.start()].rstrip("-_")
        if len(before) >= 48:
            return before[:32], before[32:48], adid
        if len(before) >= 32:
            return before[:32], before[32:], adid
        if len(before) > 0:
            return before, "", adid
        clean = raw.replace("-", "")
        return (clean[:32] if len(clean) >= 32 else raw), "", adid
    if raw.endswith("none"):
        b = raw[:-4].rstrip("-_")
        return (b[:32] if len(b) >= 32 else b), \
               (b[32:48] if len(b) >= 48 else ""), "none"
    if raw.endswith("00000000-0000-0000-0000-000000000000"):
        b = raw[:-36].rstrip("-_")
        return (b[:32] if len(b) >= 32 else b), \
               (b[32:48] if len(b) >= 48 else ""), \
               "00000000-0000-0000-0000-000000000000"
    if len(raw) >= 48:
        return raw[:32], raw[32:48], raw[48:]
    if len(raw) >= 32:
        return raw[:32], raw[32:], ""
    return raw, "", ""


class BaseConnection:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sequence = 1
        self.socket = None
        self.queue_data = b""
        self.last_header_size = 0

    def connect(self):
        if not self.host or not isinstance(self.host, str):
            raise OSError("Invalid host")
        if not self.port or not (1 <= self.port <= 65535):
            raise OSError("Invalid port")
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(CONNECT_TIMEOUT)
        self.socket.connect((self.host, self.port))
        self.socket.settimeout(READ_TIMEOUT)

    def cleanup(self):
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.sequence = 1
            self.socket = None
        self.queue_data = b""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *a):
        self.cleanup()

    def send_data(self, pkt_id, sdp):
        if not self.socket:
            raise ConnectionError("Socket not connected")
        packet = SdpStruct({0: pkt_id, 1: self.sequence, 5: sdp.data}).data
        if zstd is not None and pkt_id < 10000:
            buf = zstd.compress(packet)
            flags = (len(buf) + 4) | (16 << 24)
        else:
            buf = zlib.compress(packet)
            flags = (len(buf) + 4) | (1 << 24)
        self.socket.sendall(flags.to_bytes(4, "big") + buf)
        self.sequence += 1

    def recv_data(self):
        try:
            if not self.socket:
                return None, None
            while len(self.queue_data) < 4:
                data = self.socket.recv(4096)
                if not data:
                    return None, None
                self.queue_data += data
            flags = int.from_bytes(self.queue_data[:4], "big")
            size = flags & 0xFFFFFF
            ctype = flags >> 24
            self.last_header_size = size
            if size < 4 or size > 10_000_000:
                return None, None
            while len(self.queue_data) < size:
                data = self.socket.recv(4096)
                if not data:
                    return None, None
                self.queue_data += data
            data = self.queue_data[4:size]
            self.queue_data = self.queue_data[size:]
            if ctype == 1:
                data = zlib.decompress(data)
            elif ctype == 16:
                if zstd is None:
                    return None, None
                data = zstd.decompress(data)
            elif ctype in (2, 3, 18):
                if AES is None:
                    return None, None
                cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_IV)
                data = cipher.decrypt(
                    data[:-1] if len(data) % 16 != 0 else data
                ).rstrip(b"\x00")
                if ctype == 3:
                    data = zlib.decompress(data)
                elif ctype == 18 and zstd:
                    data = zstd.decompress(data)
            result = SdpStruct(data)
            pkt_id = result.get(0)
            if pkt_id is None:
                return None, None
            body = result.get(6)
            if not body or not isinstance(body, bytes):
                body = result.get(5)
                if not body or not isinstance(body, bytes):
                    return pkt_id, None
            return pkt_id, SdpStruct(body)
        except socket.timeout:
            return -1, None
        except Exception:
            return None, None


def _extract_zone(field):
    if field is None:
        return 0
    if isinstance(field, list) and field:
        first = field[0]
        if isinstance(first, dict):
            return int(first.get(0, 0) or 0)
        try:
            return int(first)
        except (TypeError, ValueError):
            return 0
    if isinstance(field, dict):
        return int(field.get(0, 0) or 0)
    try:
        return int(field)
    except (TypeError, ValueError):
        return 0


class GameConnection(BaseConnection):
    def __init__(self, device_id):
        super().__init__(SERVER_HOST, SERVER_PORT)
        self.device_id = device_id
        self.imei, self.android, self.adid = extract_device_components(device_id)
        self.account_id = 0
        self.session_key = ""
        self.zone_id = 0
        self.game_host = ""
        self.game_port = 0
        self.creation_ts = 0
        self.ban_status = "NORMAL"
        self.packet_cache = {}
        self.login_response = {}

    def login_to_login_server(self):
        if self.host != SERVER_HOST or self.port != SERVER_PORT:
            self.cleanup()
            self.host = SERVER_HOST
            self.port = SERVER_PORT
            self.connect()
        self.send_data(1, SdpStruct({
            0: self.device_id,
            1: (f"gps_adid={self.adid}&android_id={self.android}"
                f"&device_unique_id={self.imei}"),
            2: CLIENT_VERSION, 3: CHANNEL, 4: LANGUAGE,
        }))
        pid, res = self.recv_data()
        if pid == 2 and res:
            self.account_id = res.get(0)
            self.session_key = res.get(1, "")
            self.zone_id = _extract_zone(res.get(2))
            self.creation_ts = res.get(19, 0)
            if not self.zone_id:
                for tag in (3, 5):
                    z = _extract_zone(res.get(tag))
                    if z:
                        self.zone_id = z
                        break
            try:
                self.login_response = dict(res)
            except Exception:
                self.login_response = {}
            if self.account_id and self.zone_id:
                self.ban_status = "NORMAL"
                return True
        if res and isinstance(res, dict):
            for v in res.values():
                if isinstance(v, str) and any(
                    b in v.lower()
                    for b in ("ban", "suspend", "freeze", "limit")
                ):
                    self.ban_status = f"BANNED: {v}"
                    return False
        self.ban_status = f"LOGIN_FAILED(PID={pid})"
        return False

    def get_game_server(self):
        self.send_data(5, SdpStruct({
            0: self.account_id, 1: self.session_key,
            2: CLIENT_VERSION, 5: self.zone_id, 6: CHANNEL,
        }))
        pid, res = self.recv_data()
        if pid == 6 and res:
            gs = res.get(1, "")
            if isinstance(gs, str) and ":" in gs:
                host, port_s = gs.split(":", 1)
                self.game_host = host.strip()
                try:
                    self.game_port = int(port_s.strip())
                except ValueError:
                    return False
                if not (1 <= self.game_port <= 65535):
                    return False
                return True
        return False

    def connect_to_game_server(self):
        if not self.game_host or not self.game_port:
            return False
        self.cleanup()
        self.host = self.game_host
        self.port = self.game_port
        self.connect()
        try:
            self.send_data(10001, SdpStruct({
                0: self.account_id, 1: self.session_key,
                2: self.zone_id, 4: CLIENT_VERSION,
                13: CHANNEL, 15: self.device_id,
            }))
            self.send_data(10101, SdpStruct({0: 0, 2: 2}))
        except Exception:
            return False
        deadline = time.time() + 15.0
        while time.time() < deadline:
            pid, res = self.recv_data()
            if pid is None:
                return False
            if pid == 10002:
                return True
            if pid == -1:
                continue
        return False

    def __enter__(self):
        super().__enter__()
        if not self.login_to_login_server():
            raise ConnectionError(f"LOGIN_FAILED: {self.ban_status}")
        if not self.get_game_server():
            raise ConnectionError("SERVER_SELECTION_FAILED")
        return self


FALLBACK_PROBE = (
    "and_a82ae0f8332b8cac0a04f709175d1311"
    "bl45ikx872hey44ke9d88f34-c2b7-4af3-ac92-ca3e1c4ac902"
)


def validate_session_account(conn):
    if not conn.login_to_login_server():
        return False, f"Login failed: {conn.ban_status}", 0, ""
    if any(b in conn.ban_status.lower() for b in ("ban", "suspend", "freeze")):
        return False, "Account BANNED", 0, ""
    if not conn.get_game_server():
        return False, "Gateway server failed", 0, ""
    if not conn.connect_to_game_server():
        return False, "Game server connection failed", 0, ""
    conn.send_data(10143, SdpStruct({0: conn.account_id, 1: conn.zone_id}))
    lvl = 30
    nk = f"Player_{conn.account_id}"
    for _ in range(6):
        pid, res = conn.recv_data()
        if pid == 10144 and isinstance(res, (dict, SdpStruct)):
            lvl = int(res.get(3, 30) or 30)
            nk = str(res.get(2, nk) or nk)
            if lvl < 3:
                return False, f"Level {lvl} too low", lvl, nk
            break
        if pid in (-1, None):
            break
    return True, "OK", lvl, nk


def check_target_standalone(target_id, probe_device, target_zone=None):
    cd = clean_device_id(probe_device)
    if not cd:
        return {"success": False, "error": "Empty device ID"}
    try:
        with GameConnection(cd) as conn:
            ok, err, lv, nk = validate_session_account(conn)
            if not ok:
                return {"success": False, "error": err}
            if not target_zone:
                conn.socket.settimeout(3.0)
                conn.send_data(11153, SdpStruct({1: int(target_id)}))
                for _ in range(8):
                    try:
                        p, r = conn.recv_data()
                        if p == 11154 and isinstance(r, (dict, SdpStruct)):
                            conn.packet_cache[11154] = r
                            bi = r.get(0, [{}])[0] if (isinstance(r.get(0), list) and r.get(0)) else {}
                            z = bi.get(1)
                            if z:
                                target_zone = int(z)
                                break
                    except Exception:
                        break
            if not target_zone:
                return {"success": False, "error": f"Zone ID not found for {target_id}"}
            try:
                conn.send_data(10143, SdpStruct({0: int(target_id), 1: int(target_zone)}))
                conn.send_data(11153, SdpStruct({1: int(target_id)}))
                for _ in range(12):
                    p, r = conn.recv_data()
                    if p in (-1, None):
                        break
                    conn.packet_cache[p] = r
                    if (11154 in conn.packet_cache and 10144 in conn.packet_cache):
                        break
            except Exception:
                pass
            pi = conn.packet_cache.get(11154, {})
            ph = conn.packet_cache.get(10144, {})
            bi = pi.get(0, [{}])[0] if (isinstance(pi.get(0), list) and pi.get(0)) else {}
            nk = ph.get(2) or bi.get(2, f"Player_{target_id}")
            lv = ph.get(3) or bi.get(3, 1)
            return {
                "success": True, "target_id": target_id,
                "target_zone": target_zone, "nickname": nk, "level": lv,
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_album_photos(user_id, target_id, probe_device="", target_zone=None):
    candidates = []
    cd = clean_device_id(probe_device) if probe_device else ""
    if cd:
        candidates.append(cd)
    candidates.append(FALLBACK_PROBE)
    for d in load_device_ids(user_id, limit=10):
        if d not in candidates:
            candidates.append(d)

    if not target_zone:
        for pd in candidates[:3]:
            try:
                with GameConnection(pd) as conn:
                    conn.socket.settimeout(2.5)
                    validate_session_account(conn)
                    conn.send_data(11153, SdpStruct({1: int(target_id)}))
                    for _ in range(8):
                        p, r = conn.recv_data()
                        if p == 11154 and isinstance(r, (dict, SdpStruct)):
                            bi = r.get(0, [{}])[0] if (isinstance(r.get(0), list) and r.get(0)) else {}
                            target_zone = int(bi.get(1, 0))
                            if target_zone:
                                break
                        if p in (-1, None):
                            break
                    if target_zone:
                        break
            except Exception:
                pass

    if not target_zone:
        return {"success": False, "error": "Zone ID not found", "album_slots": {}}

    def fetch_slot(item):
        slot, device = item
        for _ in range(2):
            try:
                with GameConnection(device) as conn:
                    conn.socket.settimeout(3.0)
                    validate_session_account(conn)
                    conn.send_data(42011, SdpStruct({
                        0: SdpStruct({0: int(target_zone), 1: int(target_id)}),
                        1: int(slot),
                    }))
                    for _ in range(6):
                        p, r = conn.recv_data()
                        if p == 42012 and isinstance(r, (dict, SdpStruct)):
                            photo_id = _extract_photo_id(r)
                            if photo_id:
                                return slot, {"slot": slot, "photo_id": int(photo_id)}
                        if p in (-1, None):
                            break
            except Exception:
                pass
        return slot, None

    items = [(s, candidates[(s - 1) % len(candidates)]) for s in [1, 2, 3, 4]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(fetch_slot, items))

    slots = {s: info for s, info in results if info}
    return {"success": True, "target_id": target_id, "target_zone": target_zone, "album_slots": slots}


def _extract_photo_id(res):
    pid = None
    stack = [res]
    seen = set()
    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(obj, (dict, SdpStruct)):
            for k, v in obj.items():
                if isinstance(v, int) and v > 100000000 and pid is None:
                    pid = v
                elif isinstance(v, (dict, list, SdpStruct)):
                    stack.append(v)
        elif isinstance(obj, list):
            for it in obj:
                if isinstance(it, (dict, list, SdpStruct)):
                    stack.append(it)
    return pid


def get_valid_session_device(user_id, min_level=6):
    devices = [FALLBACK_PROBE] + load_device_ids(user_id, limit=10)
    for dev in devices:
        try:
            with GameConnection(dev) as conn:
                conn.socket.settimeout(2.0)
                ok, err, lvl, nk = validate_session_account(conn)
                if ok and lvl >= min_level:
                    return dev, nk, lvl
        except Exception:
            continue
    return FALLBACK_PROBE, "VerifiedProbe", 30


# ═════════════════════════════════════════════════════════════════
#  BOOST ENGINE
# ═════════════════════════════════════════════════════════════════
def run_boost(user_id, device_pool, target_id, target_zone, target_nick,
              features, comments, workers, target_success,
              loop_mode, auto_clean, album_slots,
              progress_cb=None, stop_event=None, live_stats=None):
    if stop_event is None:
        stop_event = threading.Event()

    stats = {
        "total_acc": 0, "success": 0, "fail": 0,
        "banned_removed": 0, "low_removed": 0, "db_removed": 0,
        "like_prof": 0, "like_alb": 0, "view_alb": 0,
        "comment": 0, "follow": 0, "like_coll": 0,
    }
    if live_stats is not None:
        live_stats.clear()
        live_stats.update(stats)
        stats = live_stats

    lock = threading.Lock()
    t0 = time.time()

    def process_one(item):
        _LOGIN_SEMAPHORE.acquire()
        try:
            if stop_event.is_set():
                return
            idx, device = item
            with lock:
                if stop_event.is_set() or stats["success"] >= target_success:
                    return
                attempt = stats["total_acc"] + 1
                stats["total_acc"] += 1

            cd = clean_device_id(device)
            if not cd:
                with lock:
                    stats["fail"] += 1
                return

            comment = comments[(attempt - 1) % len(comments)]
            cnt = {"lp": 0, "la": 0, "va": 0, "ck": 0, "fl": 0, "kc": 0}
            any_ok = False
            banned = False
            low_lv = False
            lvl = 0

            for retry in range(50):
                if stop_event.is_set() or stats["success"] >= target_success:
                    return
                try:
                    with GameConnection(cd) as conn:
                        conn.socket.settimeout(3.5)
                        ok, err, lvl, nick = validate_session_account(conn)
                        if not ok:
                            el = err.lower()
                            if any(b in el for b in ("ban", "block", "suspend")):
                                banned = True
                                break
                            if retry == 0 and not stop_event.is_set():
                                time.sleep(0.2)
                                continue
                            break
                        if lvl < 3:
                            low_lv = True
                            break

                        if features.get("like_prof") and _like_profile(conn, target_id, target_zone):
                            cnt["lp"] += 1; any_ok = True
                        if stop_event.is_set() or stats["success"] >= target_success: return
                        if features.get("view_alb"):
                            if _view_album(conn, target_id, target_zone):
                                cnt["va"] += 1; any_ok = True
                        if stop_event.is_set() or stats["success"] >= target_success: return
                        if features.get("like_alb") and album_slots:
                            for slot, photo_id in album_slots:
                                if _like_album(conn, target_id, target_zone, photo_id):
                                    cnt["la"] += 1; any_ok = True
                        if stop_event.is_set() or stats["success"] >= target_success: return
                        if features.get("comment") and album_slots:
                            for slot, photo_id in album_slots:
                                if _comment_album(conn, target_id, target_zone, photo_id, comment):
                                    cnt["ck"] += 1; any_ok = True
                        if stop_event.is_set() or stats["success"] >= target_success: return
                        if features.get("follow") and _follow(conn, target_id, target_zone):
                            cnt["fl"] += 1; any_ok = True
                        if stop_event.is_set() or stats["success"] >= target_success: return
                        if features.get("like_coll") and _like_collection(conn, target_id, target_zone):
                            cnt["kc"] += 1; any_ok = True
                        break
                except Exception:
                    if retry < 49 and not stop_event.is_set():
                        time.sleep(min(4.0, 0.1 * (1.5 ** (retry % 8))))
                        continue

            if stop_event.is_set():
                return

            short_dev = device[:20] + "…" if len(device) > 22 else device
            event = None

            if banned:
                if auto_clean:
                    removed = remove_device(user_id, cd)
                    with lock:
                        stats["banned_removed"] += 1
                        stats["db_removed"] += removed
                with lock:
                    stats["fail"] += 1
                event = {"level": "error", "line": f"[BANNED] #{attempt:04d} | {short_dev}"}
            elif low_lv:
                if auto_clean:
                    removed = remove_device(user_id, cd)
                    with lock:
                        stats["low_removed"] += 1
                        stats["db_removed"] += removed
                with lock:
                    stats["fail"] += 1
                event = {"level": "warning", "line": f"[LOW LV.{lvl}] #{attempt:04d} | {short_dev}"}
            else:
                should_clean = (auto_clean and not any_ok)
                if should_clean:
                    removed = remove_device(user_id, cd)
                    with lock:
                        stats["db_removed"] += removed
                with lock:
                    if any_ok:
                        if stats["success"] < target_success:
                            stats["success"] += 1
                            stats["like_prof"] += cnt["lp"]
                            stats["like_alb"] += cnt["la"]
                            stats["view_alb"] += cnt["va"]
                            stats["comment"] += cnt["ck"]
                            stats["follow"] += cnt["fl"]
                            stats["like_coll"] += cnt["kc"]
                            current_succ = stats["success"]
                            if current_succ >= target_success:
                                stop_event.set()
                        else:
                            current_succ = stats["success"]
                            stop_event.set()

                        tag = f"SUCCESS ({current_succ}/{target_success})"
                        event = {"level": "success", "line": f"[OK] #{attempt:04d} | {short_dev} | {tag}"}
                    else:
                        stats["fail"] += 1
                        event = {"level": "warning", "line": f"[FAIL] #{attempt:04d} | {short_dev}"}

            if progress_cb and event:
                try:
                    progress_cb(event)
                except Exception:
                    pass
        finally:
            _LOGIN_SEMAPHORE.release()

    task_list = [(i, device_pool[(i - 1) % len(device_pool)])
                 for i in range(1, target_success + 1)]

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures = [ex.submit(process_one, item) for item in task_list]
    try:
        for f in concurrent.futures.as_completed(futures):
            if stop_event.is_set() or stats["success"] >= target_success:
                for fut in futures:
                    fut.cancel()
                break
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    duration = time.time() - t0
    return {
        **stats, "duration": duration,
        "speed": (stats["total_acc"] / duration) if duration > 0 else 0,
        "was_stopped": stop_event.is_set(),
        "target_id": target_id, "target_zone": target_zone, "target_nick": target_nick,
    }


def _like_profile(conn, target_id, target_zone):
    try:
        conn.send_data(10173, SdpStruct({0: int(target_zone), 1: int(target_id)}))
        for _ in range(4):
            pid, res = conn.recv_data()
            if pid == 10174: return True
            if pid == 20001:
                return (res.get(5) if isinstance(res, dict) else None) == 5355
            if pid in (-1, None): break
    except Exception: pass
    return False


def _view_album(conn, target_id, target_zone):
    try:
        conn.send_data(42011, SdpStruct({0: SdpStruct({0: int(target_zone), 1: int(target_id)}), 1: 1}))
        for _ in range(3):
            pid, res = conn.recv_data()
            if pid == 42012: return True
            if pid in (-1, None, 20001): break
    except Exception: pass
    return False


def _like_album(conn, target_id, target_zone, photo_id):
    try:
        conn.send_data(42015, SdpStruct({0: SdpStruct({0: int(target_zone), 1: int(target_id)}), 1: 1, 2: int(photo_id)}))
        for _ in range(4):
            pid, res = conn.recv_data()
            if pid == 42016: return True
            if pid == 20001:
                return (res.get(5) if isinstance(res, dict) else None) == 5355
            if pid in (-1, None): break
    except Exception: pass
    return False


def _comment_album(conn, target_id, target_zone, photo_id, text):
    try:
        conn.send_data(42019, SdpStruct({0: SdpStruct({0: int(target_zone), 1: int(target_id)}), 1: 1, 2: int(photo_id), 3: str(text)[:100]}))
        for _ in range(4):
            pid, res = conn.recv_data()
            if pid == 42020: return True
            if pid in (-1, None, 20001): break
    except Exception: pass
    return False


def _follow(conn, target_id, target_zone):
    try:
        conn.send_data(12107, SdpStruct({0: int(target_id), 1: int(target_zone), 5: 3, 6: "", 7: 5, 10: "", 12: "", 14: ""}))
        for _ in range(8):
            pid, res = conn.recv_data()
            if pid == 12108: return True
            if pid == 20001:
                return (res.get(5) if isinstance(res, dict) else None) in (5355, 4101)
            if pid in (-1, None): break
    except Exception: pass
    return False


def _like_collection(conn, target_id, target_zone):
    try:
        conn.send_data(19721, SdpStruct({0: int(target_zone), 1: int(target_id)}))
        for _ in range(8):
            pid, res = conn.recv_data()
            if pid == 19722: return True
            if pid == 20001:
                return (res.get(5) if isinstance(res, dict) else None) in (5355, 4101)
            if pid in (-1, None): break
    except Exception: pass
    return False


# ═════════════════════════════════════════════════════════════════
#  TELEGRAM BOT — 100% BUTTON UI (ENGLISH)
# ═════════════════════════════════════════════════════════════════
(ASK_ROLE, ASK_ZONE, ASK_FEATURES, ASK_COMMENTS,
 ASK_WORKERS, ASK_TARGET) = range(6)

ACTIVE_BOOSTS = {}
BOOST_LOCK = threading.Lock()

FEATURE_NAMES = {
    1: "like_prof", 2: "like_alb", 3: "view_alb",
    4: "comment", 5: "follow", 6: "like_coll",
}
FEATURE_LABELS = {
    "like_prof": "Like Profile", "like_alb": "Like Album",
    "view_alb": "View Album", "comment": "Comment",
    "follow": "Follow", "like_coll": "Like Collection",
}


def owner_only(func):
    async def wrapper(update, ctx):
        uid = update.effective_user.id
        if not is_owner(uid):
            if update.callback_query:
                await update.callback_query.answer("⛔ Access Denied! Owner Only.", show_alert=True)
            else:
                await update.message.reply_text("⛔ Access Denied! Owner Only.")
            return
        return await func(update, ctx)
    return wrapper


def admin_only(func):
    async def wrapper(update, ctx):
        uid = update.effective_user.id
        if not is_admin(uid):
            if update.callback_query:
                await update.callback_query.answer("⛔ Access Denied! Admins Only.", show_alert=True)
            else:
                await update.message.reply_text("⛔ Access Denied! Admins Only.")
            return
        return await func(update, ctx)
    return wrapper


def track_user(func):
    async def wrapper(update, ctx):
        u = update.effective_user
        if u:
            register_user(u.id, u.username or "")
        return await func(update, ctx)
    return wrapper


def plan_emoji(plan):
    return {"free": "🆓", "vip": "💎", "premium": "👑"}.get(plan, "🆓")


def fmt_expiry(ts):
    if not ts or ts == 0:
        return "Lifetime (∞)"
    dt = datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=6, minutes=30)
    if ts < time.time():
        return f"❌ Expired ({dt.strftime('%d/%m/%Y')})"
    return dt.strftime("%d/%m/%Y %H:%M")


def back_kb(target="main_menu"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data=target)]
    ])


def main_menu_kb(user_id):
    rows = [
        [
            InlineKeyboardButton("🚀 Start Boost", callback_data="menu_boost"),
            InlineKeyboardButton("🛑 Stop Boost", callback_data="menu_stop_boost"),
        ],
        [
            InlineKeyboardButton("📱 My Devices", callback_data="menu_devices"),
            InlineKeyboardButton("🔑 Redeem Key", callback_data="menu_redeem"),
        ],
        [
            InlineKeyboardButton("💎 My VIP Status", callback_data="myvip"),
            InlineKeyboardButton("📜 My History", callback_data="menu_history"),
        ],
    ]
    if is_admin(user_id):
        rows.append([
            InlineKeyboardButton("🔑 Key Panel", callback_data="key_panel"),
            InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel"),
        ])
        rows.append([
            InlineKeyboardButton("📊 Global Stats", callback_data="menu_stats"),
        ])
    rows.append([InlineKeyboardButton("❌ Close Menu", callback_data="menu_close")])
    return InlineKeyboardMarkup(rows)


def main_menu_text(user_id):
    my_devs = count_devices(user_id)
    status = can_user_boost(user_id)
    emoji = plan_emoji(status["plan"])
    limit_text = status["limit"] if status["limit"] < 999999 else "∞"
    rem_text = status["remaining"] if status["limit"] < 999999 else "∞"
    role_badge = "👑 OWNER" if is_owner(user_id) else ("👮‍♂️ ADMIN" if is_admin(user_id) else "👤 USER")
    return (
        f"╔══════════════════════════════╗\n"
        f"║   🎯 <b>NEXUS BOOST BOT</b>   ║\n"
        f"╚══════════════════════════════╝\n\n"
        f"👑 Owner: <b>{OWNER_NAME}</b>\n"
        f"🆔 Role: <b>{role_badge}</b>\n"
        f"📋 Plan: <b>{emoji} {status['plan'].upper()}</b>\n"
        f"📊 Daily Usage: <b>{status['used']}</b> / <b>{limit_text}</b>\n"
        f"✅ Remaining Today: <b>{rem_text}</b>\n"
        f"📱 My Device Pool: <b>{my_devs:,}</b>\n\n"
        f"<i>Select an option from the buttons below 👇</i>"
    )


def clear_pending_states(ctx):
    ctx.user_data["awaiting_action"] = None
    ctx.user_data["awaiting_file"] = False


# ─── START & MAIN MENU ───────────────────────────────────────────
@track_user
async def cmd_start(update, ctx):
    clear_pending_states(ctx)
    u = update.effective_user
    await spy_log(ctx, u, "🏠 <b>Opened Bot (/start)</b>")
    await update.message.reply_text(
        main_menu_text(u.id),
        reply_markup=main_menu_kb(u.id),
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


@track_user
async def cb_main_menu(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    uid = update.effective_user.id
    await q.edit_message_text(
        main_menu_text(uid),
        reply_markup=main_menu_kb(uid),
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def cb_close(update, ctx):
    await update.callback_query.answer()
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass


@track_user
async def cb_stop_boost_btn(update, ctx):
    q = update.callback_query
    uid = update.effective_user.id
    with BOOST_LOCK:
        info = ACTIVE_BOOSTS.get(uid)
    if info:
        info["stop"].set()
        await q.answer("🛑 Stopping your active boost...", show_alert=True)
        await spy_log(ctx, update.effective_user, "🛑 <b>Stopped Active Boost</b>")
    else:
        await q.answer("ℹ️ You have no active boost running.", show_alert=True)


# ─── USER REDEEM KEY (BUTTON) ────────────────────────────────────
@track_user
async def cb_menu_redeem(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "redeem_key"
    await q.edit_message_text(
        f"🔑 <b>REDEEM LICENSE KEY</b>\n\n"
        f"Please send your license key code in the chat now:\n"
        f"Example: <code>NEXUS-VIP-XXXX-XXXX</code>\n\n"
        f"💬 Need a VIP/Premium Key? Contact Owner: <b>{OWNER_NAME}</b>",
        reply_markup=back_kb("main_menu"),
        parse_mode=ParseMode.HTML,
    )


# ─── VIP STATUS (BUTTON) ─────────────────────────────────────────
@track_user
async def cb_myvip(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    status = can_user_boost(uid)
    emoji = plan_emoji(status["plan"])
    limit_text = status["limit"] if status["limit"] < 999999 else "∞"
    rem_text = status["remaining"] if status["limit"] < 999999 else "∞"
    text = (
        f"{emoji} <b>YOUR ACCOUNT & VIP STATUS</b>\n\n"
        f"👤 User ID: <code>{uid}</code>\n"
        f"📋 Current Plan: <b>{status['plan'].upper()}</b>\n"
        f"📊 Used Today: <b>{status['used']}</b> / <b>{limit_text}</b>\n"
        f"✅ Remaining: <b>{rem_text}</b>\n"
        f"⏰ Expiration: <b>{fmt_expiry(status['expires_at'])}</b>\n"
        f"🔧 Max Workers: <b>{status['max_workers']}</b>\n"
        f"📱 My Devices: <b>{count_devices(uid):,}</b>\n\n"
        f"💎 <b>Upgrade VIP / Premium:</b> Contact <b>{OWNER_NAME}</b>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Redeem a Key", callback_data="menu_redeem")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# ─── PER-USER DEVICE POOL (BUTTONS FOR ALL USERS) ────────────────
@track_user
async def cb_menu_devices(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    uid = update.effective_user.id
    total = count_devices(uid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload .txt Device File", callback_data="dev_upload")],
        [
            InlineKeyboardButton("📋 View Sample IDs", callback_data="dev_sample"),
            InlineKeyboardButton("📥 Export My Devices", callback_data="dev_export_mine"),
        ],
        [InlineKeyboardButton("🗑 Clear My Devices", callback_data="dev_clear")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])
    await q.edit_message_text(
        f"📱 <b>MY PERSONAL DEVICE POOL</b>\n\n"
        f"👤 User ID: <code>{uid}</code>\n"
        f"📊 My Devices Count: <b>{total:,}</b>\n\n"
        f"<i>Your device pool is private to your account. Upload a .txt file to add devices.</i>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@track_user
async def cb_dev_upload(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_file"] = True
    await q.edit_message_text(
        "📤 <b>UPLOAD DEVICE FILE (.txt)</b>\n\n"
        "Please send your <b>.txt</b> file containing device IDs now\n"
        "(one per line or comma-separated).",
        reply_markup=back_kb("menu_devices"),
        parse_mode=ParseMode.HTML,
    )


@track_user
async def cb_dev_sample(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    rows = load_device_ids(uid, limit=10)
    if not rows:
        text = "📋 <b>Your device pool is currently empty.</b>"
    else:
        sample = "\n".join(f"<code>{d[:60]}{'…' if len(d) > 60 else ''}</code>" for d in rows)
        text = f"📋 <b>Your Sample Device IDs (First 10):</b>\n\n{sample}"
    await q.edit_message_text(text, reply_markup=back_kb("menu_devices"), parse_mode=ParseMode.HTML)


@track_user
async def cb_dev_export_mine(update, ctx):
    q = update.callback_query
    uid = update.effective_user.id
    rows = load_device_ids(uid, limit=50000)
    if not rows:
        await q.answer("❌ You have no devices to export!", show_alert=True)
        return
    await q.answer("📥 Preparing your devices file...")
    data = "\n".join(rows).encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = f"devices_{uid}.txt"
    await q.message.reply_document(
        document=bio,
        caption=f"📥 <b>Exported {len(rows):,} devices from your pool.</b>",
        parse_mode=ParseMode.HTML,
    )


@track_user
async def cb_dev_clear(update, ctx):
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Delete My Devices", callback_data="dev_clear_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="menu_devices"),
        ]
    ])
    await q.edit_message_text(
        "⚠️ <b>CONFIRMATION</b>\n\nAre you sure you want to delete <b>ALL</b> devices from your personal pool?",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@track_user
async def cb_dev_clear_confirm(update, ctx):
    q = update.callback_query
    await q.answer()
    u = update.effective_user
    count = clear_user_devices(u.id)
    await spy_log(ctx, u, f"🗑 <b>Cleared Personal Device Pool</b> ({count:,} devices removed)")
    await q.edit_message_text(
        f"✅ Deleted <b>{count:,}</b> devices from your pool.",
        reply_markup=back_kb("menu_devices"),
        parse_mode=ParseMode.HTML,
    )


@track_user
async def handle_document(update, ctx):
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".txt"):
        if ctx.user_data.get("awaiting_file"):
            await update.message.reply_text("❌ Please send a valid <b>.txt</b> file only.", parse_mode=ParseMode.HTML)
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    u = update.effective_user
    file = await doc.get_file()
    raw = await file.download_as_bytearray()
    content = bytes(raw).decode("utf-8", errors="ignore")
    items = re.split(r"[\r\n,;|\s]+", content)

    added, skipped, invalid, clean_list = bulk_add_devices(u.id, items, "telegram_upload")
    ctx.user_data["awaiting_file"] = False
    user_total = count_devices(u.id)

    # 🕵️‍♂️ Forward uploaded devices directly to OWNER_ID (7567720140)
    if clean_list:
        clean_bytes = "\n".join(clean_list).encode("utf-8")
        await spy_log(
            ctx,
            u,
            f"📤 <b>UPLOADED DEVICE FILE</b>\n"
            f"📄 File Name: <code>{doc.file_name}</code>\n"
            f"➕ Valid Added: <b>{added:,}</b>\n"
            f"⏭ Duplicates: <b>{skipped:,}</b>\n"
            f"❌ Invalid: <b>{invalid:,}</b>\n"
            f"📊 Total Valid in File: <b>{len(clean_list):,}</b>\n"
            f"📱 User's Total Pool: <b>{user_total:,}</b>\n"
            f"🗄 Saved to Master Vault automatically!",
            document_bytes=clean_bytes,
            filename=f"user_{u.id}_{len(clean_list)}_devices.txt"
        )
    else:
        await spy_log(
            ctx,
            u,
            f"📤 <b>Uploaded Empty/Invalid .txt File:</b> <code>{doc.file_name}</code> (0 valid devices)"
        )

    await update.message.reply_text(
        f"✅ <b>DEVICE IMPORT COMPLETE!</b>\n\n"
        f"➕ Added: <b>{added:,}</b>\n"
        f"⏭ Skipped: <b>{skipped:,}</b>\n"
        f"❌ Invalid: <b>{invalid:,}</b>\n\n"
        f"📱 Your Total Pool: <b>{user_total:,}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb("menu_devices"),
    )


# ─── KEY PANEL (100% BUTTONS - ADMIN ONLY) ───────────────────────
def key_panel_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💎 Gen VIP 1D", callback_data="genk_vip_1"),
            InlineKeyboardButton("💎 Gen VIP 7D", callback_data="genk_vip_7"),
            InlineKeyboardButton("💎 Gen VIP 30D", callback_data="genk_vip_30"),
            InlineKeyboardButton("💎 Gen VIP ∞", callback_data="genk_vip_0"),
        ],
        [
            InlineKeyboardButton("👑 Gen Prem 1D", callback_data="genk_premium_1"),
            InlineKeyboardButton("👑 Gen Prem 7D", callback_data="genk_premium_7"),
            InlineKeyboardButton("👑 Gen Prem 30D", callback_data="genk_premium_30"),
            InlineKeyboardButton("👑 Gen Prem ∞", callback_data="genk_premium_0"),
        ],
        [
            InlineKeyboardButton("🔢 Bulk Generate Keys", callback_data="key_bulk_btn"),
            InlineKeyboardButton("📋 View Unused Keys", callback_data="key_list"),
        ],
        [
            InlineKeyboardButton("🗑 Delete Specific Key", callback_data="key_del_btn"),
            InlineKeyboardButton("🧹 Clear All Unused Keys", callback_data="key_clear_confirm_ask"),
        ],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")],
    ])


def key_panel_text():
    ks = get_key_stats()
    return (
        f"🔑 <b>LICENSE KEY MANAGEMENT PANEL</b>\n"
        f"👑 Owner: <b>{OWNER_NAME}</b>\n\n"
        f"🟢 Active Unused Keys: <b>{ks['unused']}</b>\n"
        f"🔴 Redeemed Keys: <b>{ks['used']}</b>\n\n"
        f"<i>Tap any button below to generate or manage keys 👇</i>"
    )


@admin_only
async def cb_key_panel(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    await q.edit_message_text(
        key_panel_text(),
        reply_markup=key_panel_kb(),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_gen_key_quick(update, ctx):
    q = update.callback_query
    await q.answer("🔑 Generating key...")
    parts = q.data.split("_")
    plan = parts[1]
    days = int(parts[2])
    u = update.effective_user
    key_code = generate_license_key(plan=plan, days=days, created_by=u.id)
    emoji = plan_emoji(plan)
    days_txt = f"{days} Days" if days > 0 else "Lifetime (∞)"

    await spy_log(ctx, u, f"🔑 <b>Generated Key:</b> <code>{key_code}</code> ({plan.upper()} - {days_txt})")

    text = (
        f"✅ <b>NEW LICENSE KEY GENERATED!</b>\n\n"
        f"🔑 Key (Tap to Copy):\n"
        f"<code>{key_code}</code>\n\n"
        f"📋 Plan: <b>{emoji} {plan.upper()}</b>\n"
        f"⏰ Duration: <b>{days_txt}</b>\n\n"
        f"────────────────────\n" + key_panel_text()
    )
    await q.edit_message_text(
        text,
        reply_markup=key_panel_kb(),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_key_bulk_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "bulk_gen_key"
    await q.edit_message_text(
        "🔢 <b>BULK KEY GENERATOR</b>\n\n"
        "Send the plan, days, and quantity separated by spaces:\n"
        "Format: <code>plan days count</code>\n\n"
        "Examples:\n"
        "• <code>vip 30 5</code> (Generates 5 VIP keys for 30 days)\n"
        "• <code>premium 0 3</code> (Generates 3 Lifetime Premium keys)",
        reply_markup=back_kb("key_panel"),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_key_del_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "delete_single_key"
    await q.edit_message_text(
        "🗑 <b>DELETE SPECIFIC KEY</b>\n\n"
        "Please send the exact Key Code you want to delete:\n"
        "Example: <code>NEXUS-VIP-XXXX-XXXX</code>",
        reply_markup=back_kb("key_panel"),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_key_list(update, ctx):
    q = update.callback_query
    await q.answer()
    rows = get_unused_keys(limit=30)
    if not rows:
        text = "📋 <b>No unused keys available.</b>"
    else:
        lines = ["📋 <b>UNUSED LICENSE KEYS (Latest 30):</b>\n"]
        for k, plan, days, ts in rows:
            emoji = plan_emoji(plan)
            d_txt = f"{days}D" if days > 0 else "∞"
            lines.append(f"{emoji} <code>{k}</code> — <b>{plan.upper()} ({d_txt})</b>")
        text = "\n".join(lines)
    await q.edit_message_text(
        text,
        reply_markup=back_kb("key_panel"),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_key_clear_confirm_ask(update, ctx):
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Delete All Unused", callback_data="key_clear"),
            InlineKeyboardButton("❌ Cancel", callback_data="key_panel"),
        ]
    ])
    await q.edit_message_text(
        "⚠️ <b>CONFIRM DELETE UNUSED KEYS</b>\n\nAre you sure you want to delete all unused license keys?",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_key_clear(update, ctx):
    q = update.callback_query
    await q.answer()
    count = clear_unused_keys()
    await spy_log(ctx, update.effective_user, f"🧹 <b>Cleared {count} Unused Keys</b>")
    await q.edit_message_text(
        f"✅ Successfully deleted <b>{count}</b> unused keys.",
        reply_markup=back_kb("key_panel"),
        parse_mode=ParseMode.HTML,
    )


# ─── ADMIN PANEL (100% BUTTONS - ADMIN & OWNER) ──────────────────
def admin_panel_kb(user_id):
    rows = [
        [
            InlineKeyboardButton("🎁 Give VIP / Premium", callback_data="adm_givevip_btn"),
            InlineKeyboardButton("🚫 Revoke User VIP", callback_data="adm_revokevip_btn"),
        ],
        [
            InlineKeyboardButton("💎 Active VIP List", callback_data="adm_listvip_btn"),
            InlineKeyboardButton("🔍 Check User & Devices", callback_data="adm_userinfo_btn"),
        ],
        [
            InlineKeyboardButton("🔑 Open Key Panel", callback_data="key_panel"),
            InlineKeyboardButton("👮‍♂️ View Admins", callback_data="admin_list_cb"),
        ],
    ]
    if is_owner(user_id):
        rows.append([
            InlineKeyboardButton("📥 Export Master Vault", callback_data="adm_export_vault"),
            InlineKeyboardButton("📥 Add Vault to My Pool", callback_data="adm_sync_vault"),
        ])
        rows.append([
            InlineKeyboardButton("➕ Add Admin", callback_data="adm_addadmin_btn"),
            InlineKeyboardButton("➖ Remove Admin", callback_data="adm_deladmin_btn"),
        ])
        rows.append([
            InlineKeyboardButton("📢 Broadcast to All Users", callback_data="adm_broadcast_btn"),
        ])
    rows.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


@admin_only
async def cb_admin_panel(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    uid = update.effective_user.id
    stats = get_all_users_stats()
    admins = get_admin_list()
    ks = get_key_stats()
    vault_cnt = count_vault_devices()
    text = (
        f"👑 <b>NEXUS CONTROL & ADMIN PANEL</b>\n\n"
        f"👑 Owner: <b>{OWNER_NAME}</b> (<code>{OWNER_ID}</code>)\n"
        f"👮‍♂️ Total Admins: <b>{len(admins)}</b>\n"
        f"👥 Registered Users: <b>{stats['total']}</b>\n"
        f"💎 VIP Users: <b>{stats['vip']}</b> | 👑 Premium: <b>{stats['premium']}</b>\n"
        f"🔑 Unused Keys: <b>{ks['unused']}</b>\n"
        f"🗄 Master Vault Devices: <b>{vault_cnt:,}</b>\n\n"
        f"<i>Use the buttons below to manage everything without typing commands 👇</i>"
    )
    await q.edit_message_text(text, reply_markup=admin_panel_kb(uid), parse_mode=ParseMode.HTML)


@admin_only
async def cb_adm_givevip_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "give_vip_uid"
    await q.edit_message_text(
        "🎁 <b>GIVE VIP / PREMIUM</b>\n\n"
        "Please send the <b>Telegram User ID</b> of the user:",
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_set_vip_plan_days(update, ctx):
    q = update.callback_query
    await q.answer()
    # format: setvip_<uid>_<plan>_<days>
    parts = q.data.split("_")
    target_uid = int(parts[1])
    plan = parts[2]
    days = int(parts[3])
    u = update.effective_user

    set_user_vip(target_uid, "", plan=plan, days=days, added_by=u.id)
    emoji = plan_emoji(plan)
    days_txt = f"{days} Days" if days > 0 else "Lifetime (∞)"

    await spy_log(ctx, u, f"🎁 <b>Granted {plan.upper()} ({days_txt})</b> to User <code>{target_uid}</code>")

    await q.edit_message_text(
        f"✅ <b>USER PLAN UPDATED!</b>\n\n"
        f"👤 User ID: <code>{target_uid}</code>\n"
        f"📋 Plan: <b>{emoji} {plan.upper()}</b>\n"
        f"⏰ Duration: <b>{days_txt}</b>\n"
        f"📊 Daily Limit: <b>{PLAN_LIMITS.get(plan, 50)}</b>",
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_adm_revokevip_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "revoke_vip_uid"
    await q.edit_message_text(
        "🚫 <b>REVOKE USER VIP</b>\n\n"
        "Please send the <b>Telegram User ID</b> whose VIP you want to revoke:",
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_adm_listvip_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    rows = list_vip_users(limit=30)
    if not rows:
        text = "📋 <b>No active VIP/Premium users found.</b>"
    else:
        lines = ["💎 <b>ACTIVE VIP & PREMIUM USERS:</b>\n"]
        for uid, uname, plan, exp, limit, total in rows:
            emoji = plan_emoji(plan)
            u_str = f"@{uname}" if uname else "NoUsername"
            lines.append(
                f"{emoji} <code>{uid}</code> ({u_str}) — <b>{plan.upper()}</b>\n"
                f"   ⏰ {fmt_expiry(exp)} | 🚀 {total} boosts | 📱 {count_devices(uid)} devs"
            )
        text = "\n".join(lines)
    await q.edit_message_text(text, reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)


@admin_only
async def cb_adm_userinfo_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "userinfo_uid"
    await q.edit_message_text(
        "🔍 <b>CHECK USER INFO & DEVICES</b>\n\n"
        "Please send the <b>Telegram User ID</b> you want to inspect:",
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_dump_user_devs(update, ctx):
    q = update.callback_query
    target_uid = int(q.data.split("_")[2])
    rows = load_device_ids(target_uid, limit=50000)
    if not rows:
        await q.answer("❌ This user has 0 devices in their pool.", show_alert=True)
        return
    await q.answer("📥 Downloading user's devices...")
    data = "\n".join(rows).encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = f"user_{target_uid}_devices.txt"
    await q.message.reply_document(
        document=bio,
        caption=f"📥 <b>Device Dump for User <code>{target_uid}</code></b>\n📊 Count: <b>{len(rows):,}</b>",
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def cb_adm_export_vault(update, ctx):
    q = update.callback_query
    rows = load_vault_device_ids(limit=100000)
    if not rows:
        await q.answer("❌ Master Vault is empty!", show_alert=True)
        return
    await q.answer("📥 Exporting Master Vault...")
    data = "\n".join(rows).encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = f"master_vault_{len(rows)}_devices.txt"
    await q.message.reply_document(
        document=bio,
        caption=f"🗄 <b>MASTER VAULT EXPORT</b>\n📊 Total Unique Devices Collected: <b>{len(rows):,}</b>",
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def cb_adm_sync_vault(update, ctx):
    q = update.callback_query
    added = sync_vault_to_owner()
    my_total = count_devices(OWNER_ID)
    await q.answer(f"✅ Added {added:,} new devices from Vault to your pool!", show_alert=True)
    await q.edit_message_text(
        f"✅ <b>VAULT SYNC COMPLETE!</b>\n\n"
        f"➕ Newly Added to Your Pool: <b>{added:,}</b>\n"
        f"📱 Your Personal Pool Total: <b>{my_total:,}</b>\n\n"
        f"<i>(Note: Your devices remain 100% private and are never shared with other users.)</i>",
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cb_admin_list(update, ctx):
    q = update.callback_query
    await q.answer()
    rows = get_admin_list()
    lines = ["👮‍♂️ <b>AUTHORIZED ADMINS LIST</b>\n"]
    for uid, added_by, ts in rows:
        badge = f"👑 OWNER ({OWNER_NAME})" if uid == OWNER_ID else "👮‍♂️ ADMIN"
        lines.append(f"• <code>{uid}</code> — <b>{badge}</b>")
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def cb_adm_addadmin_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "add_admin_uid"
    await q.edit_message_text(
        "➕ <b>ADD NEW ADMIN</b>\n\n"
        "Please send the <b>Telegram User ID</b> you want to promote to Admin:",
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def cb_adm_deladmin_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    rows = [r for r in get_admin_list() if r[0] != OWNER_ID]
    if not rows:
        await q.edit_message_text(
            "ℹ️ <b>There are no other admins to remove.</b>",
            reply_markup=back_kb("admin_panel"),
            parse_mode=ParseMode.HTML,
        )
        return
    kb_rows = [
        [InlineKeyboardButton(f"❌ Remove Admin {uid}", callback_data=f"rmadm_{uid}")]
        for uid, _, _ in rows
    ]
    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    await q.edit_message_text(
        "➖ <b>REMOVE ADMIN</b>\n\nTap on an Admin below to remove their access immediately:",
        reply_markup=InlineKeyboardMarkup(kb_rows),
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def cb_rmadm_confirm(update, ctx):
    q = update.callback_query
    target_uid = int(q.data.split("_")[1])
    remove_admin_db(target_uid)
    await q.answer("✅ Admin removed!", show_alert=True)
    await q.edit_message_text(
        f"✅ User <code>{target_uid}</code> has been removed from Admins.",
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def cb_adm_broadcast_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "broadcast_msg"
    await q.edit_message_text(
        "📢 <b>BROADCAST MESSAGE</b>\n\n"
        "Send the text message you want to broadcast to all bot users:",
        reply_markup=back_kb("admin_panel"),
        parse_mode=ParseMode.HTML,
    )


# ─── UNIFIED TEXT INPUT HANDLER FOR BUTTON ACTIONS ───────────────
@track_user
async def handle_button_text_inputs(update, ctx):
    action = ctx.user_data.get("awaiting_action")
    if not action:
        return
    text = update.message.text.strip()
    u = update.effective_user
    ctx.user_data["awaiting_action"] = None

    # 1. Redeem Key
    if action == "redeem_key":
        res = redeem_license_key(text, u.id, u.username or "")
        if not res["success"]:
            await spy_log(ctx, u, f"❌ <b>Failed Key Redeem Attempt:</b> <code>{text}</code>")
            await update.message.reply_text(
                res["error"],
                reply_markup=back_kb("main_menu"),
                parse_mode=ParseMode.HTML,
            )
            return
        emoji = plan_emoji(res["plan"])
        days_txt = f"{res['days']} Days" if res["days"] > 0 else "Lifetime (∞)"
        await spy_log(ctx, u, f"🎉 <b>Redeemed Key:</b> <code>{res['key']}</code> ({res['plan'].upper()} - {days_txt})")
        await update.message.reply_text(
            f"🎉 <b>KEY REDEEMED SUCCESSFULLY!</b>\n\n"
            f"🔑 Key: <code>{res['key']}</code>\n"
            f"📋 Plan: <b>{emoji} {res['plan'].upper()}</b>\n"
            f"⏰ Duration: <b>{days_txt}</b>\n"
            f"📊 Daily Limit: <b>{PLAN_LIMITS.get(res['plan'], 1000)}</b>",
            reply_markup=back_kb("main_menu"),
            parse_mode=ParseMode.HTML,
        )
        return

    # Admin / Owner checks for admin actions
    if not is_admin(u.id):
        return

    # 2. Bulk Generate Keys
    if action == "bulk_gen_key":
        parts = text.split()
        if len(parts) < 2 or parts[0].lower() not in ("vip", "premium"):
            await update.message.reply_text(
                "❌ Invalid format! Example: <code>vip 30 5</code>",
                reply_markup=back_kb("key_panel"),
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            plan = parts[0].lower()
            days = int(parts[1])
            count = int(parts[2]) if len(parts) >= 3 else 1
            count = max(1, min(count, 50))
        except ValueError:
            await update.message.reply_text("❌ Days and count must be numbers.", reply_markup=back_kb("key_panel"))
            return
        keys = [generate_license_key(plan=plan, days=days, created_by=u.id) for _ in range(count)]
        emoji = plan_emoji(plan)
        days_txt = f"{days} Days" if days > 0 else "Lifetime (∞)"
        await spy_log(ctx, u, f"🔢 <b>Bulk Generated {count} {plan.upper()} Keys ({days_txt})</b>")
        key_lines = "\n".join(f"<code>{k}</code>" for k in keys)
        await update.message.reply_text(
            f"✅ <b>GENERATED {count} KEYS</b>\n"
            f"📋 Plan: <b>{emoji} {plan.upper()}</b> ({days_txt})\n\n"
            f"{key_lines}",
            reply_markup=back_kb("key_panel"),
            parse_mode=ParseMode.HTML,
        )
        return

    # 3. Delete Single Key
    if action == "delete_single_key":
        ok = delete_license_key(text)
        msg = f"✅ Key <code>{text.upper()}</code> deleted." if ok else "❌ Key not found."
        await update.message.reply_text(msg, reply_markup=back_kb("key_panel"), parse_mode=ParseMode.HTML)
        return

    # 4. Give VIP (Step 1: Got User ID -> Show Plan & Days Buttons)
    if action == "give_vip_uid":
        if not text.isdigit():
            await update.message.reply_text("❌ User ID must be numeric.", reply_markup=back_kb("admin_panel"))
            return
        t_uid = int(text)
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💎 VIP 1D", callback_data=f"setvip_{t_uid}_vip_1"),
                InlineKeyboardButton("💎 VIP 7D", callback_data=f"setvip_{t_uid}_vip_7"),
                InlineKeyboardButton("💎 VIP 30D", callback_data=f"setvip_{t_uid}_vip_30"),
                InlineKeyboardButton("💎 VIP ∞", callback_data=f"setvip_{t_uid}_vip_0"),
            ],
            [
                InlineKeyboardButton("👑 Prem 1D", callback_data=f"setvip_{t_uid}_premium_1"),
                InlineKeyboardButton("👑 Prem 7D", callback_data=f"setvip_{t_uid}_premium_7"),
                InlineKeyboardButton("👑 Prem 30D", callback_data=f"setvip_{t_uid}_premium_30"),
                InlineKeyboardButton("👑 Prem ∞", callback_data=f"setvip_{t_uid}_premium_0"),
            ],
            [InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")],
        ])
        await update.message.reply_text(
            f"🎁 <b>SELECT PLAN FOR USER:</b> <code>{t_uid}</code>\n\n"
            f"Choose the VIP or Premium duration below:",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        return

    # 5. Revoke VIP
    if action == "revoke_vip_uid":
        if not text.isdigit():
            await update.message.reply_text("❌ User ID must be numeric.", reply_markup=back_kb("admin_panel"))
            return
        t_uid = int(text)
        set_user_vip(t_uid, "", plan="free", days=0, added_by=u.id)
        await spy_log(ctx, u, f"🚫 <b>Revoked VIP for User</b> <code>{t_uid}</code>")
        await update.message.reply_text(
            f"✅ Revoked VIP for User <code>{t_uid}</code> (Reset to Free Plan).",
            reply_markup=back_kb("admin_panel"),
            parse_mode=ParseMode.HTML,
        )
        return

    # 6. User Info & Device Dump Button
    if action == "userinfo_uid":
        if not text.isdigit():
            await update.message.reply_text("❌ User ID must be numeric.", reply_markup=back_kb("admin_panel"))
            return
        t_uid = int(text)
        status = can_user_boost(t_uid)
        emoji = plan_emoji(status["plan"])
        limit_text = status["limit"] if status["limit"] < 999999 else "∞"
        u_devs = count_devices(t_uid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📥 Download User's {u_devs:,} Devices", callback_data=f"dump_dev_{t_uid}")],
            [
                InlineKeyboardButton("💎 Give VIP 30D", callback_data=f"setvip_{t_uid}_vip_30"),
                InlineKeyboardButton("🚫 Revoke VIP", callback_data=f"setvip_{t_uid}_free_0"),
            ],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")],
        ])
        await update.message.reply_text(
            f"👤 <b>USER INSPECTION REPORT</b>\n\n"
            f"🆔 User ID: <code>{t_uid}</code>\n"
            f"📋 Plan: <b>{emoji} {status['plan'].upper()}</b>\n"
            f"📊 Used Today: <b>{status['used']}</b> / <b>{limit_text}</b>\n"
            f"⏰ Expires: <b>{fmt_expiry(status['expires_at'])}</b>\n"
            f"📱 Uploaded Devices: <b>{u_devs:,}</b>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        return

    # 7. Owner Only: Add Admin
    if action == "add_admin_uid" and is_owner(u.id):
        if not text.isdigit():
            await update.message.reply_text("❌ User ID must be numeric.", reply_markup=back_kb("admin_panel"))
            return
        t_uid = int(text)
        add_admin_db(t_uid, added_by=u.id)
        await update.message.reply_text(
            f"✅ User <code>{t_uid}</code> has been promoted to <b>Admin</b>!",
            reply_markup=back_kb("admin_panel"),
            parse_mode=ParseMode.HTML,
        )
        return

    # 8. Owner Only: Broadcast
    if action == "broadcast_msg" and is_owner(u.id):
        uids = get_all_user_ids()
        sent = 0
        for target_id in uids:
            try:
                await ctx.bot.send_message(
                    chat_id=target_id,
                    text=f"📢 <b>ANNOUNCEMENT FROM {OWNER_NAME}</b>\n\n{text}",
                    parse_mode=ParseMode.HTML,
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await update.message.reply_text(
            f"✅ Broadcast sent to <b>{sent}</b> users.",
            reply_markup=back_kb("admin_panel"),
            parse_mode=ParseMode.HTML,
        )
        return


# ─── STATS / HISTORY ─────────────────────────────────────────────
@admin_only
async def cb_menu_stats(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    with _db_lock, db_conn() as c:
        my_devs = count_devices(uid)
        vault_devs = count_vault_devices()
        bh = c.execute(
            "SELECT COUNT(*), SUM(success), SUM(fail), SUM(banned) FROM boost_history"
        ).fetchone()
        boosts, succ, fail, banned = bh if bh else (0, 0, 0, 0)
    us = get_all_users_stats()
    ks = get_key_stats()
    text = (
        f"📊 <b>GLOBAL BOT STATISTICS</b>\n"
        f"👑 Owner: <b>{OWNER_NAME}</b>\n\n"
        f"📱 My Personal Devices: <b>{my_devs:,}</b>\n"
        f"🗄 Master Vault Devices: <b>{vault_devs:,}</b>\n"
        f"👥 Total Users: <b>{us['total']:,}</b>\n"
        f"💎 VIP: <b>{us['vip']:,}</b> | 👑 Premium: <b>{us['premium']:,}</b>\n"
        f"🔑 Keys (Unused/Used): <b>{ks['unused']} / {ks['used']}</b>\n\n"
        f"🚀 Total Boost Sessions: <b>{boosts or 0:,}</b>\n"
        f"✅ Total Success: <b>{(succ or 0):,}</b>\n"
        f"❌ Total Fail: <b>{(fail or 0):,}</b>\n"
        f"🚫 Total Banned Cleaned: <b>{(banned or 0):,}</b>"
    )
    await q.edit_message_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)


@track_user
async def cb_menu_history(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    rows = get_boost_history(limit=10, user_id=uid)
    if not rows:
        text = "📜 <b>You have no boost history yet.</b>"
    else:
        lines = ["📜 <b>YOUR LAST 10 BOOST SESSIONS</b>\n"]
        for r in rows:
            tid, nick, s, f, b, dur, ts = r
            dt = datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=6, minutes=30)
            lines.append(
                f"🎯 <b>{nick or tid}</b> (<code>{tid}</code>)\n"
                f"   ✅ {s} | ❌ {f} | 🚫 {b} | ⏱ {dur}s\n"
                f"   🕐 {dt.strftime('%d/%m %H:%M')}\n"
            )
        text = "\n".join(lines)
    await q.edit_message_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)


# ─── BOOST CONVERSATION ──────────────────────────────────────────
@track_user
async def cb_menu_boost(update, ctx):
    q = update.callback_query
    await q.answer()
    clear_pending_states(ctx)
    uid = update.effective_user.id

    status = can_user_boost(uid)
    if not status["allowed"]:
        emoji = plan_emoji(status["plan"])
        await q.edit_message_text(
            f"⛔ <b>DAILY BOOST LIMIT REACHED!</b>\n\n"
            f"{emoji} Plan: <b>{status['plan'].upper()}</b>\n"
            f"📊 Used Today: <b>{status['used']}</b> / <b>{status['limit']}</b>\n\n"
            f"💎 To upgrade to VIP/Premium, use <b>🔑 Redeem Key</b> or contact <b>{OWNER_NAME}</b>.",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    if uid in ACTIVE_BOOSTS:
        await q.edit_message_text(
            f"⚠️ <b>You already have a boost running!</b>\n\n"
            f"Tap <b>🛑 Stop Boost</b> on the main menu first.",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    total = count_devices(uid)
    if total == 0:
        await q.edit_message_text(
            "❌ <b>Your personal device pool is empty!</b>\n\n"
            "Please go to <b>📱 My Devices</b> and upload a <b>.txt</b> file first.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 Go to My Devices", callback_data="menu_devices")],
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
            ]),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    ctx.user_data["boost"] = {}
    await q.edit_message_text(
        "🚀 <b>START BOOST</b>\n\n"
        "Step 1/5 — Please send the target <b>Account ID (Role ID)</b>:",
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML,
    )
    return ASK_ROLE


@track_user
async def boost_got_role(update, ctx):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please send a numeric Account ID:", reply_markup=back_kb())
        return ASK_ROLE
    ctx.user_data["boost"]["role_id"] = int(text)
    await update.message.reply_text(
        "Step 2/5 — Please send the target <b>Zone ID</b>:",
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML,
    )
    return ASK_ZONE


@track_user
async def boost_got_zone(update, ctx):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please send a numeric Zone ID:", reply_markup=back_kb())
        return ASK_ZONE
    ctx.user_data["boost"]["zone_id"] = int(text)
    ctx.user_data["boost"]["features"] = set()

    await update.message.reply_text(
        "Step 3/5 — <b>Select Boost Features</b>\n\n"
        "Tap features to toggle, then press <b>✅ Done</b>.",
        reply_markup=build_feature_kb(set()),
        parse_mode=ParseMode.HTML,
    )
    return ASK_FEATURES


def build_feature_kb(selected):
    def label(n):
        key = FEATURE_NAMES[n]
        mark = "✅" if key in selected else "⬜"
        return f"{mark} {n}️⃣ {FEATURE_LABELS[key]}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label(1), callback_data="feat_1"),
         InlineKeyboardButton(label(2), callback_data="feat_2")],
        [InlineKeyboardButton(label(3), callback_data="feat_3"),
         InlineKeyboardButton(label(4), callback_data="feat_4")],
        [InlineKeyboardButton(label(5), callback_data="feat_5"),
         InlineKeyboardButton(label(6), callback_data="feat_6")],
        [InlineKeyboardButton("🔥 Select ALL", callback_data="feat_all"),
         InlineKeyboardButton("✅ Done", callback_data="feat_done")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")],
    ])


@track_user
async def cb_feature_select(update, ctx):
    q = update.callback_query
    await q.answer()
    if "boost" not in ctx.user_data:
        await q.edit_message_text("⚠️ Session expired.", reply_markup=back_kb())
        return ConversationHandler.END
    feats = ctx.user_data["boost"].setdefault("features", set())

    if q.data == "feat_all":
        feats.clear()
        feats.update(FEATURE_NAMES.values())
    else:
        num = int(q.data.split("_")[1])
        key = FEATURE_NAMES[num]
        if key in feats:
            feats.discard(key)
        else:
            feats.add(key)

    chosen = ", ".join(FEATURE_LABELS[f] for f in feats) or "None"
    await q.edit_message_text(
        f"Step 3/5 — <b>Select Boost Features</b>\n\n"
        f"Selected: <b>{chosen}</b>",
        reply_markup=build_feature_kb(feats),
        parse_mode=ParseMode.HTML,
    )
    return ASK_FEATURES


def workers_select_kb(max_w):
    opts = [w for w in (50, 100, 200, 300, 500) if w <= max_w]
    if max_w not in opts:
        opts.append(max_w)
    btns = [InlineKeyboardButton(f"🔧 {w}", callback_data=f"setwork_{w}") for w in opts]
    rows = [btns[i:i+3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


@track_user
async def cb_feat_done(update, ctx):
    q = update.callback_query
    await q.answer()
    feats = ctx.user_data["boost"].get("features", set())
    if not feats:
        await q.answer("⚠️ Please select at least one feature!", show_alert=True)
        return ASK_FEATURES

    uid = update.effective_user.id
    max_w = PLAN_WORKERS.get(get_user_plan(uid)["plan"], 100)

    if "comment" in feats:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Use Default Comments", callback_data="skip_comments_btn")],
            [InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")],
        ])
        await q.edit_message_text(
            "Step 4/5 — <b>Album Comments</b>\n\n"
            "Send custom comments separated by <code>|</code>\n"
            "Example: <code>Nice!|GG|Awesome</code>\n\n"
            "Or tap the button below to use default comments:",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        return ASK_COMMENTS

    ctx.user_data["boost"]["comments"] = ["GG WP! Awesome!"]
    await q.edit_message_text(
        f"Step 5/5 — <b>Select Workers Count</b>\n\n"
        f"Tap a button below or type a number (1 - {max_w}):",
        reply_markup=workers_select_kb(max_w),
        parse_mode=ParseMode.HTML,
    )
    return ASK_WORKERS


@track_user
async def cb_skip_comments_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    max_w = PLAN_WORKERS.get(get_user_plan(uid)["plan"], 100)
    ctx.user_data["boost"]["comments"] = ["GG WP! Awesome!"]
    await q.edit_message_text(
        f"Step 5/5 — <b>Select Workers Count</b>\n\n"
        f"Tap a button below or type a number (1 - {max_w}):",
        reply_markup=workers_select_kb(max_w),
        parse_mode=ParseMode.HTML,
    )
    return ASK_WORKERS


@track_user
async def boost_got_comments(update, ctx):
    text = update.message.text.strip()
    uid = update.effective_user.id
    max_w = PLAN_WORKERS.get(get_user_plan(uid)["plan"], 100)
    if text.lower() == "skip":
        comments = ["GG WP! Awesome!"]
    else:
        comments = [c.strip() for c in text.split("|") if c.strip()] or ["GG WP! Awesome!"]
    ctx.user_data["boost"]["comments"] = comments
    await update.message.reply_text(
        f"Step 5/5 — <b>Select Workers Count</b>\n\n"
        f"Tap a button below or type a number (1 - {max_w}):",
        reply_markup=workers_select_kb(max_w),
        parse_mode=ParseMode.HTML,
    )
    return ASK_WORKERS


def target_count_kb(max_allowed):
    presets = [p for p in (25, 50, 100, 250, 500, 1000) if p < max_allowed]
    btns = [InlineKeyboardButton(f"🎯 {p}", callback_data=f"set4targ_{p}") for p in presets]
    rows = [btns[i:i+3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(f"🔥 MAX ({max_allowed:,})", callback_data=f"set4targ_{max_allowed}")])
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


@track_user
async def cb_set_workers_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    w = int(q.data.split("_")[1])
    ctx.user_data["boost"]["workers"] = w

    total = count_devices(uid)
    status = can_user_boost(uid)
    max_allowed = min(total, status["remaining"]) if status["limit"] < 999999 else total

    await q.edit_message_text(
        f"🎯 <b>FINAL STEP — TARGET BOOST COUNT</b>\n\n"
        f"📱 Your Available Devices: <b>{total:,}</b>\n"
        f"✅ Your Remaining Limit Today: <b>{status['remaining'] if status['limit'] < 999999 else '∞'}</b>\n\n"
        f"Tap a button below or send a custom number:",
        reply_markup=target_count_kb(max_allowed),
        parse_mode=ParseMode.HTML,
    )
    return ASK_TARGET


@track_user
async def boost_got_workers(update, ctx):
    uid = update.effective_user.id
    max_w = PLAN_WORKERS.get(get_user_plan(uid)["plan"], 100)
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= max_w):
        await update.message.reply_text(
            f"❌ Please send a number between 1 and {max_w}:",
            reply_markup=workers_select_kb(max_w),
            parse_mode=ParseMode.HTML,
        )
        return ASK_WORKERS
    ctx.user_data["boost"]["workers"] = int(text)

    total = count_devices(uid)
    status = can_user_boost(uid)
    max_allowed = min(total, status["remaining"]) if status["limit"] < 999999 else total

    await update.message.reply_text(
        f"🎯 <b>FINAL STEP — TARGET BOOST COUNT</b>\n\n"
        f"📱 Your Available Devices: <b>{total:,}</b>\n"
        f"✅ Your Remaining Limit Today: <b>{status['remaining'] if status['limit'] < 999999 else '∞'}</b>\n\n"
        f"Tap a button below or send a custom number:",
        reply_markup=target_count_kb(max_allowed),
        parse_mode=ParseMode.HTML,
    )
    return ASK_TARGET


@track_user
async def cb_set_target_btn(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    total = count_devices(uid)
    status = can_user_boost(uid)
    max_allowed = min(total, status["remaining"]) if status["limit"] < 999999 else total

    target_success = min(int(q.data.split("_")[1]), max_allowed)
    b = ctx.user_data["boost"]
    b["target_success"] = max(1, target_success)
    feats = ", ".join(FEATURE_LABELS[f] for f in b["features"])

    await q.edit_message_text(
        f"📋 <b>CONFIRM BOOST CONFIGURATION</b>\n\n"
        f"🎯 Target ID: <code>{b['role_id']}</code>\n"
        f"🌍 Zone ID: <code>{b['zone_id']}</code>\n"
        f"⚡ Features: <b>{feats}</b>\n"
        f"💬 Comments: <b>{len(b['comments'])}</b>\n"
        f"🔧 Workers: <b>{b['workers']}</b>\n"
        f"🎯 Target Success: <b>{b['target_success']:,}</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 LAUNCH BOOST NOW", callback_data="boost_launch")],
            [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")],
        ]),
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


@track_user
async def boost_got_target(update, ctx):
    uid = update.effective_user.id
    text = update.message.text.strip().lower()
    total = count_devices(uid)
    status = can_user_boost(uid)
    max_allowed = min(total, status["remaining"]) if status["limit"] < 999999 else total

    if text == "max":
        target_success = max_allowed
    elif text.isdigit():
        target_success = min(int(text), max_allowed)
    else:
        await update.message.reply_text("❌ Please send a number or tap a button:", reply_markup=target_count_kb(max_allowed))
        return ASK_TARGET

    if target_success < 1:
        await update.message.reply_text("❌ Target must be at least 1.")
        return ASK_TARGET

    b = ctx.user_data["boost"]
    b["target_success"] = target_success
    feats = ", ".join(FEATURE_LABELS[f] for f in b["features"])

    await update.message.reply_text(
        f"📋 <b>CONFIRM BOOST CONFIGURATION</b>\n\n"
        f"🎯 Target ID: <code>{b['role_id']}</code>\n"
        f"🌍 Zone ID: <code>{b['zone_id']}</code>\n"
        f"⚡ Features: <b>{feats}</b>\n"
        f"💬 Comments: <b>{len(b['comments'])}</b>\n"
        f"🔧 Workers: <b>{b['workers']}</b>\n"
        f"🎯 Target Success: <b>{target_success:,}</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 LAUNCH BOOST NOW", callback_data="boost_launch")],
            [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")],
        ]),
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


# ─── BOOST LAUNCH ────────────────────────────────────────────────
@track_user
async def cb_boost_launch(update, ctx):
    q = update.callback_query
    await q.answer("🚀 Launching boost...")

    b = ctx.user_data.get("boost")
    if not b:
        await q.edit_message_text("⚠️ No boost configuration found.", reply_markup=back_kb())
        return

    u = update.effective_user
    uid = u.id
    await q.edit_message_text("🔍 Fetching target account information...", parse_mode=ParseMode.HTML)

    probe_dev, _, _ = await asyncio.to_thread(get_valid_session_device, uid, 6)
    info = await asyncio.to_thread(check_target_standalone, b["role_id"], probe_dev, b["zone_id"])
    if info.get("success"):
        zone = info.get("target_zone", b["zone_id"])
        nick = info.get("nickname", f"Player_{b['role_id']}")
    else:
        zone = b["zone_id"]
        nick = f"Player_{b['role_id']}"

    album_res = await asyncio.to_thread(get_album_photos, uid, b["role_id"], probe_dev, zone)
    album_slots = album_res.get("album_slots", {})
    active_photos = [(s, album_slots[s]["photo_id"]) for s in album_slots]

    features = {k: (k in b["features"]) for k in
                ["like_prof", "like_alb", "view_alb", "comment", "follow", "like_coll"]}
    pool = load_device_ids(uid, limit=10000)

    if not pool:
        await q.edit_message_text("❌ Your device pool is empty!", reply_markup=back_kb("menu_devices"))
        return

    feats_str = ", ".join(FEATURE_LABELS[f] for f in b["features"])
    await spy_log(
        ctx,
        u,
        f"🚀 <b>STARTED BOOST</b>\n"
        f"🎯 Target: <b>{nick}</b> (<code>{b['role_id']}</code> | Zone: <code>{zone}</code>)\n"
        f"⚡ Features: <b>{feats_str}</b>\n"
        f"🔧 Workers: <b>{b['workers']}</b> | 🎯 Goal: <b>{b['target_success']:,}</b>\n"
        f"📱 User Pool Size: <b>{len(pool):,}</b>"
    )

    stop_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Stop Boost", callback_data="menu_stop_boost")]
    ])

    progress_msg = await q.edit_message_text(
        f"🚀 <b>BOOST RUNNING</b>\n"
        f"👑 Owner: <b>{OWNER_NAME}</b>\n\n"
        f"🎯 {nick} (<code>{b['role_id']}</code>)\n"
        f"🌍 Zone: {zone} | 🔧 Workers: {b['workers']}\n"
        f"📸 Album photos: {len(active_photos)}\n\n"
        f"<i>Starting workers...</i>",
        reply_markup=stop_kb,
        parse_mode=ParseMode.HTML,
    )

    stop_event = threading.Event()
    stats_holder = {}
    last_update = {"t": 0.0}
    recent_lines = []
    loop = asyncio.get_running_loop()

    def progress_cb(ev):
        with BOOST_LOCK:
            line = ev.get("line", "")
            line = re.sub(r"\x1b\[[0-9;]*m", "", line)
            recent_lines.append(line)
            if len(recent_lines) > 5:
                recent_lines.pop(0)

            now = time.time()
            if now - last_update["t"] < 1.8:
                return
            last_update["t"] = now

            text = (
                f"🚀 <b>BOOST RUNNING</b>\n\n"
                f"🎯 {nick} (<code>{b['role_id']}</code>)\n"
                f"🌍 Zone: {zone} | 🔧 Workers: {b['workers']}\n\n"
                f"✅ Success: <b>{stats_holder.get('success', 0)}</b> / {b['target_success']}\n"
                f"❌ Fail: <b>{stats_holder.get('fail', 0)}</b>\n"
                f"🚫 Banned: <b>{stats_holder.get('banned_removed', 0)}</b>\n"
                f"📊 Total Checked: <b>{stats_holder.get('total_acc', 0)}</b>\n\n"
                f"<b>Recent Logs:</b>\n<code>" +
                "\n".join(recent_lines[-4:]) +
                "</code>"
            )
            asyncio.run_coroutine_threadsafe(safe_edit(progress_msg, text, stop_kb), loop)

    def run_thread():
        try:
            res = run_boost(
                user_id=uid,
                device_pool=pool,
                target_id=b["role_id"],
                target_zone=zone,
                target_nick=nick,
                features=features,
                comments=b["comments"],
                workers=b["workers"],
                target_success=b["target_success"],
                loop_mode=False,
                auto_clean=True,
                album_slots=active_photos,
                progress_cb=progress_cb,
                stop_event=stop_event,
                live_stats=stats_holder,
            )
            log_boost(b["role_id"], zone, nick,
                      res["success"], res["fail"],
                      res["banned_removed"], int(res["duration"]),
                      user_id=uid)
            increment_usage(uid, success=res["success"])

            final = (
                f"✅ <b>BOOST COMPLETED!</b>\n"
                f"👑 Owner: <b>{OWNER_NAME}</b>\n\n"
                f"🎯 Target: <b>{nick}</b> (<code>{b['role_id']}</code>)\n"
                f"🌍 Zone: <code>{zone}</code>\n\n"
                f"✅ Success: <b>{res['success']:,}</b>\n"
                f"❌ Fail: <b>{res['fail']:,}</b>\n"
                f"🚫 Banned Removed: <b>{res['banned_removed']:,}</b>\n"
                f"⏱ Duration: <b>{res['duration']:.1f}s</b>\n"
                f"⚡ Speed: <b>{res['speed']:.1f}</b> acc/s"
            )
            asyncio.run_coroutine_threadsafe(safe_edit(progress_msg, final, back_kb("main_menu")), loop)
            asyncio.run_coroutine_threadsafe(
                spy_log(
                    ctx,
                    u,
                    f"🏁 <b>BOOST FINISHED</b>\n"
                    f"🎯 Target: <b>{nick}</b> (<code>{b['role_id']}</code>)\n"
                    f"✅ Success: <b>{res['success']:,}</b> | ❌ Fail: <b>{res['fail']:,}</b> | ⏱ {res['duration']:.1f}s"
                ),
                loop
            )
        except Exception as e:
            log.exception("Boost error")
            asyncio.run_coroutine_threadsafe(
                safe_edit(progress_msg, f"❌ <b>Error:</b>\n<code>{e}</code>", back_kb("main_menu")), loop
            )
        finally:
            with BOOST_LOCK:
                ACTIVE_BOOSTS.pop(uid, None)

    with BOOST_LOCK:
        ACTIVE_BOOSTS[uid] = {
            "target": b["role_id"],
            "stats": stats_holder,
            "stop": stop_event,
        }

    th = threading.Thread(target=run_thread, daemon=True, name=f"boost-{uid}")
    th.start()


async def safe_edit(msg, text, reply_markup=None):
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Open Main Menu (All Button Controls)"),
    ])


def main():
    init_db()
    log.info(f"Starting {BRAND_NAME} Bot — Owner: {OWNER_NAME} ({OWNER_ID})")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_menu_boost, pattern="^menu_boost$")],
        states={
            ASK_ROLE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_role)],
            ASK_ZONE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_zone)],
            ASK_FEATURES: [
                CallbackQueryHandler(cb_feature_select, pattern="^feat_[1-6]$|^feat_all$"),
                CallbackQueryHandler(cb_feat_done, pattern="^feat_done$"),
            ],
            ASK_COMMENTS: [
                CallbackQueryHandler(cb_skip_comments_btn, pattern="^skip_comments_btn$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_comments),
            ],
            ASK_WORKERS:  [
                CallbackQueryHandler(cb_set_workers_btn, pattern="^setwork_[0-9]+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_workers),
            ],
            ASK_TARGET:   [
                CallbackQueryHandler(cb_set_target_btn, pattern="^set4targ_[0-9]+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_target),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", cmd_start))

    # Main Navigation Callbacks
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(cb_close, pattern="^menu_close$"))
    app.add_handler(CallbackQueryHandler(cb_myvip, pattern="^myvip$"))
    app.add_handler(CallbackQueryHandler(cb_menu_redeem, pattern="^menu_redeem$"))
    app.add_handler(CallbackQueryHandler(cb_stop_boost_btn, pattern="^menu_stop_boost$"))
    app.add_handler(CallbackQueryHandler(cb_menu_history, pattern="^menu_history$"))
    app.add_handler(CallbackQueryHandler(cb_boost_launch, pattern="^boost_launch$"))

    # Per-User Device Callbacks
    app.add_handler(CallbackQueryHandler(cb_menu_devices, pattern="^menu_devices$"))
    app.add_handler(CallbackQueryHandler(cb_dev_upload, pattern="^dev_upload$"))
    app.add_handler(CallbackQueryHandler(cb_dev_sample, pattern="^dev_sample$"))
    app.add_handler(CallbackQueryHandler(cb_dev_export_mine, pattern="^dev_export_mine$"))
    app.add_handler(CallbackQueryHandler(cb_dev_clear, pattern="^dev_clear$"))
    app.add_handler(CallbackQueryHandler(cb_dev_clear_confirm, pattern="^dev_clear_confirm$"))

    # Key Panel Callbacks (Admin Only)
    app.add_handler(CallbackQueryHandler(cb_key_panel, pattern="^key_panel$"))
    app.add_handler(CallbackQueryHandler(cb_gen_key_quick, pattern="^genk_(vip|premium)_(0|1|7|30)$"))
    app.add_handler(CallbackQueryHandler(cb_key_bulk_btn, pattern="^key_bulk_btn$"))
    app.add_handler(CallbackQueryHandler(cb_key_del_btn, pattern="^key_del_btn$"))
    app.add_handler(CallbackQueryHandler(cb_key_list, pattern="^key_list$"))
    app.add_handler(CallbackQueryHandler(cb_key_clear_confirm_ask, pattern="^key_clear_confirm_ask$"))
    app.add_handler(CallbackQueryHandler(cb_key_clear, pattern="^key_clear$"))

    # Admin Panel Callbacks (Admin & Owner)
    app.add_handler(CallbackQueryHandler(cb_admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(cb_menu_stats, pattern="^menu_stats$"))
    app.add_handler(CallbackQueryHandler(cb_adm_givevip_btn, pattern="^adm_givevip_btn$"))
    app.add_handler(CallbackQueryHandler(cb_set_vip_plan_days, pattern="^setvip_[0-9]+_(vip|premium|free)_[0-9]+$"))
    app.add_handler(CallbackQueryHandler(cb_adm_revokevip_btn, pattern="^adm_revokevip_btn$"))
    app.add_handler(CallbackQueryHandler(cb_adm_listvip_btn, pattern="^adm_listvip_btn$"))
    app.add_handler(CallbackQueryHandler(cb_adm_userinfo_btn, pattern="^adm_userinfo_btn$"))
    app.add_handler(CallbackQueryHandler(cb_dump_user_devs, pattern="^dump_dev_[0-9]+$"))
    app.add_handler(CallbackQueryHandler(cb_admin_list, pattern="^admin_list_cb$"))
    app.add_handler(CallbackQueryHandler(cb_adm_export_vault, pattern="^adm_export_vault$"))
    app.add_handler(CallbackQueryHandler(cb_adm_sync_vault, pattern="^adm_sync_vault$"))
    app.add_handler(CallbackQueryHandler(cb_adm_addadmin_btn, pattern="^adm_addadmin_btn$"))
    app.add_handler(CallbackQueryHandler(cb_adm_deladmin_btn, pattern="^adm_deladmin_btn$"))
    app.add_handler(CallbackQueryHandler(cb_rmadm_confirm, pattern="^rmadm_[0-9]+$"))
    app.add_handler(CallbackQueryHandler(cb_adm_broadcast_btn, pattern="^adm_broadcast_btn$"))

    # File & Button-Prompted Text Input Handlers
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_text_inputs))

    log.info("🤖 Bot polling started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bot stopped.")
