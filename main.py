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
        '--- main.log', tail('main.log', 8)])


IMPORT = {'bound': False}


def _display_name(cr, uri):
    try:
        cur = cr.query(uri, None, None, None, None)
        if cur is not None:
            try:
                i = cur.getColumnIndex('_display_name')
                if i >= 0 and cur.moveToFirst():
                    return cur.getString(i)
            finally:
                cur.close()
    except Exception:
        pass
    return ''


def on_picked(request, result, intent):
    if request != 4242:
        return
    try:
        if result != -1 or intent is None:
            return
        import re as _re
        from jnius import autoclass
        from android import mActivity
        File = autoclass('java.io.File')
        FOS = autoclass('java.io.FileOutputStream')
        FileUtils = autoclass('android.os.FileUtils')
        cr = mActivity.getContentResolver()
        uris = []
        clip = intent.getClipData()
        if clip is not None:
            for i in range(clip.getItemCount()):
                uris.append(clip.getItemAt(i).getUri())
        else:
            u = intent.getData()
            if u is not None:
                uris.append(u)
        n_ok = 0
        for u in uris:
            name = os.path.basename(_display_name(cr, u) or '') or ('fichier_%d' % int(time.time()))
            name = _re.sub(r'[^A-Za-z0-9._ -]', '_', name)[:80]
            ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''
            sub = 'docs' if ext in ('txt', 'md') else 'media'
            d = os.path.join(DATA, sub)
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, name)
            ins = cr.openInputStream(u)
            outs = FOS(File(dest))
            try:
                FileUtils.copy(ins, outs)
            finally:
                ins.close()
                outs.close()
            n_ok += 1
        if n_ok:
            open(os.path.join(DATA, 'kb_reindex.flag'), 'w').write('1')
        log('import: %d fichier(s)' % n_ok)
    except Exception as e:
        log('import: ' + repr(e))


def open_picker():
    try:
        from jnius import autoclass
        from android import mActivity, activity
        if not IMPORT['bound']:
            activity.bind(on_activity_result=on_picked)
            IMPORT['bound'] = True
        Intent = autoclass('android.content.Intent')
        it = Intent(Intent.ACTION_OPEN_DOCUMENT)
        it.addCategory(Intent.CATEGORY_OPENABLE)
        it.setType('*/*')
        it.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)
        mActivity.startActivityForResult(it, 4242)
        return True
    except Exception as e:
        log('picker: ' + repr(e))
        return False


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def do_POST(self):
        if self.path.split('?')[0] == '/api/say':
            n = int(self.headers.get('Content-Length', 0) or 0)
            t = self.rfile.read(min(n, 2000)).decode('utf-8', 'replace').strip()
            if t:
                tmp = os.path.join(DATA, 'niko_say.tmp')
                with open(tmp, 'w', encoding='utf-8') as f:
                    f.write(t)
                os.replace(tmp, os.path.join(DATA, 'niko_say.txt'))
            self.send_response(200)
        else:
            self.send_response(404)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        if self.path.split('?')[0] == '/api/import':
            b = json.dumps({'ok': open_picker()}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
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
        if self.path.split('?')[0] in ('/api/ecoute', '/api/wav'):
            import re as _re, glob as _glob
            adir = os.path.join(DATA, 'audio')
            if self.path.startswith('/api/wav'):
                m = _re.search(r'[?&]f=([A-Za-z0-9_.]+)', self.path)
                name = m.group(1) if m else ''
                fp = os.path.join(adir, name)
                if not _re.fullmatch(r'seg_\d+\.wav', name) or not os.path.isfile(fp):
                    self.send_response(404)
                    self.end_headers()
                    return
                b = open(fp, 'rb').read()
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            rows = ''
            fl = sorted(_glob.glob(os.path.join(adir, 'seg_*.wav')), key=os.path.getmtime, reverse=True)
            for fp in fl[:12]:
                nm = os.path.basename(fp)
                rows += ('<p>%s &middot; %.1f s<br><audio controls preload="none" src="/api/wav?f=%s"></audio><br>'
                         '<a download="%s" href="/api/wav?f=%s">telecharger</a></p>') % (
                         time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(fp))),
                         (os.path.getsize(fp) - 44) / 32000.0, nm, nm, nm)
            page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<body style="font:16px sans-serif;background:#111;color:#eee;padding:12px">'
                    '<h3>Dernieres phrases entendues par Niko</h3>'
                    '<p>C\'est le son envoye a Vosk, apres nettoyage. Les plus recentes en premier.</p>'
                    + (rows or '<p>(aucune pour l\'instant)</p>'))
            b = page.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
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
            import brain      # télécharge le modèle si besoin ; le service lance ensuite Qwen
            threading.Thread(target=brain.ensure, daemon=True).start()
        except Exception as e:
            log('brain: ' + repr(e))
    ThreadingHTTPServer(('127.0.0.1', 5000), H).serve_forever()
