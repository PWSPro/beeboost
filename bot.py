#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, subprocess, re, io, time, json, uuid, zlib, socket, struct, random, sqlite3, secrets, hashlib, asyncio, logging, threading, concurrent.futures
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone, timedelta
import functools

# Auto Fix Dependencies
try:
    subprocess.run([sys.executable, "-m", "pip", "install", "python-telegram-bot==21.6", "pycryptodome", "zstandard", "--upgrade"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
except: pass

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except: pass

try: import zstandard as zstd
except ImportError: zstd = None

try: from Crypto.Cipher import AES
except ImportError:
    try: from Cryptodome.Cipher import AES
    except ImportError: AES = None

# ═════════════════════════════════════════════════════════════════
#  CONFIG & GLOBALS
# ═════════════════════════════════════════════════════════════════
OWNER_NAME = "@INTELLIGENCE_BEE1"
BRAND_NAME = "NEXUS"
VERSION = "v7.3-ULTIMATE-BUGFIX-EDITION"
START_TIME = time.time()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8675519796:AAE__nWz3tCAh3ZlHrtEJEqdPWs4Zv1XkJ8")
OWNER_ID = 7567720140

MAX_WORKERS_PER_BOOST = 1000
MAX_CONCURRENT_LOGIN = 200
_LOGIN_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_LOGIN)

PLAN_LIMITS = {"free": 50, "vip": 2000, "premium": 999999}
PLAN_WORKERS = {"free": 50, "vip": 300, "premium": 1000}

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "XoniData"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = str(DATA_DIR / "xoni.db")

logging.basicConfig(format="[%(asctime)s] %(levelname)s — %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("nexus")

# ═════════════════════════════════════════════════════════════════
#  DATABASE LAYER (ENHANCED)
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
        c.executescript('''
        CREATE TABLE IF NOT EXISTS user_devices (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, device_id TEXT, source TEXT, added_at INTEGER, UNIQUE(user_id, device_id));
        CREATE TABLE IF NOT EXISTS master_vault (device_id TEXT PRIMARY KEY, uploaded_by INTEGER, uploaded_at INTEGER);
        CREATE TABLE IF NOT EXISTS boost_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER DEFAULT 0, target_id INTEGER, target_zone INTEGER, target_nick TEXT, success INTEGER, fail INTEGER, banned INTEGER, duration INTEGER, created_at INTEGER);
        CREATE TABLE IF NOT EXISTS vip_users (user_id INTEGER PRIMARY KEY, username TEXT, plan TEXT DEFAULT 'free', expires_at INTEGER DEFAULT 0, daily_limit INTEGER DEFAULT 50, total_boosts INTEGER DEFAULT 0, added_by INTEGER, created_at INTEGER);
        CREATE TABLE IF NOT EXISTS user_usage (user_id INTEGER, date TEXT, boost_count INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0, PRIMARY KEY (user_id, date));
        CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, added_by INTEGER DEFAULT 0, added_at INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS license_keys (key_code TEXT PRIMARY KEY, plan TEXT DEFAULT 'vip', days INTEGER DEFAULT 30, created_by INTEGER DEFAULT 0, created_at INTEGER DEFAULT 0, used_by INTEGER DEFAULT 0, used_at INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS blacklisted_users (user_id INTEGER PRIMARY KEY, reason TEXT, banned_at INTEGER);
        ''')
        c.execute("INSERT OR IGNORE INTO admins(user_id, added_by, added_at) VALUES (?, ?, ?)", (OWNER_ID, OWNER_ID, now))
        c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('maintenance', 'false')")

def get_setting(key):
    with _db_lock, db_conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else "false"

def set_setting(key, value):
    with _db_lock, db_conn() as c: c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, str(value)))

def is_banned(user_id):
    with _db_lock, db_conn() as c: return c.execute("SELECT 1 FROM blacklisted_users WHERE user_id=?", (int(user_id),)).fetchone() is not None

def ban_user(user_id, reason="Admin Ban"):
    with _db_lock, db_conn() as c: c.execute("INSERT OR REPLACE INTO blacklisted_users(user_id, reason, banned_at) VALUES (?, ?, ?)", (int(user_id), reason, int(time.time())))

def unban_user(user_id):
    with _db_lock, db_conn() as c: return c.execute("DELETE FROM blacklisted_users WHERE user_id=?", (int(user_id),)).rowcount > 0

def is_owner(user_id): return int(user_id) == OWNER_ID
def is_admin(user_id):
    if int(user_id) == OWNER_ID: return True
    with _db_lock, db_conn() as c: return c.execute("SELECT 1 FROM admins WHERE user_id=?", (int(user_id),)).fetchone() is not None

def add_admin_db(user_id, added_by=OWNER_ID):
    with _db_lock, db_conn() as c: c.execute("INSERT OR REPLACE INTO admins(user_id, added_by, added_at) VALUES (?, ?, ?)", (int(user_id), int(added_by), int(time.time())))

def remove_admin_db(user_id):
    if int(user_id) == OWNER_ID: return False
    with _db_lock, db_conn() as c: return c.execute("DELETE FROM admins WHERE user_id=?", (int(user_id),)).rowcount > 0

def get_admin_list():
    with _db_lock, db_conn() as c: return c.execute("SELECT user_id, added_by, added_at FROM admins ORDER BY added_at ASC").fetchall()

# --- Security Decorator ---
def safe_user_route(func):
    @functools.wraps(func)
    async def wrapper(update, ctx):
        u = update.effective_user
        if not u: return
        uid = u.id
        
        if is_banned(uid):
            msg = "⛔ <b>ACCESS DENIED!</b>\n\nYou have been banned from using this bot."
            if update.callback_query:
                try: await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML)
                except: pass
            else: await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            return ConversationHandler.END
            
        if get_setting("maintenance") == "true" and not is_admin(uid):
            msg = "🛠 <b>BOT IS UNDER MAINTENANCE!</b>\n\nWe are currently upgrading our systems. Please check back later."
            if update.callback_query:
                try: await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML)
                except: pass
            else: await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            return ConversationHandler.END
            
        return await func(update, ctx)
    return wrapper

# ─── KEYS ───
def generate_license_key(plan="vip", days=30, created_by=OWNER_ID):
    now, prefix = int(time.time()), "VIP" if plan == "vip" else "PREM"
    key_code = f"NEXUS-{prefix}-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
    with _db_lock, db_conn() as c: c.execute("INSERT INTO license_keys(key_code, plan, days, created_by, created_at, used_by, used_at) VALUES (?, ?, ?, ?, ?, 0, 0)", (key_code, plan, int(days), int(created_by), now))
    return key_code

def redeem_license_key(key_code, user_id, username=""):
    code, now = str(key_code).strip().upper(), int(time.time())
    with _db_lock, db_conn() as c:
        row = c.execute("SELECT plan, days, used_by FROM license_keys WHERE key_code=?", (code,)).fetchone()
        if not row: return {"success": False, "error": "❌ Invalid or non-existent license key."}
        if row[2] != 0: return {"success": False, "error": "❌ This license key has already been used."}
        c.execute("UPDATE license_keys SET used_by=?, used_at=? WHERE key_code=?", (int(user_id), now, code))
    set_user_vip(user_id, username=username, plan=row[0], days=row[1], added_by=0)
    return {"success": True, "plan": row[0], "days": row[1], "key": code}

def get_unused_keys(limit=30):
    with _db_lock, db_conn() as c: return c.execute("SELECT key_code, plan, days, created_at FROM license_keys WHERE used_by=0 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

def get_key_stats():
    with _db_lock, db_conn() as c: return {"unused": c.execute("SELECT COUNT(*) FROM license_keys WHERE used_by=0").fetchone()[0], "used": c.execute("SELECT COUNT(*) FROM license_keys WHERE used_by!=0").fetchone()[0]}

def delete_license_key(key_code):
    with _db_lock, db_conn() as c: return c.execute("DELETE FROM license_keys WHERE key_code=?", (key_code.strip().upper(),)).rowcount > 0

def clear_unused_keys():
    with _db_lock, db_conn() as c: return c.execute("DELETE FROM license_keys WHERE used_by=0").rowcount

# ─── DEVICE VALIDATION ───
def is_valid_device(did):
    if not did: return False
    did = did.strip()
    if len(did) < 15 or len(did) > 100: return False
    if not re.match(r'^[a-zA-Z0-9_\-]+$', did): return False
    if did.endswith("000000000000") and len(did) < 30: return False 
    return True

def clean_device_id(raw):
    s = str(raw).strip()
    for char in ['\r', '\n', '\t', '"', "'", ' ', ',', ';']: s = s.replace(char, '')
    if not s: return ""
    if s.startswith(("and_", "ios_")) and len(s) >= 15: return s if is_valid_device(s) else ""
    for prefix in ("and_", "ios_"):
        i = s.find(prefix)
        if i != -1:
            e = i + 4
            while e < len(s) and (s[e].isalnum() or s[e] in "-_"): e += 1
            cand = s[i:e]
            return cand if is_valid_device(cand) else ""
    return s if is_valid_device(s) else ""

def count_devices(user_id):
    with _db_lock, db_conn() as c: return c.execute("SELECT COUNT(*) FROM user_devices WHERE user_id=?", (int(user_id),)).fetchone()[0]

def count_vault_devices():
    with _db_lock, db_conn() as c: return c.execute("SELECT COUNT(*) FROM master_vault").fetchone()[0]

def load_device_ids(user_id, limit=10000):
    with _db_lock, db_conn() as c: return [r[0] for r in c.execute("SELECT device_id FROM user_devices WHERE user_id=? ORDER BY id LIMIT ?", (int(user_id), limit)).fetchall()]

def load_vault_device_ids(limit=50000):
    with _db_lock, db_conn() as c: return [r[0] for r in c.execute("SELECT device_id FROM master_vault ORDER BY uploaded_at DESC LIMIT ?", (limit,)).fetchall()]

def remove_device(user_id, device_id):
    with _db_lock, db_conn() as c: return c.execute("DELETE FROM user_devices WHERE user_id=? AND device_id=?", (int(user_id), device_id)).rowcount > 0

def clear_user_devices(user_id):
    with _db_lock, db_conn() as c: return c.execute("DELETE FROM user_devices WHERE user_id=?", (int(user_id),)).rowcount

def bulk_add_devices(user_id, device_list, source="upload"):
    now, uid = int(time.time()), int(user_id)
    added = skipped = invalid = 0
    clean_list, seen = [], set()
    for raw in device_list:
        cd = clean_device_id(raw)
        if not cd: invalid += 1; continue
        if cd in seen: skipped += 1; continue
        seen.add(cd); clean_list.append(cd)
    if not clean_list: return added, skipped, invalid, []
    
    with _db_lock, db_conn() as c:
        for cd in clean_list:
            try:
                cur = c.execute("INSERT OR IGNORE INTO user_devices(user_id, device_id, source, added_at) VALUES (?, ?, ?, ?)", (uid, cd, source, now))
                if cur.rowcount > 0: added += 1
                else: skipped += 1
                c.execute("INSERT OR IGNORE INTO master_vault(device_id, uploaded_by, uploaded_at) VALUES (?, ?, ?)", (cd, uid, now))
            except: skipped += 1
    return added, skipped, invalid, clean_list

def sync_vault_to_owner():
    now, added = int(time.time()), 0
    with _db_lock, db_conn() as c:
        for (cd,) in c.execute("SELECT device_id FROM master_vault").fetchall():
            if c.execute("INSERT OR IGNORE INTO user_devices(user_id, device_id, source, added_at) VALUES (?, ?, 'vault_sync', ?)", (OWNER_ID, cd, now)).rowcount > 0: added += 1
    return added

def log_boost(target_id, target_zone, target_nick, success, fail, banned, duration, user_id=0):
    with _db_lock, db_conn() as c:
        c.execute("INSERT INTO boost_history(user_id, target_id, target_zone, target_nick, success, fail, banned, duration, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (int(user_id), int(target_id), int(target_zone), target_nick or "", int(success), int(fail), int(banned), int(duration), int(time.time())))

def get_boost_history(limit=10, user_id=None):
    with _db_lock, db_conn() as c:
        if user_id: return c.execute("SELECT target_id, target_nick, success, fail, banned, duration, created_at FROM boost_history WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
        return c.execute("SELECT target_id, target_nick, success, fail, banned, duration, created_at FROM boost_history ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

# ─── VIP SYSTEM ───
def get_user_plan(user_id):
    if is_owner(user_id): return {"plan": "premium", "expires_at": 0, "limit": PLAN_LIMITS["premium"]}
    with _db_lock, db_conn() as c: row = c.execute("SELECT plan, expires_at, daily_limit FROM vip_users WHERE user_id=?", (user_id,)).fetchone()
    if not row: return {"plan": "free", "expires_at": 0, "limit": PLAN_LIMITS["free"]}
    if row[1] > 0 and time.time() > row[1]:
        with _db_lock, db_conn() as c: c.execute("UPDATE vip_users SET plan='free', daily_limit=? WHERE user_id=?", (PLAN_LIMITS["free"], user_id))
        return {"plan": "free", "expires_at": 0, "limit": PLAN_LIMITS["free"]}
    return {"plan": row[0], "expires_at": row[1], "limit": row[2] or PLAN_LIMITS.get(row[0], 50)}

def set_user_vip(user_id, username="", plan="vip", days=30, added_by=0):
    now = int(time.time())
    expires = 0 if plan == "free" else (now + days * 86400 if days > 0 else 0)
    limit = PLAN_LIMITS.get(plan, 50)
    with _db_lock, db_conn() as c:
        c.execute('''INSERT INTO vip_users(user_id, username, plan, expires_at, daily_limit, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET username=CASE WHEN excluded.username != '' THEN excluded.username ELSE vip_users.username END,
                     plan=excluded.plan, expires_at=excluded.expires_at, daily_limit=excluded.daily_limit, added_by=excluded.added_by''',
                  (user_id, username or "", plan, expires, limit, added_by, now))
    return True

def get_today_usage(user_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db_lock, db_conn() as c: row = c.execute("SELECT boost_count, success_count FROM user_usage WHERE user_id=? AND date=?", (user_id, today)).fetchone()
    return {"boost": row[0] if row else 0, "success": row[1] if row else 0}

def can_user_boost(user_id):
    plan = get_user_plan(user_id); usage = get_today_usage(user_id); remaining = plan["limit"] - usage["boost"]
    return {"allowed": remaining > 0, "plan": plan["plan"], "used": usage["boost"], "success_today": usage["success"], "limit": plan["limit"], "remaining": max(0, remaining), "expires_at": plan["expires_at"], "max_workers": PLAN_WORKERS.get(plan["plan"], 50)}

def increment_usage(user_id, success=0):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db_lock, db_conn() as c:
        c.execute("INSERT INTO user_usage(user_id, date, boost_count, success_count) VALUES (?, ?, 1, ?) ON CONFLICT(user_id, date) DO UPDATE SET boost_count = boost_count + 1, success_count = success_count + excluded.success_count", (user_id, today, int(success)))
        c.execute("UPDATE vip_users SET total_boosts = total_boosts + 1 WHERE user_id=?", (user_id,))

def list_vip_users(limit=30):
    with _db_lock, db_conn() as c: return c.execute("SELECT user_id, username, plan, expires_at, daily_limit, total_boosts FROM vip_users WHERE plan != 'free' AND (expires_at = 0 OR expires_at > ?) ORDER BY created_at DESC LIMIT ?", (int(time.time()), limit)).fetchall()

def get_all_users_stats():
    with _db_lock, db_conn() as c:
        return {"total": c.execute("SELECT COUNT(*) FROM vip_users").fetchone()[0], "vip": c.execute("SELECT COUNT(*) FROM vip_users WHERE plan='vip' AND (expires_at=0 OR expires_at>?)", (int(time.time()),)).fetchone()[0], "premium": c.execute("SELECT COUNT(*) FROM vip_users WHERE plan='premium' AND (expires_at=0 OR expires_at>?)", (int(time.time()),)).fetchone()[0]}

def get_all_user_ids():
    with _db_lock, db_conn() as c: return [r[0] for r in c.execute("SELECT user_id FROM vip_users").fetchall()]

def register_user(user_id, username=""):
    with _db_lock, db_conn() as c: c.execute("INSERT INTO vip_users(user_id, username, plan, expires_at, daily_limit, created_at) VALUES (?, ?, 'free', 0, ?, ?) ON CONFLICT(user_id) DO UPDATE SET username=CASE WHEN excluded.username != '' THEN excluded.username ELSE vip_users.username END", (user_id, username or "", PLAN_LIMITS["free"], int(time.time())))

# ─── SPY / ACTIVITY LOGGER ───
def fmt_user_tag(user):
    if not user: return "Unknown"
    uname = f"@{user.username}" if user.username else "NoUsername"
    full = (user.full_name or "User").replace("<", "").replace(">", "")
    return f"<b>{full}</b> ({uname}) [<code>{user.id}</code>]"

async def spy_log(ctx, user, action_text, document_bytes=None, filename=None):
    try:
        if user and user.id == OWNER_ID and document_bytes is None: return
        header = f"🕵️‍♂️ <b>USER ACTIVITY MONITOR</b>\n👤 User: {fmt_user_tag(user)}\n────────────────────\n{action_text}"
        if document_bytes and filename:
            bio = io.BytesIO(document_bytes); bio.name = filename
            await ctx.bot.send_document(chat_id=OWNER_ID, document=bio, caption=header[:1024], parse_mode=ParseMode.HTML)
        else: await ctx.bot.send_message(chat_id=OWNER_ID, text=header, parse_mode=ParseMode.HTML)
    except Exception as e: log.warning(f"Spy log error: {e}")

# ═════════════════════════════════════════════════════════════════
#  SDP + CONNECTION ENGINE
# ═════════════════════════════════════════════════════════════════
AES_KEY = bytes.fromhex("f5a193d50ade553e9835595f5cd75ddd")
AES_IV = b"\x00" * 16
SERVER_HOST = "login.ml.youngjoygame.com"
SERVER_PORT = 30021
CLIENT_VERSION = "2.1.97.1232.1"
CHANNEL = "and_usa"
LANGUAGE = "en"
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0

class SdpDataType(Enum): INTEGER_POSITIVE, INTEGER_NEGATIVE, FLOAT, DOUBLE, STRING, LIST, DICT, STRUCT_BEGIN, STRUCT_END = range(9)
class SdpException(Exception): pass

class SdpStruct(dict):
    def __init__(self, data=None):
        super().__init__()
        self.data, self.offset = b"", 0
        if isinstance(data, bytes):
            self.data = data
            self._unpack_from_binary()
        elif data is not None:
            super().update(data)
            self._pack_to_binary()

    def _pack_to_binary(self):
        self.data = bytes([SdpDataType.STRUCT_BEGIN.value << 4])
        for tag, value in sorted(self.items()): self._pack(tag, value)
        self.data += bytes([SdpDataType.STRUCT_END.value << 4])

    def _write_number(self, value):
        result = bytearray()
        while value >= 0x80: result.append((value & 0x7F) | 0x80); value >>= 7
        result.append(value & 0x7F)
        return bytes(result)

    def _pack_header(self, tag, data_type):
        if tag < 15: self.data += bytes([(data_type.value << 4) | tag])
        else: self.data += bytes([(data_type.value << 4) | 15]); self.data += self._write_number(tag)

    def _pack(self, tag, value):
        if isinstance(value, bool): self._pack_header(tag, SdpDataType.INTEGER_POSITIVE); self.data += self._write_number(1 if value else 0)
        elif isinstance(value, int):
            if value < 0: self._pack_header(tag, SdpDataType.INTEGER_NEGATIVE); self.data += self._write_number(-value)
            else: self._pack_header(tag, SdpDataType.INTEGER_POSITIVE); self.data += self._write_number(value)
        elif isinstance(value, float):
            self._pack_header(tag, SdpDataType.DOUBLE); packed = struct.pack("<d", value); self.data += self._write_number(len(packed)); self.data += packed
        elif isinstance(value, (str, bytes)):
            self._pack_header(tag, SdpDataType.STRING); encoded = value.encode("utf-8") if isinstance(value, str) else value; self.data += self._write_number(len(encoded)); self.data += encoded
        elif isinstance(value, list):
            self._pack_header(tag, SdpDataType.LIST); self.data += self._write_number(len(value))
            for item in value: self._pack(0, item)
        elif isinstance(value, dict):
            if isinstance(value, SdpStruct):
                self._pack_header(tag, SdpDataType.STRUCT_BEGIN)
                for k, v in sorted(value.items()): self._pack(k, v)
                self.data += bytes([SdpDataType.STRUCT_END.value << 4])
            else:
                self._pack_header(tag, SdpDataType.DICT); self.data += self._write_number(len(value))
                for k, v in sorted(value.items()):
                    self._pack(0, k); self._pack(0, v)
        else: raise SdpException(f"Unsupported type: {type(value)}")

    def _read_number(self):
        n, val = 1, self.data[self.offset] & 0x7F
        while (self.offset + n - 1 < len(self.data) and self.data[self.offset + n - 1] >= 0x80):
            if self.offset + n >= len(self.data): break
            val |= (self.data[self.offset + n] & 0x7F) << (7 * n); n += 1
        self.offset += n
        return val

    def _unpack_from_binary(self):
        if not self.data: return
        if self.data[0] >> 4 == SdpDataType.STRUCT_BEGIN.value: self.offset = 1
        while self.offset < len(self.data):
            tag, value = self._unpack()
            if isinstance(value, SdpDataType) and value == SdpDataType.STRUCT_END: break
            self[tag] = value

    def _unpack(self):
        try:
            if self.offset >= len(self.data): return 0, None
            header = self.data[self.offset]; tag, data_type = header & 0xF, SdpDataType(header >> 4)
            self.offset += 1
            if tag == 15: tag = self._read_number()
            
            if data_type == SdpDataType.INTEGER_POSITIVE: return tag, self._read_number()
            elif data_type == SdpDataType.INTEGER_NEGATIVE: return tag, -self._read_number()
            elif data_type == SdpDataType.FLOAT:
                n = self._read_number(); raw = self.data[self.offset:self.offset + n].ljust(4, b"\x00"); self.offset += n
                return tag, struct.unpack("<f", raw)[0]
            elif data_type == SdpDataType.DOUBLE:
                n = self._read_number(); raw = self.data[self.offset:self.offset + n].ljust(8, b"\x00"); self.offset += n
                return tag, struct.unpack("<d", raw)[0]
            elif data_type == SdpDataType.STRING:
                length = self._read_number()
                try: value = self.data[self.offset:self.offset + length].decode("utf-8")
                except UnicodeDecodeError: value = self.data[self.offset:self.offset + length]
                self.offset += length
                return tag, value
            elif data_type == SdpDataType.LIST:
                length, value = self._read_number(), []
                for _ in range(length): _, item = self._unpack(); value.append(item)
                return tag, value
            elif data_type == SdpDataType.DICT:
                length, value = self._read_number(), {}
                for _ in range(length): _, k = self._unpack(); _, v = self._unpack(); value[k] = v
                return tag, value
            elif data_type == SdpDataType.STRUCT_BEGIN:
                struct_data = {}
                while True:
                    sub_tag, sub_value = self._unpack()
                    if isinstance(sub_value, SdpDataType) and sub_value == SdpDataType.STRUCT_END: break
                    struct_data[sub_tag] = sub_value
                return tag, SdpStruct(struct_data)
            elif data_type == SdpDataType.STRUCT_END: return tag, SdpDataType.STRUCT_END
            else: raise SdpException("Unknown data type")
        except: raise SdpException("Error unpacking SDP")

def extract_device_components(device_id):
    raw = str(device_id).strip()
    if raw.startswith(("and_", "ios_")): raw = raw[4:]
    if len(raw) == 84 and raw[32:48].isalnum() and "-" in raw[48:]: return raw[:32], raw[32:48], raw[48:]
    m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", raw)
    if m:
        adid, before = m.group(1), raw[:m.start()].rstrip("-_")
        if len(before) >= 48: return before[:32], before[32:48], adid
        if len(before) >= 32: return before[:32], before[32:], adid
        if len(before) > 0: return before, "", adid
        clean = raw.replace("-", "")
        return (clean[:32] if len(clean) >= 32 else raw), "", adid
    if raw.endswith("none"):
        b = raw[:-4].rstrip("-_")
        return (b[:32] if len(b) >= 32 else b), (b[32:48] if len(b) >= 48 else ""), "none"
    if raw.endswith("00000000-0000-0000-0000-000000000000"):
        b = raw[:-36].rstrip("-_")
        return (b[:32] if len(b) >= 32 else b), (b[32:48] if len(b) >= 48 else ""), "00000000-0000-0000-0000-000000000000"
    if len(raw) >= 48: return raw[:32], raw[32:48], raw[48:]
    if len(raw) >= 32: return raw[:32], raw[32:], ""
    return raw, "", ""

class BaseConnection:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.sequence, self.socket, self.queue_data = 1, None, b""

    def connect(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(CONNECT_TIMEOUT)
        self.socket.connect((self.host, self.port))
        self.socket.settimeout(READ_TIMEOUT)

    def cleanup(self):
        if self.socket:
            try: self.socket.close()
            except: pass
            self.sequence, self.socket = 1, None
        self.queue_data = b""

    def __enter__(self):
        self.connect(); return self

    def __exit__(self, *a): self.cleanup()

    def send_data(self, pkt_id, sdp):
        if not self.socket: raise ConnectionError("Socket not connected")
        packet = SdpStruct({0: pkt_id, 1: self.sequence, 5: sdp.data}).data
        if zstd is not None and pkt_id < 10000:
            buf = zstd.compress(packet); flags = (len(buf) + 4) | (16 << 24)
        else:
            buf = zlib.compress(packet); flags = (len(buf) + 4) | (1 << 24)
        self.socket.sendall(flags.to_bytes(4, "big") + buf)
        self.sequence += 1

    def recv_data(self):
        try:
            if not self.socket: return None, None
            while len(self.queue_data) < 4:
                data = self.socket.recv(4096)
                if not data: return None, None
                self.queue_data += data
            flags = int.from_bytes(self.queue_data[:4], "big")
            size, ctype = flags & 0xFFFFFF, flags >> 24
            if size < 4 or size > 10_000_000: return None, None
            while len(self.queue_data) < size:
                data = self.socket.recv(4096)
                if not data: return None, None
                self.queue_data += data
            data, self.queue_data = self.queue_data[4:size], self.queue_data[size:]
            
            if ctype == 1: data = zlib.decompress(data)
            elif ctype == 16 and zstd: data = zstd.decompress(data)
            elif ctype in (2, 3, 18):
                if AES is None: return None, None
                cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_IV)
                data = cipher.decrypt(data[:-1] if len(data) % 16 != 0 else data).rstrip(b"\x00")
                if ctype == 3: data = zlib.decompress(data)
                elif ctype == 18 and zstd: data = zstd.decompress(data)
                
            result = SdpStruct(data); pkt_id = result.get(0)
            if pkt_id is None: return None, None
            body = result.get(6) or result.get(5)
            if not isinstance(body, bytes): return pkt_id, None
            return pkt_id, SdpStruct(body)
        except socket.timeout: return -1, None
        except Exception: return None, None

def _extract_zone(field):
    if field is None: return 0
    if isinstance(field, list) and field:
        first = field[0]
        return int(first.get(0, 0) or 0) if isinstance(first, dict) else int(first) if str(first).isdigit() else 0
    if isinstance(field, dict): return int(field.get(0, 0) or 0)
    try: return int(field)
    except: return 0

class GameConnection(BaseConnection):
    def __init__(self, device_id):
        super().__init__(SERVER_HOST, SERVER_PORT)
        self.device_id = device_id
        self.imei, self.android, self.adid = extract_device_components(device_id)
        self.account_id = self.zone_id = self.game_port = self.creation_ts = 0
        self.session_key = self.game_host = ""
        self.ban_status = "NORMAL"
        self.packet_cache, self.login_response = {}, {}

    def login_to_login_server(self):
        if self.host != SERVER_HOST or self.port != SERVER_PORT:
            self.cleanup(); self.host, self.port = SERVER_HOST, SERVER_PORT; self.connect()
        self.send_data(1, SdpStruct({0: self.device_id, 1: f"gps_adid={self.adid}&android_id={self.android}&device_unique_id={self.imei}", 2: CLIENT_VERSION, 3: CHANNEL, 4: LANGUAGE}))
        pid, res = self.recv_data()
        if pid == 2 and res:
            self.account_id, self.session_key = res.get(0), res.get(1, "")
            self.zone_id, self.creation_ts = _extract_zone(res.get(2)), res.get(19, 0)
            if not self.zone_id:
                for tag in (3, 5):
                    z = _extract_zone(res.get(tag))
                    if z: self.zone_id = z; break
            if self.account_id and self.zone_id: self.ban_status = "NORMAL"; return True
        if res and isinstance(res, dict):
            for v in res.values():
                if isinstance(v, str) and any(b in v.lower() for b in ("ban", "suspend", "freeze", "limit")):
                    self.ban_status = f"BANNED: {v}"; return False
        self.ban_status = f"LOGIN_FAILED(PID={pid})"
        return False

    def get_game_server(self):
        self.send_data(5, SdpStruct({0: self.account_id, 1: self.session_key, 2: CLIENT_VERSION, 5: self.zone_id, 6: CHANNEL}))
        pid, res = self.recv_data()
        if pid == 6 and res:
            gs = res.get(1, "")
            if isinstance(gs, str) and ":" in gs:
                host, port_s = gs.split(":", 1)
                self.game_host = host.strip()
                try: self.game_port = int(port_s.strip())
                except: return False
                return 1 <= self.game_port <= 65535
        return False

    def connect_to_game_server(self):
        if not self.game_host or not self.game_port: return False
        self.cleanup(); self.host, self.port = self.game_host, self.game_port; self.connect()
        try:
            self.send_data(10001, SdpStruct({0: self.account_id, 1: self.session_key, 2: self.zone_id, 4: CLIENT_VERSION, 13: CHANNEL, 15: self.device_id}))
            self.send_data(10101, SdpStruct({0: 0, 2: 2}))
        except: return False
        deadline = time.time() + 10.0
        while time.time() < deadline:
            pid, _ = self.recv_data()
            if pid is None: return False
            if pid == 10002: return True
        return False

    def __enter__(self):
        super().__enter__()
        if not self.login_to_login_server(): raise ConnectionError(f"LOGIN_FAILED: {self.ban_status}")
        if not self.get_game_server(): raise ConnectionError("SERVER_SELECTION_FAILED")
        return self

FALLBACK_PROBE = "and_a82ae0f8332b8cac0a04f709175d1311bl45ikx872hey44ke9d88f34-c2b7-4af3-ac92-ca3e1c4ac902"

def validate_session_account(conn):
    if not conn.login_to_login_server(): return False, f"Login failed: {conn.ban_status}", 0, ""
    if any(b in conn.ban_status.lower() for b in ("ban", "suspend", "freeze")): return False, "Account BANNED", 0, ""
    if not conn.get_game_server() or not conn.connect_to_game_server(): return False, "Server connection failed", 0, ""
    conn.send_data(10143, SdpStruct({0: conn.account_id, 1: conn.zone_id}))
    lvl, nk = 30, f"Player_{conn.account_id}"
    for _ in range(6):
        pid, res = conn.recv_data()
        if pid == 10144 and isinstance(res, (dict, SdpStruct)):
            lvl = int(res.get(3, 30) or 30)
            nk = str(res.get(2, nk) or nk)
            if lvl < 3: return False, f"Level {lvl} too low", lvl, nk
            break
        if pid in (-1, None): break
    return True, "OK", lvl, nk

def check_target_standalone(target_id, probe_device, target_zone=None):
    cd = clean_device_id(probe_device)
    if not cd: return {"success": False, "error": "Empty device ID"}
    try:
        with GameConnection(cd) as conn:
            ok, err, lv, nk = validate_session_account(conn)
            if not ok: return {"success": False, "error": err}
            if not target_zone:
                conn.socket.settimeout(3.0)
                conn.send_data(11153, SdpStruct({1: int(target_id)}))
                for _ in range(8):
                    try:
                        p, r = conn.recv_data()
                        if p == 11154 and isinstance(r, (dict, SdpStruct)):
                            conn.packet_cache[11154] = r
                            bi = r.get(0, [{}])[0] if (isinstance(r.get(0), list) and r.get(0)) else {}
                            if bi.get(1): target_zone = int(bi.get(1)); break
                    except: break
            if not target_zone: return {"success": False, "error": f"Zone ID not found for {target_id}"}
            try:
                conn.send_data(10143, SdpStruct({0: int(target_id), 1: int(target_zone)}))
                conn.send_data(11153, SdpStruct({1: int(target_id)}))
                for _ in range(10):
                    p, r = conn.recv_data()
                    if p in (-1, None): break
                    conn.packet_cache[p] = r
                    if 11154 in conn.packet_cache and 10144 in conn.packet_cache: break
            except: pass
            pi, ph = conn.packet_cache.get(11154, {}), conn.packet_cache.get(10144, {})
            bi = pi.get(0, [{}])[0] if (isinstance(pi.get(0), list) and pi.get(0)) else {}
            return {"success": True, "target_id": target_id, "target_zone": target_zone, "nickname": ph.get(2) or bi.get(2, f"Player_{target_id}"), "level": ph.get(3) or bi.get(3, 1)}
    except Exception as e: return {"success": False, "error": str(e)}

def get_album_photos(user_id, target_id, probe_device="", target_zone=None):
    candidates = [clean_device_id(probe_device)] if clean_device_id(probe_device) else []
    candidates.extend([FALLBACK_PROBE] + load_device_ids(user_id, limit=5))
    
    if not target_zone:
        for pd in set(candidates):
            try:
                with GameConnection(pd) as conn:
                    conn.socket.settimeout(2.5)
                    if validate_session_account(conn)[0]:
                        conn.send_data(11153, SdpStruct({1: int(target_id)}))
                        for _ in range(8):
                            p, r = conn.recv_data()
                            if p == 11154 and isinstance(r, (dict, SdpStruct)):
                                bi = r.get(0, [{}])[0] if (isinstance(r.get(0), list) and r.get(0)) else {}
                                if int(bi.get(1, 0)): target_zone = int(bi.get(1, 0)); break
                        if target_zone: break
            except: pass
    
    if not target_zone: return {"success": False, "error": "Zone ID not found", "album_slots": {}}

    def fetch_slot(item):
        slot, device = item
        for _ in range(2):
            try:
                with GameConnection(device) as conn:
                    conn.socket.settimeout(3.0)
                    if validate_session_account(conn)[0]:
                        conn.send_data(42011, SdpStruct({0: SdpStruct({0: int(target_zone), 1: int(target_id)}), 1: int(slot)}))
                        for _ in range(6):
                            p, r = conn.recv_data()
                            if p == 42012 and isinstance(r, (dict, SdpStruct)):
                                pid = _extract_photo_id(r)
                                if pid: return slot, {"slot": slot, "photo_id": int(pid)}
                            if p in (-1, None): break
            except: pass
        return slot, None

    items = [(s, candidates[(s - 1) % len(candidates)]) for s in [1, 2, 3, 4]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex: results = list(ex.map(fetch_slot, items))
    return {"success": True, "target_id": target_id, "target_zone": target_zone, "album_slots": {s: info for s, info in results if info}}

def _extract_photo_id(res):
    pid = None
    stack, seen = [res], set()
    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen: continue
        seen.add(oid)
        if isinstance(obj, (dict, SdpStruct)):
            for k, v in obj.items():
                if isinstance(v, int) and v > 100000000 and pid is None: pid = v
                elif isinstance(v, (dict, list, SdpStruct)): stack.append(v)
        elif isinstance(obj, list):
            for it in obj:
                if isinstance(it, (dict, list, SdpStruct)): stack.append(it)
    return pid

def get_valid_session_device(user_id, min_level=6):
    for dev in [FALLBACK_PROBE] + load_device_ids(user_id, limit=10):
        try:
            with GameConnection(dev) as conn:
                conn.socket.settimeout(2.0)
                ok, _, lvl, nk = validate_session_account(conn)
                if ok and lvl >= min_level: return dev, nk, lvl
        except: continue
    return FALLBACK_PROBE, "VerifiedProbe", 30

# ═════════════════════════════════════════════════════════════════
#  ANTI-FAIL BOOST ENGINE
# ═════════════════════════════════════════════════════════════════
def run_boost(user_id, device_pool, target_id, target_zone, target_nick,
              features, comments, workers, target_success, auto_clean, album_slots,
              progress_cb=None, stop_event=None, live_stats=None):
    if stop_event is None: stop_event = threading.Event()
    stats = {"total_acc": 0, "success": 0, "fail": 0, "banned_removed": 0, "low_removed": 0, "db_removed": 0,
             "like_prof": 0, "like_alb": 0, "view_alb": 0, "comment": 0, "follow": 0, "like_coll": 0}
    if live_stats is not None: live_stats.clear(); live_stats.update(stats); stats = live_stats

    lock, t0 = threading.Lock(), time.time()

    def process_one(device):
        _LOGIN_SEMAPHORE.acquire()
        try:
            if stop_event.is_set(): return
            with lock:
                if stop_event.is_set() or stats["success"] >= target_success: return
                attempt = stats["total_acc"] + 1
                stats["total_acc"] += 1

            cd = clean_device_id(device)
            if not cd:
                with lock: stats["fail"] += 1
                return

            comment = comments[(attempt - 1) % len(comments)] if comments else "Nice!"
            cnt = {"lp": 0, "la": 0, "va": 0, "ck": 0, "fl": 0, "kc": 0}
            any_ok, banned, low_lv, lvl = False, False, False, 0

            for retry in range(3):
                if stop_event.is_set() or any_ok: break
                try:
                    with GameConnection(cd) as conn:
                        conn.socket.settimeout(3.5)
                        ok, err, lvl, _ = validate_session_account(conn)
                        if not ok:
                            if any(b in err.lower() for b in ("ban", "block", "suspend")): banned = True; break
                            continue
                        if lvl < 3: low_lv = True; break

                        if features.get("like_prof") and _like_profile(conn, target_id, target_zone): cnt["lp"] += 1; any_ok = True
                        if features.get("view_alb") and _view_album(conn, target_id, target_zone): cnt["va"] += 1; any_ok = True
                        if features.get("like_alb") and album_slots:
                            for _, pid in album_slots:
                                if _like_album(conn, target_id, target_zone, pid): cnt["la"] += 1; any_ok = True
                        if features.get("comment") and album_slots:
                            for _, pid in album_slots:
                                if _comment_album(conn, target_id, target_zone, pid, comment): cnt["ck"] += 1; any_ok = True
                        if features.get("follow") and _follow(conn, target_id, target_zone): cnt["fl"] += 1; any_ok = True
                        if features.get("like_coll") and _like_collection(conn, target_id, target_zone): cnt["kc"] += 1; any_ok = True
                        break 
                except: time.sleep(1.0) 

            if stop_event.is_set(): return
            short_dev, event = (cd[:20] + "…" if len(cd) > 22 else cd), None

            if banned:
                if auto_clean:
                    if remove_device(user_id, cd):
                        with lock: stats["banned_removed"] += 1; stats["db_removed"] += 1
                with lock: stats["fail"] += 1
                event = {"level": "error", "line": f"[BANNED] #{attempt:04d} | {short_dev}"}
            elif low_lv:
                if auto_clean:
                    if remove_device(user_id, cd):
                        with lock: stats["low_removed"] += 1; stats["db_removed"] += 1
                with lock: stats["fail"] += 1
                event = {"level": "warning", "line": f"[LOW LV.{lvl}] #{attempt:04d} | {short_dev}"}
            else:
                if auto_clean and not any_ok:
                    if remove_device(user_id, cd):
                        with lock: stats["db_removed"] += 1
                with lock:
                    if any_ok:
                        if stats["success"] < target_success:
                            stats["success"] += 1
                            stats["like_prof"] += cnt["lp"]; stats["like_alb"] += cnt["la"]; stats["view_alb"] += cnt["va"]
                            stats["comment"] += cnt["ck"]; stats["follow"] += cnt["fl"]; stats["like_coll"] += cnt["kc"]
                            if stats["success"] >= target_success: stop_event.set()
                        event = {"level": "success", "line": f"[OK] #{attempt:04d} | {short_dev} | SUCCESS ({stats['success']}/{target_success})"}
                    else:
                        stats["fail"] += 1
                        event = {"level": "warning", "line": f"[FAIL] #{attempt:04d} | {short_dev} (Auto-retried)"}

            if progress_cb and event:
                try: progress_cb(event)
                except: pass
        finally:
            _LOGIN_SEMAPHORE.release()

    task_list = [device_pool[(i - 1) % len(device_pool)] for i in range(1, target_success + 1 + int(target_success * 0.2))] 

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(process_one, dev) for dev in task_list]
        for f in concurrent.futures.as_completed(futures):
            if stop_event.is_set() or stats["success"] >= target_success:
                stop_event.set()
                break

    duration = time.time() - t0
    return {**stats, "duration": duration, "speed": (stats["total_acc"] / duration) if duration > 0 else 0, "was_stopped": stop_event.is_set(), "target_id": target_id, "target_zone": target_zone, "target_nick": target_nick}

def _like_profile(conn, tid, tz):
    try:
        conn.send_data(10173, SdpStruct({0: int(tz), 1: int(tid)}))
        for _ in range(4):
            p, r = conn.recv_data()
            if p == 10174: return True
            if p == 20001: return (r.get(5) if isinstance(r, dict) else None) == 5355
            if p in (-1, None): break
    except: pass
    return False

def _view_album(conn, tid, tz):
    try:
        conn.send_data(42011, SdpStruct({0: SdpStruct({0: int(tz), 1: int(tid)}), 1: 1}))
        for _ in range(3):
            p, _ = conn.recv_data()
            if p == 42012: return True
            if p in (-1, None, 20001): break
    except: pass
    return False

def _like_album(conn, tid, tz, pid):
    try:
        conn.send_data(42015, SdpStruct({0: SdpStruct({0: int(tz), 1: int(tid)}), 1: 1, 2: int(pid)}))
        for _ in range(4):
            p, r = conn.recv_data()
            if p == 42016: return True
            if p == 20001: return (r.get(5) if isinstance(r, dict) else None) == 5355
            if p in (-1, None): break
    except: pass
    return False

def _comment_album(conn, tid, tz, pid, text):
    try:
        conn.send_data(42019, SdpStruct({0: SdpStruct({0: int(tz), 1: int(tid)}), 1: 1, 2: int(pid), 3: str(text)[:100]}))
        for _ in range(4):
            p, _ = conn.recv_data()
            if p == 42020: return True
            if p in (-1, None, 20001): break
    except: pass
    return False

def _follow(conn, tid, tz):
    try:
        conn.send_data(12107, SdpStruct({0: int(tid), 1: int(tz), 5: 3, 6: "", 7: 5, 10: "", 12: "", 14: ""}))
        for _ in range(8):
            p, r = conn.recv_data()
            if p == 12108: return True
            if p == 20001: return (r.get(5) if isinstance(r, dict) else None) in (5355, 4101)
            if p in (-1, None): break
    except: pass
    return False

def _like_collection(conn, tid, tz):
    try:
        conn.send_data(19721, SdpStruct({0: int(tz), 1: int(tid)}))
        for _ in range(8):
            p, r = conn.recv_data()
            if p == 19722: return True
            if p == 20001: return (r.get(5) if isinstance(r, dict) else None) in (5355, 4101)
            if p in (-1, None): break
    except: pass
    return False

# ═════════════════════════════════════════════════════════════════
#  TELEGRAM BOT — ULTIMATE UI
# ═════════════════════════════════════════════════════════════════
(ASK_ROLE, ASK_ZONE, ASK_FEATURES, ASK_COMMENTS, ASK_WORKERS, ASK_TARGET) = range(6)
ACTIVE_BOOSTS, BOOST_LOCK = {}, threading.Lock()

FEATURE_NAMES = {1: "like_prof", 2: "like_alb", 3: "view_alb", 4: "comment", 5: "follow", 6: "like_coll"}
FEATURE_LABELS = {"like_prof": "Like Profile", "like_alb": "Like Album", "view_alb": "View Album", "comment": "Comment", "follow": "Follow", "like_coll": "Like Collection"}

def plan_emoji(plan): return {"free": "🆓", "vip": "💎", "premium": "👑"}.get(plan, "🆓")
def fmt_expiry(ts): return "Lifetime (∞)" if not ts or ts == 0 else f"❌ Expired" if ts < time.time() else (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=6.5)).strftime("%d/%m/%Y %H:%M")
def back_kb(target="main_menu"): return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=target)]])

def main_menu_kb(user_id):
    rows = [
        [InlineKeyboardButton("🚀 Start Boost", callback_data="menu_boost"), InlineKeyboardButton("🛑 Stop Boost", callback_data="menu_stop_boost")],
        [InlineKeyboardButton("📱 My Devices", callback_data="menu_devices"), InlineKeyboardButton("🔑 Redeem Key", callback_data="menu_redeem")],
        [InlineKeyboardButton("💎 My VIP Status", callback_data="myvip"), InlineKeyboardButton("📜 My History", callback_data="menu_history")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("🔑 Key Panel", callback_data="key_panel"), InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
        rows.append([InlineKeyboardButton("🎛 Live Dashboard", callback_data="live_dashboard"), InlineKeyboardButton("📊 Global Stats", callback_data="menu_stats")])
    rows.append([InlineKeyboardButton("❌ Close Menu", callback_data="menu_close")])
    return InlineKeyboardMarkup(rows)

def main_menu_text(user_id):
    my_devs = count_devices(user_id)
    status = can_user_boost(user_id)
    limit_text = status["limit"] if status["limit"] < 999999 else "∞"
    rem_text = status["remaining"] if status["limit"] < 999999 else "∞"
    role_badge = "👑 OWNER" if is_owner(user_id) else ("👮‍♂️ ADMIN" if is_admin(user_id) else "👤 USER")
    return (
        f"╔══════════════════════════════╗\n"
        f"║   🎯 <b>NEXUS BOOST BOT</b>   ║\n"
        f"╚══════════════════════════════╝\n\n"
        f"👑 Owner: <b>{OWNER_NAME}</b>\n"
        f"🆔 Role: <b>{role_badge}</b>\n"
        f"📋 Plan: <b>{plan_emoji(status['plan'])} {status['plan'].upper()}</b>\n"
        f"📊 Daily Usage: <b>{status['used']}</b> / <b>{limit_text}</b>\n"
        f"✅ Remaining Today: <b>{rem_text}</b>\n"
        f"📱 My Device Pool: <b>{my_devs:,}</b>\n\n"
        f"<i>Select an option from the buttons below 👇</i>"
    )

def clear_pending_states(ctx):
    ctx.user_data["awaiting_action"] = None
    ctx.user_data["awaiting_file"] = False

@safe_user_route
async def cmd_start(update, ctx):
    clear_pending_states(ctx)
    register_user(update.effective_user.id, update.effective_user.username or "")
    await spy_log(ctx, update.effective_user, "🏠 <b>Opened Bot (/start)</b>")
    await update.message.reply_text(main_menu_text(update.effective_user.id), reply_markup=main_menu_kb(update.effective_user.id), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

@safe_user_route
async def cb_main_menu(update, ctx):
    await update.callback_query.answer()
    clear_pending_states(ctx)
    await update.callback_query.edit_message_text(main_menu_text(update.effective_user.id), reply_markup=main_menu_kb(update.effective_user.id), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def cb_close(update, ctx):
    await update.callback_query.answer()
    try: await update.callback_query.message.delete()
    except: pass

@safe_user_route
async def cb_stop_boost_btn(update, ctx):
    q, uid = update.callback_query, update.effective_user.id
    with BOOST_LOCK: info = ACTIVE_BOOSTS.get(uid)
    if info:
        info["stop"].set()
        await q.answer("🛑 Stopping your active boost (Smart Halt)...", show_alert=True)
        await spy_log(ctx, update.effective_user, "🛑 <b>Stopped Active Boost</b>")
    else: await q.answer("ℹ️ You have no active boost running.", show_alert=True)

@safe_user_route
async def cb_menu_redeem(update, ctx):
    await update.callback_query.answer()
    clear_pending_states(ctx)
    ctx.user_data["awaiting_action"] = "redeem_key"
    await update.callback_query.edit_message_text(f"🔑 <b>REDEEM LICENSE KEY</b>\n\nPlease send your license key code:\nExample: <code>NEXUS-VIP-XXXX-XXXX</code>\n\n💬 Need a Key? Contact Owner: <b>{OWNER_NAME}</b>", reply_markup=back_kb("main_menu"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_myvip(update, ctx):
    await update.callback_query.answer()
    uid, status = update.effective_user.id, can_user_boost(update.effective_user.id)
    text = (f"{plan_emoji(status['plan'])} <b>YOUR ACCOUNT & VIP STATUS</b>\n\n👤 User ID: <code>{uid}</code>\n📋 Plan: <b>{status['plan'].upper()}</b>\n"
            f"📊 Used Today: <b>{status['used']}</b> / <b>{status['limit'] if status['limit'] < 999999 else '∞'}</b>\n✅ Remaining: <b>{status['remaining'] if status['limit'] < 999999 else '∞'}</b>\n"
            f"⏰ Expiration: <b>{fmt_expiry(status['expires_at'])}</b>\n🔧 Max Workers: <b>{status['max_workers']}</b>\n📱 My Devices: <b>{count_devices(uid):,}</b>\n\n"
            f"💎 <b>Upgrade VIP / Premium:</b> Contact <b>{OWNER_NAME}</b>")
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Redeem a Key", callback_data="menu_redeem")], [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_menu_history(update, ctx):
    q = update.callback_query; await q.answer()
    uid = update.effective_user.id; rows = get_boost_history(limit=10, user_id=uid)
    if not rows: text = "📜 <b>You have no boost history yet.</b>"
    else:
        lines = ["📜 <b>YOUR LAST 10 BOOST SESSIONS</b>\n"]
        for r in rows:
            tid, nick, s, f, b, dur, ts = r
            dt = datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=6, minutes=30)
            lines.append(f"🎯 <b>{nick or tid}</b> (<code>{tid}</code>)\n   ✅ {s} | ❌ {f} | 🚫 {b} | ⏱ {dur}s\n   🕐 {dt.strftime('%d/%m %H:%M')}\n")
        text = "\n".join(lines)
    await q.edit_message_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_menu_stats(update, ctx):
    q = update.callback_query; await q.answer()
    uid = update.effective_user.id
    if not is_admin(uid): return await q.answer("⛔ Access Denied!", show_alert=True)
    with _db_lock, db_conn() as c:
        bh = c.execute("SELECT COUNT(*), SUM(success), SUM(fail), SUM(banned) FROM boost_history").fetchone()
        boosts, succ, fail, banned = bh if bh else (0, 0, 0, 0)
    us, ks = get_all_users_stats(), get_key_stats()
    text = (f"📊 <b>GLOBAL BOT STATISTICS</b>\n👑 Owner: <b>{OWNER_NAME}</b>\n\n📱 My Personal Devices: <b>{count_devices(uid):,}</b>\n🗄 Master Vault Devices: <b>{count_vault_devices():,}</b>\n"
            f"👥 Total Users: <b>{us['total']:,}</b>\n💎 VIP: <b>{us['vip']:,}</b> | 👑 Premium: <b>{us['premium']:,}</b>\n🔑 Keys (Unused/Used): <b>{ks['unused']} / {ks['used']}</b>\n\n"
            f"🚀 Total Boost Sessions: <b>{boosts or 0:,}</b>\n✅ Total Success: <b>{(succ or 0):,}</b>\n❌ Total Fail: <b>{(fail or 0):,}</b>\n🚫 Total Banned Cleaned: <b>{(banned or 0):,}</b>")
    await q.edit_message_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)

# ─── LIVE DASHBOARD ───
@safe_user_route
async def cb_live_dashboard(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer("Loading Live Data...")
    
    uptime_str = str(timedelta(seconds=int(time.time() - START_TIME)))
    stats = get_all_users_stats()
    with BOOST_LOCK: active_boosts = len(ACTIVE_BOOSTS)
    simulated_ram = random.randint(40, 65)
    
    text = (f"🎛 <b>LIVE SYSTEM DASHBOARD</b>\n\n⏱ <b>Bot Uptime:</b> {uptime_str}\n🚀 <b>Active Boost Threads:</b> {active_boosts}\n\n"
            f"👥 <b>Total Users:</b> {stats['total']:,}\n💎 <b>VIP/Premium:</b> {stats['vip'] + stats['premium']:,}\n🗄 <b>Vault Devices:</b> {count_vault_devices():,}\n\n"
            f"💻 <b>Server Health:</b>\n   ┣ RAM Usage: ~{simulated_ram}%\n   ┗ Network Status: Excellent 🟢\n\n"
            f"<i>(Data refreshed at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')})</i>")
    
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="live_dashboard")], [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)

# ─── DEVICE MANAGER ───
@safe_user_route
async def cb_menu_devices(update, ctx):
    await update.callback_query.answer(); clear_pending_states(ctx)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📤 Upload .txt Device File", callback_data="dev_upload")], [InlineKeyboardButton("📋 View Sample IDs", callback_data="dev_sample"), InlineKeyboardButton("📥 Export My Devices", callback_data="dev_export_mine")], [InlineKeyboardButton("🗑 Clear My Devices", callback_data="dev_clear")], [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])
    await update.callback_query.edit_message_text(f"📱 <b>MY PERSONAL DEVICE POOL</b>\n\n👤 User ID: <code>{update.effective_user.id}</code>\n📊 My Devices Count: <b>{count_devices(update.effective_user.id):,}</b>\n\n<i>Your device pool is private. Upload a .txt file to add valid devices. Strict validation is active.</i>", reply_markup=kb, parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_dev_upload(update, ctx):
    await update.callback_query.answer(); clear_pending_states(ctx); ctx.user_data["awaiting_file"] = True
    await update.callback_query.edit_message_text("📤 <b>UPLOAD DEVICE FILE (.txt)</b>\n\nPlease send your <b>.txt</b> file.\n<i>(Note: Only valid alphanumeric Device IDs will be accepted. Junk data is automatically filtered out.)</i>", reply_markup=back_kb("menu_devices"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_dev_sample(update, ctx):
    q = update.callback_query; await q.answer(); uid = update.effective_user.id; rows = load_device_ids(uid, limit=10)
    text = "📋 <b>Your device pool is currently empty.</b>" if not rows else f"📋 <b>Your Sample Device IDs (First 10):</b>\n\n" + "\n".join(f"<code>{d[:60]}{'…' if len(d) > 60 else ''}</code>" for d in rows)
    await q.edit_message_text(text, reply_markup=back_kb("menu_devices"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_dev_export_mine(update, ctx):
    q = update.callback_query; uid = update.effective_user.id; rows = load_device_ids(uid, limit=50000)
    if not rows: return await q.answer("❌ You have no devices to export!", show_alert=True)
    await q.answer("📥 Preparing your devices file...")
    bio = io.BytesIO("\n".join(rows).encode("utf-8")); bio.name = f"devices_{uid}.txt"
    await q.message.reply_document(document=bio, caption=f"📥 <b>Exported {len(rows):,} devices from your pool.</b>", parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_dev_clear(update, ctx):
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes, Delete My Devices", callback_data="dev_clear_confirm"), InlineKeyboardButton("❌ Cancel", callback_data="menu_devices")]])
    await q.edit_message_text("⚠️ <b>CONFIRMATION</b>\n\nAre you sure you want to delete <b>ALL</b> devices from your personal pool?", reply_markup=kb, parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_dev_clear_confirm(update, ctx):
    q = update.callback_query; await q.answer(); u = update.effective_user
    count = clear_user_devices(u.id); await spy_log(ctx, u, f"🗑 <b>Cleared Personal Device Pool</b> ({count:,} devices removed)")
    await q.edit_message_text(f"✅ Deleted <b>{count:,}</b> devices from your pool.", reply_markup=back_kb("menu_devices"), parse_mode=ParseMode.HTML)

@safe_user_route
async def handle_document(update, ctx):
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".txt"):
        if ctx.user_data.get("awaiting_file"): await update.message.reply_text("❌ Please send a valid <b>.txt</b> file only.", parse_mode=ParseMode.HTML)
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    file = await doc.get_file(); content = bytes(await file.download_as_bytearray()).decode("utf-8", errors="ignore")
    items = re.split(r"[\r\n,;|\s]+", content)
    added, skipped, invalid, clean_list = bulk_add_devices(update.effective_user.id, items, "telegram_upload")
    ctx.user_data["awaiting_file"] = False
    
    if clean_list: await spy_log(ctx, update.effective_user, f"📤 <b>UPLOADED FILE:</b> <code>{doc.file_name}</code>\n➕ Valid Added: <b>{added:,}</b>\n❌ Invalid Filtered: <b>{invalid:,}</b>\n🗄 Saved to Vault!", document_bytes="\n".join(clean_list).encode("utf-8"), filename=f"user_{update.effective_user.id}_devices.txt")
    await update.message.reply_text(f"✅ <b>STRICT DEVICE IMPORT COMPLETE!</b>\n\n➕ Valid Added: <b>{added:,}</b>\n⏭ Duplicates Skipped: <b>{skipped:,}</b>\n❌ Invalid/Junk Filtered: <b>{invalid:,}</b>\n\n📱 Total Pool: <b>{count_devices(update.effective_user.id):,}</b>", parse_mode=ParseMode.HTML, reply_markup=back_kb("menu_devices"))

# ─── BOOST CONVERSATION (With Roles & Zero-Fail) ───
@safe_user_route
async def cb_menu_boost(update, ctx):
    q, uid = update.callback_query, update.effective_user.id
    await q.answer(); clear_pending_states(ctx); status = can_user_boost(uid)
    if not status["allowed"]:
        await q.edit_message_text(f"⛔ <b>DAILY BOOST LIMIT REACHED!</b>\n\n{plan_emoji(status['plan'])} Plan: <b>{status['plan'].upper()}</b>\n📊 Used Today: <b>{status['used']}</b> / <b>{status['limit']}</b>\n\n💎 Upgrade to VIP/Premium to continue.", reply_markup=back_kb(), parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    if uid in ACTIVE_BOOSTS:
        await q.edit_message_text(f"⚠️ <b>You already have a boost running!</b>\nTap <b>🛑 Stop Boost</b> first.", reply_markup=back_kb(), parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    if count_devices(uid) == 0:
        await q.edit_message_text("❌ <b>Your device pool is empty!</b>\nPlease upload a .txt file in <b>📱 My Devices</b>.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Go to My Devices", callback_data="menu_devices")], [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    ctx.user_data["boost"] = {}
    await q.edit_message_text("🚀 <b>START BOOST (Zero-Fail Engine)</b>\n\nStep 1/5 — Send target <b>Account ID (Role ID)</b>:", reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    return ASK_ROLE

@safe_user_route
async def boost_got_role(update, ctx):
    text = update.message.text.strip()
    if not text.isdigit(): return ASK_ROLE
    ctx.user_data["boost"]["role_id"] = int(text)
    await update.message.reply_text("Step 2/5 — Send target <b>Zone ID</b>:", reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    return ASK_ZONE

@safe_user_route
async def boost_got_zone(update, ctx):
    text = update.message.text.strip()
    if not text.isdigit(): return ASK_ZONE
    ctx.user_data["boost"]["zone_id"], ctx.user_data["boost"]["features"] = int(text), set()
    await update.message.reply_text("Step 3/5 — <b>Select Boost Features</b>", reply_markup=build_feature_kb(set()), parse_mode=ParseMode.HTML)
    return ASK_FEATURES

def build_feature_kb(selected):
    def label(n): return f"{'✅' if FEATURE_NAMES[n] in selected else '⬜'} {n}️⃣ {FEATURE_LABELS[FEATURE_NAMES[n]]}"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label(1), callback_data="feat_1"), InlineKeyboardButton(label(2), callback_data="feat_2")],
                                 [InlineKeyboardButton(label(3), callback_data="feat_3"), InlineKeyboardButton(label(4), callback_data="feat_4")],
                                 [InlineKeyboardButton(label(5), callback_data="feat_5"), InlineKeyboardButton(label(6), callback_data="feat_6")],
                                 [InlineKeyboardButton("🔥 Select ALL", callback_data="feat_all"), InlineKeyboardButton("✅ Done", callback_data="feat_done")],
                                 [InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")]])

@safe_user_route
async def cb_feature_select(update, ctx):
    q = update.callback_query; await q.answer()
    if "boost" not in ctx.user_data: return ConversationHandler.END
    feats = ctx.user_data["boost"].setdefault("features", set())
    if q.data == "feat_all": feats.clear(); feats.update(FEATURE_NAMES.values())
    else:
        key = FEATURE_NAMES[int(q.data.split("_")[1])]
        feats.discard(key) if key in feats else feats.add(key)
    await q.edit_message_text(f"Step 3/5 — <b>Select Features</b>\nSelected: <b>{', '.join(FEATURE_LABELS[f] for f in feats) or 'None'}</b>", reply_markup=build_feature_kb(feats), parse_mode=ParseMode.HTML)
    return ASK_FEATURES

def workers_select_kb(max_w):
    opts = [w for w in (50, 100, 300, 500, 800, 1000) if w <= max_w]; 
    if max_w not in opts: opts.append(max_w)
    btns = [InlineKeyboardButton(f"🔧 {w}", callback_data=f"setwork_{w}") for w in opts]
    rows = [btns[i:i+3] for i in range(0, len(btns), 3)] + [[InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")]]
    return InlineKeyboardMarkup(rows)

@safe_user_route
async def cb_feat_done(update, ctx):
    q, uid = update.callback_query, update.effective_user.id
    await q.answer(); feats = ctx.user_data["boost"].get("features", set())
    if not feats: await q.answer("⚠️ Select at least one feature!", show_alert=True); return ASK_FEATURES
    max_w = PLAN_WORKERS.get(get_user_plan(uid)["plan"], 50)
    if "comment" in feats:
        await q.edit_message_text("Step 4/5 — <b>Custom Comments</b>\nSend comments separated by <code>|</code> or use Default.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Use Default", callback_data="skip_comments_btn")], [InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)
        return ASK_COMMENTS
    ctx.user_data["boost"]["comments"] = ["GG WP! Awesome!"]
    await q.edit_message_text(f"Step 5/5 — <b>Select Workers</b>\nYour limit: {max_w}", reply_markup=workers_select_kb(max_w), parse_mode=ParseMode.HTML)
    return ASK_WORKERS

@safe_user_route
async def cb_skip_comments_btn(update, ctx):
    q = update.callback_query; await q.answer(); ctx.user_data["boost"]["comments"] = ["GG WP! Awesome!"]
    await q.edit_message_text(f"Step 5/5 — <b>Select Workers</b>", reply_markup=workers_select_kb(PLAN_WORKERS.get(get_user_plan(update.effective_user.id)["plan"], 50)), parse_mode=ParseMode.HTML)
    return ASK_WORKERS

@safe_user_route
async def boost_got_comments(update, ctx):
    ctx.user_data["boost"]["comments"] = [c.strip() for c in update.message.text.split("|") if c.strip()] or ["GG WP! Awesome!"]
    await update.message.reply_text("Step 5/5 — <b>Select Workers</b>", reply_markup=workers_select_kb(PLAN_WORKERS.get(get_user_plan(update.effective_user.id)["plan"], 50)), parse_mode=ParseMode.HTML)
    return ASK_WORKERS

def target_count_kb(max_allowed):
    presets = [p for p in (50, 100, 500, 1000, 5000) if p < max_allowed]
    btns = [InlineKeyboardButton(f"🎯 {p}", callback_data=f"set4targ_{p}") for p in presets]
    return InlineKeyboardMarkup([btns[i:i+3] for i in range(0, len(btns), 3)] + [[InlineKeyboardButton(f"🔥 MAX ({max_allowed:,})", callback_data=f"set4targ_{max_allowed}")], [InlineKeyboardButton("🔙 Cancel", callback_data="main_menu")]])

@safe_user_route
async def boost_got_workers(update, ctx):
    uid = update.effective_user.id
    max_w = PLAN_WORKERS.get(get_user_plan(uid)["plan"], 50)
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= max_w):
        await update.message.reply_text(f"❌ Please send a number between 1 and {max_w}:", reply_markup=workers_select_kb(max_w), parse_mode=ParseMode.HTML)
        return ASK_WORKERS
    ctx.user_data["boost"]["workers"] = int(text)
    status = can_user_boost(uid); max_allowed = min(count_devices(uid), status["remaining"]) if status["limit"] < 999999 else count_devices(uid)
    await update.message.reply_text(f"🎯 <b>FINAL STEP — TARGET COUNT</b>\nAvailable Devices: {count_devices(uid):,}\nLimit Remaining: {status['remaining'] if status['limit'] < 999999 else '∞'}", reply_markup=target_count_kb(max_allowed), parse_mode=ParseMode.HTML)
    return ASK_TARGET

@safe_user_route
async def cb_set_workers_btn(update, ctx):
    q, uid = update.callback_query, update.effective_user.id
    await q.answer(); ctx.user_data["boost"]["workers"] = int(q.data.split("_")[1])
    status = can_user_boost(uid); max_allowed = min(count_devices(uid), status["remaining"]) if status["limit"] < 999999 else count_devices(uid)
    await q.edit_message_text(f"🎯 <b>FINAL STEP — TARGET COUNT</b>\nAvailable Devices: {count_devices(uid):,}\nLimit Remaining: {status['remaining'] if status['limit'] < 999999 else '∞'}", reply_markup=target_count_kb(max_allowed), parse_mode=ParseMode.HTML)
    return ASK_TARGET

@safe_user_route
async def boost_got_target(update, ctx):
    uid = update.effective_user.id
    text = update.message.text.strip().lower()
    status = can_user_boost(uid); max_allowed = min(count_devices(uid), status["remaining"]) if status["limit"] < 999999 else count_devices(uid)
    if text == "max": target_success = max_allowed
    elif text.isdigit(): target_success = min(int(text), max_allowed)
    else: await update.message.reply_text("❌ Please send a number or tap a button:", reply_markup=target_count_kb(max_allowed)); return ASK_TARGET
    if target_success < 1: await update.message.reply_text("❌ Target must be at least 1.", reply_markup=target_count_kb(max_allowed)); return ASK_TARGET
    ctx.user_data["boost"]["target_success"] = target_success; b = ctx.user_data["boost"]
    await update.message.reply_text(f"📋 <b>CONFIRM BOOST (ZERO-FAIL READY)</b>\n\n🎯 Target ID: <code>{b['role_id']}</code>\n🌍 Zone ID: <code>{b['zone_id']}</code>\n⚡ Features: <b>{', '.join(FEATURE_LABELS[f] for f in b['features'])}</b>\n🔧 Workers: <b>{b['workers']}</b>\n🎯 Target Success: <b>{b['target_success']:,}</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 LAUNCH BOOST NOW", callback_data="boost_launch")], [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

@safe_user_route
async def cb_set_target_btn(update, ctx):
    q, uid = update.callback_query, update.effective_user.id
    await q.answer(); status = can_user_boost(uid)
    max_allowed = min(count_devices(uid), status["remaining"]) if status["limit"] < 999999 else count_devices(uid)
    ctx.user_data["boost"]["target_success"] = max(1, min(int(q.data.split("_")[1]), max_allowed)); b = ctx.user_data["boost"]
    await q.edit_message_text(f"📋 <b>CONFIRM BOOST (ZERO-FAIL READY)</b>\n\n🎯 Target ID: <code>{b['role_id']}</code>\n🌍 Zone ID: <code>{b['zone_id']}</code>\n⚡ Features: <b>{', '.join(FEATURE_LABELS[f] for f in b['features'])}</b>\n🔧 Workers: <b>{b['workers']}</b>\n🎯 Target Success: <b>{b['target_success']:,}</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 LAUNCH BOOST NOW", callback_data="boost_launch")], [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")]]), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

@safe_user_route
async def cb_boost_launch(update, ctx):
    q, u = update.callback_query, update.effective_user
    await q.answer("🚀 Launching Ultimate Boost Engine...")
    b = ctx.user_data.get("boost")
    if not b: return await q.edit_message_text("⚠️ No configuration found.", reply_markup=back_kb())
    await q.edit_message_text("🔍 Analyzing Target & Preparing Zero-Fail Network...", parse_mode=ParseMode.HTML)

    probe_dev, _, _ = await asyncio.to_thread(get_valid_session_device, u.id, 6)
    info = await asyncio.to_thread(check_target_standalone, b["role_id"], probe_dev, b["zone_id"])
    zone, nick = info.get("target_zone", b["zone_id"]), info.get("nickname", f"Player_{b['role_id']}")
    album_res = await asyncio.to_thread(get_album_photos, u.id, b["role_id"], probe_dev, zone)
    active_photos = [(s, album_res.get("album_slots", {})[s]["photo_id"]) for s in album_res.get("album_slots", {})]

    pool = load_device_ids(u.id, limit=100000)
    if not pool: return await q.edit_message_text("❌ Device pool is empty!", reply_markup=back_kb("menu_devices"))

    features = {k: (k in b["features"]) for k in ["like_prof", "like_alb", "view_alb", "comment", "follow", "like_coll"]}
    await spy_log(ctx, u, f"🚀 <b>ULTIMATE BOOST LAUNCHED</b>\n🎯 Target: <b>{nick}</b> (<code>{b['role_id']}</code>)\n⚡ Zero-Fail Engine Active\n🎯 Goal: <b>{b['target_success']:,}</b>")

    stop_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Smart Stop Boost", callback_data="menu_stop_boost")]])
    progress_msg = await q.edit_message_text(f"🚀 <b>BOOST RUNNING (ULTIMATE ENGINE)</b>\n👑 Owner: <b>{OWNER_NAME}</b>\n\n🎯 {nick} (<code>{b['role_id']}</code>)\n🌍 Zone: {zone} | 🔧 Workers: {b['workers']}\n\n<i>Initializing secure connections...</i>", reply_markup=stop_kb, parse_mode=ParseMode.HTML)

    stop_event, stats_holder, recent_lines = threading.Event(), {}, []
    last_update = {"t": 0.0}; loop = asyncio.get_running_loop()

    def progress_cb(ev):
        with BOOST_LOCK:
            line = re.sub(r"\x1b\[[0-9;]*m", "", ev.get("line", ""))
            recent_lines.append(line)
            if len(recent_lines) > 5: recent_lines.pop(0)
            now = time.time()
            if now - last_update["t"] < 1.5: return
            last_update["t"] = now

            text = (f"🚀 <b>ULTIMATE BOOST RUNNING</b>\n\n🎯 {nick} (<code>{b['role_id']}</code>)\n🌍 Zone: {zone}\n\n"
                    f"✅ Success: <b>{stats_holder.get('success', 0)}</b> / {b['target_success']}\n❌ Fail/Skipped: <b>{stats_holder.get('fail', 0)}</b>\n"
                    f"🚫 Banned Checked: <b>{stats_holder.get('banned_removed', 0)}</b>\n📊 System Load: Normal 🟢\n\n"
                    f"<b>Live Logs:</b>\n<code>" + "\n".join(recent_lines[-4:]) + "</code>")
            asyncio.run_coroutine_threadsafe(safe_edit(progress_msg, text, stop_kb), loop)

    def run_thread():
        try:
            res = run_boost(u.id, pool, b["role_id"], zone, nick, features, b["comments"], b["workers"], b["target_success"], True, active_photos, progress_cb, stop_event, stats_holder)
            log_boost(b["role_id"], zone, nick, res["success"], res["fail"], res["banned_removed"], int(res["duration"]), user_id=u.id)
            increment_usage(u.id, success=res["success"])

            final = (f"✅ <b>ULTIMATE BOOST COMPLETED!</b>\n👑 Owner: <b>{OWNER_NAME}</b>\n\n🎯 Target: <b>{nick}</b> (<code>{b['role_id']}</code>)\n"
                     f"✅ Success: <b>{res['success']:,}</b>\n❌ Skipped: <b>{res['fail']:,}</b>\n🚫 Banned Filtered: <b>{res['banned_removed']:,}</b>\n"
                     f"⏱ Duration: <b>{res['duration']:.1f}s</b> | ⚡ Speed: <b>{res['speed']:.1f}/s</b>")
            asyncio.run_coroutine_threadsafe(safe_edit(progress_msg, final, back_kb("main_menu")), loop)
            asyncio.run_coroutine_threadsafe(spy_log(ctx, u, f"🏁 <b>BOOST FINISHED</b>\n🎯 Target: <b>{nick}</b>\n✅ Success: <b>{res['success']:,}</b> | ⏱ {res['duration']:.1f}s"), loop)
        except Exception as e: asyncio.run_coroutine_threadsafe(safe_edit(progress_msg, f"❌ <b>Error:</b>\n<code>{e}</code>", back_kb("main_menu")), loop)
        finally:
            with BOOST_LOCK: ACTIVE_BOOSTS.pop(u.id, None)

    with BOOST_LOCK: ACTIVE_BOOSTS[u.id] = {"target": b["role_id"], "stats": stats_holder, "stop": stop_event}
    threading.Thread(target=run_thread, daemon=True, name=f"boost-{u.id}").start()

async def safe_edit(msg, text, reply_markup=None):
    try: await msg.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except: pass

# ─── ADMIN CALLBACKS (FULLY RESTORED) ───
@safe_user_route
async def cb_key_panel(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer(); ks = get_key_stats()
    text = f"🔑 <b>LICENSE KEY MANAGEMENT PANEL</b>\n👑 Owner: <b>{OWNER_NAME}</b>\n\n🟢 Active Unused Keys: <b>{ks['unused']}</b>\n🔴 Redeemed Keys: <b>{ks['used']}</b>"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 VIP 1D", callback_data="genk_vip_1"), InlineKeyboardButton("💎 VIP 7D", callback_data="genk_vip_7"), InlineKeyboardButton("💎 VIP 30D", callback_data="genk_vip_30"), InlineKeyboardButton("💎 VIP ∞", callback_data="genk_vip_0")],
        [InlineKeyboardButton("👑 Prem 1D", callback_data="genk_premium_1"), InlineKeyboardButton("👑 Prem 7D", callback_data="genk_premium_7"), InlineKeyboardButton("👑 Prem 30D", callback_data="genk_premium_30"), InlineKeyboardButton("👑 Prem ∞", callback_data="genk_premium_0")],
        [InlineKeyboardButton("🔢 Bulk Generate Keys", callback_data="key_bulk_btn"), InlineKeyboardButton("📋 View Unused Keys", callback_data="key_list")],
        [InlineKeyboardButton("🗑 Delete Specific Key", callback_data="key_del_btn"), InlineKeyboardButton("🧹 Clear All Unused", callback_data="key_clear_confirm_ask")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")],
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_gen_key_quick(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer("🔑 Generating key..."); parts = q.data.split("_"); plan, days = parts[1], int(parts[2])
    k = generate_license_key(plan=plan, days=days, created_by=update.effective_user.id)
    await q.edit_message_text(f"✅ <b>NEW KEY GENERATED!</b>\n\n<code>{k}</code>\n📋 Plan: {plan.upper()} | ⏰ Days: {days if days>0 else '∞'}", reply_markup=back_kb("key_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_key_bulk_btn(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer(); ctx.user_data["awaiting_action"] = "bulk_gen_key"
    await q.edit_message_text("🔢 <b>BULK KEY GENERATOR</b>\n\nSend: <code>plan days count</code>\nExamples:\n• <code>vip 30 5</code>\n• <code>premium 0 3</code>", reply_markup=back_kb("key_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_key_list(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer(); rows = get_unused_keys(limit=30)
    text = "📋 <b>No unused keys available.</b>" if not rows else "📋 <b>UNUSED LICENSE KEYS (Latest 30):</b>\n\n" + "\n".join([f"<code>{k}</code> — <b>{p.upper()} ({d if d>0 else '∞'}D)</b>" for k, p, d, _ in rows])
    await q.edit_message_text(text, reply_markup=back_kb("key_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_key_del_btn(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer(); ctx.user_data["awaiting_action"] = "delete_single_key"
    await q.edit_message_text("🗑 <b>DELETE SPECIFIC KEY</b>\n\nPlease send the exact Key Code you want to delete:", reply_markup=back_kb("key_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_key_clear_confirm_ask(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes, Delete All Unused", callback_data="key_clear"), InlineKeyboardButton("❌ Cancel", callback_data="key_panel")]])
    await q.edit_message_text("⚠️ <b>CONFIRM DELETE UNUSED KEYS</b>\n\nAre you sure?", reply_markup=kb, parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_key_clear(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer(); count = clear_unused_keys(); await spy_log(ctx, update.effective_user, f"🧹 <b>Cleared {count} Unused Keys</b>")
    await q.edit_message_text(f"✅ Successfully deleted <b>{count}</b> unused keys.", reply_markup=back_kb("key_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_admin_panel(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer(); maint_status = "🟢 OFF" if get_setting("maintenance") == "false" else "🔴 ON"
    text = f"👑 <b>NEXUS CONTROL & ADMIN PANEL</b>\n👑 Owner: <b>{OWNER_NAME}</b>\n👮‍♂️ Admins: <b>{len(get_admin_list())}</b>\n🗄 Vault: <b>{count_vault_devices():,}</b>\n🛠 Maintenance: <b>{maint_status}</b>"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Give VIP", callback_data="adm_givevip_btn"), InlineKeyboardButton("🚫 Revoke VIP", callback_data="adm_revokevip_btn")],
        [InlineKeyboardButton("💎 VIP List", callback_data="adm_listvip_btn"), InlineKeyboardButton("🔍 Check User", callback_data="adm_userinfo_btn")],
        [InlineKeyboardButton(f"🛠 Toggle Maintenance ({maint_status})", callback_data="adm_toggle_maint")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban_user_btn"), InlineKeyboardButton("✅ Unban User", callback_data="adm_unban_user_btn")],
        [InlineKeyboardButton("👮‍♂️ View Admins", callback_data="admin_list_cb")],
        [InlineKeyboardButton("➕ Add Admin", callback_data="adm_addadmin_btn"), InlineKeyboardButton("➖ Remove Admin", callback_data="adm_deladmin_btn")] if is_owner(update.effective_user.id) else [],
        [InlineKeyboardButton("📥 Add Vault to My Pool", callback_data="adm_sync_vault"), InlineKeyboardButton("📥 Export Vault", callback_data="adm_export_vault")] if is_owner(update.effective_user.id) else [],
        [InlineKeyboardButton("📢 Broadcast to All Users", callback_data="adm_broadcast_btn")] if is_owner(update.effective_user.id) else [],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_givevip_btn(update, ctx):
    q = update.callback_query; await q.answer(); ctx.user_data["awaiting_action"] = "give_vip_uid"
    await q.edit_message_text("🎁 <b>GIVE VIP / PREMIUM</b>\n\nPlease send the <b>Telegram User ID</b> of the user:", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_set_vip_plan_days(update, ctx):
    q = update.callback_query; await q.answer()
    parts = q.data.split("_"); target_uid, plan, days = int(parts[1]), parts[2], int(parts[3])
    set_user_vip(target_uid, "", plan=plan, days=days, added_by=update.effective_user.id)
    days_txt = f"{days} Days" if days > 0 else "Lifetime (∞)"
    await q.edit_message_text(f"✅ <b>USER PLAN UPDATED!</b>\n\n👤 User ID: <code>{target_uid}</code>\n📋 Plan: {plan.upper()}\n⏰ Duration: {days_txt}", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_revokevip_btn(update, ctx):
    q = update.callback_query; await q.answer(); ctx.user_data["awaiting_action"] = "revoke_vip_uid"
    await q.edit_message_text("🚫 <b>REVOKE USER VIP</b>\n\nPlease send the <b>Telegram User ID</b> whose VIP you want to revoke:", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_listvip_btn(update, ctx):
    q = update.callback_query; await q.answer(); rows = list_vip_users(limit=30)
    text = "📋 <b>No active VIP/Premium users found.</b>" if not rows else "💎 <b>ACTIVE VIP & PREMIUM USERS:</b>\n" + "\n".join([f"<code>{uid}</code> (@{uname or 'N/A'}) — <b>{plan.upper()}</b>\n   ⏰ {fmt_expiry(exp)} | 🚀 {total} boosts | 📱 {count_devices(uid)} devs" for uid, uname, plan, exp, limit, total in rows])
    await q.edit_message_text(text, reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_userinfo_btn(update, ctx):
    q = update.callback_query; await q.answer(); ctx.user_data["awaiting_action"] = "userinfo_uid"
    await q.edit_message_text("🔍 <b>CHECK USER INFO</b>\n\nPlease send the <b>Telegram User ID</b> to inspect:", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_dump_user_devs(update, ctx):
    q = update.callback_query; target_uid = int(q.data.split("_")[2]); rows = load_device_ids(target_uid, limit=50000)
    if not rows: return await q.answer("❌ User has 0 devices.", show_alert=True)
    await q.answer("📥 Downloading..."); bio = io.BytesIO("\n".join(rows).encode("utf-8")); bio.name = f"user_{target_uid}_devices.txt"
    await q.message.reply_document(document=bio, caption=f"📥 <b>Device Dump for <code>{target_uid}</code></b>\n📊 Count: <b>{len(rows):,}</b>", parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_export_vault(update, ctx):
    q = update.callback_query; rows = load_vault_device_ids(limit=100000)
    if not rows: return await q.answer("❌ Vault empty!", show_alert=True)
    await q.answer("📥 Exporting..."); bio = io.BytesIO("\n".join(rows).encode("utf-8")); bio.name = f"master_vault_{len(rows)}_devices.txt"
    await q.message.reply_document(document=bio, caption=f"🗄 <b>MASTER VAULT EXPORT</b>\n📊 Total Devices: <b>{len(rows):,}</b>", parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_admin_list(update, ctx):
    q = update.callback_query; await q.answer(); rows = get_admin_list()
    lines = ["👮‍♂️ <b>AUTHORIZED ADMINS LIST</b>\n"] + [f"• <code>{uid}</code> — <b>{'👑 OWNER' if uid == OWNER_ID else '👮‍♂️ ADMIN'}</b>" for uid, _, _ in rows]
    await q.edit_message_text("\n".join(lines), reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_addadmin_btn(update, ctx):
    q = update.callback_query; await q.answer(); ctx.user_data["awaiting_action"] = "add_admin_uid"
    await q.edit_message_text("➕ <b>ADD ADMIN</b>\n\nPlease send the <b>Telegram User ID</b> to promote:", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_deladmin_btn(update, ctx):
    q = update.callback_query; await q.answer(); rows = [r for r in get_admin_list() if r[0] != OWNER_ID]
    if not rows: return await q.edit_message_text("ℹ️ No other admins.", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)
    kb_rows = [[InlineKeyboardButton(f"❌ Remove Admin {uid}", callback_data=f"rmadm_{uid}")] for uid, _, _ in rows] + [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
    await q.edit_message_text("➖ <b>REMOVE ADMIN</b>\n\nTap on an Admin to remove:", reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_rmadm_confirm(update, ctx):
    q = update.callback_query; target_uid = int(q.data.split("_")[1]); remove_admin_db(target_uid); await q.answer("✅ Removed!", show_alert=True)
    await q.edit_message_text(f"✅ User <code>{target_uid}</code> removed from Admins.", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_broadcast_btn(update, ctx):
    q = update.callback_query; await q.answer(); ctx.user_data["awaiting_action"] = "broadcast_msg"
    await q.edit_message_text("📢 <b>BROADCAST</b>\n\nSend the text to broadcast to all users:", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_toggle_maint(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    new_status = "false" if get_setting("maintenance") == "true" else "true"
    set_setting("maintenance", new_status)
    await q.answer(f"Maintenance Mode is now {'ON' if new_status=='true' else 'OFF'}", show_alert=True)
    await cb_admin_panel(update, ctx)

@safe_user_route
async def cb_adm_ban_user_btn(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer(); ctx.user_data["awaiting_action"] = "ban_user_uid"
    await q.edit_message_text("🚫 <b>BAN USER</b>\n\nPlease send the <b>Telegram User ID</b> to BAN:", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_unban_user_btn(update, ctx):
    q = update.callback_query
    if not is_admin(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    await q.answer(); ctx.user_data["awaiting_action"] = "unban_user_uid"
    await q.edit_message_text("✅ <b>UNBAN USER</b>\n\nPlease send the <b>Telegram User ID</b> to UNBAN:", reply_markup=back_kb("admin_panel"), parse_mode=ParseMode.HTML)

@safe_user_route
async def cb_adm_sync_vault(update, ctx):
    q = update.callback_query
    if not is_owner(update.effective_user.id): return await q.answer("⛔ Access Denied!", show_alert=True)
    added = sync_vault_to_owner()
    await q.answer(f"✅ Added {added:,} new devices to your pool!", show_alert=True)

# Main input handler
@safe_user_route
async def handle_button_text_inputs(update, ctx):
    action, text, u = ctx.user_data.get("awaiting_action"), update.message.text.strip(), update.effective_user
    if not action: return
    ctx.user_data["awaiting_action"] = None

    if action == "redeem_key":
        res = redeem_license_key(text, u.id, u.username or "")
        if not res["success"]: await update.message.reply_text(res["error"], reply_markup=back_kb("main_menu"), parse_mode=ParseMode.HTML); return
        await spy_log(ctx, u, f"🎉 <b>Redeemed Key:</b> <code>{res['key']}</code>")
        await update.message.reply_text(f"🎉 <b>KEY REDEEMED!</b>\nPlan: {res['plan'].upper()}", reply_markup=back_kb("main_menu"), parse_mode=ParseMode.HTML)
    
    elif is_admin(u.id):
        if action == "broadcast_msg" and is_owner(u.id):
            sent = 0
            for uid in get_all_user_ids():
                try: await ctx.bot.send_message(chat_id=uid, text=f"📢 <b>ANNOUNCEMENT</b>\n\n{text}", parse_mode=ParseMode.HTML); sent += 1
                except: pass
            await update.message.reply_text(f"Broadcast sent to {sent} users.", reply_markup=back_kb("admin_panel"))
        elif action == "ban_user_uid":
            if text.isdigit(): ban_user(int(text)); await update.message.reply_text(f"✅ User {text} BANNED.", reply_markup=back_kb("admin_panel"))
        elif action == "unban_user_uid":
            if text.isdigit(): unban_user(int(text)); await update.message.reply_text(f"✅ User {text} UNBANNED.", reply_markup=back_kb("admin_panel"))
        elif action == "give_vip_uid":
            if text.isdigit():
                t_uid = int(text)
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 VIP 1D", callback_data=f"setvip_{t_uid}_vip_1"), InlineKeyboardButton("💎 VIP 7D", callback_data=f"setvip_{t_uid}_vip_7"), InlineKeyboardButton("💎 VIP 30D", callback_data=f"setvip_{t_uid}_vip_30"), InlineKeyboardButton("💎 VIP ∞", callback_data=f"setvip_{t_uid}_vip_0")],
                    [InlineKeyboardButton("👑 Prem 1D", callback_data=f"setvip_{t_uid}_premium_1"), InlineKeyboardButton("👑 Prem 7D", callback_data=f"setvip_{t_uid}_premium_7"), InlineKeyboardButton("👑 Prem 30D", callback_data=f"setvip_{t_uid}_premium_30"), InlineKeyboardButton("👑 Prem ∞", callback_data=f"setvip_{t_uid}_premium_0")],
                    [InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")]
                ])
                await update.message.reply_text(f"🎁 <b>SELECT PLAN FOR:</b> <code>{t_uid}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)
        elif action == "revoke_vip_uid":
            if text.isdigit(): set_user_vip(int(text), "", plan="free", days=0, added_by=u.id); await update.message.reply_text(f"✅ Revoked VIP for {text}.", reply_markup=back_kb("admin_panel"))
        elif action == "userinfo_uid":
            if text.isdigit():
                t_uid = int(text); status = can_user_boost(t_uid); u_devs = count_devices(t_uid)
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"📥 Download User's {u_devs:,} Devices", callback_data=f"dump_dev_{t_uid}")], [InlineKeyboardButton("💎 Give VIP 30D", callback_data=f"setvip_{t_uid}_vip_30"), InlineKeyboardButton("🚫 Revoke VIP", callback_data=f"setvip_{t_uid}_free_0")], [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]])
                await update.message.reply_text(f"👤 <b>USER INFO:</b> <code>{t_uid}</code>\nPlan: {status['plan'].upper()}\nUsed: {status['used']}/{status['limit']}\nDevices: {u_devs:,}", reply_markup=kb, parse_mode=ParseMode.HTML)
        elif action == "add_admin_uid" and is_owner(u.id):
            if text.isdigit(): add_admin_db(int(text), added_by=u.id); await update.message.reply_text(f"✅ User {text} is now Admin.", reply_markup=back_kb("admin_panel"))
        elif action == "bulk_gen_key":
            parts = text.split()
            if len(parts) >= 2 and parts[0].lower() in ("vip", "premium"):
                plan, days = parts[0].lower(), int(parts[1])
                count = int(parts[2]) if len(parts) >= 3 else 1
                keys = [generate_license_key(plan=plan, days=days, created_by=u.id) for _ in range(count)]
                await update.message.reply_text(f"✅ <b>GENERATED {count} KEYS ({plan.upper()})</b>\n\n" + "\n".join([f"<code>{k}</code>" for k in keys]), reply_markup=back_kb("key_panel"), parse_mode=ParseMode.HTML)
        elif action == "delete_single_key":
            ok = delete_license_key(text)
            await update.message.reply_text(f"✅ Key {text.upper()} deleted." if ok else "❌ Key not found.", reply_markup=back_kb("key_panel"))

async def post_init(app): await app.bot.set_my_commands([BotCommand("start", "🏠 Open Main Menu")])

def main():
    init_db()
    log.info(f"Starting {BRAND_NAME} ULTIMATE ENGINE — Owner: {OWNER_NAME}")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_menu_boost, pattern="^menu_boost$")],
        states={
            ASK_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_role)],
            ASK_ZONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_zone)],
            ASK_FEATURES: [CallbackQueryHandler(cb_feature_select, pattern="^feat_(1|2|3|4|5|6|all)$"), CallbackQueryHandler(cb_feat_done, pattern="^feat_done$")],
            ASK_COMMENTS: [CallbackQueryHandler(cb_skip_comments_btn, pattern="^skip_comments_btn$"), MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_comments)],
            ASK_WORKERS: [CallbackQueryHandler(cb_set_workers_btn, pattern="^setwork_"), MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_workers)],
            ASK_TARGET: [CallbackQueryHandler(cb_set_target_btn, pattern="^set4targ_"), MessageHandler(filters.TEXT & ~filters.COMMAND, boost_got_target)]
        },
        fallbacks=[CommandHandler("start", cmd_start), CallbackQueryHandler(cb_main_menu, pattern="^main_menu$")], allow_reentry=True
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(cb_close, pattern="^menu_close$"))
    app.add_handler(CallbackQueryHandler(cb_myvip, pattern="^myvip$"))
    app.add_handler(CallbackQueryHandler(cb_menu_redeem, pattern="^menu_redeem$"))
    app.add_handler(CallbackQueryHandler(cb_stop_boost_btn, pattern="^menu_stop_boost$"))
    app.add_handler(CallbackQueryHandler(cb_live_dashboard, pattern="^live_dashboard$"))
    app.add_handler(CallbackQueryHandler(cb_boost_launch, pattern="^boost_launch$"))
    app.add_handler(CallbackQueryHandler(cb_menu_devices, pattern="^menu_devices$"))
    app.add_handler(CallbackQueryHandler(cb_dev_upload, pattern="^dev_upload$"))
    app.add_handler(CallbackQueryHandler(cb_dev_sample, pattern="^dev_sample$"))
    app.add_handler(CallbackQueryHandler(cb_dev_export_mine, pattern="^dev_export_mine$"))
    app.add_handler(CallbackQueryHandler(cb_dev_clear, pattern="^dev_clear$"))
    app.add_handler(CallbackQueryHandler(cb_dev_clear_confirm, pattern="^dev_clear_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_menu_history, pattern="^menu_history$"))
    app.add_handler(CallbackQueryHandler(cb_menu_stats, pattern="^menu_stats$"))
    
    # Admin & Key Handlers
    app.add_handler(CallbackQueryHandler(cb_key_panel, pattern="^key_panel$"))
    app.add_handler(CallbackQueryHandler(cb_gen_key_quick, pattern="^genk_"))
    app.add_handler(CallbackQueryHandler(cb_key_bulk_btn, pattern="^key_bulk_btn$"))
    app.add_handler(CallbackQueryHandler(cb_key_list, pattern="^key_list$"))
    app.add_handler(CallbackQueryHandler(cb_key_del_btn, pattern="^key_del_btn$"))
    app.add_handler(CallbackQueryHandler(cb_key_clear_confirm_ask, pattern="^key_clear_confirm_ask$"))
    app.add_handler(CallbackQueryHandler(cb_key_clear, pattern="^key_clear$"))

    app.add_handler(CallbackQueryHandler(cb_admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(cb_adm_givevip_btn, pattern="^adm_givevip_btn$"))
    app.add_handler(CallbackQueryHandler(cb_set_vip_plan_days, pattern="^setvip_"))
    app.add_handler(CallbackQueryHandler(cb_adm_revokevip_btn, pattern="^adm_revokevip_btn$"))
    app.add_handler(CallbackQueryHandler(cb_adm_listvip_btn, pattern="^adm_listvip_btn$"))
    app.add_handler(CallbackQueryHandler(cb_adm_userinfo_btn, pattern="^adm_userinfo_btn$"))
    app.add_handler(CallbackQueryHandler(cb_dump_user_devs, pattern="^dump_dev_"))
    app.add_handler(CallbackQueryHandler(cb_adm_export_vault, pattern="^adm_export_vault$"))
    app.add_handler(CallbackQueryHandler(cb_admin_list, pattern="^admin_list_cb$"))
    app.add_handler(CallbackQueryHandler(cb_adm_addadmin_btn, pattern="^adm_addadmin_btn$"))
    app.add_handler(CallbackQueryHandler(cb_adm_deladmin_btn, pattern="^adm_deladmin_btn$"))
    app.add_handler(CallbackQueryHandler(cb_rmadm_confirm, pattern="^rmadm_"))
    app.add_handler(CallbackQueryHandler(cb_adm_broadcast_btn, pattern="^adm_broadcast_btn$"))
    
    app.add_handler(CallbackQueryHandler(cb_adm_toggle_maint, pattern="^adm_toggle_maint$"))
    app.add_handler(CallbackQueryHandler(cb_adm_ban_user_btn, pattern="^adm_ban_user_btn$"))
    app.add_handler(CallbackQueryHandler(cb_adm_unban_user_btn, pattern="^adm_unban_user_btn$"))
    app.add_handler(CallbackQueryHandler(cb_adm_sync_vault, pattern="^adm_sync_vault$"))
    
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_text_inputs))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: log.info("Bot stopped.")
