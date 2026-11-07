import os, time, subprocess, ssl, json, threading, urllib.request, urllib.error


def ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception as e:
        log('certifi: ' + repr(e))
        return ssl.create_default_context()

URL = 'https://huggingface.co/HauhauCS/Qwen3.5-2B-Uncensored-HauhauCS-Aggressive/resolve/main/Qwen3.5-2B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf'
SIZE = 1270808032
PORT = 8080
ST = {'stage': 'init', 'pct': 0, 'err': ''}
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
MODEL = os.path.join(DATA, 'models', 'qwen35-2b-unc.gguf')
LOG = os.path.join(DATA, 'llama.log')
EMB_REPO = 'PeterAM4/Qwen3-Embedding-0.6B-GGUF'
EMB_PORT = 8082
EMB = {'stage': 'off', 'pct': 0, 'err': ''}
EMB_MODEL = os.path.join(DATA, 'models', 'qwen3-emb-q4.gguf')
EMB_LOG = os.path.join(DATA, 'emb.log')
WH_PORT = 8083
WH = {'stage': 'off', 'pct': 0, 'err': ''}
WH_FILE = 'ggml-base-q5_1.bin'
WH_URL = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/' + WH_FILE
WH_MODEL = os.path.join(DATA, 'models', WH_FILE)
WH_LOG = os.path.join(DATA, 'whisper.log')
WH_ARGS = [['-t', '4', '-l', 'fr', '-bo', '1', '-bs', '1', '-ac', '640'], ['-t', '4', '-l', 'fr', '-bo', '1', '-bs', '1'], ['-t', '4', '-l', 'fr']]   # 2e jeu : au cas ou un option serait inconnue
WH_LOCK = threading.Lock()


def log(m):
    try:
        open(os.path.join(DATA, 'brain.log'), 'a').write(time.strftime('%H:%M:%S ') + m + '\n')
    except Exception:
        pass


def binary():
    p = os.environ.get('JULIA_LLAMA_BIN')
    if p and os.path.exists(p):
        return p
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity or autoclass('org.kivy.android.PythonService').mService
        d = act.getApplicationInfo().nativeLibraryDir
        p = os.path.join(d, 'libllama_server.so')
        log('nativeLibraryDir=' + d + ' contenu=' + str(os.listdir(d)))
        if os.path.exists(p):
            try:
                open(os.path.join(DATA, 'llama_bin.txt'), 'w').write(p)
            except Exception:
                pass
            return p
    except Exception as e:
        log('jnius: ' + repr(e))
    return None


def alive():
    try:
        urllib.request.urlopen('http://127.0.0.1:%d/health' % PORT, timeout=2).read()
        return True
    except Exception:
        return False


def get_model():
    for p in (MODEL, os.path.expanduser('~/qwen.gguf')):
        if os.path.exists(p) and os.path.getsize(p) >= SIZE:
            return p
    os.makedirs(os.path.dirname(MODEL), exist_ok=True)
    part = MODEL + '.part'
    while True:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have >= SIZE:
            break
        try:
            req = urllib.request.Request(URL, headers={'Range': 'bytes=%d-' % have})
            with urllib.request.urlopen(req, timeout=30, context=ctx()) as r:
                mode = 'ab'
                if have and r.status != 206:
                    mode, have = 'wb', 0
                with open(part, mode) as f:
                    while True:
                        b = r.read(1 << 16)
                        if not b:
                            break
                        f.write(b)
                        have += len(b)
                        ST['pct'] = int(100 * have / SIZE)
        except Exception as e:
            ST['err'] = repr(e)
            log('telechargement: ' + repr(e))
            time.sleep(5)
    os.rename(part, MODEL)
    return MODEL


def emb_alive():
    try:
        urllib.request.urlopen('http://127.0.0.1:%d/health' % EMB_PORT, timeout=2).read()
        return True
    except Exception:
        return False


def emb_source():
    d = json.loads(urllib.request.urlopen('https://huggingface.co/api/models/' + EMB_REPO, timeout=30, context=ctx()).read())
    for x in d['siblings']:
        n = x['rfilename']
        if 'Q4_K_M' in n and n.endswith('.gguf'):
            url = 'https://huggingface.co/%s/resolve/main/%s' % (EMB_REPO, n)
            r = urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=0-0'}), timeout=30, context=ctx())
            return url, int(r.headers['Content-Range'].split('/')[-1])
    raise RuntimeError('modele embeddings introuvable')


def emb_model():
    if os.path.exists(EMB_MODEL):
        return EMB_MODEL
    url, size = emb_source()
    part, fails = EMB_MODEL + '.part', 0
    os.makedirs(os.path.dirname(EMB_MODEL), exist_ok=True)
    while True:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have >= size:
            break
        try:
            req = urllib.request.Request(url, headers={'Range': 'bytes=%d-' % have})
            with urllib.request.urlopen(req, timeout=30, context=ctx()) as r:
                mode = 'ab'
                if have and r.status != 206:
                    mode, have = 'wb', 0
                with open(part, mode) as f:
                    while True:
                        b = r.read(1 << 16)
                        if not b:
                            break
                        f.write(b)
                        have += len(b)
                        EMB['pct'] = int(100 * have / size)
        except Exception as e:
            fails += 1
            EMB['err'] = repr(e)
            log('embeddings telechargement: ' + repr(e))
            if fails > 40:
                raise
            time.sleep(5)
    os.rename(part, EMB_MODEL)
    return EMB_MODEL


def svc_fresh():
    try:
        t = int(open(os.path.join(DATA, 'service_hb.txt')).read().split()[0])
        return time.time() - t < 20
    except Exception:
        return False


def wait_service():
    for _ in range(15):
        if svc_fresh():
            return True
        time.sleep(1)
    return False


def ensure_emb():
    if os.environ.get('JULIA_SVC') != '1' and svc_fresh():
        return
    try:
        if emb_alive():
            EMB['stage'] = 'ready'
            return
        b = binary()
        if not b:
            EMB['stage'], EMB['err'] = 'error', 'binaire introuvable'
            return
        EMB['stage'] = 'download'
        m = emb_model()
        EMB['stage'] = 'start'
        subprocess.Popen([b, '-m', m, '--embedding', '--pooling', 'last', '-c', '512', '-np', '1', '-t', '4', '-lm', 'none',
                          '--host', '127.0.0.1', '--port', str(EMB_PORT)], stdout=open(EMB_LOG, 'ab'), stderr=subprocess.STDOUT)
        for _ in range(60):
            if emb_alive():
                EMB['stage'] = 'ready'
                return
            time.sleep(2)
        EMB['stage'], EMB['err'] = 'error', 'serveur embeddings muet'
    except Exception as e:
        EMB['stage'], EMB['err'] = 'error', repr(e)
        log('embeddings: ' + repr(e))


def wh_binary():
    b = binary()
    if not b:
        return None
    p = os.path.join(os.path.dirname(b), 'libwhisper_server.so')
    return p if os.path.exists(p) else None


def wh_alive():
    try:
        urllib.request.urlopen('http://127.0.0.1:%d/' % WH_PORT, timeout=2).read()
        return True
    except urllib.error.HTTPError:
        return True      # le serveur repond (meme par une erreur) : il est vivant
    except Exception:
        return False


def wh_model():
    if os.path.exists(WH_MODEL) and os.path.getsize(WH_MODEL) > 30000000:
        return WH_MODEL
    r = urllib.request.urlopen(urllib.request.Request(WH_URL, headers={'Range': 'bytes=0-0'}), timeout=30, context=ctx())
    size = int(r.headers['Content-Range'].split('/')[-1])
    part, fails = WH_MODEL + '.part', 0
    os.makedirs(os.path.dirname(WH_MODEL), exist_ok=True)
    while True:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have >= size:
            break
        try:
            req = urllib.request.Request(WH_URL, headers={'Range': 'bytes=%d-' % have})
            with urllib.request.urlopen(req, timeout=30, context=ctx()) as r:
                mode = 'ab'
                if have and r.status != 206:
                    mode, have = 'wb', 0
                with open(part, mode) as f:
                    while True:
                        b = r.read(1 << 16)
                        if not b:
                            break
                        f.write(b)
                        have += len(b)
                        WH['pct'] = int(100 * have / size)
        except Exception as e:
            fails += 1
            WH['err'] = repr(e)
            log('whisper telechargement: ' + repr(e))
            if fails > 40:
                raise
            time.sleep(5)
    os.rename(part, WH_MODEL)
    return WH_MODEL


def ensure_wh():
    if os.environ.get('JULIA_SVC') != '1' and svc_fresh():
        return
    if not WH_LOCK.acquire(False):
        return
    try:
        if wh_alive():
            WH['stage'] = 'ready'
            return
        b = wh_binary()
        if not b:
            WH['stage'], WH['err'] = 'error', 'libwhisper_server.so absent'
            return
        WH['stage'] = 'download'
        m = wh_model()
        WH['stage'] = 'start'
        for args in WH_ARGS:
            p = subprocess.Popen([b, '-m', m] + args + ['--host', '127.0.0.1', '--port', str(WH_PORT)],
                                 stdout=open(WH_LOG, 'ab'), stderr=subprocess.STDOUT)
            for _ in range(45):
                if wh_alive():
                    WH['stage'] = 'ready'
                    return
                if p.poll() is not None:
                    break
                time.sleep(1)
            if p.poll() is None:
                p.kill()
            log('whisper: arret avec ' + ' '.join(args))
        WH['stage'], WH['err'] = 'error', 'serveur whisper muet (voir whisper.log)'
    except Exception as e:
        WH['stage'], WH['err'] = 'error', repr(e)
        log('whisper: ' + repr(e))
    finally:
        WH_LOCK.release()


def start_emb():
    threading.Thread(target=ensure_wh, daemon=True).start()   # Niko : plus de serveur d'embeddings


def ensure():
    if alive():
        ST['stage'] = 'ready'
        start_emb()
        return
    try:
        b = binary()
        if not b:
            ST['stage'], ST['err'] = 'error', 'binaire introuvable'
            return
        ST['stage'] = 'download'
        m = get_model()
        ST['stage'] = 'start'
        if os.environ.get('JULIA_SVC') != '1' and wait_service():
            for _ in range(60):
                if alive():
                    ST['stage'] = 'ready'
                    start_emb()
                    return
                time.sleep(1)
        out = open(LOG, 'ab')
        subprocess.Popen([b, '-m', m, '-t', '4', '-c', '4096', '-np', '2', '-fa', 'on', '-lm', 'none', '--chat-template-kwargs', '{"enable_thinking": false}',
                          '--host', '127.0.0.1', '--port', str(PORT)],
                         stdout=out, stderr=subprocess.STDOUT)
        for _ in range(120):
            if alive():
                ST['stage'] = 'ready'
                start_emb()
                return
            time.sleep(2)
        ST['stage'], ST['err'] = 'error', 'le serveur ne repond pas'
    except Exception as e:
        ST['stage'], ST['err'] = 'error', repr(e)
        log(repr(e))
