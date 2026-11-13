# /// script
# requires-python = ">=3.12"
# # zxing-cpp 3.x has only stable-ABI (abi3) wheels for Python 3.12; they need python3.dll, which
# # OrcaSlicer 2.5.0 for Windows does not ship, so Windows x64 stays on the cp312 wheels of 2.3.
# dependencies = [
#     "qrcode>=7.4,<9",
#     "Pillow>=10,<13",
#     "zxing-cpp>=2.3,<3; sys_platform == 'win32' and platform_machine == 'AMD64'",
#     "zxing-cpp>=2.3,<4; sys_platform != 'win32' or platform_machine != 'AMD64'",
# ]
#
# [tool.orcaslicer.plugin]
# name = "Filament QR"
# description = "QR-этикетки для катушек и выбор филамента сканированием с телефона"
# author = "OrcaSlicer-AI"
# version = "1.3.1"
# ///
"""Filament QR: per-spool QR labels and phone scanning for OrcaSlicer.

Each spool gets a short code (FQ-XXXXXX) bound to an OrcaSlicer filament
profile. Scanning a label with a phone on the local network queues a request
"put this profile into filament slot N". The queue lives in SQLite and survives
restarts. On OrcaSlicer 2.5.0+ the plugin asks for a native confirmation and
selects the profile through the preset API; on older hosts it only tells the
user which profile to pick by hand.

Threading: the LAN server threads only touch the store and the in-memory
session table. Preset reads happen in Pages callbacks on the GUI thread; the
selection itself (begin_update/commit, which must not run on the GUI thread)
runs on one plugin-owned worker at a time.
"""
import hashlib
import importlib
import io
import json
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import tempfile
import threading
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    import orca  # the embedded OrcaSlicer module; an unrelated `orca` package may exist elsewhere
    if not hasattr(orca, 'host'):
        orca = None
except ImportError:
    orca = None

DEFAULT_PORT = 8765
MAX_JSON_BODY = 16 * 1024
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_SIDE = 2600
MAX_IMAGE_PIXELS = MAX_IMAGE_SIDE * MAX_IMAGE_SIDE
MAX_DECLARED_JPEG_PIXELS = 64_000_000
MAX_HTTP_WORKERS = 8
MAX_CONCURRENT_DECODES = 2
REQUEST_TIMEOUT = 10.0
CODE_RE = re.compile(r'^FQ-[0-9A-F]{6}$')
COLOUR_RE = re.compile(r'^#[0-9A-F]{6}$')

ACTIVE_STATUSES = ('queued', 'manual', 'needs_attention', 'cancelled')
SELECT_JOIN_TIMEOUT = 3.0
PERMISSION_TEXT = 'OrcaSlicer заблокировал сетевой доступ плагина: разрешите его в окне «Плагины»'


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def normalize_colour(text):
    """'' or an upper-case #RRGGBB; anything else is rejected."""
    colour = str(text or '').strip().upper()
    if colour and not COLOUR_RE.match(colour):
        raise ValueError('Цвет должен быть в формате #RRGGBB')
    return colour


def normalize_code(text):
    text = str(text).strip().upper()
    if text.startswith('FQ:'):
        text = text[3:]
    if re.fullmatch(r'[0-9A-F]{6}', text):
        text = 'FQ-' + text
    if not CODE_RE.fullmatch(text):
        raise ValueError('Ожидался код вида FQ-XXXXXX (шесть шестнадцатеричных символов)')
    return text


# --------------------------------------------------------------------------- labels

def qr_svg(spool):
    """Self-contained printable 56 x 72 mm label. The symbol uses ECC H."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H

    code = normalize_code(spool['code'])
    material = re.sub(r'[^\w+.-]', '', str(spool.get('material', '')).upper())[:10] or 'FILAMENT'
    brand = str(spool.get('brand', ''))
    title = str(spool.get('profile', ''))
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=4, box_size=1)
    qr.add_data('FQ:' + code)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    x0, y0, size = 5.5, 14.0, 45.0
    step = size / len(matrix)
    rects = ''.join(
        f'<rect x="{x0 + ix * step:.4f}" y="{y0 + iy * step:.4f}" width="{step + .008:.4f}" height="{step + .008:.4f}"/>'
        for iy, row in enumerate(matrix) for ix, dark in enumerate(row) if dark)
    cy = y0 + size / 2
    colour = normalize_colour(spool.get('color', ''))
    swatch = (f'<g class="swatch"><circle cx="47.5" cy="6.2" r="3.3" fill="{colour}" stroke="#111" '
              f'stroke-width=".35"/><text x="47.5" y="12" text-anchor="middle" font-family="Arial,sans-serif" '
              f'font-size="1.9" fill="#333">{colour}</text></g>') if colour else ''
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="56mm" height="72mm" viewBox="0 0 56 72">'
        '<rect width="56" height="72" fill="white"/>'
        '<rect x="0.5" y="0.5" width="55" height="71" rx="2" fill="none" stroke="#303644" stroke-width=".35"/>'
        '<text x="28" y="5.5" text-anchor="middle" font-family="Arial,sans-serif" font-size="3.5" '
        'font-weight="bold" fill="#111">FILAMENT QR</text>'
        f'{swatch}'
        f'<text x="28" y="10" text-anchor="middle" font-family="Arial,sans-serif" font-size="3.2" '
        f'fill="#333">{escape((brand or title)[:26])}</text>'
        f'<g fill="#111">{rects}</g>'
        f'<rect x="{28 - 6.2:.2f}" y="{cy - 4.6:.2f}" width="12.4" height="9.2" rx=".9" fill="white" '
        'stroke="#111" stroke-width=".35"/>'
        f'<text x="28" y="{cy - 0.9:.2f}" text-anchor="middle" font-family="Arial,sans-serif" font-size="2.9" '
        f'font-weight="bold" fill="#111">{escape(material)}</text>'
        f'<text x="28" y="{cy + 2.5:.2f}" text-anchor="middle" font-family="Arial,sans-serif" font-size="2.25" '
        f'font-weight="bold" fill="#111">{escape(code[3:])}</text>'
        '<text x="28" y="62.8" text-anchor="middle" font-family="Arial,sans-serif" font-size="4.1" '
        f'font-weight="bold" fill="#111">{escape(material)} · {escape(code[3:])}</text>'
        f'<text x="28" y="67.2" text-anchor="middle" font-family="Arial,sans-serif" font-size="2.15" '
        f'fill="#333">{escape(title[:34])}</text>'
        '</svg>')


def url_qr_svg(url):
    """Square QR code of a server address for the phone camera."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=2, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    rects = ''.join(f'<rect x="{ix}" y="{iy}" width="1.02" height="1.02"/>'
                    for iy, row in enumerate(matrix) for ix, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 {size} {size}" '
            f'shape-rendering="crispEdges"><rect width="{size}" height="{size}" fill="white"/>'
            f'<g fill="#111">{rects}</g></svg>')


def label_page(spool):
    return (f'<!doctype html><meta charset="utf-8"><title>Этикетка {escape(spool["code"])}</title><style>'
            'body{font:16px Arial;text-align:center;margin:24px}svg{max-width:96vw;height:auto}'
            '@page{margin:0;size:56mm 72mm}@media print{body{margin:0}button{display:none}}'
            '</style><button onclick="print()">Печать 56 × 72 мм</button>'
            f'<div>{qr_svg(spool)}</div>')


# --------------------------------------------------------------------------- store

SCHEMA_VERSION = 2


class Store:
    """SQLite store for spools and the durable scan queue. Safe to share between threads."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.migrate()

    @contextmanager
    def _db(self):
        with self._lock:
            db = sqlite3.connect(str(self.path), timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def schema_version(self):
        with self._db() as db:
            return db.execute('PRAGMA user_version').fetchone()[0]

    def migrate(self):
        with self._db() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError('База катушек создана более новой версией плагина')
            if version < 1:
                # Version 0 is either an empty file or the prototype, which had the same spools table.
                db.execute('''CREATE TABLE IF NOT EXISTS spools (
                    code TEXT PRIMARY KEY, profile TEXT NOT NULL, printer TEXT NOT NULL,
                    material TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)''')
                db.execute('''CREATE TABLE IF NOT EXISTS pending_scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL, profile TEXT NOT NULL, slot INTEGER NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '', host_revision TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL, updated TEXT NOT NULL)''')
                db.execute('CREATE INDEX IF NOT EXISTS pending_status ON pending_scans(status, id)')
                db.execute('PRAGMA user_version = 1')
            if version < 2:
                db.execute("ALTER TABLE spools ADD COLUMN color TEXT NOT NULL DEFAULT ''")
                db.execute("ALTER TABLE pending_scans ADD COLUMN color TEXT NOT NULL DEFAULT ''")
                db.execute('PRAGMA user_version = 2')

    # spools

    def list_spools(self):
        with self._db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM spools ORDER BY created DESC, code DESC')]

    def get_spool(self, code):
        code = normalize_code(code)
        with self._db() as db:
            row = db.execute('SELECT * FROM spools WHERE code=?', (code,)).fetchone()
            return dict(row) if row else None

    def add_spool(self, profile, printer, material, brand='', notes='', token=None, color=''):
        profile, printer = str(profile).strip(), str(printer).strip()
        color = normalize_colour(color)
        material, brand, notes = str(material).strip() or 'FILAMENT', str(brand).strip(), str(notes).strip()
        if not profile:
            raise ValueError('Выберите профиль филамента')
        if len(profile) > 180 or len(printer) > 180 or len(material) > 40 or len(brand) > 80 or len(notes) > 300:
            raise ValueError('Слишком длинное значение')
        token = token or (lambda: secrets.token_hex(3).upper())
        with self._db() as db:
            for _ in range(20):
                code = 'FQ-' + token()
                try:
                    db.execute('INSERT INTO spools (code, profile, printer, material, brand, notes, created, color) '
                               'VALUES (?,?,?,?,?,?,?,?)',
                               (code, profile, printer, material, brand, notes, utc_now(), color))
                except sqlite3.IntegrityError:
                    continue
                return dict(db.execute('SELECT * FROM spools WHERE code=?', (code,)).fetchone())
        raise RuntimeError('Не удалось создать уникальный код')

    def delete_spool(self, code):
        code = normalize_code(code)
        with self._db() as db:
            return db.execute('DELETE FROM spools WHERE code=?', (code,)).rowcount > 0

    # scan queue

    def enqueue_scan(self, code, slot, status='queued'):
        spool = self.get_spool(code)
        if spool is None:
            raise LookupError('Катушка с этим кодом не найдена')
        now = utc_now()
        with self._db() as db:
            cur = db.execute('INSERT INTO pending_scans (code, profile, slot, status, created, updated, color) '
                             'VALUES (?,?,?,?,?,?,?)',
                             (spool['code'], spool['profile'], int(slot), status, now, now, spool['color']))
            return dict(db.execute('SELECT * FROM pending_scans WHERE id=?', (cur.lastrowid,)).fetchone())

    def get_pending(self, pending_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM pending_scans WHERE id=?', (int(pending_id),)).fetchone()
            return dict(row) if row else None

    def list_pending(self, statuses=ACTIVE_STATUSES):
        marks = ','.join('?' * len(statuses))
        with self._db() as db:
            return [dict(r) for r in db.execute(
                f'SELECT * FROM pending_scans WHERE status IN ({marks}) ORDER BY id', tuple(statuses))]

    def next_queued(self):
        with self._db() as db:
            row = db.execute("SELECT * FROM pending_scans WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            return dict(row) if row else None

    def mark(self, pending_id, status, message='', attempted=False, host_revision=None):
        with self._db() as db:
            db.execute('UPDATE pending_scans SET status=?, message=?, updated=?, attempts=attempts+?, '
                       'host_revision=COALESCE(?, host_revision) WHERE id=?',
                       (status, str(message)[:300], utc_now(), 1 if attempted else 0, host_revision,
                        int(pending_id)))

    def recent_done(self, limit=5):
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM pending_scans WHERE status='done' ORDER BY id DESC LIMIT ?", (limit,))]


# --------------------------------------------------------------------------- authentication

class AuthError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class SessionManager:
    """PIN login for phones. Tokens are kept only as SHA-256 digests."""

    FAILURE_WINDOW = 60.0
    MAX_FAILURES = 6
    MAX_TRACKED_IPS = 256
    MAX_SESSIONS = 64
    IDLE_TIMEOUT = 8 * 3600.0
    ABSOLUTE_TIMEOUT = 24 * 3600.0

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions = {}   # digest -> (created, last_seen)
        self._failures = {}   # ip -> [timestamps]
        self.pin = self._new_pin()

    @staticmethod
    def _new_pin():
        return f'{secrets.randbelow(1_000_000):06d}'

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def authenticate(self, pin, client_ip):
        now = self._clock()
        with self._lock:
            attempts = [t for t in self._failures.get(client_ip, ()) if now - t < self.FAILURE_WINDOW]
            if len(attempts) >= self.MAX_FAILURES:
                self._failures[client_ip] = attempts
                raise AuthError(429, 'Слишком много попыток. Повторите через минуту')
            if not secrets.compare_digest(str(pin).encode('utf-8'), self.pin.encode('utf-8')):
                attempts.append(now)
                self._failures[client_ip] = attempts
                self._prune_failures(now)
                raise AuthError(403, 'Неверный PIN')
            self._failures.pop(client_ip, None)
            self._prune_sessions(now)
            if len(self._sessions) >= self.MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda d: self._sessions[d][1])
                del self._sessions[oldest]
            token = secrets.token_urlsafe(32)
            self._sessions[self._digest(token)] = (now, now)
            return token

    def validate(self, token):
        if not token:
            return False
        now = self._clock()
        digest = self._digest(token)
        with self._lock:
            entry = self._sessions.get(digest)
            if entry is None:
                return False
            created, last_seen = entry
            if now - created > self.ABSOLUTE_TIMEOUT or now - last_seen > self.IDLE_TIMEOUT:
                del self._sessions[digest]
                return False
            self._sessions[digest] = (created, now)
            return True

    def revoke(self, token):
        with self._lock:
            self._sessions.pop(self._digest(token or ''), None)

    def revoke_all(self):
        with self._lock:
            self._sessions.clear()

    def rotate_pin(self):
        with self._lock:
            self.pin = self._new_pin()
            self._sessions.clear()
            self._failures.clear()
            return self.pin

    def session_count(self):
        now = self._clock()
        with self._lock:
            self._prune_sessions(now)
            return len(self._sessions)

    def _prune_sessions(self, now):
        for digest, (created, last_seen) in list(self._sessions.items()):
            if now - created > self.ABSOLUTE_TIMEOUT or now - last_seen > self.IDLE_TIMEOUT:
                del self._sessions[digest]

    def _prune_failures(self, now):
        for ip, stamps in list(self._failures.items()):
            if not any(now - t < self.FAILURE_WINDOW for t in stamps):
                del self._failures[ip]
        while len(self._failures) > self.MAX_TRACKED_IPS:
            self._failures.pop(next(iter(self._failures)))


# --------------------------------------------------------------------------- QR decoding

class DecodeError(ValueError):
    pass


def decoder_error():
    """'' when Pillow and zxing-cpp import, otherwise the real import failure for the user."""
    try:
        for module in ('PIL.Image', 'PIL.ImageOps', 'zxingcpp'):
            importlib.import_module(module)
    except Exception as exc:
        return f'{type(exc).__name__}: {exc}'[:300]
    return ''


def decode_qr_image(data):
    """Return the normalized FQ codes found in an image, rejecting oversized input before decoding."""
    if len(data) > MAX_IMAGE_BYTES:
        raise DecodeError('Изображение слишком большое')
    reason = decoder_error()
    if reason:
        raise DecodeError(f'Распознавание фото недоступно ({reason}). Введите код с этикетки вручную')
    from PIL import Image, ImageOps
    import zxingcpp

    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                # JPEG can be decoded at 1/2..1/8 scale, so large phone photos stay cheap; other formats cannot.
                limit = MAX_DECLARED_JPEG_PIXELS if img.format == 'JPEG' else MAX_IMAGE_PIXELS * 4
                if width * height > limit:
                    raise DecodeError('Разрешение изображения слишком большое')
                img.draft('RGB', (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                img = ImageOps.exif_transpose(img)
                results = zxingcpp.read_barcodes(img)
        except DecodeError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise DecodeError('Разрешение изображения слишком большое') from exc
        except Exception as exc:
            raise DecodeError('Не удалось прочитать изображение') from exc

    codes = []
    for result in results:
        try:
            code = normalize_code(result.text)
        except ValueError:
            continue
        if code not in codes:
            codes.append(code)
    if not codes:
        raise DecodeError('QR Filament на фото не найден. Подойдите ближе или введите код вручную')
    return codes


# --------------------------------------------------------------------------- OrcaSlicer host

@dataclass
class HostCapabilities:
    mode: str                      # 'automatic' | 'manual'
    storage: bool
    colour: bool = False           # the host can set a slot's colour (preset.filament.set_slot_colour)

    @classmethod
    def discover(cls, orca_module):
        host = getattr(orca_module, 'host', None)
        editor = getattr(getattr(host, 'preset', None), 'filament', None)
        automatic = callable(getattr(host, 'preset_bundle', None)) and callable(getattr(editor, 'begin_update', None))
        storage = callable(getattr(getattr(host, 'plugin', None), 'storage', None))
        colour = automatic and callable(getattr(editor, 'set_slot_colour', None))
        return cls('automatic' if automatic else 'manual', storage, colour)


@dataclass
class WorkspaceState:
    printer: str = ''
    slots: list = field(default_factory=list)      # preset name per zero-based slot
    profiles: list = field(default_factory=list)   # compatible, visible filament presets
    dirty: bool = False
    colours: list = field(default_factory=list)    # #RRGGBB per zero-based slot, [] when unknown


def slot_colours(bundle):
    read = getattr(bundle, 'full_config_value', None)
    raw = read('filament_colour') if callable(read) else None
    colours = []
    for item in str(raw or '').split(';') if raw else []:
        try:
            colours.append(normalize_colour(item.strip().strip('"')))
        except ValueError:
            colours.append('')
    return colours


def read_workspace(host, caps):
    """Read printer, filament slots and compatible profiles. GUI thread only."""
    bundle = host.preset_bundle()
    filaments = bundle.filaments
    profiles = []
    for i in range(filaments.size()):
        preset = filaments.preset(i)
        if preset.is_visible and preset.is_compatible:
            profiles.append(preset.name)
    edited = filaments.edited_preset()
    return WorkspaceState(printer=str(bundle.printers.selected_preset_name()),
                          slots=list(bundle.current_filament_preset_names()),
                          profiles=profiles,
                          dirty=bool(edited.is_dirty) if edited else False,
                          colours=slot_colours(bundle))


CANCELLED_TEXT = 'Отменено в OrcaSlicer — можно повторить'


def prepare_pending(host, store, pending, caps):
    """Check a queued scan on the GUI thread. Returns 'select' when OrcaSlicer must select the profile
    (the scan stays queued), otherwise the status it was marked with."""
    pid, slot, profile = pending['id'], int(pending['slot']), pending['profile']
    try:
        ws = read_workspace(host, caps)
    except Exception as exc:
        store.mark(pid, 'needs_attention', f'Не удалось прочитать состояние OrcaSlicer: {exc}')
        return 'needs_attention'

    if slot >= len(ws.slots):
        store.mark(pid, 'needs_attention', f'У принтера нет слота {slot + 1}')
        return 'needs_attention'
    colour = pending.get('color', '')
    current_colour = ws.colours[slot] if slot < len(ws.colours) else ''
    if ws.slots[slot] == profile and not (caps.colour and colour and colour != current_colour):
        store.mark(pid, 'done', 'Профиль уже выбран')
        return 'done'
    if profile not in ws.profiles:
        store.mark(pid, 'needs_attention', 'Профиль отсутствует или не совместим с текущим принтером')
        return 'needs_attention'
    if caps.mode != 'automatic':
        store.mark(pid, 'manual', f'Выберите «{profile}» в слоте {slot + 1} вручную')
        return 'manual'
    return 'select'


def select_in_slot(host, slot, profile, current, colour=''):
    """Confirm and select profile (and, when the host supports it, colour) into zero-based slot that now
    holds current. Must not run on the GUI thread. Returns (status, message) for the store."""
    editor = host.preset.filament
    set_colour = getattr(editor, 'set_slot_colour', None) if colour else None
    question = f'Выбрать профиль филамента?\n\nСлот {slot + 1}: «{current}» → «{profile}»'
    if colour:
        question += f'\nЦвет: {colour}' + ('' if callable(set_colour) else ' (задайте в слоте вручную)')
    question += '\n\nЗапрос пришёл со сканера Filament QR.'
    if host.ui.message(question, title='Filament QR', buttons='yes_no', icon='question') != 'yes':
        return 'cancelled', CANCELLED_TEXT
    try:
        transaction = editor.begin_update(slot + 1, profile)
    except RuntimeError:
        return 'cancelled', CANCELLED_TEXT
    except ValueError as exc:
        return 'needs_attention', f'OrcaSlicer не может выбрать профиль: {exc}'
    except PermissionError:
        return 'needs_attention', PERMISSION_TEXT
    try:
        transaction.commit()
    except (RuntimeError, ValueError):
        return 'needs_attention', 'Состояние OrcaSlicer изменилось во время выбора — повторите'
    if transaction.target_name != profile:
        return 'needs_attention', 'OrcaSlicer выбрал другой профиль'
    if not colour:
        return 'done', 'Профиль выбран'
    if not callable(set_colour):
        return 'done', f'Профиль выбран. Цвет {colour} задайте в слоте вручную'
    try:
        set_colour(slot + 1, colour)
    except Exception as exc:
        return 'needs_attention', f'Профиль выбран, но цвет {colour} не установлен: {exc}'
    return 'done', f'Профиль и цвет {colour} выбраны'


# --------------------------------------------------------------------------- shared service state

class Service:
    """State shared by the LAN server and the GUI. Never calls orca."""

    def __init__(self, store, sessions=None):
        self.store = store
        self.sessions = sessions or SessionManager()
        self.decode_slots = threading.BoundedSemaphore(MAX_CONCURRENT_DECODES)
        self._lock = threading.Lock()
        self._workspace = WorkspaceState()
        self.queue_changed = threading.Event()

    def set_workspace(self, ws):
        with self._lock:
            self._workspace = ws

    def workspace(self):
        with self._lock:
            return self._workspace

    def scan(self, code, slot=0):
        code = normalize_code(code)
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            raise ValueError('Некорректный номер слота') from None
        slots = len(self.workspace().slots)
        if slot < 0 or (slots and slot >= slots):
            raise ValueError('Такого слота филамента нет')
        pending = self.store.enqueue_scan(code, slot)
        self.queue_changed.set()
        return pending

    def phone_status(self):
        ws = self.workspace()
        return {'printer': ws.printer, 'slots': ws.slots,
                'pending': self.store.list_pending(), 'recent': self.store.recent_done(),
                'spools': self.store.list_spools()}


# --------------------------------------------------------------------------- LAN server

class _BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def server_bind(self):
        # HTTPServer.server_bind() resolves the host name (getfqdn), which OrcaSlicer audits as a
        # permission request for a target the plugin cannot declare; the name is never used.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def __init__(self, address, handler, service):
        self.service = service
        self.workers = threading.BoundedSemaphore(MAX_HTTP_WORKERS)
        self.active = 0
        self.active_cv = threading.Condition()
        super().__init__(address, handler)

    def handle_error(self, request, client_address):
        pass  # clients dropping connections are routine; nothing useful to report

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        with self.active_cv:
            self.active += 1
        try:
            super().process_request(request, client_address)
        except Exception:
            self._release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release()

    def _release(self):
        self.workers.release()
        with self.active_cv:
            self.active -= 1
            self.active_cv.notify_all()

    def wait_idle(self, timeout):
        deadline = time.monotonic() + timeout
        with self.active_cv:
            while self.active > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.active_cv.wait(left)
        return True


SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                               "img-src data:; connect-src 'self'; form-action 'none'; frame-ancestors 'none'",
}


class _Handler(BaseHTTPRequestHandler):
    server_version = 'FilamentQR/1.0'
    timeout = REQUEST_TIMEOUT

    def log_message(self, fmt, *args):
        pass

    @property
    def service(self):
        return self.server.service

    def respond(self, status, content, mime='application/json; charset=utf-8', headers=None):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode('utf-8')
        elif isinstance(content, str):
            content = content.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def token(self):
        for part in self.headers.get('Cookie', '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == 'fq_sid':
                return value
        return ''

    def authorized(self):
        return self.service.sessions.validate(self.token())

    def is_loopback(self):
        return self.client_address[0] in ('127.0.0.1', '::1', '::ffff:127.0.0.1')

    def read_body(self, limit):
        raw = self.headers.get('Content-Length')
        try:
            length = int(raw)
        except (TypeError, ValueError):
            raise ValueError('Не указан размер данных') from None
        if length <= 0 or length > limit:
            raise ValueError('Некорректный размер данных')
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError('Данные получены не полностью')
        return body

    def read_json(self):
        try:
            data = json.loads(self.read_body(MAX_JSON_BODY))
        except json.JSONDecodeError:
            raise ValueError('Некорректный JSON') from None
        if not isinstance(data, dict):
            raise ValueError('Некорректный JSON')
        return data

    def do_GET(self):
        try:
            path = unquote(urlparse(self.path).path)
            if path == '/':
                return self.respond(200, PHONE_HTML, 'text/html; charset=utf-8')
            if path == '/api/status':
                if not self.authorized():
                    return self.respond(401, {'error': 'Введите PIN'})
                return self.respond(200, self.service.phone_status())
            if path.startswith('/label/'):
                if not (self.authorized() or self.is_loopback()):
                    return self.respond(401, 'Требуется вход', 'text/plain; charset=utf-8')
                spool = self.service.store.get_spool(path[len('/label/'):])
                if not spool:
                    return self.respond(404, 'Катушка не найдена', 'text/plain; charset=utf-8')
                return self.respond(200, label_page(spool), 'text/html; charset=utf-8')
            return self.respond(404, {'error': 'Не найдено'})
        except ValueError as exc:
            return self.respond(400, {'error': str(exc)})
        except Exception:
            return self.respond(500, {'error': 'Внутренняя ошибка'})

    def do_POST(self):
        try:
            url = urlparse(self.path)
            if url.path == '/api/auth':
                body = self.read_json()
                try:
                    token = self.service.sessions.authenticate(body.get('pin', ''), self.client_address[0])
                except AuthError as exc:
                    return self.respond(exc.status, {'error': str(exc)})
                cookie = (f'fq_sid={token}; HttpOnly; SameSite=Strict; Path=/; '
                          f'Max-Age={int(SessionManager.ABSOLUTE_TIMEOUT)}')
                return self.respond(200, {'ok': True}, headers={'Set-Cookie': cookie})
            if not self.authorized():
                return self.respond(401, {'error': 'Введите PIN'})
            if url.path == '/api/logout':
                self.service.sessions.revoke(self.token())
                return self.respond(200, {'ok': True}, headers={'Set-Cookie': 'fq_sid=; Max-Age=0; Path=/'})
            if url.path == '/api/scan':
                body = self.read_json()
                return self.respond(200, {'pending': self.service.scan(body.get('code', ''), body.get('slot', 0))})
            if url.path == '/api/decode':
                slot = parse_qs(url.query).get('slot', ['0'])[0]
                if not self.service.decode_slots.acquire(blocking=False):
                    return self.respond(429, {'error': 'Распознавание занято, повторите через секунду'})
                try:
                    codes = decode_qr_image(self.read_body(MAX_IMAGE_BYTES))
                finally:
                    self.service.decode_slots.release()
                for code in codes:
                    try:
                        return self.respond(200, {'pending': self.service.scan(code, slot)})
                    except LookupError:
                        continue
                return self.respond(404, {'error': 'QR распознан, но такой катушки нет в базе'})
            return self.respond(404, {'error': 'Не найдено'})
        except LookupError as exc:
            return self.respond(404, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(400, {'error': str(exc)})
        except Exception:
            return self.respond(500, {'error': 'Внутренняя ошибка'})


class FilamentHttpServer:
    def __init__(self, service):
        self.service = service
        self._server = None
        self._thread = None

    @property
    def address(self):
        return self._server.server_address[:2] if self._server else None

    def start(self, bind, port):
        self.stop()
        try:
            self._server = _BoundedServer((bind, int(port)), _Handler, self.service)
        except OSError as exc:
            self._server = None
            if isinstance(exc, PermissionError) and exc.errno is None:  # OrcaSlicer's audit denial
                raise RuntimeError(PERMISSION_TEXT) from exc
            raise RuntimeError(f'Не удалось открыть порт {port}: {exc.strerror or exc}') from exc
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': 0.2},
                                        name='filament-qr-http', daemon=True)
        self._thread.start()
        return self.address

    def stop(self, timeout=3.0):
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is None:
            return True
        server.shutdown()
        server.server_close()
        thread.join(timeout)
        return server.wait_idle(timeout) and not thread.is_alive()


def lan_addresses():
    # Only the outgoing-interface probe: resolving the host name would make OrcaSlicer ask for a
    # permission on a target that cannot be declared in advance.
    addrs = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))  # no packet is sent for UDP connect
            addrs.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith(('127.', '169.254.')))


# --------------------------------------------------------------------------- settings

class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.lan_enabled = False
        self.port = DEFAULT_PORT
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.lan_enabled = bool(data.get('lan_enabled', False))
            port = int(data.get('port', DEFAULT_PORT))
            self.port = port if 1024 <= port <= 65535 else DEFAULT_PORT
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        data = json.dumps({'lan_enabled': self.lan_enabled, 'port': self.port})
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix='.settings-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# --------------------------------------------------------------------------- plugin controller

class Controller:
    """Everything the Pages capability does, without depending on the orca module."""

    def __init__(self, host, caps, data_dir):
        self.host = host
        self.caps = caps
        self.data_dir = Path(data_dir) if data_dir else None
        self.store = self.settings = self.service = self.server = None
        self.server_error = ''
        self.hosts = ['127.0.0.1']
        self._lock = threading.Lock()
        self._closed = False
        self._selecting = None
        self._url_qrs = ((), [])
        # Imported here, on the GUI thread during on_load, so request threads reuse the loaded modules.
        self.decoder_error = decoder_error()
        if self.data_dir is not None:
            self.store = Store(self.data_dir / 'filament_qr.sqlite3')
            self.settings = Settings(self.data_dir / 'settings.json')
            self.service = Service(self.store)
            self.server = FilamentHttpServer(self.service)

    @property
    def available(self):
        return self.store is not None

    def start_server(self):
        if not self.available:
            return
        bind = '0.0.0.0' if self.settings.lan_enabled else '127.0.0.1'
        self.hosts = (lan_addresses() or ['IP-адрес-компьютера']) if self.settings.lan_enabled else ['127.0.0.1']
        try:
            self.server.start(bind, self.settings.port)
            self.server_error = ''
        except RuntimeError as exc:
            self.server_error = str(exc)

    def shutdown(self):
        with self._lock:
            self._closed = True
            worker = self._selecting
        if worker is not None:
            worker.join(SELECT_JOIN_TIMEOUT)
        if self.server:
            self.server.stop()
        if self.service:
            self.service.sessions.revoke_all()

    def refresh_workspace(self):
        try:
            ws = read_workspace(self.host, self.caps)
        except Exception:
            ws = WorkspaceState()
        if self.service:
            self.service.set_workspace(ws)
        return ws

    def process_queue(self):
        """Handle queued scans on the GUI thread; at most one selection runs at a time on a worker.
        Returns True when the queue or workspace may have changed."""
        if not self.available or self._closed:
            return False
        if self._selecting is not None and self._selecting.is_alive():
            return False
        changed = False
        self.service.queue_changed.clear()
        for _ in range(10):
            pending = self.store.next_queued()
            if pending is None:
                break
            changed = True
            if prepare_pending(self.host, self.store, pending, self.caps) == 'select':
                self._start_selection(pending)
                break
        if self.caps.mode != 'automatic':
            ws = self.service.workspace()
            for pending in self.store.list_pending(('manual',)):
                slot = int(pending['slot'])
                if slot < len(ws.slots) and ws.slots[slot] == pending['profile']:
                    self.store.mark(pending['id'], 'done', 'Профиль выбран')
        return changed

    def _start_selection(self, pending):
        pid, slot, profile, colour = pending['id'], int(pending['slot']), pending['profile'], pending.get('color', '')
        slots = self.refresh_workspace().slots
        current = slots[slot] if slot < len(slots) else ''

        def run():
            try:
                status, message = select_in_slot(self.host, slot, profile, current, colour)
            except Exception as exc:
                status, message = 'needs_attention', f'Ошибка OrcaSlicer при выборе профиля: {exc}'
            with self._lock:
                if not self._closed:
                    self.store.mark(pid, status, message, attempted=True)

        self._selecting = threading.Thread(target=run, name='filament-qr-select', daemon=True)
        self._selecting.start()

    def url_qrs(self, urls):
        """QR codes of the server addresses, rebuilt only when the address list changes."""
        key = tuple(urls)
        if self._url_qrs[0] != key:
            try:
                codes = [{'url': url, 'svg': url_qr_svg(url)} for url in key]
            except ImportError:
                codes = []
            self._url_qrs = (key, codes)
        return self._url_qrs[1]

    def state(self, ws=None):
        ws = ws or self.refresh_workspace()
        base = {'mode': self.caps.mode, 'available': self.available, 'printer': ws.printer, 'slots': ws.slots,
                'profiles': ws.profiles, 'dirty': ws.dirty, 'colours': ws.colours, 'colour_api': self.caps.colour,
                'decoder_error': self.decoder_error}
        if not self.available:
            return base
        address = self.server.address
        port = address[1] if address else self.settings.port
        urls = [f'http://{h}:{port}/' for h in self.hosts] if address else []
        base.update({
            'spools': self.store.list_spools(),
            'pending': self.store.list_pending(),
            'recent': self.store.recent_done(),
            'lan_enabled': self.settings.lan_enabled,
            'port': port,
            'urls': urls,
            'url_qrs': self.url_qrs(urls),
            'pin': self.service.sessions.pin,
            'sessions': self.service.sessions.session_count(),
            'server_error': self.server_error,
        })
        return base

    def handle(self, data):
        """Handle one page message; returns the messages to post back."""
        if self._closed:
            return []
        kind = data.get('type') if isinstance(data, dict) else None
        if kind == 'tick':
            ws = self.refresh_workspace()
            if self.process_queue():
                ws = None
            return [{'type': 'state', 'state': self.state(ws)}]
        if not self.available:
            return [{'type': 'error', 'message': 'Хранилище плагина недоступно: нужен OrcaSlicer 2.5.0 или новее'}]
        out = []
        if kind == 'create':
            ws = self.refresh_workspace()
            profile = str(data.get('profile', ''))
            if profile not in ws.profiles:
                raise ValueError('Профиль недоступен для текущего принтера')
            spool = self.store.add_spool(profile, ws.printer, data.get('material', ''), data.get('brand', ''),
                                         data.get('notes', ''), color=data.get('color', ''))
            out += [{'type': 'created', 'spool': spool}, self.label(spool['code'])]
        elif kind == 'delete':
            self.store.delete_spool(data.get('code', ''))
        elif kind == 'label':
            out.append(self.label(data.get('code', '')))
        elif kind == 'use':
            self.service.scan(data.get('code', ''), data.get('slot', 0))
            self.process_queue()
        elif kind == 'retry':
            self.store.mark(int(data.get('id', 0)), 'queued', 'Повтор')
            self.process_queue()
        elif kind == 'dismiss':
            self.store.mark(int(data.get('id', 0)), 'dismissed')
        elif kind == 'set_lan':
            self.settings.lan_enabled = bool(data.get('enabled'))
            port = int(data.get('port', self.settings.port))
            if not 1024 <= port <= 65535:
                raise ValueError('Порт должен быть от 1024 до 65535')
            self.settings.port = port
            self.settings.save()
            self.service.sessions.revoke_all()
            self.start_server()
        elif kind == 'rotate_pin':
            self.service.sessions.rotate_pin()
        elif kind == 'revoke_sessions':
            self.service.sessions.revoke_all()
        else:
            return []
        return out + [{'type': 'state', 'state': self.state()}]

    def label(self, code):
        spool = self.store.get_spool(code)
        if spool is None:
            raise LookupError('Катушка не найдена')
        return {'type': 'label', 'code': spool['code'], 'svg': qr_svg(spool)}


# --------------------------------------------------------------------------- pages

PHONE_HTML = r'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Filament QR</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0e1119;color:#f1f3fa;font:16px system-ui}
main{max-width:580px;margin:auto;padding:20px}h1{font-size:26px;margin:6px 0 12px}p,small{color:#b8bfcd}
section{background:#1b2230;border:1px solid #313e54;border-radius:16px;padding:16px;margin:14px 0}
input,select,button{font:inherit;border-radius:10px;padding:12px;border:1px solid #485570;width:100%;margin:6px 0}
input,select{background:#0e1420;color:#fff}button{background:#568bff;color:#fff;font-weight:700;border:0}
button.secondary{background:#2b3444}.error{color:#ff8e89}.ok{color:#93f1a8}.row{border-bottom:1px solid #3a4455;padding:10px 0}
label.camera{display:block;text-align:center;background:#568bff;border-radius:10px;padding:12px;font-weight:700}
#photo{display:none}a{color:#adc8ff}.sw{display:inline-block;width:16px;height:16px;border-radius:50%;border:1px solid #889;vertical-align:middle;margin-right:6px}
</style><main><h1>🧵 Filament QR</h1><p>Выбор катушки для OrcaSlicer</p>
<section id="login"><h2>Вход</h2><p>Введите PIN со страницы Filament QR в OrcaSlicer.</p>
<input id="pin" inputmode="numeric" maxlength="6" placeholder="6 цифр"><button id="enter">Войти</button><p id="loginmsg" class="error"></p></section>
<div id="app" hidden><section><h2>Сканировать</h2><p id="printer"></p>
<label for="slot">Слот филамента</label><select id="slot"></select>
<label for="photo" class="camera">📷 Сфотографировать QR</label><input id="photo" type="file" accept="image/*" capture="environment">
<p>Или введите код с этикетки:</p><input id="code" autocomplete="off" placeholder="FQ-12AB34"><button id="scan">Выбрать катушку</button>
<p id="msg"></p></section><section><h2>Очередь</h2><div id="queue"></div></section>
<section><h2>Катушки</h2><div id="spools"></div></section><button id="logout" class="secondary">Выйти</button></div></main>
<script>
const $=id=>document.getElementById(id);
async function call(path,body,isFile){const opt=body===undefined?{}:{method:'POST',headers:isFile?{'Content-Type':'application/octet-stream'}:{'Content-Type':'application/json'},body:isFile?body:JSON.stringify(body)};
 const r=await fetch(path,opt);let j={};try{j=await r.json()}catch(e){}if(!r.ok){const e=new Error(j.error||('Ошибка '+r.status));e.status=r.status;throw e}return j}
function say(text,ok){$('msg').textContent=text;$('msg').className=ok?'ok':'error'}
function showApp(on){$('login').hidden=on;$('app').hidden=!on}
function slot(){return parseInt($('slot').value||'0',10)}
$('enter').onclick=async()=>{try{await call('/api/auth',{pin:$('pin').value});showApp(true);await load()}catch(e){$('loginmsg').textContent=e.message}};
$('logout').onclick=async()=>{try{await call('/api/logout',{})}catch(e){}showApp(false)};
$('scan').onclick=async()=>{try{const d=await call('/api/scan',{code:$('code').value,slot:slot()});say('Отправлено в OrcaSlicer: '+d.pending.profile,true);$('code').value='';await load()}catch(e){say(e.message,false)}};
$('photo').onchange=async()=>{const f=$('photo').files&&$('photo').files[0];if(!f)return;say('Распознаём QR…',true);
 try{const d=await call('/api/decode?slot='+slot(),f,true);say('Отправлено в OrcaSlicer: '+d.pending.profile,true);await load()}catch(e){say(e.message,false)}finally{$('photo').value=''}};
function text(tag,t){const e=document.createElement(tag);e.textContent=t;return e}
function swatch(c){const e=document.createElement('span');e.className='sw';if(c){e.style.background=c}else{e.style.visibility='hidden'}return e}
async function load(){let d;try{d=await call('/api/status')}catch(e){if(e.status===401)showApp(false);return}
 showApp(true);$('printer').textContent=d.printer?'Принтер: '+d.printer:'';
 const keep=$('slot').value;$('slot').replaceChildren();(d.slots.length?d.slots:['']).forEach((name,i)=>{const o=text('option','Слот '+(i+1)+(name?' — '+name:''));o.value=i;$('slot').append(o)});if(keep)$('slot').value=keep;
 $('queue').replaceChildren();for(const p of d.pending){const r=document.createElement('div');r.className='row';const h=text('b','');h.append(swatch(p.color),p.code+' → слот '+(p.slot+1));r.append(h,text('div',p.profile),text('small',p.message||p.status));$('queue').append(r)}
 for(const p of d.recent){const r=document.createElement('div');r.className='row';r.append(text('span','✓ '+p.profile+' → слот '+(p.slot+1)));$('queue').append(r)}
 if(!d.pending.length&&!d.recent.length)$('queue').append(text('p','Пусто'));
 $('spools').replaceChildren();for(const s of d.spools){const r=document.createElement('div');r.className='row';const a=text('a','Этикетка');a.href='/label/'+encodeURIComponent(s.code);a.target='_blank';
  const h=text('b','');h.append(swatch(s.color),s.code+' · '+s.material+(s.brand?' · '+s.brand:''));r.append(h,text('div',s.profile),a);$('spools').append(r)}}
load();setInterval(()=>{if(!$('app').hidden)load()},3000);
</script></html>'''

PLUGIN_HTML = r'''<!doctype html><html lang="ru"><meta charset="utf-8"><style>
:root{font-family:system-ui;color:var(--orca-fg,#eee);background:var(--orca-bg,#222)}body{margin:16px;max-width:1100px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:760px){.grid{grid-template-columns:1fr}}
section{border:1px solid var(--orca-border,#666);border-radius:10px;padding:14px;margin-bottom:16px}h2{margin-top:0;font-size:18px}
select,input,button{font:inherit;padding:7px;margin:4px 0;border-radius:6px;max-width:100%}select,input{width:100%;background:transparent;color:inherit;border:1px solid var(--orca-border,#888)}
option{color:#000}button{background:var(--orca-accent,#4a89e8);color:var(--orca-accent-fg,#fff);border:0;cursor:pointer}button.secondary{background:transparent;color:inherit;border:1px solid var(--orca-border,#888)}
.row{border-bottom:1px solid var(--orca-border,#555);padding:8px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row .grow{flex:1;min-width:200px}
.sw{display:inline-block;width:14px;height:14px;border-radius:50%;border:1px solid #888;vertical-align:middle;margin-right:6px}.warn{color:#e6a756}.err{color:#ff8e89}.ok{color:#8fe3a0}.muted{opacity:.75}.pin{font-size:26px;font-weight:700;letter-spacing:4px}
#label svg{width:220px;background:#fff}.qrs{display:flex;gap:12px;flex-wrap:wrap}.qrs figure{margin:0;text-align:center}.qrs svg{width:160px;height:160px;background:#fff;padding:4px;border-radius:6px}.qrs figcaption{font-size:12px;opacity:.8}code{user-select:all}
</style><h1>🧵 Filament QR</h1><div id="banner"></div>
<div class="grid"><section><h2>Новая катушка</h2>
<label>Профиль филамента</label><select id="profile"></select>
<label>Материал</label><input id="material" maxlength="40" placeholder="PLA / PETG / ABS">
<label>Производитель</label><input id="brand" maxlength="80" placeholder="eSUN">
<label>Цвет катушки</label><input id="color" type="color" value="#ffffff" style="width:80px;height:36px;padding:2px">
<label>Заметка</label><input id="notes" maxlength="300">
<button id="add">Создать катушку и этикетку</button><p id="status"></p><div id="label"></div></section>
<section><h2>Телефон</h2><div id="phone"></div>
<label><input type="checkbox" id="lan" style="width:auto"> Разрешить доступ из локальной сети</label>
<label>Порт</label><input id="port" type="number" min="1024" max="65535">
<button id="applyLan" class="secondary">Применить</button> <button id="rotate" class="secondary">Новый PIN</button> <button id="revoke" class="secondary">Отключить телефоны</button>
<p class="muted">Используйте только в доверенной домашней сети: соединение идёт по HTTP без шифрования. При первом запуске Windows может спросить разрешение брандмауэра — разрешите для частной сети.</p></section></div>
<section><h2>Очередь сканирований</h2><div id="queue"></div></section>
<section><h2>Катушки</h2><div id="spools"></div></section>
<script>
let S={};const $=id=>document.getElementById(id);
function post(type,data){orca.postMessage(Object.assign({type},data||{}))}
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function btn(text,fn,cls){const b=el('button',text,cls);b.onclick=fn;return b}
function swatch(c){const e=el('span',undefined,'sw');if(c){e.style.background=c;e.title=c}else{e.style.visibility='hidden'}return e}
function slotSelect(){const s=el('select');(S.slots&&S.slots.length?S.slots:['']).forEach((n,i)=>{const o=el('option','Слот '+(i+1)+(n?' — '+n:''));o.value=i;s.append(o)});s.style.width='auto';return s}
$('add').onclick=()=>post('create',{profile:$('profile').value,material:$('material').value,brand:$('brand').value,color:$('color').value,notes:$('notes').value});
$('applyLan').onclick=()=>post('set_lan',{enabled:$('lan').checked,port:parseInt($('port').value,10)});
$('rotate').onclick=()=>post('rotate_pin');$('revoke').onclick=()=>post('revoke_sessions');
const STATUS={queued:'в очереди',manual:'выберите вручную',needs_attention:'нужно внимание',cancelled:'отменено'};
function render(s){S=s;
 const b=$('banner');b.replaceChildren();
 if(!s.available)b.append(el('p','Хранилище плагина недоступно: нужен OrcaSlicer 2.5.0 или новее.','err'));
 else if(s.mode!=='automatic')b.append(el('p','Режим без автоматического выбора: после сканирования выберите показанный профиль в слоте вручную.','warn'));
 if(s.decoder_error)b.append(el('p','Распознавание фото недоступно: '+s.decoder_error+'. Коды с этикеток можно вводить вручную.','err'));
 if(s.mode==='automatic'&&!s.colour_api)b.append(el('p','Эта версия OrcaSlicer не даёт плагинам менять цвет слота: после выбора задайте цвет катушки в слоте вручную.','muted'));
 b.append(el('p','Принтер: '+(s.printer||'—')+'   ·   Слоты: '+((s.slots||[]).map((n,i)=>(i+1)+': '+n).join('; ')||'—'),'muted'));
 const old=$('profile').value;$('profile').replaceChildren();for(const n of s.profiles||[]){const o=el('option',n);o.value=n;$('profile').append(o)}if((s.profiles||[]).includes(old))$('profile').value=old;
 if(!s.available)return;
 const ph=$('phone');ph.replaceChildren();
 if(s.server_error)ph.append(el('p',s.server_error,'err'));
 for(const u of s.urls||[]){const p=el('p','Адрес: ');p.append(el('code',u));ph.append(p)}
 if(s.lan_enabled){const box=el('div',undefined,'qrs');for(const q of s.url_qrs||[]){const f=el('figure');f.innerHTML=q.svg;f.append(el('figcaption',q.url));box.append(f)}
  if(box.childNodes.length){ph.append(el('p','Наведите камеру телефона на код, чтобы открыть страницу:','muted'));ph.append(box)}}
 if(!s.lan_enabled)ph.append(el('p','Сейчас доступ только с этого компьютера. Включите доступ из сети, чтобы сканировать телефоном.','warn'));
 const pin=el('p','PIN: ');pin.append(el('span',s.pin,'pin'));ph.append(pin);ph.append(el('p','Подключённых телефонов: '+s.sessions,'muted'));
 if(document.activeElement!==$('port'))$('port').value=s.port;if(document.activeElement!==$('lan'))$('lan').checked=s.lan_enabled;
 const q=$('queue');q.replaceChildren();
 for(const p of s.pending){const r=el('div',undefined,'row');const t=el('div',undefined,'grow');const h=el('b');h.append(swatch(p.color),p.code+' → слот '+(p.slot+1)+': '+p.profile);t.append(h);t.append(el('div',(STATUS[p.status]||p.status)+(p.message?' — '+p.message:''),p.status==='needs_attention'?'err':'warn'));
  r.append(t,btn('Повторить',()=>post('retry',{id:p.id})),btn('Убрать',()=>post('dismiss',{id:p.id}),'secondary'));q.append(r)}
 for(const p of s.recent){q.append(el('div','✓ '+p.profile+' → слот '+(p.slot+1)+' ('+p.updated+')','row ok'))}
 if(!s.pending.length&&!s.recent.length)q.append(el('p','Пусто','muted'));
 const sp=$('spools');sp.replaceChildren();
 for(const item of s.spools){const r=el('div',undefined,'row');const t=el('div',undefined,'grow');const h=el('b');h.append(swatch(item.color),item.code+' · '+item.material+(item.brand?' · '+item.brand:'')+(item.color?' · '+item.color:''));t.append(h);t.append(el('div',item.profile,'muted'));
  const sel=slotSelect();r.append(t,sel,btn('Выбрать',()=>post('use',{code:item.code,slot:parseInt(sel.value,10)})),btn('Этикетка',()=>post('label',{code:item.code}),'secondary'),
  btn('Удалить',()=>{if(confirm('Удалить катушку '+item.code+'?'))post('delete',{code:item.code})},'secondary'));sp.append(r)}
 if(!s.spools.length)sp.append(el('p','Катушек пока нет','muted'))}
orca.onMessage(d=>{if(d.type==='state')render(d.state);
 else if(d.type==='label'){$('label').innerHTML=d.svg;const u=(S.urls&&S.urls.length)?'http://127.0.0.1:'+S.port+'/label/'+d.code:'';$('status').textContent=u?'Печать этикетки: откройте '+u+' в браузере':'';$('status').className='muted'}
 else if(d.type==='created'){$('status').textContent='Создана катушка '+d.spool.code;$('status').className='ok'}
 else if(d.type==='error'){$('status').textContent='Ошибка: '+d.message;$('status').className='err'}});
post('tick');setInterval(()=>post('tick'),1500);
</script></html>'''


if orca is not None:
    class FilamentQRPage(orca.pages.PagesPluginCapabilityBase):
        controller = None

        def get_name(self):
            return 'Filament QR'

        def get_ui(self):
            return PLUGIN_HTML

        def on_load(self):
            caps = HostCapabilities.discover(orca)
            data_dir = None
            if caps.storage:
                try:
                    data_dir = orca.host.plugin.storage()
                except Exception:
                    data_dir = None
            self.controller = Controller(orca.host, caps, data_dir)
            self.controller.start_server()

        def on_unload(self):
            controller, self.controller = self.controller, None
            if controller is not None:
                controller.shutdown()

        def on_message(self, data):
            if self.controller is None:
                return
            try:
                messages = self.controller.handle(data)
            except (ValueError, LookupError, RuntimeError, sqlite3.Error) as exc:
                messages = [{'type': 'error', 'message': str(exc)}]
            for message in messages:
                self.post_message(message)

    @orca.plugin
    class FilamentQRPlugin(orca.base):
        def register_capabilities(self):
            if callable(getattr(orca, 'request_permissions', None)):
                orca.request_permissions(network_socket=['127.0.0.1', '0.0.0.0', '192.0.2.1'], threading=True)
            orca.register_capability(FilamentQRPage)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run the Filament QR phone server without OrcaSlicer')
    parser.add_argument('--data', required=True, help='directory for the spool database')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--lan', action='store_true', help='listen on all interfaces (trusted LAN only)')
    args = parser.parse_args()
    service = Service(Store(Path(args.data) / 'filament_qr.sqlite3'))
    service.set_workspace(WorkspaceState(printer='Demo printer', slots=['Generic PLA'], profiles=['Generic PLA']))
    server = FilamentHttpServer(service)
    host_addr = server.start('0.0.0.0' if args.lan else '127.0.0.1', args.port)
    print(f'http://{host_addr[0]}:{host_addr[1]}/  PIN {service.sessions.pin}', flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
