# Oreilles de Julia : mot de réveil (« dis Julia », « salut Julia ») avec Vosk.
# La classe Java org.jacques.julia.Ears écoute le micro ; ici on télécharge le modèle,
# on lit les résultats et on détecte le mot de réveil. Tout tourne dans le service.
import os, time, json, re, zipfile, urllib.request, queue, threading

DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
MODELS = os.path.join(DATA, 'models')
ZIP = os.path.join(MODELS, 'vosk-model-small-fr-0.22.zip')
DIR = os.path.join(MODELS, 'vosk-model-small-fr-0.22')
URL = 'https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip'
STATE = {'s': 'init', 'wakes': 0, 'heard': '', 'last_wake': 0.0, 'mic': '', 'app': '?', 'conv': False, 'hf': True, 'mode': 'precis', 'mode_now': ''}

NAME = r'(julia|julie|giulia|jolie|joulia)'
WAKE = re.compile(r'\b(dis|salut|ok|okay|hey|hé|eh|oh|bonjour)\s+' + NAME + r'\b')
ALONE = re.compile(r'^\s*' + NAME + r'\s*$')


def is_wake(t):
    return bool(WAKE.search(t) or ALONE.match(t))


def _ctx():
    try:
        import brain
        return brain.ctx()
    except Exception:
        import ssl
        return ssl.create_default_context()


def ensure_model():
    if os.path.isdir(os.path.join(DIR, 'am')):
        return True
    os.makedirs(MODELS, exist_ok=True)
    STATE['s'] = 'telechargement modele'
    req = urllib.request.urlopen(URL, timeout=60, context=_ctx())
    total = int(req.headers.get('Content-Length') or 0)
    got = 0
    tmp = ZIP + '.part'
    with open(tmp, 'wb') as f:
        while True:
            b = req.read(262144)
            if not b:
                break
            f.write(b)
            got += len(b)
            if total:
                STATE['s'] = 'telechargement modele %d%%' % (got * 100 // total)
    os.replace(tmp, ZIP)
    STATE['s'] = 'decompression'
    with zipfile.ZipFile(ZIP) as z:
        z.extractall(MODELS)
    try:
        os.remove(ZIP)
    except Exception:
        pass
    return os.path.isdir(os.path.join(DIR, 'am'))


def buzz():
    # petite vibration : on sait que Julia t'a entendu sans regarder l'écran
    try:
        from jnius import autoclass
        ctx = autoclass('org.kivy.android.PythonService').mService
        ctx.getSystemService('vibrator').vibrate(200)
    except Exception:
        pass


# ---------- liaison avec l'appli : tout ce que les oreilles entendent part vers conv.py ----------
APP = 'http://127.0.0.1:5000'
Q = queue.Queue(maxsize=80)


def _post(path, obj):
    req = urllib.request.Request(APP + path, data=json.dumps(obj).encode(), headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=1.5).read() or b'{}')


def _relaunch():
    # l'appli est fermée : on la rouvre (permission « par-dessus les autres applis » requise, déjà accordée)
    try:
        from jnius import autoclass
        ctx = autoclass('org.kivy.android.PythonService').mService
        i = ctx.getPackageManager().getLaunchIntentForPackage(str(ctx.getPackageName()))
        i.addFlags(0x10000000)
        ctx.startActivity(i)
    except Exception:
        pass


def _sender():
    last_relaunch = 0.0
    while True:
        try:
            kind, text, wake, conf = Q.get(timeout=0.5)
        except queue.Empty:
            try:   # l'appli dit si une conversation est ouverte (ouverte aussi par le bouton micro)
                d = json.loads(urllib.request.urlopen(APP + '/api/conv', timeout=1.5).read())
                STATE['conv'], STATE['hf'], STATE['app'], STATE['mode'] = bool(d.get('active')), bool(d.get('hf', 1)), 'ok', d.get('mic') or STATE['mode']
            except Exception:
                STATE['app'] = 'injoignable'
            continue
        try:
            d = _post('/api/conv/ear', {'kind': kind, 'text': text, 'wake': wake, 'conf': conf})
            STATE['conv'], STATE['hf'], STATE['app'], STATE['mode'] = bool(d.get('active')), bool(d.get('hf', 1)), 'ok', d.get('mic') or STATE['mode']
        except Exception:
            STATE['app'] = 'injoignable'
            if wake and time.time() - last_relaunch > 25:
                last_relaunch = time.time()
                _relaunch()


def _forward(kind, text, wake, conf=None):
    try:
        Q.put_nowait((kind, text, wake, conf))
    except queue.Full:
        pass


def run():
    try:
        from jnius import autoclass
        Ears = autoclass('org.jacques.julia.Ears')   # préchargé dans le fil principal du service
        if not ensure_model():
            STATE['s'] = 'modele absent'
            return
        STATE['s'] = 'demarrage micro'
        STATE['mode_now'] = STATE['mode']
        r = str(Ears.start(DIR, '', STATE['mode_now']))
        if r != 'ok':
            STATE['s'] = r[:90]
            return
        STATE['s'] = 'ecoute'
        try:
            STATE['mic'] = str(Ears.info())
        except Exception:
            pass
        threading.Thread(target=_sender, daemon=True).start()
        while True:
            if STATE['mode'] != STATE['mode_now']:      # changement « Précis / Anti-écho » depuis Contrôle
                STATE['mode_now'] = STATE['mode']
                Ears.stop()
                time.sleep(0.4)
                r = str(Ears.start(DIR, '', STATE['mode_now']))
                STATE['s'] = 'ecoute' if r == 'ok' else r[:90]
                try:
                    STATE['mic'] = str(Ears.info())
                except Exception:
                    pass
            m = Ears.poll()
            if m is None:
                time.sleep(0.05)
                continue
            kind, _, js = str(m).partition('|')
            try:
                d = json.loads(js)
            except Exception:
                continue
            if kind == 'E':
                STATE['s'] = 'erreur micro: ' + str(d.get('err', ''))[:70]
                continue
            t = (d.get('text') or d.get('partial') or '').strip().lower()
            if t:
                STATE['heard'] = t[-40:]
            wake = False
            if kind in ('P', 'R') and t and is_wake(t) and time.time() - STATE['last_wake'] > 4 and not STATE['conv']:
                STATE['last_wake'] = time.time()
                STATE['wakes'] += 1
                wake = True
                if STATE['hf']:
                    buzz()
            conf = None
            if kind == 'R':
                cs = [w.get('conf') for w in (d.get('result') or []) if isinstance(w, dict) and w.get('conf') is not None]
                if cs:
                    conf = sum(cs) / len(cs)
            if kind in ('P', 'R') and t and (wake or STATE['conv']):
                _forward(kind, t, wake, conf)
    except Exception as e:
        STATE['s'] = ('erreur: ' + type(e).__name__ + ' ' + str(e))[:110]
