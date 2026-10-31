# Service Julia : battement de coeur + gardien des serveurs du modele
import os, time, traceback, threading

DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')

def w(name, txt):
    try:
        with open(os.path.join(DATA, name), 'w') as f:
            f.write(txt)
    except Exception:
        pass

w('service_boot.txt', 'demarre pid=%d t=%d' % (os.getpid(), int(time.time())))
try:
    import brain
except Exception:
    brain = None
    w('service_err.txt', 'import brain: ' + traceback.format_exc())

try:
    import overlay
    threading.Thread(target=overlay.run, daemon=True).start()
except Exception:
    w('service_err.txt', 'overlay: ' + traceback.format_exc()[-250:])

def port_proc(port):
    # un processus llama_server ecoute-t-il deja sur ce port ?
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
                return True
    except Exception:
        pass
    return False

def model_ready():
    try:
        return os.path.getsize(brain.MODEL) >= brain.SIZE
    except Exception:
        return False

miss_llm = miss_emb = 0
last_llm = last_emb = 0
while True:
    try:
        w('service_hb.txt', '%d %d' % (int(time.time()), os.getpid()))
        if brain and model_ready():
            now = time.time()
            down = (not port_proc(8080)) and (not brain.alive())
            miss_llm = miss_llm + 1 if down else 0
            if miss_llm >= 2 and now - last_llm > 60:
                last_llm = now
                w('service_srv.txt', 'relance modele %d' % int(now))
                threading.Thread(target=brain.ensure, daemon=True).start()
            if (not down) and os.path.exists(brain.EMB_MODEL):
                edown = (not port_proc(8082)) and (not brain.emb_alive())
                miss_emb = miss_emb + 1 if edown else 0
                if miss_emb >= 2 and now - last_emb > 60:
                    last_emb = now
                    threading.Thread(target=brain.start_emb, daemon=True).start()
    except Exception:
        w('service_err.txt', traceback.format_exc()[-300:])
    time.sleep(5)
