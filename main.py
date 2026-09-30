import os, json, threading, time, re, sqlite3, ssl, html
import urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
DB = os.path.join(DATA, 'julia.db')
GENERAL = 'http://127.0.0.1:8080'
CODER = 'http://127.0.0.1:8081'
NOBRAIN = "Mon cerveau n'est pas lancé. Ouvre Termux et lance julia_brain.sh."
ST = {'active': False, 't0': 0.0, 'err': None}
GEN = {'id': 0, 'text': '', 'done': True, 'brain': True, 'stage': '', 'model': 'general', 'truncated': False}
VOUS = re.compile(r"\b(vous|votre|vos)\b", re.I)
CODE_RE = re.compile(r"\b(code|coder|coding|python|javascript|java|html|css|bash|shell|termux|linux|script|fonction|"
                     r"variable|boucle|bug|compile|programme|programmer|programmation|api|sql|json|regex|git|github|"
                     r"algorithme|debug|déboguer)\b", re.I)
LONG_RE = re.compile(r"(raconte|explique|détaill|développe|histoire|résume|décris|comment fonctionne|pourquoi|"
                     r"continue|la suite|plus de détails|en détail)", re.I)
ENCYC = re.compile(r"(c'est qui|qui est|qui était|qui sont|qu'est-ce que|qu'est ce que|c'est quoi|parle-moi de|parle moi de)", re.I)
FRESH = re.compile(r"\b(aujourd'hui|actualités?|dernier|derniers|dernière|récent|maintenant|en ce moment|météo|score|"
                   r"résultats?|prix|cours|combien coûte|président|premier ministre|sortie|news)\b", re.I)
EXPL = re.compile(r"\b(cherche|recherche|google|sur internet|sur le web|vérifie)\b", re.I)

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl.create_default_context()

try:
    from plyer import stt, tts
    PLYER_ERR = None
except Exception as e:
    stt = tts = None
    PLYER_ERR = str(e)

try:
    from android.permissions import request_permissions, Permission
    from android.runnable import run_on_ui_thread
    request_permissions([Permission.RECORD_AUDIO])
    ANDROID = True
except Exception:
    ANDROID = False

    def run_on_ui_thread(f):
        return f


# ---------- Mémoire (SQLite) ----------
def sql(query, args=(), fetch=False):
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        cur = c.execute(query, args)
        rows = [dict(r) for r in cur.fetchall()] if fetch else None
        c.commit()
        return rows
    finally:
        c.close()


sql('CREATE TABLE IF NOT EXISTS conv(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, role TEXT, text TEXT)')
sql('CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, text TEXT UNIQUE)')
sql('CREATE TABLE IF NOT EXISTS cache(query TEXT PRIMARY KEY, ts REAL, result TEXT)')

PATTERNS = [
    (r"\bje m'appelle ([\w'\-]+(?: [\w'\-]+)?)", "Il s'appelle {}"),
    (r"\bmon (?:pr[ée]nom|nom) est ([\w'\-]+)", "Il s'appelle {}"),
    (r"\bj'habite (?:à|a|en|au|aux) ([\w'\- ]{2,30})", "Il habite à {}"),
    (r"\bj'aime (?:bien |beaucoup )?(?:le |la |les |l')?([\w'\- ]{2,30})", "Il aime {}"),
    (r"\bje travaille (?:comme|en tant que) ([\w'\- ]{2,30})", "Il travaille comme {}"),
]


def extract_facts(text):
    t = text.replace("’", "'")
    out = []
    for pat, fmt in PATTERNS:
        m = re.search(pat, t, re.I)
        if m:
            val = m.group(1).strip(" .,!?")
            if len(val) >= 2:
                out.append(fmt.format(val))
    return out


# ---------- Internet (Wikipédia + DuckDuckGo), avec cache ----------
UA = {'User-Agent': 'Julia/1.0 (assistant vocale personnelle)', 'Accept-Language': 'fr,en;q=0.8'}


def http_get(url, timeout=8):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode('utf-8', 'ignore')


def wiki_search(q, lang='fr'):
    url = ('https://%s.wikipedia.org/w/api.php?action=query&format=json&generator=search&gsrlimit=2'
           '&prop=extracts&exintro=1&explaintext=1&exchars=600&redirects=1&gsrsearch=%s'
           % (lang, urllib.parse.quote(q)))
    d = json.loads(http_get(url))
    pages = sorted(((d.get('query') or {}).get('pages') or {}).values(), key=lambda p: p.get('index', 9))
    return [['Wikipédia - ' + p['title'], p['extract'].strip()] for p in pages if p.get('extract')]


def ddg_search(q):
    page = http_get('https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(q))

    def tags(s):
        return html.unescape(re.sub(r'<[^>]+>', '', s)).strip()

    titles = [tags(x) for x in re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)]
    snips = [tags(x) for x in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)]
    return [['Web - ' + t, s] for t, s in zip(titles, snips)][:3]


def entity(text):
    t = text.replace('’', "'")
    t = re.sub(r"(?i)(c'est qui|qui est|qui était|qui sont|qu'est-ce que|qu'est ce que|c'est quoi|parle-moi de|"
               r"parle moi de|cherche|recherche|sur internet|sur le web|dis-moi)", ' ', t)
    t = re.sub(r"[?!.,]", ' ', t)
    return re.sub(r'\s+', ' ', t).strip() or text


def web_context(text):
    enc, fresh, expl = ENCYC.search(text), FRESH.search(text), EXPL.search(text)
    if not (enc or fresh or expl):
        return ''
    q = entity(text)
    key = q.lower()
    ttl = 1800 if (fresh or expl) else 7 * 86400
    row = sql('SELECT ts,result FROM cache WHERE query=?', (key,), fetch=True)
    if row and time.time() - row[0]['ts'] < ttl:
        res = json.loads(row[0]['result'])
    else:
        res = []
        for fn in ((ddg_search, wiki_search) if (fresh or expl) else (wiki_search, ddg_search)):
            try:
                res = fn(q)
            except Exception:
                res = []
            if res:
                break
        if res:
            sql('INSERT OR REPLACE INTO cache(query,ts,result) VALUES(?,?,?)', (key, time.time(), json.dumps(res)))
    if not res:
        return ''
    return '\n'.join('[%s] %s' % (t, s[:600]) for t, s in res[:2])[:1100]


# ---------- Cerveau (deux serveurs llama : Général 8080, Coder 8081) ----------
def system_prompt(lang, facts, web, code):
    if lang == 'EN':
        s = ("You are Julia, a warm and direct local voice assistant. Always answer in English. "
             "Usually answer in 1 to 3 short spoken sentences, no lists, no emojis. If asked for a story, an "
             "explanation or details, you may expand in one short paragraph (6 sentences max). "
             "If you don't know, say so instead of guessing.")
        if code:
            s += " For code, give a short block then explain in one sentence what it does."
        if facts:
            s += "\nWhat you know about the user: " + " ; ".join(facts) + "."
        if web:
            s += "\nInformation found on the Internet (use it first, do not copy it word for word):\n" + web
        return s
    s = ("Tu es Julia, une assistante vocale locale, chaleureuse et directe. Tu réponds toujours en français, "
         "sans emojis, comme à l'oral. En général tu réponds en 1 à 3 phrases courtes, sans listes. "
         "Si on te demande une histoire, une explication ou des détails, tu peux développer en un court "
         "paragraphe (6 phrases maximum). Tu tutoies toujours l'utilisateur, même s'il te vouvoie. "
         "Si tu ne sais pas, dis-le simplement au lieu d'inventer.")
    if code:
        s += (" Pour le code, donne un court bloc de code, puis explique en une phrase ce qu'il fait, "
              "sans lire le code à voix haute.")
    if facts:
        s += "\nCe que tu sais de l'utilisateur : " + " ; ".join(facts) + "."
    if web:
        s += ("\nInformations trouvées sur Internet (appuie-toi dessus en priorité, sans les recopier mot pour mot) :\n"
              + web)
    return s


SHOT_FR = [{'role': 'user', 'content': 'Salut, tu peux te présenter ?'},
           {'role': 'assistant', 'content': "Salut ! Moi c'est Julia, ton assistante vocale. Dis-moi ce dont tu as besoin."}]
SHOT_EN = [{'role': 'user', 'content': 'Hi, can you introduce yourself?'},
           {'role': 'assistant', 'content': "Hi! I'm Julia, your voice assistant. Tell me what you need."}]


def build_messages(text, lang, mem, web, code):
    facts, hist = [], []
    if mem:
        for f in extract_facts(text):
            sql('INSERT OR IGNORE INTO facts(ts,text) VALUES(?,?)', (time.time(), f))
        facts = [r['text'] for r in sql('SELECT text FROM facts ORDER BY id DESC LIMIT 12', fetch=True)]
        rows = sql('SELECT role,text FROM conv ORDER BY id DESC LIMIT 12', fetch=True)[::-1]
        pairs, i = [], 0
        while i + 1 < len(rows):
            if rows[i]['role'] == 'user' and rows[i + 1]['role'] == 'assistant':
                pairs.append((rows[i]['text'], rows[i + 1]['text']))
                i += 2
            else:
                i += 1
        for u, a in pairs[-3:]:
            if lang == 'EN' or not VOUS.search(a):
                hist += [{'role': 'user', 'content': u}, {'role': 'assistant', 'content': a}]
    shot = [] if code else (SHOT_EN if lang == 'EN' else SHOT_FR)
    return [{'role': 'system', 'content': system_prompt(lang, facts, web, code)}] + shot + hist + \
           [{'role': 'user', 'content': text}]


def clean_think(t):
    return re.sub(r'<think>.*?(</think>|$)', '', t, flags=re.S).strip()


def trim_sentence(t):
    idx = max(t.rfind(c) for c in '.!?…')
    if idx >= int(len(t) * 0.4):
        return t[:idx + 1]
    return t.rstrip() + '…'


def pick(text, pref):
    if pref in ('code', 'general'):
        return pref
    return 'code' if CODE_RE.search(text) else 'general'


def stream_brain(model, messages, on_text, text):
    code = model == 'code'
    n = 520 if code else (380 if LONG_RE.search(text) else 150)
    body = json.dumps({'messages': messages, 'max_tokens': n, 'temperature': 0.2 if code else 0.5,
                       'top_p': 0.9, 'repeat_penalty': 1.1, 'stream': True, 'cache_prompt': True}).encode()
    url = (CODER if code else GENERAL) + '/v1/chat/completions'
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    finish = None
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode('utf-8', 'ignore').strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                break
            try:
                ch = json.loads(payload)['choices'][0]
            except Exception:
                continue
            delta = (ch.get('delta') or {}).get('content') or ''
            if delta:
                on_text(delta)
            if ch.get('finish_reason'):
                finish = ch['finish_reason']
    return finish


def generate(gid, text, lang, voice, mem, web, pref):
    model = pick(text, pref)
    ctx = ''
    if web:
        GEN['stage'] = 'search'
        try:
            ctx = web_context(text)
        except Exception:
            ctx = ''
    if GEN['id'] != gid:
        return
    GEN['stage'] = 'think'
    msgs = build_messages(text, lang, mem, ctx, model == 'code')

    def on_text(d):
        if GEN['id'] == gid:
            GEN['text'] += d

    ok, finish = False, None
    for m in (model, 'general' if model == 'code' else 'code'):
        try:
            GEN['model'] = m
            finish = stream_brain(m, msgs, on_text, text)
            ok = True
            break
        except Exception:
            if GEN['text']:
                break
    if GEN['id'] != gid:
        return
    out = clean_think(GEN['text'])
    truncated = bool(ok and finish == 'length' and out)
    if truncated:
        out = trim_sentence(out)
    if not out:
        out = "Je n'ai pas de réponse pour l'instant." if ok else NOBRAIN
    GEN.update(text=out, brain=ok, truncated=truncated, stage='')
    if mem and ok:
        now = time.time()
        sql('INSERT INTO conv(ts,role,text) VALUES(?,?,?)', (now, 'user', text))
        sql('INSERT INTO conv(ts,role,text) VALUES(?,?,?)', (now, 'assistant', out))
    if voice:
        threading.Thread(target=speak_text, args=(out, 1.0, 1.0, lang, ''), daemon=True).start()
    GEN['done'] = True


def start_ask(data):
    GEN['id'] += 1
    gid = GEN['id']
    GEN.update(text='', done=False, brain=True, stage='', truncated=False)
    threading.Thread(target=generate, daemon=True,
                     args=(gid, str(data.get('text', '')), data.get('lang', 'FR'), data.get('voice', 0),
                           data.get('mem', 1), data.get('web', 1), data.get('model', 'auto'))).start()
    return {'ok': True, 'id': gid}


def gen_state():
    t = GEN['text'] if GEN['done'] else clean_think(GEN['text'])
    return {'text': t, 'done': GEN['done'], 'brain': GEN['brain'], 'id': GEN['id'],
            'stage': GEN['stage'], 'model': GEN['model'], 'truncated': GEN['truncated']}


def memory_info():
    n = sql("SELECT COUNT(*) AS n FROM conv WHERE role='user'", fetch=True)[0]['n']
    facts = sql('SELECT id,text FROM facts ORDER BY id DESC LIMIT 50', fetch=True)
    recent = sql('SELECT role,text FROM (SELECT id,role,text FROM conv ORDER BY id DESC LIMIT 8) ORDER BY id', fetch=True)
    return {'count': n, 'facts': facts, 'recent': recent}


# ---------- Voix (TextToSpeech Android natif, avec repli plyer) ----------
NT = {'eng': None, 'ready': False, 'vmap': {}, 'keep': None}


@run_on_ui_thread
def _native_make():
    try:
        from jnius import autoclass, PythonJavaClass, java_method

        class Init(PythonJavaClass):
            __javainterfaces__ = ['android/speech/tts/TextToSpeech$OnInitListener']
            __javacontext__ = 'app'

            @java_method('(I)V')
            def onInit(self, status):
                NT['ready'] = (status == 0)

        TextToSpeech = autoclass('android.speech.tts.TextToSpeech')
        Activity = autoclass('org.kivy.android.PythonActivity')
        NT['keep'] = Init()
        NT['eng'] = TextToSpeech(Activity.mActivity, NT['keep'])
    except Exception:
        NT['eng'] = None


def native_voices(lang):
    eng = NT['eng']
    if eng is None or not NT['ready']:
        return []
    want = 'en' if lang == 'EN' else 'fr'
    names = {}
    try:
        it = eng.getVoices().iterator()
        while it.hasNext():
            v = it.next()
            if v.getLocale().getLanguage() == want and not v.isNetworkConnectionRequired():
                names[v.getName()] = v
    except Exception:
        return []
    NT['vmap'].update(names)
    return sorted(names)


def native_say(text, pitch, rate, lang, vname):
    eng = NT['eng']
    if eng is None or not NT['ready']:
        return False
    try:
        from jnius import autoclass
        Locale = autoclass('java.util.Locale')
        TextToSpeech = autoclass('android.speech.tts.TextToSpeech')
        eng.setLanguage(Locale.ENGLISH if lang == 'EN' else Locale.FRENCH)
        if vname:
            native_voices(lang)
            if vname in NT['vmap']:
                eng.setVoice(NT['vmap'][vname])
        eng.setPitch(float(pitch))
        eng.setSpeechRate(float(rate))
        eng.speak(text, TextToSpeech.QUEUE_ADD, None, 'julia')
        return True
    except Exception:
        return False


def speak_text(text, pitch=1.0, rate=1.0, lang='FR', vname=''):
    if not text:
        return ''
    if native_say(text, pitch, rate, lang, vname):
        return 'native'
    if tts is not None:
        try:
            tts.speak(text)
            return 'plyer'
        except Exception:
            pass
    return ''


def speak_stop():
    try:
        if NT['eng'] is not None:
            NT['eng'].stop()
    except Exception:
        pass


# ---------- Micro (STT) ----------
@run_on_ui_thread
def _stt_start(lang):
    try:
        try:
            stt.language = lang
        except Exception:
            pass
        stt.start()
    except Exception as e:
        ST['err'] = str(e)


@run_on_ui_thread
def _stt_stop():
    try:
        stt.stop()
    except Exception:
        pass


def listen(lang):
    if stt is None:
        return {'ok': False, 'error': 'plyer indisponible : ' + str(PLYER_ERR)}
    for name in ('results', 'partial_results', 'errors'):
        try:
            getattr(stt, name).clear()
        except Exception:
            pass
    ST.update(active=True, t0=time.time(), err=None)
    _stt_start(lang)
    return {'ok': True}


def stt_state():
    now = time.time()
    res = list(getattr(stt, 'results', None) or [])
    part = list(getattr(stt, 'partial_results', None) or [])
    errs = list(getattr(stt, 'errors', None) or [])
    listening = ST['active'] and not res and not ST['err']
    if listening and now - ST['t0'] > 1.5 and not getattr(stt, 'listening', True):
        listening = False
    if listening and now - ST['t0'] > 12:
        _stt_stop()
        listening = False
    if not listening:
        ST['active'] = False
    error = ST['err'] or (str(errs[-1]) if errs and not res else None)
    return {'listening': listening, 'partial': str(part[-1]) if part else '',
            'text': str(res[0]) if res else '', 'error': error}


# ---------- Serveur ----------
class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == '/api/ping':
            return self._json({'ok': True, 'app': 'Julia', 'voice': stt is not None})
        if u.path == '/api/listen':
            return self._json(listen(q.get('lang', ['fr-FR'])[0]))
        if u.path == '/api/stt':
            return self._json(stt_state())
        if u.path == '/api/gen':
            return self._json(gen_state())
        if u.path == '/api/voices':
            return self._json({'voices': native_voices(q.get('lang', ['FR'])[0]), 'native': NT['ready']})
        if u.path == '/api/memory':
            return self._json(memory_info())
        super().do_GET()

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        try:
            data = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            data = {}
        if self.path == '/api/ask':
            return self._json(start_ask(data))
        if self.path == '/api/speak':
            threading.Thread(target=speak_text, daemon=True,
                             args=(str(data.get('text', '')), data.get('pitch', 1.0), data.get('rate', 1.0),
                                   data.get('lang', 'FR'), str(data.get('voice', '')))).start()
            return self._json({'ok': True})
        if self.path == '/api/speak/stop':
            speak_stop()
            return self._json({'ok': True})
        if self.path == '/api/memory/clear':
            sql('DELETE FROM conv')
            sql('DELETE FROM facts')
            sql('DELETE FROM cache')
            return self._json({'ok': True})
        if self.path == '/api/memory/delete':
            sql('DELETE FROM facts WHERE id=?', (int(data.get('id', 0)),))
            return self._json({'ok': True})
        self._json({'ok': False})

    def log_message(self, *a):
        pass


if ANDROID:
    _native_make()

if not os.environ.get('JULIA_NOSERVER'):
    ThreadingHTTPServer(('127.0.0.1', 5000), H).serve_forever()
