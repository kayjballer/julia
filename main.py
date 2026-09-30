import os, json, threading, time, re, sqlite3
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
DB = os.path.join(DATA, 'julia.db')
BRAIN = 'http://127.0.0.1:8080/v1/chat/completions'
NOBRAIN = "Mon cerveau n'est pas lancé. Ouvre Termux et démarre llama-server."
ST = {'active': False, 't0': 0.0, 'err': None}

try:
    from plyer import stt, tts
    PLYER_ERR = None
except Exception as e:
    stt = tts = None
    PLYER_ERR = str(e)

try:
    from android.permissions import request_permissions, Permission
    from android.runnable import run_on_ui_thread
    request_permissions([Permission.RECORD_AUDIO])
except Exception:
    def run_on_ui_thread(f):
        return f


# ---------- Mémoire (SQLite) ----------
def sql(query, args=(), fetch=False):
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        cur = c.execute(query, args)
        rows = [dict(r) for r in cur.fetchall()] if fetch else None
        c.commit()
        return rows
    finally:
        c.close()


sql('CREATE TABLE IF NOT EXISTS conv(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, role TEXT, text TEXT)')
sql('CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, text TEXT UNIQUE)')

PATTERNS = [
    (r"\bje m'appelle ([\w'\-]+(?: [\w'\-]+)?)", "Il s'appelle {}"),
    (r"\bmon (?:pr[ée]nom|nom) est ([\w'\-]+)", "Il s'appelle {}"),
    (r"\bj'habite (?:à|a|en|au|aux) ([\w'\- ]{2,30})", "Il habite à {}"),
    (r"\bj'aime (?:bien |beaucoup )?(?:le |la |les |l')?([\w'\- ]{2,30})", "Il aime {}"),
    (r"\bje travaille (?:comme|en tant que) ([\w'\- ]{2,30})", "Il travaille comme {}"),
]


def extract_facts(text):
    t = text.replace("’", "'")
    out = []
    for pat, fmt in PATTERNS:
        m = re.search(pat, t, re.I)
        if m:
            val = m.group(1).strip(" .,!?")
            if len(val) >= 2:
                out.append(fmt.format(val))
    return out


# ---------- Cerveau (llama-server de Termux) ----------
def system_prompt(lang, facts):
    if lang == 'EN':
        s = ("You are Julia, a warm and direct local voice assistant. Always answer in English, "
             "in one or two short spoken sentences, no lists, no emojis. If you don't know, say so.")
        if facts:
            s += "\nWhat you know about the user: " + " ; ".join(facts) + "."
        return s
    s = ("Tu es Julia, une assistante vocale locale, chaleureuse et directe. "
         "Tu réponds toujours en français, en une ou deux phrases courtes, sans listes, "
         "sans emojis, comme à l'oral. Tu tutoies toujours l'utilisateur. "
         "Si tu ne sais pas, dis-le simplement.")
    if facts:
        s += "\nCe que tu sais de l'utilisateur : " + " ; ".join(facts) + "."
    return s


def ask_brain(messages):
    body = json.dumps({'messages': messages, 'max_tokens': 90,
                       'temperature': 0.6, 'stream': False}).encode()
    req = urllib.request.Request(BRAIN, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode())
    out = d['choices'][0]['message']['content']
    return re.sub(r'<think>.*?</think>', '', out, flags=re.S).strip()


def brain_alive():
    try:
        urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=1.5).read()
        return True
    except Exception:
        return False


def _speak(text):
    try:
        tts.speak(text)
    except Exception:
        pass


def reply(text, lang, voice, mem):
    facts, hist = [], []
    if mem:
        for f in extract_facts(text):
            sql('INSERT OR IGNORE INTO facts(ts,text) VALUES(?,?)', (time.time(), f))
        facts = [r['text'] for r in sql('SELECT text FROM facts ORDER BY id DESC LIMIT 12', fetch=True)]
        rows = sql('SELECT role,text FROM conv ORDER BY id DESC LIMIT 6', fetch=True)
        hist = [{'role': r['role'], 'content': r['text']} for r in reversed(rows)]
    msgs = [{'role': 'system', 'content': system_prompt(lang, facts)}] + hist + \
           [{'role': 'user', 'content': text}]
    ok = True
    try:
        out = ask_brain(msgs) or "Je n'ai pas de réponse pour l'instant."
    except Exception:
        out, ok = NOBRAIN, False
    if mem and ok:
        now = time.time()
        sql('INSERT INTO conv(ts,role,text) VALUES(?,?,?)', (now, 'user', text))
        sql('INSERT INTO conv(ts,role,text) VALUES(?,?,?)', (now, 'assistant', out))
    if voice and tts is not None:
        threading.Thread(target=_speak, args=(out,), daemon=True).start()
    return {'reply': out, 'brain': ok}


def memory_info():
    n = sql("SELECT COUNT(*) AS n FROM conv WHERE role='user'", fetch=True)[0]['n']
    facts = sql('SELECT id,text FROM facts ORDER BY id DESC LIMIT 50', fetch=True)
    recent = sql('SELECT role,text FROM (SELECT id,role,text FROM conv ORDER BY id DESC LIMIT 8) ORDER BY id', fetch=True)
    return {'count': n, 'facts': facts, 'recent': recent}


# ---------- Voix (STT) ----------
@run_on_ui_thread
def _stt_start(lang):
    try:
        try:
            stt.language = lang
        except Exception:
            pass
        stt.start()
    except Exception as e:
        ST['err'] = str(e)


@run_on_ui_thread
def _stt_stop():
    try:
        stt.stop()
    except Exception:
        pass


def listen(lang):
    if stt is None:
        return {'ok': False, 'error': 'plyer indisponible : ' + str(PLYER_ERR)}
    for name in ('results', 'partial_results', 'errors'):
        try:
            getattr(stt, name).clear()
        except Exception:
            pass
    ST.update(active=True, t0=time.time(), err=None)
    _stt_start(lang)
    return {'ok': True}


def stt_state():
    now = time.time()
    res = list(getattr(stt, 'results', None) or [])
    part = list(getattr(stt, 'partial_results', None) or [])
    errs = list(getattr(stt, 'errors', None) or [])
    listening = ST['active'] and not res and not ST['err']
    if listening and now - ST['t0'] > 1.5 and not getattr(stt, 'listening', True):
        listening = False
    if listening and now - ST['t0'] > 12:
        _stt_stop()
        listening = False
    if not listening:
        ST['active'] = False
    error = ST['err'] or (str(errs[-1]) if errs and not res else None)
    return {'listening': listening, 'partial': str(part[-1]) if part else '',
            'text': str(res[0]) if res else '', 'error': error}


# ---------- Serveur ----------
class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/api/ping':
            return self._json({'ok': True, 'app': 'Julia', 'voice': stt is not None})
        if u.path == '/api/brain':
            return self._json({'ok': brain_alive()})
        if u.path == '/api/listen':
            lang = parse_qs(u.query).get('lang', ['fr-FR'])[0]
            return self._json(listen(lang))
        if u.path == '/api/stt':
            return self._json(stt_state())
        if u.path == '/api/memory':
            return self._json(memory_info())
        super().do_GET()

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        try:
            data = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            data = {}
        if self.path == '/api/reply':
            return self._json(reply(str(data.get('text', '')), data.get('lang', 'FR'),
                                    data.get('voice', 1), data.get('mem', 1)))
        if self.path == '/api/memory/clear':
            sql('DELETE FROM conv')
            sql('DELETE FROM facts')
            return self._json({'ok': True})
        if self.path == '/api/memory/delete':
            sql('DELETE FROM facts WHERE id=?', (int(data.get('id', 0)),))
            return self._json({'ok': True})
        self._json({'ok': False})

    def log_message(self, *a):
        pass


ThreadingHTTPServer(('127.0.0.1', 5000), H).serve_forever()
