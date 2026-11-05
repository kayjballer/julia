#!/usr/bin/env python3
# Patch Julia : Whisper small (whisper-server) + moins de silences.
# Usage : cd ~/julia && python3 apply_whisper.py
# Tout est verifie AVANT d'ecrire : si un morceau ne correspond pas, rien n'est modifie.
import os, sys

EDITS = {}   # chemin -> liste de (ancien, nouveau)


def rep(path, old, new):
    EDITS.setdefault(path, []).append((old, new))


# ------------------------------------------------------------------ brain.py
rep('brain.py', "import os, time, subprocess, ssl, json, threading, urllib.request\n",
    "import os, time, subprocess, ssl, json, threading, urllib.request, urllib.error\n")

rep('brain.py', "EMB_LOG = os.path.join(DATA, 'emb.log')\n", """EMB_LOG = os.path.join(DATA, 'emb.log')
WH_PORT = 8083
WH = {'stage': 'off', 'pct': 0, 'err': ''}
WH_FILE = 'ggml-small-q5_1.bin'
WH_URL = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/' + WH_FILE
WH_MODEL = os.path.join(DATA, 'models', WH_FILE)
WH_LOG = os.path.join(DATA, 'whisper.log')
WH_ARGS = [['-t', '4', '-l', 'fr', '-bo', '1', '-bs', '1'], ['-t', '4', '-l', 'fr']]   # 2e jeu : au cas ou un option serait inconnue
WH_LOCK = threading.Lock()
""")

rep('brain.py', "def start_emb():\n    threading.Thread(target=ensure_emb, daemon=True).start()\n", '''def wh_binary():
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
    if os.path.exists(WH_MODEL) and os.path.getsize(WH_MODEL) > 100000000:
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
    threading.Thread(target=ensure_wh, daemon=True).start()
    threading.Thread(target=ensure_emb, daemon=True).start()
''')

# ---------------------------------------------------------------- service.py
rep('service.py', "G = {'th': None, 'th2': None, 'miss_emb': 0, 'last_llm': 0, 'last_emb': 0}\n",
    "G = {'th': None, 'th2': None, 'th3': None, 'miss_emb': 0, 'miss_wh': 0, 'last_llm': 0, 'last_emb': 0, 'last_wh': 0, 'wh': 'attend'}\n")
rep('service.py', "def llama_pid(port):\n", "def llama_pid(port, name=b'llama_server'):\n")
rep('service.py', "            if b'llama_server' in c and tok in c + b'\\x00':\n",
    "            if name in c and tok in c + b'\\x00':\n")
rep('service.py', """                        G['th2'] = threading.Thread(target=brain.start_emb, daemon=True)
                        G['th2'].start()
""", """                        G['th2'] = threading.Thread(target=brain.start_emb, daemon=True)
                        G['th2'].start()
                if up and os.path.exists(brain.WH_MODEL):
                    wdown = (not llama_pid(8083, b'whisper_server')) and (not brain.wh_alive())
                    G['wh'] = 'arret' if wdown else 'actif'
                    G['miss_wh'] = G['miss_wh'] + 1 if wdown else 0
                    if G['miss_wh'] >= 2 and (G['th3'] is None or not G['th3'].is_alive()) and now - G['last_wh'] > 60:
                        G['last_wh'] = now
                        G['th3'] = threading.Thread(target=brain.ensure_wh, daemon=True)
                        G['th3'].start()
                elif up and brain.WH.get('stage') == 'error':
                    G['wh'] = 'erreur ' + str(brain.WH.get('err'))[:50]
""")
rep('service.py', "            w('service_info.txt', 'cerveau: %s | popup: %s | %s' % (cerv, pop, ear))\n",
    "            w('service_info.txt', 'cerveau: %s | whisper: %s | popup: %s | %s' % (cerv, G['wh'], pop, ear))\n")

# ------------------------------------------------------------------- ears.py
rep('ears.py', "STATE = {'s': 'init', 'wakes': 0, 'heard': '', 'last_wake': 0.0, 'mic': '', 'app': '?', 'conv': False, 'hf': True, 'mode': 'precis', 'mode_now': ''}\n",
    "STATE = {'s': 'init', 'wakes': 0, 'heard': '', 'last_wake': 0.0, 'mic': '', 'app': '?', 'conv': False, 'hf': True, 'mode': 'precis', 'mode_now': '', 'cap': False, 'wav': ''}\nSEGDIR = os.path.join(DATA, 'seg')\n")
rep('ears.py', "            kind, text, wake, conf = Q.get(timeout=0.5)\n", "            kind, text, wake, conf, wav = Q.get(timeout=0.5)\n")
rep('ears.py', "            d = _post('/api/conv/ear', {'kind': kind, 'text': text, 'wake': wake, 'conf': conf})\n",
    "            d = _post('/api/conv/ear', {'kind': kind, 'text': text, 'wake': wake, 'conf': conf, 'wav': wav})\n")
rep('ears.py', "def _forward(kind, text, wake, conf=None):\n    try:\n        Q.put_nowait((kind, text, wake, conf))\n",
    "def _forward(kind, text, wake, conf=None, wav=''):\n    try:\n        Q.put_nowait((kind, text, wake, conf, wav))\n")
rep('ears.py', "        STATE['s'] = 'demarrage micro'\n        STATE['mode_now'] = STATE['mode']\n",
    "        STATE['s'] = 'demarrage micro'\n        STATE['mode_now'] = STATE['mode']\n        try:\n            os.makedirs(SEGDIR, exist_ok=True)\n            Ears.setDir(SEGDIR)\n        except Exception:\n            pass\n")
rep('ears.py', "        while True:\n            if STATE['mode'] != STATE['mode_now']:",
    """        while True:
            cap = bool(STATE['conv']) or (time.time() - STATE['last_wake'] < 10)   # on garde l'audio des phrases pour Whisper
            if cap != STATE['cap']:
                STATE['cap'] = cap
                try:
                    Ears.setCapture(cap)
                except Exception:
                    pass
            if STATE['mode'] != STATE['mode_now']:""")
rep('ears.py', "            if kind == 'E':\n                STATE['s'] = 'erreur micro: ' + str(d.get('err', ''))[:70]\n                continue\n",
    "            if kind == 'E':\n                STATE['s'] = 'erreur micro: ' + str(d.get('err', ''))[:70]\n                continue\n            if kind == 'W':\n                STATE['wav'] = str(d.get('path', ''))\n                continue\n")
rep('ears.py', "            if kind in ('P', 'R') and t and (wake or STATE['conv']):\n                _forward(kind, t, wake, conf)\n",
    "            wav = ''\n            if kind == 'R':\n                wav, STATE['wav'] = STATE['wav'], ''\n            if kind in ('P', 'R') and t and (wake or STATE['conv']):\n                _forward(kind, t, wake, conf, wav)\n")

# ------------------------------------------------------------------- conv.py
rep('conv.py', "import re, time, threading, unicodedata, collections, difflib\n",
    "import re, time, threading, unicodedata, collections, difflib, io, json, wave, urllib.request\n")
rep('conv.py', "'pause': 'normale', 'conf': 0.45, 'mic': 'precis', 'barge': 0}\nPAUSE = {'courte': 0.8, 'normale': 1.3, 'longue': 2.0}",
    "'pause': 'courte', 'conf': 0.45, 'mic': 'precis', 'barge': 0, 'asr': 1}\nPAUSE = {'vive': 0.55, 'courte': 0.8, 'normale': 1.3, 'longue': 2.0}")
rep('conv.py', "'buf': [], 'buf_conf': [], 'buf_t': 0.0,", "'buf': [], 'buf_conf': [], 'buf_wav': [], 'asr': 0, 'buf_t': 0.0,")
rep('conv.py', "    for k in ('mem', 'web', 'hf', 'barge'):\n", "    for k in ('mem', 'web', 'hf', 'barge', 'asr'):\n")
rep('conv.py', "def ear(kind, text, wake=False, conf=None):\n", "def ear(kind, text, wake=False, conf=None, wav=''):\n")
rep('conv.py', "            if stop or (BYE.search(ws) and len(ws.split()) <= 6):\n                S['buf'], S['buf_conf'] = [], []\n",
    "            if stop or (BYE.search(ws) and len(ws.split()) <= 6):\n                S['buf'], S['buf_conf'], S['buf_wav'] = [], [], []\n")
rep('conv.py', "                S['buf'].append(text)\n                S['buf_conf'].append(conf)\n",
    "                S['buf'].append(text)\n                S['buf_conf'].append(conf)\n                if wav and wav.endswith('.wav') and '/seg/seg_' in wav:\n                    S['buf_wav'].append(wav)\n")
rep('conv.py', "S.update(active=False, phase='idle', buf=[], buf_conf=[])", "S.update(active=False, phase='idle', buf=[], buf_conf=[], buf_wav=[])")
rep('conv.py', "                if not S['active']:\n                    S['buf'], S['buf_conf'] = [], []\n",
    "                if not S['active']:\n                    S['buf'], S['buf_conf'], S['buf_wav'] = [], [], []\n")
rep('conv.py', "    return (not S['buf']) and now - S['buf_t'] > 1.0", "    return (not S['buf']) and not S['asr'] and now - S['buf_t'] > 1.0")
rep('conv.py', "                    elif any(j['mode'] == 'stream' and j['st'] != 'prête' for j in J):\n                        S['phase'] = 'thinking'",
    "                    elif S['asr'] or any(j['mode'] == 'stream' and j['st'] != 'prête' for j in J):\n                        S['phase'] = 'thinking'")
rep('conv.py', "                    if not J and not b and not S['buf'] and now - S['last'] > IDLE_S:",
    "                    if not J and not b and not S['buf'] and not S['asr'] and now - S['last'] > IDLE_S:")
rep('conv.py', """def _flush():
    if not S['buf']:
        return
    text = ' '.join(S['buf'])
    cs = [x for x in S['buf_conf'] if x is not None]
    conf = (sum(cs) / len(cs)) if cs else None
    S['buf'], S['buf_conf'] = [], []
    _request(text, conf)
""", '''# ---------- Whisper : retranscrit toute la phrase (audio recolle) au lieu du texte de Vosk ----------
WH_URL = 'http://127.0.0.1:8083/inference'
HALLU = re.compile(r"sous[- ]?titr|amara\\.org|merci d'avoir (?:regard|suivi)", re.I)
WAKE_P = re.compile(r"^\\s*(?:(?:dis|salut|ok|okay|hey|hé|eh|oh|bonjour)[\\s,.!?]+)?(?:julia|julie|giulia|jolie|joulia)\\b[\\s,.!?]*", re.I)


def _wav_join(paths):
    out, fmt = [], None
    for p in paths:
        try:
            with wave.open(p, 'rb') as w:
                f = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                if fmt is None:
                    fmt = f
                if f != fmt:
                    continue
                out.append(w.readframes(w.getnframes()))
        except Exception:
            continue
    if not out:
        return None
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(fmt[0])
        w.setsampwidth(fmt[1])
        w.setframerate(fmt[2])
        w.writeframes((b'\\x00\\x00' * 3200).join(out))      # 0,2 s de silence entre deux morceaux
    return buf.getvalue()


def _asr(paths):
    wav = _wav_join(paths)
    if not wav:
        return None
    lang = (CFG['lang'] or 'fr')[:2].lower()
    if not lang.isalpha():
        lang = 'auto'
    b = '----julia%d' % int(time.time() * 1000)
    body = b''
    for k, v in (('temperature', '0.0'), ('temperature_inc', '0.0'), ('response_format', 'json'), ('language', lang)):
        body += ('--%s\\r\\nContent-Disposition: form-data; name="%s"\\r\\n\\r\\n%s\\r\\n' % (b, k, v)).encode()
    body += ('--%s\\r\\nContent-Disposition: form-data; name="file"; filename="a.wav"\\r\\nContent-Type: audio/wav\\r\\n\\r\\n' % b).encode()
    body += wav + ('\\r\\n--%s--\\r\\n' % b).encode()
    req = urllib.request.Request(WH_URL, data=body, headers={'Content-Type': 'multipart/form-data; boundary=' + b})
    d = json.loads(urllib.request.urlopen(req, timeout=25).read())
    t = re.sub(r"[\\[\\(][^\\]\\)]*[\\]\\)]", ' ', d.get('text') or '')
    t = ' '.join(t.split())
    t = WAKE_P.sub('', t).strip()
    if not t or HALLU.search(t):
        return None
    return t


def _asr_request(text, conf, wavs):
    t0, w = time.time(), None
    try:
        w = _asr(wavs)
    except Exception as e:
        S['err'] = ('whisper: ' + repr(e))[:80]
    with L:
        S['asr'] = max(0, S['asr'] - 1)
        if not S['active']:
            return
        S['last'] = time.time()
        if w:
            _log(w, None, 'whisper %.1fs' % (time.time() - t0))
            _request(w, None)
        else:
            _log(text, conf, 'whisper indisponible : texte Vosk')
            _request(text, conf)


def _flush():
    if not S['buf']:
        return
    text = ' '.join(S['buf'])
    cs = [x for x in S['buf_conf'] if x is not None]
    conf = (sum(cs) / len(cs)) if cs else None
    wavs = S['buf_wav']
    S['buf'], S['buf_conf'], S['buf_wav'] = [], [], []
    if CFG['asr'] and wavs:
        S['asr'] += 1
        S.update(phase='thinking', user=text)
        threading.Thread(target=_asr_request, args=(text, conf, wavs), daemon=True).start()
        return
    _request(text, conf)
''')

# ------------------------------------------------------------------- main.py
rep('main.py', "return self._json(conv.ear(str(data.get('kind', 'P')), str(data.get('text', '')), bool(data.get('wake')), data.get('conf')))",
    "return self._json(conv.ear(str(data.get('kind', 'P')), str(data.get('text', '')), bool(data.get('wake')), data.get('conf'), str(data.get('wav') or '')))")

# ----------------------------------------------------------------- Ears.java
JAVA = 'java_src/org/jacques/julia/Ears.java'
rep(JAVA, "import java.util.concurrent.ConcurrentLinkedQueue;\n",
    "import java.util.concurrent.ConcurrentLinkedQueue;\nimport java.io.File;\nimport java.io.FileOutputStream;\nimport java.nio.ByteBuffer;\nimport java.nio.ByteOrder;\n")
rep(JAVA, "    private static String infoTxt = \"\";\n", '''    private static String infoTxt = "";

    /* audio de la phrase en cours, pour Whisper : 0,6 s avant la parole + la parole (25 s max) */
    private static final int PRE = 9600;
    private static final int MAXS = 16000 * 25;
    private static short[] seg = new short[MAXS + 4096];
    private static int segN = 0;
    private static int segId = 0;
    private static boolean inSeg = false;
    private static volatile boolean cap = false;
    private static String dir = "";

    public static void setDir(String d) {
        dir = d == null ? "" : d;
        try { new File(dir).mkdirs(); } catch (Throwable t) { }
    }

    public static void setCapture(boolean b) {
        cap = b;
    }

    private static void add(short[] b, int n) {
        if (!inSeg && segN + n > PRE) {
            int keep = Math.max(0, PRE - n);
            if (segN > keep) { System.arraycopy(seg, segN - keep, seg, 0, keep); segN = keep; }
        }
        if (segN + n > seg.length) n = seg.length - segN;
        if (n > 0) { System.arraycopy(b, 0, seg, segN, n); segN += n; }
    }

    private static boolean hasText(String r) {
        return r != null && !r.replaceAll("\\\\s", "").contains("\\"text\\":\\"\\"");
    }

    private static String dump() {
        try {
            String path = dir + "/seg_" + (segId++ % 16) + ".wav";
            int bytes = segN * 2;
            ByteBuffer bb = ByteBuffer.allocate(44 + bytes).order(ByteOrder.LITTLE_ENDIAN);
            bb.put("RIFF".getBytes()); bb.putInt(36 + bytes); bb.put("WAVE".getBytes()); bb.put("fmt ".getBytes());
            bb.putInt(16); bb.putShort((short) 1); bb.putShort((short) 1); bb.putInt(16000); bb.putInt(32000);
            bb.putShort((short) 2); bb.putShort((short) 16); bb.put("data".getBytes()); bb.putInt(bytes);
            for (int i = 0; i < segN; i++) bb.putShort(seg[i]);
            FileOutputStream f = new FileOutputStream(path);
            f.write(bb.array());
            f.close();
            return path;
        } catch (Throwable t) {
            return null;
        }
    }
''')
rep(JAVA, '''                            if (rec.acceptWaveForm(b, n)) {
                                lastPartial = "";
                                push("R", rec.getResult());
                            } else {
                                String p = rec.getPartialResult();
                                if (p != null && !p.equals(lastPartial)) {
                                    lastPartial = p;
                                    push("P", p);
                                }
                            }''', '''                            add(b, n);
                            if (rec.acceptWaveForm(b, n)) {
                                lastPartial = "";
                                String res = rec.getResult();
                                if (cap && dir.length() > 0 && segN > 3200 && hasText(res)) {
                                    String wp = dump();
                                    if (wp != null) push("W", "{\\"path\\":\\"" + wp + "\\"}");
                                }
                                inSeg = false;
                                segN = 0;
                                push("R", res);
                            } else {
                                String p = rec.getPartialResult();
                                if (p != null && !p.equals(lastPartial)) {
                                    lastPartial = p;
                                    if (!p.replaceAll("\\\\s", "").equals("{\\"partial\\":\\"\\"}")) inSeg = true;
                                    push("P", p);
                                }
                            }''')
rep(JAVA, "        ar = null; aec = null; model = null; th = null;\n",
    "        ar = null; aec = null; model = null; th = null;\n        inSeg = false; segN = 0;\n")

# --------------------------------------------------------- nouveau workflow
WORKFLOW = '''name: Build whisper-server Android

on:
  workflow_dispatch:
  push:
    branches: [ phase-a ]
    paths: [ '.github/workflows/whisper-server.yml' ]

jobs:
  build:
    runs-on: ubuntu-22.04
    timeout-minutes: 60
    steps:
      - name: Cloner whisper.cpp
        run: git clone --depth 1 https://github.com/ggml-org/whisper.cpp

      - name: Compiler (arm64, ARMv8 de base)
        run: |
          cd whisper.cpp
          cmake -B build \\
            -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake \\
            -DANDROID_ABI=arm64-v8a \\
            -DANDROID_PLATFORM=android-28 \\
            -DCMAKE_BUILD_TYPE=Release \\
            -DGGML_NATIVE=OFF \\
            -DGGML_CPU_ARM_ARCH=armv8-a \\
            -DGGML_OPENMP=OFF \\
            -DWHISPER_BUILD_TESTS=OFF \\
            -DWHISPER_BUILD_EXAMPLES=ON \\
            -DWHISPER_BUILD_SERVER=ON \\
            -DBUILD_SHARED_LIBS=OFF
          cmake --build build --config Release -j4 --target whisper-server

      - name: Renommer et reduire la taille
        run: |
          $ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip whisper.cpp/build/bin/whisper-server
          cp whisper.cpp/build/bin/whisper-server libwhisper_server.so
          ls -l libwhisper_server.so

      - name: Upload binaire
        uses: actions/upload-artifact@v4
        with:
          name: whisper-android-arm64
          path: libwhisper_server.so
'''

# ------------------------------------------------------------------ execution
def main():
    if os.path.exists('brain.py') and 'WH_PORT' in open('brain.py', encoding='utf-8').read():
        print('Deja applique (WH_PORT trouve dans brain.py). Rien a faire.')
        return
    new = {}
    bad = []
    for path, edits in EDITS.items():
        if not os.path.exists(path):
            bad.append('fichier absent : ' + path)
            continue
        s = open(path, encoding='utf-8').read()
        for i, (old, nw) in enumerate(edits, 1):
            n = s.count(old)
            if n != 1:
                bad.append('%s : morceau %d trouve %d fois (attendu 1) : %r' % (path, i, n, old[:70]))
            else:
                s = s.replace(old, nw)
        new[path] = s
    if bad:
        print("ERREUR, rien n'a ete modifie :")
        for b in bad:
            print(' -', b)
        sys.exit(1)
    for path, s in new.items():
        open(path, 'w', encoding='utf-8').write(s)
        print('modifie  ', path)
    os.makedirs('.github/workflows', exist_ok=True)
    open('.github/workflows/whisper-server.yml', 'w', encoding='utf-8').write(WORKFLOW)
    print('cree      .github/workflows/whisper-server.yml')
    print('\nTermine.')


main()
