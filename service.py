# Service Julia : battement de coeur, gardien du modele, popup flottant (fil principal)
import os, time, traceback, threading
os.environ['JULIA_SVC'] = '1'
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')

def w(name, txt):
    try:
        with open(os.path.join(DATA, name), 'w') as f:
            f.write(txt)
    except Exception:
        pass

w('service_boot.txt', 'demarre pid=%d t=%d' % (os.getpid(), int(time.time())))
w('service_err.txt', '')
try:
    from jnius import autoclass as _ac
    for _c in ('org.kivy.android.PythonService', 'org.kivy.android.PythonActivity', 'android.provider.Settings',
               'android.os.Handler', 'android.os.Looper', 'android.webkit.WebView',
               'android.view.WindowManager$LayoutParams', 'android.app.ActivityManager$RunningAppProcessInfo'):
        try:
            _ac(_c)
        except Exception:
            pass
except Exception:
    pass
try:
    _p = open(os.path.join(DATA, 'llama_bin.txt')).read().strip()
    if _p and os.path.exists(_p):
        os.environ['JULIA_LLAMA_BIN'] = _p
except Exception:
    pass
try:
    import brain
except Exception:
    brain = None
    w('service_err.txt', 'import brain: ' + traceback.format_exc()[-250:])
try:
    import overlay as OV
except Exception:
    OV = None
    w('service_err.txt', 'overlay: ' + traceback.format_exc()[-250:])

def llama_pid(port):
    tok = b'\x00%d\x00' % port
    try:
        for p in os.listdir('/proc'):
            if not p.isdigit():
                continue
            try:
                c = open('/proc/%s/cmdline' % p, 'rb').read()
            except Exception:
                continue
            if b'llama_server' in c and tok in c + b'\x00':
                return int(p)
    except Exception:
        pass
    return 0

def ppid(pid):
    try:
        return int(open('/proc/%d/stat' % pid).read().rsplit(')', 1)[1].split()[1])
    except Exception:
        return 0

def model_ready():
    try:
        return os.path.getsize(brain.MODEL) >= brain.SIZE
    except Exception:
        return False

G = {'th': None, 'th2': None, 'miss_emb': 0, 'last_llm': 0, 'last_emb': 0}

def guard():
    while True:
        try:
            w('service_hb.txt', '%d %d' % (int(time.time()), os.getpid()))
            cerv = 'init'
            if brain is None:
                cerv = 'brain absent'
            elif not model_ready():
                cerv = 'attend le modele'
            else:
                now = time.time()
                pid = llama_pid(8080)
                up = bool(pid) or brain.alive()
                if up:
                    cerv = 'actif (service)' if (pid and ppid(pid) == os.getpid()) else 'actif (appli)'
                elif (G['th'] is None or not G['th'].is_alive()) and now - G['last_llm'] > 45:
                    G['last_llm'] = now
                    G['th'] = threading.Thread(target=brain.ensure, daemon=True)
                    G['th'].start()
                    cerv = 'lancement'
                else:
                    cerv = 'demarrage'
                if brain.ST.get('stage') == 'error':
                    cerv += ' [' + str(brain.ST.get('err'))[:80] + ']'
                if up and os.path.exists(brain.EMB_MODEL):
                    edown = (not llama_pid(8082)) and (not brain.emb_alive())
                    G['miss_emb'] = G['miss_emb'] + 1 if edown else 0
                    if G['miss_emb'] >= 2 and (G['th2'] is None or not G['th2'].is_alive()) and now - G['last_emb'] > 60:
                        G['last_emb'] = now
                        G['th2'] = threading.Thread(target=brain.start_emb, daemon=True)
                        G['th2'].start()
            pop = OV.STATE['s'] if (OV is not None and hasattr(OV, 'STATE')) else 'absent'
            w('service_info.txt', 'cerveau: %s | popup: %s' % (cerv, pop))
        except Exception:
            w('service_err.txt', traceback.format_exc()[-300:])
        time.sleep(5)

threading.Thread(target=guard, daemon=True).start()
if OV is not None:
    OV.run()
while True:
    time.sleep(60)
