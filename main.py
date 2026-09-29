import os, json, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
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


def _speak(text):
    try:
        tts.speak(text)
    except Exception:
        pass


def reply(text, voice):
    r = 'Tu as dit : ' + text
    if voice and tts is not None:
        threading.Thread(target=_speak, args=(r,), daemon=True).start()
    return r


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
        if u.path == '/api/listen':
            lang = parse_qs(u.query).get('lang', ['fr-FR'])[0]
            return self._json(listen(lang))
        if u.path == '/api/stt':
            return self._json(stt_state())
        super().do_GET()

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        try:
            data = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            data = {}
        if self.path == '/api/reply':
            return self._json({'reply': reply(str(data.get('text', '')), data.get('voice', 1))})
        if self.path == '/api/memory/clear':
            return self._json({'ok': True})
        self._json({'ok': False})

    def log_message(self, *a):
        pass


ThreadingHTTPServer(('127.0.0.1', 5000), H).serve_forever()
