# /// script
# requires-python = ">=3.12"
# dependencies = ["qrcode>=7.4,<9", "Pillow>=10,<13", "zxing-cpp>=2.3,<4"]
#
# [tool.orcaslicer.plugin]
# name = "Filament QR Manager"
# description = "Per-spool QR labels and LAN scanning, with safe preset-switch queue"
# author = "Community"
# version = "0.1.0"
# ///
"""OrcaSlicer Filament QR Manager.

Scanned spool selection is queued, NOT applied to OrcaSlicer in stock builds:
its publicly documented plugin API only exposes read-only presets. The plugin
never modifies Orca preset files, so unsaved changes cannot be lost by it.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
from urllib.parse import urlparse, unquote

try:
    import orca  # available only inside OrcaSlicer
except ImportError:
    orca = None

PORT = int(os.environ.get('ORCA_QR_PORT', '8765'))
MAX_IMAGE = 6 * 1024 * 1024
CODE_RE = re.compile(r'^FQ-[0-9A-F]{6}$')


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def normalize_code(text):
    text = str(text).strip().upper()
    if text.startswith('FQ:'):
        text = text[3:]
    if re.fullmatch(r'[0-9A-F]{6}', text):
        text = 'FQ-' + text
    if not CODE_RE.fullmatch(text):
        raise ValueError('Ожидался код FQ-XXXXXX (шесть шестнадцатеричных символов)')
    return text


def material_text(v):
    if isinstance(v, list):
        return str(v[0]) if v else ''
    return str(v or '')


def qr_svg(code, material, brand='', title=''):
    """Self-contained, printable 56x72 mm SVG. Symbol uses ECC H."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H
    code = normalize_code(code)
    material = re.sub(r'[^\w+.-]', '', material.upper())[:10] or 'FILAMENT'
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=4, box_size=1)
    qr.add_data('FQ:' + code)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    # Matrix includes 4-module quiet zone. The SVG prints at the fixed size.
    x0, y0, size = 5.5, 14.0, 45.0
    step = size / n
    rects = []
    for iy, row in enumerate(matrix):
        for ix, dark in enumerate(row):
            if dark:
                rects.append(f'<rect x="{x0+ix*step:.4f}" y="{y0+iy*step:.4f}" width="{step+.008:.4f}" height="{step+.008:.4f}"/>')
    # White central tag occupies ~24% of the width, ECC H compensates.
    cy = y0 + size/2
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="56mm" height="72mm" viewBox="0 0 56 72">
<rect width="56" height="72" fill="white"/>
<rect x="0.5" y="0.5" width="55" height="71" rx="2" fill="none" stroke="#303644" stroke-width=".35"/>
<text x="28" y="5.5" text-anchor="middle" font-family="Arial,sans-serif" font-size="3.5" font-weight="bold" fill="#111">FILAMENT QR</text>
<text x="28" y="10" text-anchor="middle" font-family="Arial,sans-serif" font-size="3.2" fill="#333">{escape((brand or title)[:26])}</text>
<g fill="#111">{''.join(rects)}</g>
<rect x="{28-6.2:.2f}" y="{cy-4.6:.2f}" width="12.4" height="9.2" rx=".9" fill="white" stroke="#111" stroke-width=".35"/>
<text x="28" y="{cy-0.9:.2f}" text-anchor="middle" font-family="Arial,sans-serif" font-size="2.9" font-weight="bold" fill="#111">{escape(material)}</text>
<text x="28" y="{cy+2.5:.2f}" text-anchor="middle" font-family="Arial,sans-serif" font-size="2.25" font-weight="bold" fill="#111">{escape(code[3:])}</text>
<text x="28" y="62.8" text-anchor="middle" font-family="Arial,sans-serif" font-size="4.1" font-weight="bold" fill="#111">{escape(material)} · {escape(code[3:])}</text>
<text x="28" y="67.2" text-anchor="middle" font-family="Arial,sans-serif" font-size="2.15" fill="#333">{escape(title[:34])}</text>
</svg>'''


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._conn() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS spools (
             code TEXT PRIMARY KEY, profile TEXT NOT NULL, printer TEXT NOT NULL,
             material TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '',
             notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)''')

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.lock, self._conn() as db:
            db.row_factory = sqlite3.Row
            return [dict(x) for x in db.execute('SELECT * FROM spools ORDER BY created DESC, code DESC')]

    def get(self, code):
        code = normalize_code(code)
        with self.lock, self._conn() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM spools WHERE code=?', (code,)).fetchone()
            return dict(row) if row else None

    def add(self, profile, printer, material, brand='', notes=''):
        if not profile.strip():
            raise ValueError('Выберите профиль OrcaSlicer')
        if not printer.strip():
            raise ValueError('Принтер не определён')
        if len(profile)>180 or len(printer)>180 or len(material)>40 or len(brand)>80 or len(notes)>300:
            raise ValueError('Слишком длинное значение')
        with self.lock, self._conn() as db:
            for _ in range(20):
                code = 'FQ-' + secrets.token_hex(3).upper()
                try:
                    db.execute('INSERT INTO spools VALUES (?,?,?,?,?,?,?)',
                               (code,profile,printer,material,brand,notes,utc_now()))
                    db.commit()
                    return self.get(code)
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError('Не получилось создать уникальный код')

    def delete(self, code):
        code = normalize_code(code)
        with self.lock, self._conn() as db:
            return bool(db.execute('DELETE FROM spools WHERE code=?', (code,)).rowcount)


class Service:
    def __init__(self, data_dir, port=PORT, bind='0.0.0.0'):
        self.store = Store(Path(data_dir) / 'filament_qr.sqlite3')
        self.port, self.bind = port, bind
        self.pin = f'{secrets.randbelow(1_000_000):06d}'
        self.sessions = set()
        self.auth_failures = {}
        self.state_lock = threading.RLock()
        self.presets = []
        self.active = []
        self.printer = ''
        self.dirty = False
        self.pending = None
        self.last_event = None
        self.httpd = None
        self.thread = None
        self.error = ''
        self.lan_ips = self.discover_lan_ips()

    @staticmethod
    def discover_lan_ips():
        try:
            addrs = {a for a in socket.gethostbyname_ex(socket.gethostname())[2]
                     if not a.startswith(('127.', '169.254.'))}
            return sorted(addrs) or ['IP_ВАШЕГО_ПК']
        except OSError:
            return ['IP_ВАШЕГО_ПК']

    def snapshot(self):
        with self.state_lock:
            return dict(presets=list(self.presets), active=list(self.active),
                        printer=self.printer, dirty=self.dirty, pending=self.pending,
                        last_event=self.last_event, spools=self.store.list(),
                        port=self.port, pin=self.pin, error=self.error, lan_ips=self.lan_ips)

    def update_orca(self, presets, active, printer, dirty):
        with self.state_lock:
            self.presets = list(presets)
            self.active = list(active)
            self.printer = str(printer)
            self.dirty = bool(dirty)
            if self.pending:
                if self.pending['printer'] != self.printer:
                    self.pending['status'] = 'wrong_printer'
                elif self.pending['profile'] in self.active and not self.dirty:
                    self.last_event = 'Профиль выбран в OrcaSlicer: ' + self.pending['profile']
                    self.pending = None
                else:
                    self.pending['status'] = 'save_required' if self.dirty else 'manual_switch'

    def create(self, profile, material, brand='', notes=''):
        with self.state_lock:
            if self.dirty:
                raise ValueError('Сначала сохраните изменения профиля в OrcaSlicer')
            if profile not in self.presets:
                raise ValueError('Профиль отсутствует среди доступных в OrcaSlicer')
            return self.store.add(profile, self.printer, material, brand, notes)

    def scan(self, text):
        code = normalize_code(text)
        spool = self.store.get(code)
        if spool is None:
            raise LookupError('Катушка с этим кодом не найдена')
        with self.state_lock:
            if self.printer and spool['printer'] != self.printer:
                self.last_event = 'Профиль катушки привязан к другому принтеру'
                self.pending = dict(spool, status='wrong_printer')
            elif spool['profile'] in self.active and not self.dirty:
                self.last_event = 'Уже выбран: ' + spool['profile']
                self.pending = None
            else:
                self.pending = dict(spool, status=('save_required' if self.dirty else 'manual_switch'))
                self.last_event = 'Сканирование: ' + spool['code']
        return {'spool': spool, 'status': self.pending['status'] if self.pending else 'already_active'}

    def start(self):
        parent = self
        class Handler(BaseHTTPRequestHandler):
            server_version = 'FilamentQR/0.1'
            def log_message(self, fmt, *args):
                pass
            def respond(self, status, content, mime='application/json; charset=utf-8', headers=None):
                if isinstance(content, (dict, list)):
                    content = json.dumps(content, ensure_ascii=False).encode('utf-8')
                elif isinstance(content, str):
                    content = content.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Referrer-Policy', 'no-referrer')
                for k,v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(content)
            def authorized(self):
                cookies = self.headers.get('Cookie','')
                token = next((p.strip()[7:] for p in cookies.split(';') if p.strip().startswith('fq_sid=')), '')
                with parent.state_lock:
                    return bool(token and token in parent.sessions)
            def read_body(self, limit=32768):
                n = int(self.headers.get('Content-Length','0'))
                if n <= 0 or n > limit:
                    raise ValueError('Некорректный размер данных')
                return self.rfile.read(n)
            def do_GET(self):
                try:
                    path = unquote(urlparse(self.path).path)
                    if path == '/':
                        return self.respond(200, PHONE_HTML, 'text/html; charset=utf-8')
                    if path == '/api/status':
                        if not self.authorized():
                            return self.respond(401, {'error':'Сначала введите PIN'})
                        s = parent.snapshot()
                        return self.respond(200, {'spools':s['spools'], 'pending':s['pending'],
                                                  'last_event':s['last_event'], 'printer':s['printer']})
                    if path.startswith('/label/'):
                        if not self.authorized() and self.client_address[0] not in ('127.0.0.1','::1'):
                            return self.respond(401, 'Требуется вход', 'text/plain; charset=utf-8')
                        code = normalize_code(path.removeprefix('/label/'))
                        item = parent.store.get(code)
                        if not item:
                            return self.respond(404, 'Катушка не найдена', 'text/plain; charset=utf-8')
                        svg = qr_svg(code,item['material'],item['brand'],item['profile'])
                        doc = f'''<!doctype html><meta charset="utf-8"><title>Этикетка {code}</title><style>
                        body{{font:16px Arial;text-align:center;margin:24px}} svg{{max-width:96vw;height:auto}}
                        @page{{margin:0;size:56mm 72mm}} @media print{{body{{margin:0}}button{{display:none}}}}
                        </style><button onclick="print()">Печать 56 × 72 мм</button><div>{svg}</div>'''
                        return self.respond(200,doc,'text/html; charset=utf-8')
                    return self.respond(404, {'error':'Не найдено'})
                except Exception as exc:
                    return self.respond(400, {'error':str(exc)})
            def do_POST(self):
                try:
                    path = urlparse(self.path).path
                    if path == '/api/auth':
                        body = json.loads(self.read_body())
                        import time
                        ip = self.client_address[0]
                        now = time.monotonic()
                        with parent.state_lock:
                            attempts = [t for t in parent.auth_failures.get(ip, []) if now-t<60]
                            parent.auth_failures[ip] = attempts
                            if len(attempts) >= 6:
                                return self.respond(429, {'error':'Слишком много попыток. Повторите через минуту'})
                            if not secrets.compare_digest(str(body.get('pin','')), parent.pin):
                                attempts.append(now)
                                return self.respond(403, {'error':'Неверный PIN'})
                            parent.auth_failures.pop(ip,None)
                        token = secrets.token_urlsafe(32)
                        with parent.state_lock:
                            parent.sessions.add(token)
                        return self.respond(200, {'ok':True}, headers={'Set-Cookie':f'fq_sid={token}; HttpOnly; SameSite=Strict; Path=/'})
                    if not self.authorized():
                        return self.respond(401, {'error':'Введите PIN'})
                    if path == '/api/scan':
                        obj = json.loads(self.read_body())
                        return self.respond(200, parent.scan(obj.get('code','')))
                    if path == '/api/decode':
                        data = self.read_body(MAX_IMAGE)
                        import zxingcpp
                        from PIL import Image, ImageOps
                        with Image.open(io.BytesIO(data)) as img:
                            img = ImageOps.exif_transpose(img)
                            img.thumbnail((2600,2600))
                            found = zxingcpp.read_barcodes(img)
                        if not found:
                            raise ValueError('QR на фотографии не распознан. Попробуйте ближе или введите код.')
                        for item in found:
                            try:
                                return self.respond(200,parent.scan(item.text))
                            except (LookupError,ValueError):
                                pass
                        raise ValueError('QR распознан, но такой катушки нет в базе')
                    return self.respond(404,{'error':'Не найдено'})
                except LookupError as exc:
                    return self.respond(404, {'error':str(exc)})
                except Exception as exc:
                    return self.respond(400, {'error':str(exc)})
        try:
            self.httpd = ThreadingHTTPServer((self.bind,self.port),Handler)
            self.httpd.daemon_threads = True
            self.port = self.httpd.server_address[1]
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name='filament-qr-http')
            self.thread.start()
        except OSError as exc:
            self.error = f'Не удалось открыть {self.port}: {exc}'
        return self.error == ''

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=2)


PHONE_HTML = r'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Filament QR</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0e1119;color:#f1f3fa;font:16px system-ui}
main{max-width:580px;margin:auto;padding:20px}h1{font-size:27px;margin:6px 0 12px}p,small{color:#b8bfcd}
section{background:#1b2230;border:1px solid #313e54;border-radius:17px;padding:18px;margin:16px 0}
input,button{font:inherit;border-radius:10px;padding:13px;border:1px solid #485570}input{background:#0e1420;color:white;max-width:100%;width:100%;margin:8px 0}
button{background:#568bff;color:#fff;font-weight:700;border:0;cursor:pointer;margin:6px 0}button.outline{background:#2b3444}
.error{color:#ff8e89}.ok{color:#93f1a8}.chip{background:#29364b;border-radius:7px;padding:4px 8px;display:inline-block}
a{color:#adc8ff}article{border-bottom:1px solid #3a4455;padding:12px 0}article:last-child{border-bottom:0}
</style><main><h1>🧵 Filament QR</h1><p>Сканирование катушек для OrcaSlicer · локальная сеть</p>
<section id="login"><h2>Подключение</h2><p>Введите PIN из вкладки Filament QR в OrcaSlicer.</p>
<input id="pin" type="text" inputmode="numeric" maxlength="6" placeholder="6-значный PIN"><button id="enter">Подключиться</button></section>
<div id="app" hidden><section><h2>Сканировать</h2><p>Фото через камеру телефона (работает даже без HTTPS).</p>
<label for="photo" class="chip">📷 Открыть камеру</label><input id="photo" type="file" accept="image/*" capture="environment">
<p>Или введите короткий код с наклейки:</p><input id="code" autocomplete="off" placeholder="FQ-12AB34"><button id="scan">Выбрать катушку</button>
<p id="msg"></p></section><section><h2>Катушки</h2><div id="items"></div></section></div></main>
<script>
const $=id=>document.getElementById(id);
async function call(path,body,file=false){let opt=body===undefined?{}:{method:'POST',headers:file?{}:{'Content-Type':'application/json'},body:file?body:JSON.stringify(body)};
 let r=await fetch(path,opt);let j=await r.json();if(!r.ok)throw new Error(j.error||'Ошибка');return j}
function message(text,ok){$('msg').textContent=text;$('msg').className=ok?'ok':'error'}
$('enter').onclick=async()=>{try{await call('/api/auth',{pin:$('pin').value});$('login').hidden=true;$('app').hidden=false;await load()}catch(e){alert(e.message)}};
$('scan').onclick=async()=>{try{const d=await call('/api/scan',{code:$('code').value});message('Катушка '+d.spool.code+' · '+d.spool.profile+' · '+d.status,true);await load()}catch(e){message(e.message,false)}};
$('photo').onchange=async()=>{const f=$('photo').files?.[0];if(!f)return;message('Распознаём QR…',true);try{const d=await call('/api/decode',f,true);message('Катушка '+d.spool.code+' · '+d.spool.profile+' · '+d.status,true);await load()}catch(e){message(e.message,false)}finally{$('photo').value=''}};
async function load(){try{let d=await call('/api/status');$('items').replaceChildren();if(d.pending){const p=document.createElement('p');p.textContent='Ожидает выбора в OrcaSlicer: '+d.pending.code+' — '+d.pending.profile+' ('+d.pending.status+')';$('items').append(p)}
for(const s of d.spools){let a=document.createElement('article');let b=document.createElement('b');b.textContent=s.material+' · '+s.code+' · '+s.brand;
let p=document.createElement('div');p.textContent=s.profile;let link=document.createElement('a');link.href='/label/'+encodeURIComponent(s.code);link.textContent='Печатать этикетку';link.target='_blank';a.append(b,p,link);$('items').append(a)}}catch(e){}}
load().then(()=>{});setInterval(()=>{if(!$('app').hidden)load()},3000);
</script></html>'''


PLUGIN_HTML = r'''<!doctype html><html lang="ru"><meta charset="utf-8"><style>
:root{font-family:system-ui;color:var(--orca-fg,#eee);background:var(--orca-bg,#222)}body{margin:16px;max-width:1080px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}section{border:1px solid var(--orca-border,#666);border-radius:12px;padding:16px}
select,input,button{font:inherit;padding:9px;margin:6px 0;max-width:100%;border-radius:7px}select,input{width:100%;background:transparent;color:inherit;border:1px solid #888}
option{color:black}button{background:var(--orca-accent,#4a89e8);color:var(--orca-accent-fg,#fff);border:0;cursor:pointer}
#status{white-space:pre-wrap;font-weight:600}.warning{color:#e6a756}.spool{border-bottom:1px solid #666;padding:8px 0}
svg{width:230px;max-width:100%;background:white}@media(max-width:750px){.grid{grid-template-columns:1fr}}
</style><h1>🧵 Filament QR Manager</h1><p>Управление катушками и QR-этикетками. Профили остаются в OrcaSlicer.</p>
<div class="grid"><section><h2>Новая катушка</h2>
<p>Сначала сохраните профиль филамента штатной кнопкой OrcaSlicer. Затем привяжите его к катушке.</p>
<label>Профиль OrcaSlicer</label><select id="profile"></select>
<label>Тип материала</label><input id="material" placeholder="PETG / PLA / ABS / TPU" maxlength="40">
<label>Производитель / цвет</label><input id="brand" placeholder="eSUN, чёрный" maxlength="80">
<button id="add">Создать катушку и QR</button><p id="status"></p>
</section><section><h2>Телефон</h2><div id="connection"></div><p>ПК и телефон должны находиться в одной локальной сети. PIN действует до перезапуска плагина.</p>
<h3>Текущий статус</h3><div id="current"></div><div id="pending" class="warning"></div>
</section></div><section><h2>Катушки</h2><div id="spools"></div><div id="label"></div>
<p class="warning">Автоматическое переключение пресета не поддерживается штатным Python API OrcaSlicer. После сканирования выберите показанный профиль в слайсере. Сначала сохраните несохранённые настройки.</p></section>
<script>
let state={};const $=id=>document.getElementById(id);
function post(type,data={}){orca.postMessage({type,...data})}
$('add').onclick=()=>post('create',{profile:$('profile').value,material:$('material').value,brand:$('brand').value});
function render(s){state=s;const old=$('profile').value;$('profile').replaceChildren();for(const name of s.presets){const opt=document.createElement('option');opt.value=name;opt.textContent=name;$('profile').append(opt)}
if(s.presets.includes(old))$('profile').value=old;
$('connection').textContent='Адрес: '+s.lan_ips.map(ip=>'http://'+ip+':'+s.port).join('  или  ')+'  ·  PIN: '+s.pin+(s.error?' · '+s.error:'');
$('current').textContent='Принтер: '+s.printer+'\nАктивный филамент: '+s.active.join(', ')+(s.dirty?' · Есть несохранённые изменения':'');
$('pending').textContent=s.pending?'QR: '+s.pending.code+' → '+s.pending.profile+' · '+(s.pending.status==='save_required'?'СНАЧАЛА СОХРАНИТЕ ТЕКУЩИЕ НАСТРОЙКИ':s.pending.status==='wrong_printer'?'ДРУГОЙ ПРИНТЕР':'выберите профиль вручную'):'';
$('spools').replaceChildren();for(const item of s.spools){const row=document.createElement('div');row.className='spool';const title=document.createElement('b');title.textContent=item.code+' — '+item.material+' — '+item.profile;
const btn=document.createElement('button');btn.textContent='Этикетка';btn.onclick=()=>post('label',{code:item.code});row.append(title,document.createTextNode(' '),btn);$('spools').append(row)}}
orca.onMessage(d=>{if(d.type==='state')render(d.state);if(d.type==='label'){$('label').innerHTML=d.svg;$('status').textContent='Для печати: откройте http://127.0.0.1:'+state.port+'/label/'+d.code+' в браузере ПК';}if(d.type==='error')$('status').textContent='Ошибка: '+d.message;if(d.type==='created'){$('status').textContent='Создана катушка '+d.spool.code;post('label',{code:d.spool.code});post('tick')}});
post('tick');setInterval(()=>post('tick'),1500);
</script></html>'''


_service = None


def read_live_orca():
    """This function must run on Orca's GUI thread."""
    bundle = orca.host.preset_bundle()
    names = [p.name for i in range(bundle.filaments.size())
             if (p := bundle.filaments.preset(i)).is_visible and p.is_compatible]
    active = bundle.current_filament_preset_names()
    printer = bundle.printers.selected_preset_name()
    edited = bundle.filaments.edited_preset()
    dirty = bool(edited.is_dirty) if edited else False
    return names, active, printer, dirty


if orca is not None:
    class FilamentQRPage(orca.pages.PagesPluginCapabilityBase):
        def get_name(self):
            return 'Filament QR'
        def get_ui(self):
            return PLUGIN_HTML
        def on_load(self):
            global _service
            path = orca.host.plugin.storage()
            _service = Service(path)
            _service.start()
            try:
                _service.update_orca(*read_live_orca())
            except Exception:
                pass
        def on_unload(self):
            global _service
            if _service:
                _service.stop()
                _service = None
        def on_message(self, data):
            if not isinstance(data, dict) or _service is None:
                return
            try:
                t = data.get('type')
                if t == 'tick':
                    _service.update_orca(*read_live_orca())
                    self.post_message({'type':'state','state':_service.snapshot()})
                elif t == 'create':
                    profile = str(data.get('profile',''))
                    material = str(data.get('material','')).strip() or 'FILAMENT'
                    item = _service.create(profile,material,str(data.get('brand','')))
                    self.post_message({'type':'created','spool':item})
                elif t == 'label':
                    item = _service.store.get(str(data.get('code','')))
                    if item:
                        self.post_message({'type':'label','code':item['code'],
                                           'svg':qr_svg(item['code'],item['material'],item['brand'],item['profile'])})
            except Exception as exc:
                self.post_message({'type':'error','message':str(exc)})

    @orca.plugin
    class FilamentQRPlugin(orca.base):
        def register_capabilities(self):
            orca.request_permissions(threading=True,
                network_socket=[f"('0.0.0.0', {PORT})"])
            orca.register_capability(FilamentQRPage)


if __name__ == '__main__':
    import argparse
    import tempfile
    import time
    ap = argparse.ArgumentParser(description='Test Filament QR without OrcaSlicer')
    ap.add_argument('--standalone', action='store_true')
    ap.add_argument('--data', default=str(Path.home()/'.filament-qr-test'))
    ap.add_argument('--port', type=int, default=PORT)
    args = ap.parse_args()
    if not args.standalone:
        ap.error('Standalone mode requires --standalone')
    s = Service(args.data,args.port)
    s.update_orca(['Generic PLA @ Demo', 'PETG @ Demo'], ['Generic PLA @ Demo'], 'Demo Printer',False)
    print('Test service:', 'http://127.0.0.1:'+str(s.port), 'PIN:', s.pin, flush=True)
    if s.start():
        try:
            while True: time.sleep(.4)
        except KeyboardInterrupt: pass
        finally: s.stop()
    else: print(s.error)
