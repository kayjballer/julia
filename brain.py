import os, time, subprocess, ssl, urllib.request


def ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception as e:
        log('certifi: ' + repr(e))
        return ssl.create_default_context()

URL = 'https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf'
SIZE = 1107409472
PORT = 8080
ST = {'stage': 'init', 'pct': 0, 'err': ''}
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
MODEL = os.path.join(DATA, 'models', 'qwen3-1.7b.gguf')
LOG = os.path.join(DATA, 'llama.log')


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
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        d = act.getApplicationInfo().nativeLibraryDir
        p = os.path.join(d, 'libllama_server.so')
        log('nativeLibraryDir=' + d + ' contenu=' + str(os.listdir(d)))
        if os.path.exists(p):
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


def ensure():
    if alive():
        ST['stage'] = 'ready'
        return
    try:
        b = binary()
        if not b:
            ST['stage'], ST['err'] = 'error', 'binaire introuvable'
            return
        ST['stage'] = 'download'
        m = get_model()
        ST['stage'] = 'start'
        out = open(LOG, 'ab')
        subprocess.Popen([b, '-m', m, '-t', '4', '-c', '2048', '-np', '1', '-fa', 'on', '-lm', 'none', '--chat-template-kwargs', '{"enable_thinking": false}',
                          '--host', '127.0.0.1', '--port', str(PORT)],
                         stdout=out, stderr=subprocess.STDOUT)
        for _ in range(120):
            if alive():
                ST['stage'] = 'ready'
                return
            time.sleep(2)
        ST['stage'], ST['err'] = 'error', 'le serveur ne repond pas'
    except Exception as e:
        ST['stage'], ST['err'] = 'error', repr(e)
        log(repr(e))
