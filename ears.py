# Oreilles de Julia : mot de réveil (« dis Julia », « salut Julia ») avec Vosk.
# La classe Java org.jacques.julia.Ears écoute le micro ; ici on télécharge le modèle,
# on lit les résultats et on détecte le mot de réveil. Tout tourne dans le service.
import os, time, json, re, zipfile, urllib.request

DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
MODELS = os.path.join(DATA, 'models')
ZIP = os.path.join(MODELS, 'vosk-model-small-fr-0.22.zip')
DIR = os.path.join(MODELS, 'vosk-model-small-fr-0.22')
URL = 'https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip'
STATE = {'s': 'init', 'wakes': 0, 'heard': '', 'last_wake': 0.0}

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


def run():
    try:
        from jnius import autoclass
        Ears = autoclass('org.jacques.julia.Ears')   # préchargé dans le fil principal du service
        if not ensure_model():
            STATE['s'] = 'modele absent'
            return
        STATE['s'] = 'demarrage micro'
        r = str(Ears.start(DIR, ''))
        if r != 'ok':
            STATE['s'] = r[:90]
            return
        STATE['s'] = 'ecoute'
        while True:
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
            if kind in ('P', 'R') and t and is_wake(t) and time.time() - STATE['last_wake'] > 4:
                STATE['last_wake'] = time.time()
                STATE['wakes'] += 1
                buzz()
    except Exception as e:
        STATE['s'] = ('erreur: ' + type(e).__name__ + ' ' + str(e))[:110]
