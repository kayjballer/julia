# Niko : l'appli ne fait que trois choses : demander le micro, lancer le service, afficher l'écran.
import os, json, threading, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
ANDROID = 'ANDROID_ARGUMENT' in os.environ or 'ANDROID_PRIVATE' in os.environ
STATE_FILE = os.path.join(DATA, 'niko_state.json')
PERMS = ['android.permission.RECORD_AUDIO', 'android.permission.POST_NOTIFICATIONS']
SVC = {'started': False}


def log(m):
    try:
        open(os.path.join(DATA, 'main.log'), 'a').write(time.strftime('%H:%M:%S ') + m + '\n')
    except Exception:
        pass


def start_service():
    if SVC['started']:
        return
    try:
        from jnius import autoclass
        from android import mActivity
        Service = autoclass('org.jacques.julia.ServiceJulia')
        try:
            Service.start(mActivity, '', 'Niko', "J'écoute", '')
        except Exception:
            Service.start(mActivity, '')
        SVC['started'] = True
    except Exception as e:
        log('start_service: ' + repr(e))


def on_perms(perms, grants):
    try:
        mic_ok = any(p.endswith('RECORD_AUDIO') and g for p, g in zip(perms, grants))
    except Exception:
        mic_ok = False
    if mic_ok:
        start_service()
    else:
        log('micro refuse')


def ask_permissions():
    try:
        from android.permissions import request_permissions
        request_permissions(PERMS, on_perms)
    except Exception as e:
        log('permissions: ' + repr(e))


def read_state():
    try:
        st = json.load(open(STATE_FILE))
        if time.time() - os.path.getmtime(STATE_FILE) > 20:
            st = dict(st, phase='demarrage', status='le service ne repond plus')
        return st
    except Exception:
        return {'phase': 'demarrage', 'awake': False, 'level': 0.0, 'heard': '', 'said': '', 'status': 'demarrage'}


def tail(name, n=25):
    try:
        return ''.join(open(os.path.join(DATA, name), encoding='utf-8', errors='replace').readlines()[-n:]).strip()
    except Exception:
        return '(vide)'


def debug_text():
    st = read_state()
    return '\n'.join([
        'phase: %s | eveille: %s | volume: %.3f' % (st.get('phase'), st.get('awake'), st.get('level') or 0),
        'micro: %s' % st.get('mic', ''),
        'stats: %s' % st.get('stats', ''),
        'statut: %s' % st.get('status', ''),
        '--- service_info', tail('service_info.txt', 3),
        '--- service_err', tail('service_err.txt', 6),
        '--- niko.log', tail('niko.log', 30),
        '--- whisper.log', tail('whisper.log', 12),
        '--- main.log', tail('main.log', 8)])


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def do_GET(self):
        if self.path.split('?')[0] == '/api/state':
            st = read_state()
            try:
                import brain
                if brain.ST.get('stage') == 'download':
                    st['status'] = 'telechargement du modele %d%%' % brain.ST.get('pct', 0)
                    st['phase'] = 'demarrage'
            except Exception:
                pass
            b = json.dumps(st).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if self.path.split('?')[0] == '/api/debug':
            b = debug_text().encode('utf-8', 'replace')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


if not os.environ.get('JULIA_NOSERVER'):
    if ANDROID:
        ask_permissions()
        try:
            import brain      # télécharge le modèle si besoin ; le service lance ensuite Qwen et Whisper
            threading.Thread(target=brain.ensure, daemon=True).start()
        except Exception as e:
            log('brain: ' + repr(e))
    ThreadingHTTPServer(('127.0.0.1', 5000), H).serve_forever()
