# Service Julia : battement de coeur + journal de demarrage et d'erreur
import os, time, traceback

DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')

def w(name, txt):
    try:
        with open(os.path.join(DATA, name), 'w') as f:
            f.write(txt)
    except Exception:
        pass

w('service_boot.txt', 'demarre pid=%d t=%d' % (os.getpid(), int(time.time())))
try:
    while True:
        w('service_hb.txt', '%d %d' % (int(time.time()), os.getpid()))
        time.sleep(5)
except Exception:
    w('service_err.txt', traceback.format_exc())
