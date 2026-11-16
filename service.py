# Service Niko : tourne en continu. Micro -> Vosk -> (mot "Niko") -> Qwen -> voix.
import os, time, json, re, threading, traceback, unicodedata, urllib.request
import router
os.environ['JULIA_SVC'] = '1'
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')

# ---- réglages (en dur : Niko n'a pas d'écran de réglages) ----
LLM_URL = 'http://127.0.0.1:8080/v1/chat/completions'
ACTIVE_WINDOW = 15          # secondes pendant lesquelles Niko continue d'écouter sans qu'on redise son nom
IDLE_RESET = 300            # secondes sans parler : Niko oublie la conversation
MAX_TOKENS = 160
WAKE = ('niko', 'nico', 'nikko', 'nicko', 'niquo', 'nigo', 'miko')
GREET = ('dis', 'salut', 'hey', 'hé', 'he', 'ok', 'oh', 'ho', 'bonjour', 'coucou', 'ben', 'eh')
STOP = ('stop', 'tais toi', 'silence', 'ca suffit', 'arrete', 'arrete toi', 'ferme la')
HALLU = ()   # Vosk ne fait pas les hallucinations de Whisper
GATE_GRAMMAR = '["niko", "nico", "[unk]"]'   # passe rapide : on ne cherche que le nom de Niko...
GATE_MS = 2600                                  # ...dans les 2,6 premieres secondes de la phrase
SYSTEM = ("Tu es Niko, un assistant vocal qui vit dans le téléphone de Jacques. Tu parles français, avec un ton "
          "direct, chaleureux et naturel. Tes réponses sont dites à voix haute : 1 à 3 phrases courtes, sans liste, "
          "sans markdown, sans émoji, sans symboles. Si tu ne sais pas, dis-le simplement. Nous sommes le {date}, il est {heure}.")

AUDIO_DIR = os.path.join(DATA, 'audio')
STATE = {'phase': 'demarrage', 'awake': False, 'level': 0.0, 'heard': '', 'said': '', 'status': 'demarrage', 'stats': '', 'mic': ''}
_last_write = [0.0]


def w(name, txt):
    try:
        with open(os.path.join(DATA, name), 'w') as f:
            f.write(txt)
    except Exception:
        pass


def log(m):
    try:
        open(os.path.join(DATA, 'niko.log'), 'a').write(time.strftime('%H:%M:%S ') + m + '\n')
    except Exception:
        pass


def push_state(force=False, **kw):
    STATE.update(kw)
    now = time.time()
    if not force and now - _last_write[0] < 0.15:
        return
    _last_write[0] = now
    try:
        tmp = os.path.join(DATA, 'niko_state.tmp')
        with open(tmp, 'w') as f:
            json.dump(STATE, f)
        os.replace(tmp, os.path.join(DATA, 'niko_state.json'))
    except Exception:
        pass


w('service_boot.txt', 'demarre pid=%d t=%d' % (os.getpid(), int(time.time())))
w('service_err.txt', '')

# jnius : toutes les classes Java sont chargées ICI, dans le fil principal (les autres fils ne retrouvent pas les classes de l'appli)
try:
    from jnius import autoclass, PythonJavaClass, java_method
    PythonService = autoclass('org.kivy.android.PythonService')
    Handler = autoclass('android.os.Handler')
    Looper = autoclass('android.os.Looper')
    TextToSpeech = autoclass('android.speech.tts.TextToSpeech')
    Locale = autoclass('java.util.Locale')
    Ears = autoclass('org.jacques.julia.Ears')
except Exception:
    w('service_err.txt', 'jnius: ' + traceback.format_exc()[-300:])
    raise

try:
    Stt = autoclass('org.jacques.julia.Stt')
except Exception:
    Stt = None
    w('service_err.txt', 'Stt: ' + traceback.format_exc()[-200:])

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


# ---------------- Gardien : relance Qwen s'il tombe ----------------
def llama_pid(port, name=b'llama_server'):
    tok = b'\x00%d\x00' % port
    try:
        for p in os.listdir('/proc'):
            if not p.isdigit():
                continue
            try:
                c = open('/proc/%s/cmdline' % p, 'rb').read()
            except Exception:
                continue
            if name in c and tok in c + b'\x00':
                return int(p)
    except Exception:
        pass
    return 0


def model_ready():
    try:
        return os.path.getsize(brain.MODEL) >= brain.SIZE
    except Exception:
        return False


G = {'th': None, 'th3': None, 'miss_wh': 0, 'last_llm': 0, 'last_wh': 0, 'ready': False, 'status': 'demarrage'}


def guard():
    while True:
        try:
            w('service_hb.txt', '%d %d' % (int(time.time()), os.getpid()))
            if brain is None:
                G['ready'], G['status'] = False, 'cerveau absent'
            elif not model_ready():
                G['ready'], G['status'] = False, 'telechargement du modele'
            else:
                now = time.time()
                up = bool(llama_pid(8080)) or brain.alive()
                if not up:
                    G['ready'], G['status'] = False, 'cerveau en route'
                    if (G['th'] is None or not G['th'].is_alive()) and now - G['last_llm'] > 45:
                        G['last_llm'] = now
                        G['th'] = threading.Thread(target=brain.ensure, daemon=True)
                        G['th'].start()
                else:
                    if VOSK['ok']:
                        G['ready'], G['status'] = True, 'pret'
                    else:
                        G['ready'], G['status'] = False, "chargement de l'oreille (Vosk)"
            w('service_info.txt', 'cerveau/oreille: %s | micro: %s' % (G['status'], str(Ears.info())))
        except Exception:
            w('service_err.txt', traceback.format_exc()[-300:])
        time.sleep(5)


# ---------------- Voix (TextToSpeech Android) ----------------
TTS = {'eng': None, 'ready': False, 'keep': []}


class _Init(PythonJavaClass):
    __javainterfaces__ = ['android/speech/tts/TextToSpeech$OnInitListener']
    __javacontext__ = 'app'

    @java_method('(I)V')
    def onInit(self, status):
        try:
            if status == 0:
                code = TTS['eng'].setLanguage(Locale.FRENCH)
                TTS['eng'].setSpeechRate(1.05)
                choose_voice()
                TTS['ready'] = True
                log('voix prete (setLanguage=%s ; -1/-2 = voix francaise absente)' % code)
            else:
                log('voix: echec init status=%s' % status)
        except Exception:
            log('tts init: ' + traceback.format_exc()[-200:])


class _Run(PythonJavaClass):
    __javainterfaces__ = ['java/lang/Runnable']
    __javacontext__ = 'app'

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    @java_method('()V')
    def run(self):
        try:
            self.fn()
        except Exception:
            log('run: ' + traceback.format_exc()[-200:])


def tts_make():
    def make():
        init = _Init()
        TTS['keep'].append(init)
        TTS['eng'] = TextToSpeech(PythonService.mService, init)
    r = _Run(make)
    TTS['keep'].append(r)
    Handler(Looper.getMainLooper()).post(r)


def say(text):
    text = clean_tts(text)
    if not text or not TTS['ready']:
        return
    try:
        try:
            TTS['eng'].speak(autoclass('java.lang.String')(text), TextToSpeech.QUEUE_ADD, None, 'niko')
        except Exception:
            TTS['eng'].speak(text, TextToSpeech.QUEUE_ADD, None)
    except Exception:
        log('say: ' + traceback.format_exc()[-200:])


WAIT_EARLY = False      # False = ne pas parler avant la transcription
WAIT = {'on': False, 'gen': 0, 'kind': 'autre', 'said': False, 'last': 0.0, 'word': ''}
WAIT_GENERIC = ('Hmm...', 'Alors...', 'Voyons...', 'Attends...', 'Euh...')


def wait_word():
    import random
    a = router.VOIX.get(WAIT['kind'], router.VOIX['autre'])[2]
    pool = [x for x in (tuple(a) + WAIT_GENERIC) if x != WAIT['word']]
    return random.choice(pool)


def wait_say(word):
    p, v, _ = router.VOIX.get(WAIT['kind'], router.VOIX['autre'])
    voice_set(p, v)
    Ears.setMuted(True)
    push_state(phase='parle', force=True)
    say(word)
    log('attente : ' + word)
    WAIT['said'] = True
    WAIT['word'] = word
    WAIT['last'] = time.time()


def wait_loop(g):
    while WAIT['on'] and WAIT['gen'] == g:
        time.sleep(0.15)
        if not (WAIT['on'] and WAIT['gen'] == g):
            break
        if time.time() - WAIT['last'] < 1.6 or tts_busy():
            continue
        try:
            wait_say(wait_word())
        except Exception:
            log('attente: ' + traceback.format_exc()[-150:])


def wait_start(first=''):
    WAIT['gen'] += 1
    WAIT['on'] = True
    WAIT['last'] = time.time() - 0.6      # premier mot apres environ 1 s de silence
    if first:
        try:
            wait_say(first)
        except Exception:
            pass


def wait_stop():
    WAIT['on'] = False


def choose_voice():
    # meilleure voix francaise installee : hors ligne, qualite max, France en priorite
    try:
        from jnius import cast
        it = TTS['eng'].getVoices().iterator()
        best, bs = None, -100000
        while it.hasNext():
            o = it.next()
            try:
                v = cast('android.speech.tts.Voice', o)
            except Exception:
                v = o
            loc = v.getLocale()
            if str(loc.getLanguage()) != 'fr':
                continue
            net = bool(v.isNetworkConnectionRequired())
            sc = int(v.getQuality()) * 10 + (60 if str(loc.getCountry()) == 'FR' else 0) - (4000 if net else 0)
            log('voix fr : %s | q%d | reseau %s | score %d' % (v.getName(), v.getQuality(), net, sc))
            if sc > bs:
                best, bs = v, sc
        if best is not None:
            TTS['eng'].setVoice(best)
            log('voix choisie : ' + str(best.getName()))
        else:
            log('voix fr : aucune trouvee')
    except Exception:
        log('voix: ' + traceback.format_exc()[-200:])


def voice_set(p, v):
    try:
        TTS['eng'].setPitch(float(p))
        TTS['eng'].setSpeechRate(float(v))
    except Exception:
        pass


def tts_stop():
    WAIT['on'] = False
    try:
        TTS['eng'].stop()
    except Exception:
        pass


def tts_busy():
    try:
        return bool(TTS['ready'] and TTS['eng'].isSpeaking())
    except Exception:
        return False


def tts_wait():
    time.sleep(0.4)
    t0 = time.time()
    while tts_busy() and time.time() - t0 < 90:
        push_state(level=0.5 + 0.3 * ((time.time() * 4) % 1.0))
        time.sleep(0.1)


_EMOJI = re.compile('[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]')


def clean_tts(t):
    t = re.sub(r'<think>.*?</think>', '', t, flags=re.S)
    t = _EMOJI.sub('', t)
    t = re.sub(r'[*_#`>~|]+', '', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


# ---------------- Oreille : Vosk (local) ----------------
def norm(t):
    t = unicodedata.normalize('NFD', t.lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r"[^a-z0-9' ]+", ' ', t).strip()


VOSK_DIR = os.path.join(DATA, 'models', 'vosk-model-small-fr-0.22')
VOSK_URL = 'https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip'
VOSK = {'ok': False}


def vosk_prepare():
    try:
        try:
            os.remove(os.path.join(DATA, 'models', 'ggml-base-q5_1.bin'))
        except Exception:
            pass
        if Stt is None:
            log('vosk: classe Stt absente')
            return
        if not os.path.isdir(os.path.join(VOSK_DIR, 'am')):
            import zipfile
            os.makedirs(os.path.dirname(VOSK_DIR), exist_ok=True)
            tmp = VOSK_DIR + '.zip'
            log('vosk: telechargement du modele (41 Mo)')
            r = urllib.request.urlopen(VOSK_URL, timeout=60, context=(brain.ctx() if brain else None))
            with open(tmp, 'wb') as f:
                while True:
                    c = r.read(262144)
                    if not c:
                        break
                    f.write(c)
            with zipfile.ZipFile(tmp) as z:
                z.extractall(os.path.dirname(VOSK_DIR))
            os.remove(tmp)
        res = str(Stt.load(VOSK_DIR))
        VOSK['ok'] = (res == 'ok')
        log('vosk: ' + res)
    except Exception:
        log('vosk: ' + traceback.format_exc()[-200:])


ASR_DIR = os.path.join(DATA, 'models', 'sherpa-fr')
ASR_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-fr-2023-04-14.tar.bz2'
ASR = {'ok': False, 'busy': False}
try:
    AsrJ = autoclass('org.jacques.julia.Asr')
except Exception:
    AsrJ = None


def asr_have():
    try:
        fs = os.listdir(ASR_DIR)
    except Exception:
        return False

    def has(p):
        return any(f.startswith(p) and f.endswith('.onnx') for f in fs)
    return 'tokens.txt' in fs and has('encoder') and has('decoder') and has('joiner')


def asr_on_wifi():
    try:
        cm = PythonService.mService.getSystemService('connectivity')
        caps = cm.getNetworkCapabilities(cm.getActiveNetwork())
        return bool(caps.hasTransport(1))
    except Exception:
        return True


ASR_REPO = 'shaojieli/sherpa-onnx-streaming-zipformer-fr-2023-04-14'


def asr_download():
    import shutil
    old = ASR_DIR + '.tar.bz2'
    if os.path.exists(old):
        os.remove(old)          # reste de l'ancien essai (398 Mo)
    part = ASR_DIR + '.part'
    shutil.rmtree(part, ignore_errors=True)
    os.makedirs(part)
    ctx = brain.ctx() if brain else None
    base = 'https://huggingface.co/'
    r = urllib.request.urlopen(base + 'api/models/' + ASR_REPO, timeout=60, context=ctx)
    names = [x['rfilename'] for x in json.loads(r.read().decode('utf-8')).get('siblings', [])]
    log('asr: fichiers du depot : ' + ', '.join(n for n in names if n.endswith('.onnx') or n == 'tokens.txt'))

    def choose(prefix, want_int8):
        c = [n for n in names if '/' not in n and n.startswith(prefix) and n.endswith('.onnx')]
        pref = [n for n in c if ('.int8.' in n) == want_int8]
        return (pref or c or [None])[0]

    todo = [choose('encoder', True), choose('decoder', False), choose('joiner', False),
            'tokens.txt' if 'tokens.txt' in names else None]
    if None in todo:
        raise RuntimeError('fichiers du modele introuvables : %s' % todo)
    for n in todo:
        log('asr: telechargement ' + n)
        r = urllib.request.urlopen(base + ASR_REPO + '/resolve/main/' + n, timeout=60, context=ctx)
        done, last = 0, time.time()
        with open(os.path.join(part, n), 'wb') as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 60:
                    last = time.time()
                    log('asr: %s : %d Mo' % (n, done >> 20))
    shutil.rmtree(ASR_DIR, ignore_errors=True)
    os.rename(part, ASR_DIR)
    log('asr: modele pret : ' + ', '.join(sorted(os.listdir(ASR_DIR))))


def asr_prepare():
    if ASR['busy']:
        return
    ASR['busy'] = True
    try:
        if AsrJ is None:
            log('asr: classe Asr absente (on reste sur Vosk)')
            return
        if not asr_have():
            while not asr_on_wifi():
                log('asr: pas de Wi-Fi, on attend avant de telecharger 398 Mo')
                time.sleep(300)
            asr_download()
        res = str(AsrJ.load(ASR_DIR))
        ASR['ok'] = (res == 'ok')
        log('asr: ' + res)
    except Exception:
        log('asr: ' + traceback.format_exc()[-200:])
    finally:
        ASR['busy'] = False


def asr_text(path, from_ms=0):
    # meme forme que vosk_text : (texte, ms, cpu_ms) ou None si sherpa n'est pas pret
    if not (ASR['ok'] and AsrJ is not None):
        return None
    try:
        j = json.loads(str(AsrJ.transcribeFrom(path, int(from_ms))))
    except Exception:
        log('asr: ' + traceback.format_exc()[-200:])
        return None
    if 'err' in j:
        log('asr: ' + str(j['err'])[:150])
        return None
    log('  moteur : sherpa')
    return str(j.get('text', '')).strip().lower(), int(j.get('ms', 0)), int(j.get('cpu', 0))


def vosk_text(path, from_ms=0):
    r = asr_text(path, from_ms)
    if r is not None:
        return r
    return vosk_text_old(path, from_ms)


def vosk_text_old(path, from_ms=0):
    """Transcription libre, a partir de from_ms. Renvoie (texte, ms, cpu_ms) ou None si Vosk a plante."""
    try:
        if from_ms > 0:
            j = json.loads(str(Stt.transcribeFrom(path, int(from_ms))))
        else:
            j = json.loads(str(Stt.transcribe(path, '')))
        if 'err' in j:
            log('vosk: ' + str(j['err'])[:150])
            return None
        return (j.get('text') or '').strip(), int(j.get('ms') or 0), int(j.get('cpu') or 0)
    except Exception:
        log('vosk: ' + traceback.format_exc()[-200:])
        return None


def vosk_gate(path):
    """Passe rapide : le nom de Niko est-il dit au debut de la phrase ? (Vosk limite a 2 mots + [unk] : bien plus fiable
    que la transcription libre, et bien plus rapide.)
    Renvoie (trouve, fin_du_nom_en_ms (-1 si inconnue), ms, cpu_ms, texte_brut) ou None si Vosk a plante."""
    try:
        j = json.loads(str(Stt.wake(path, GATE_GRAMMAR, GATE_MS)))
        if 'err' in j:
            log('vosk (nom): ' + str(j['err'])[:150])
            return None
        ms, cpu = int(j.get('ms') or 0), int(j.get('cpu') or 0)
        raw = '(' + str(j.get('text') or '').strip() + ')'
        words = j.get('result')
        if isinstance(words, list) and words:
            for wd in words:
                if norm(str(wd.get('word', ''))) in ('niko', 'nico'):
                    st, en = float(wd.get('start', 0)), float(wd.get('end', 0))
                    if en - st <= 1.2:      # un "Niko" dure moins d'une seconde ; plus long = du bruit force dans le mot
                        return True, int(en * 1000), ms, cpu, raw
            return False, 0, ms, cpu, raw
        if any(norm(t) in ('niko', 'nico') for t in str(j.get('text') or '').split()):
            return True, -1, ms, cpu, raw     # pas d'horodatage : on transcrira toute la phrase
        return False, 0, ms, cpu, raw
    except Exception:
        log('vosk (nom): ' + traceback.format_exc()[-200:])
        return None


def is_noise(text):
    n = norm(text)
    if len(n) < 2:
        return True
    low = text.lower()
    if re.fullmatch(r'[\[\(\*♪].*', text.strip()):      # [BLANK_AUDIO], (musique), ...
        return True
    return any(h in low for h in HALLU)


def lev1(a, b):
    """Vrai si a et b different d'une seule lettre au plus (remplacement, ajout ou retrait)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def is_wake(n):
    return n in WAKE or (3 <= len(n) <= 6 and lev1(n, 'niko'))


def split_wake(text):
    """Renvoie (appele, commande). Le nom doit tomber dans les 4 premiers mots (ou en deux mots : "ni ko")."""
    words = text.split()
    for i, wd in enumerate(words[:4]):
        n = norm(wd)
        two = n + norm(words[i + 1]) if i + 1 < len(words) else ''
        if is_wake(n) or (two and len(n) <= 3 and is_wake(two)):
            rest = words[i + (2 if (not is_wake(n)) else 1):]
            while rest and norm(rest[0]) in GREET:
                rest = rest[1:]
            return True, ' '.join(rest).strip(' ,.!?;:')
    return False, text


# ---------------- Cerveau : Qwen en streaming, phrase par phrase vers la voix ----------------
HIST = []
_SENT = re.compile(r'(?<=[.!?…:;])\s+|\n+')


REGLES_ORALES = (
    " Tu parles comme dans une vraie conversation orale entre amis : 1 à 3 phrases courtes, "
    "langage parlé naturel, tu peux dire « ouais », « bah », « en fait », « tiens ». "
    "Va droit au but, sans formule d'assistant (pas de « bien sûr », pas de « excellente question »). "
    "Ne dis jamais le prénom de la personne : dans une vraie conversation on ne répète pas le prénom de l'autre. "
    "Ne répète pas la question. Réponds d'abord à ce qui est demandé, puis ajoute au plus une petite relance, "
    "seulement si elle est naturelle. Pas de liste, pas d'émojis, pas de markdown. "
    "Si tu ne sais pas, dis-le simplement."
)


def system_prompt():
    jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
    mois = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']
    d = time.localtime()
    date = '%s %d %s %d' % (jours[d.tm_wday], d.tm_mday, mois[d.tm_mon - 1], d.tm_year)
    return SYSTEM.replace('Jacques', 'celui qui te parle').format(date=date, heure=time.strftime('%H:%M', d)) + REGLES_ORALES


def ask(text):
    msgs = [{'role': 'system', 'content': system_prompt()}] + HIST[-8:] + [{'role': 'user', 'content': text}]
    body = json.dumps({'messages': msgs, 'max_tokens': MAX_TOKENS, 'temperature': 1.0, 'top_p': 0.95, 'top_k': 64,
                       'stream': True, 'cache_prompt': True}).encode()
    req = urllib.request.Request(LLM_URL, data=body, headers={'Content-Type': 'application/json'})
    full, buf, spoken = '', '', False

    def flush(chunk):
        nonlocal spoken
        chunk = clean_tts(chunk)
        if len(chunk) < 2:
            return
        if not spoken:
            Ears.setMuted(True)         # on coupe le micro dès la première phrase dite
            push_state(phase='parle', force=True)
            spoken = True
        say(chunk)

    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode('utf-8', 'ignore').strip()
            if not line.startswith('data:'):
                continue
            d = line[5:].strip()
            if d == '[DONE]':
                break
            try:
                delta = json.loads(d)['choices'][0]['delta'].get('content') or ''
            except Exception:
                continue
            if not delta:
                continue
            if not full:
                wait_stop()
            full += delta
            buf += delta
            push_state(said=clean_tts(full))
            if not spoken and len(buf) >= 24 and ',' in buf[12:]:
                k = buf.index(',', 12) + 1
                flush(buf[:k])
                buf = buf[k:].lstrip()
            parts = _SENT.split(buf)
            if len(parts) > 1:
                for p in parts[:-1]:
                    flush(p)
                buf = parts[-1]
    flush(buf)
    full = clean_tts(full)
    if full:
        HIST.extend([{'role': 'user', 'content': text}, {'role': 'assistant', 'content': full}])
        del HIST[:-12]
    return full, spoken


# ---------------- Boucle principale : écoute -> comprend -> répond ----------------
S = {'active_until': 0.0, 'last_talk': 0.0}


def awake():
    return time.time() < S['active_until']


def settle(spoken):
    """Fin d'un tour : on attend que la voix finisse, puis on rouvre le micro."""
    if spoken:
        tts_wait()
    Ears.setMuted(False)
    voice_set(1.0, 1.05)
    S['active_until'] = time.time() + ACTIVE_WINDOW
    S['last_talk'] = time.time()
    push_state(phase='veille', awake=True, level=0.0, force=True)


def handle(info):
    WAIT['said'] = False
    WAIT['kind'] = 'autre'
    try:
        if WAIT_EARLY and awake():
            wait_start()
        handle_inner(info)
    finally:
        wait_stop()
        if WAIT['said']:
            if tts_busy():
                tts_wait()
            Ears.setMuted(False)


def handle_inner(info):
    path = info.get('path', '')
    try:
        dur = (os.path.getsize(path) - 44) / 32000.0
    except Exception:
        dur = 0.0
    log('phrase captee : %.1f s | voix %.3f (crete %.2f) | gain x%.1f | bruit %.4f' % (
        dur, info.get('lvl', 0), info.get('pk', 0), info.get('g', 1), info.get('nz', 0)))
    push_state(phase='reflexion', force=True)
    called, cmd = False, ''
    try:
        if awake():
            # Niko vient de parler : on peut lui repondre sans redire son nom -> transcription libre de toute la phrase
            r = vosk_text(path)
            if r is None:
                log('  oreille Vosk indisponible')
                push_state(phase='veille', awake=awake(), force=True)
                return
            text, ms, cpu = r
            log('  vosk %.1f s (cpu %.1f s) | entendu : "%s"' % (ms / 1000.0, cpu / 1000.0, text))
            if is_noise(text):
                log('  -> ignore (bruit)')
                push_state(phase='veille', awake=awake(), force=True)
                return
            text = router.fix_name(text)
            called, cmd = split_wake(text)
            w0 = text.split()[0].lower().strip(',.!?') if text.split() else ''
            if called and not cmd and w0 in ('bonjour', 'salut', 'coucou', 'hello', 'bonsoir', 'hey'):
                cmd = w0
            cmd = cmd if called else text
        else:
            # passe rapide : le nom de Niko est-il dit au debut ? Sinon on ne transcrit pas le reste (gain de temps)
            g = vosk_gate(path)
            if g is None:
                log('  oreille Vosk indisponible')
                push_state(phase='veille', awake=awake(), force=True)
                return
            found, end_ms, ms, cpu, raw = g
            log('  nom Niko : %s (vosk %.1f s, cpu %.1f s) %s' % ('oui' if found else 'non', ms / 1000.0, cpu / 1000.0, raw))
            if not found:
                log('  -> ignore (pas appele par son nom)')
                push_state(phase='veille', awake=False, heard='', force=True)       # on n'a pas parlé à Niko
                return
            called = True
            push_state(awake=True, force=True)
            wait_start()
            if end_ms >= 0 and dur * 1000.0 - end_ms < 400:
                cmd = ''                                  # il a juste dit "Niko"
            else:
                # on ne transcrit que ce qui suit le nom
                r = vosk_text(path, max(end_ms, 0))
                if r is None:
                    log('  oreille Vosk indisponible')
                    push_state(phase='veille', awake=awake(), force=True)
                    return
                text, ms, cpu = r
                log('  commande : "%s" (vosk %.1f s, cpu %.1f s)' % (text, ms / 1000.0, cpu / 1000.0))
                cmd = '' if is_noise(text) else text
                if cmd:
                    again, rest = split_wake(cmd)         # si le nom traine encore dans le texte, on l'enleve
                    if again:
                        cmd = rest
    except Exception:
        log('vosk: ' + traceback.format_exc()[-200:])
        push_state(phase='veille', awake=awake(), force=True)
        return
    shown = ('Niko, ' + cmd) if (called and cmd) else (cmd or 'Niko')
    push_state(heard=shown, said='', awake=True, force=True)
    log('  -> pour Niko : "%s"' % cmd)
    if time.time() - S['last_talk'] > IDLE_RESET:
        del HIST[:]
    if called and not cmd:
        wait_stop()
        Ears.setMuted(True)
        say('Oui ?')
        push_state(phase='parle', force=True)
        settle(True)
        return
    if norm(cmd) in STOP:
        tts_stop()
        settle(False)
        return
    try:
        kind = router.qtype(cmd)
        p, v, amo = router.amorce(kind)
        log('role: %s | voix %.2f x%.2f' % (kind, p, v))
        WAIT['kind'] = kind
        voice_set(p, v)
        wait_start('' if WAIT['said'] else amo)
        _, spoken = ask(cmd)
        wait_stop()
        spoken = spoken or WAIT['said']
    except Exception:
        wait_stop()
        log('qwen: ' + traceback.format_exc()[-200:])
        Ears.setMuted(True)
        say("Je n'arrive pas à réfléchir pour l'instant.")
        settle(True)
        return
    settle(spoken)


def exit_reasons():
    """Pourquoi le service precedent s'est arrete (Android garde l'historique)."""
    try:
        ctx = PythonService.mService
        am = ctx.getSystemService('activity')
        lst = am.getHistoricalProcessExitReasons(ctx.getPackageName(), 0, 4)
        for i in range(lst.size()):
            e = lst.get(i)
            log('fin precedente : %s raison %s statut %s : %s' % (e.getProcessName(), e.getReason(), e.getStatus(), str(e.getDescription())))
    except Exception:
        log('fin precedente: ' + traceback.format_exc()[-150:])


def beat():
    """Le service signale qu'il est vivant meme quand il attend un long calcul."""
    while True:
        time.sleep(3)
        try:
            push_state(force=True)
        except Exception:
            pass


def main():
    log('service Niko demarre')
    exit_reasons()
    threading.Thread(target=beat, daemon=True).start()
    tts_make()
    threading.Thread(target=guard, daemon=True).start()
    threading.Thread(target=vosk_prepare, daemon=True).start()
    threading.Thread(target=asr_prepare, daemon=True).start()
    started = False
    last_chk = 0.0
    while True:
        try:
            now = time.time()
            if not started:
                r = str(Ears.start(AUDIO_DIR))
                if r == 'ok':
                    started = True
                    log('micro ouvert : ' + str(Ears.info()))
                else:
                    push_state(status='micro: ' + r, force=True)
                    log('micro: ' + r)
                    time.sleep(5)
                    continue
            if awake() is False and STATE.get('awake'):
                push_state(awake=False, force=True)
            if STATE['phase'] == 'demarrage' and G['ready'] and TTS['ready']:
                push_state(phase='veille', force=True)
            if not G['ready'] and STATE['phase'] != 'demarrage':
                push_state(phase='demarrage', force=True)
            if now - last_chk > 1.0:
                last_chk = now
                push_state(status=G['status'], stats=str(Ears.stats()), mic=str(Ears.info()), force=True)
            if STATE['phase'] == 'veille':
                push_state(phase='ecoute' if Ears.voice() else 'veille', level=float(Ears.level()))
            elif STATE['phase'] == 'ecoute':
                push_state(phase='ecoute' if Ears.voice() else 'veille', level=float(Ears.level()))
            ev = Ears.poll()
            if ev is None:
                time.sleep(0.04)
                continue
            kind, _, payload = str(ev).partition('|')
            if kind == 'E':
                log('micro erreur: ' + payload)
                try:
                    Ears.stop()
                except Exception:
                    pass
                started = False
                continue
            if kind == 'W' and G['ready'] and TTS['ready']:
                handle(json.loads(payload))
        except Exception:
            w('service_err.txt', traceback.format_exc()[-300:])
            time.sleep(1)


main()
