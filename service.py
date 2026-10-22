# Service Julia (étape A de l'écoute continue) : pour l'instant, un battement de coeur
# qui prouve que le service tourne, même quand la fenêtre de l'app est fermée.
import os, time, json

DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
PATH = os.path.join(DATA, 'service_hb.json')
T0 = time.time()

while True:
    try:
        with open(PATH, 'w') as f:
            json.dump({'t': time.time(), 'since': T0, 'pid': os.getpid()}, f)
    except Exception:
        pass
    time.sleep(5)
