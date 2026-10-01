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
GEN = {'id': 0, 'text': '', 'done': True, 'brain': True, 'stage': '', 'model': 'general', 'truncated': False,
       'web_status': 'none', 'web_error': '', 'sources': []}
LASTWEB = {'ts': 0, 'ctx': '', 'sources': []}
VOUS = re.compile(r"\b(vous|votre|vos)\b", re.I)
CODE_RE = re.compile(r"\b(code|coder|coding|python|javascript|java|html|css|bash|shell|termux|linux|script|fonction|"
                     r"variable|boucle|bug|compile|programme|programmer|programmation|api|sql|json|regex|git|github|"
                     r"algorithme|debug|déboguer)\b", re.I)
LONG_RE = re.compile(r"(raconte|explique|détaill|développe|histoire|résume|décris|comment fonctionne|pourquoi|"
                     r"continue|la suite|plus de détails|en détail)", re.I)
ENCYC = re.compile(r"(c'est qui|qui est|qui était|qui sont|qu'est-ce que|qu'est ce que|c'est quoi|parle-moi de|parle moi de)", re.I)
FRESH = re.compile(r"\b(aujourd'hui|actualités?|dernier|derniers|dernière|récent|maintenant|en ce moment|météo|score|"
                   r"résultats?|prix|cours|combien coûte|président|premier ministre|sortie|news)\b", re.I)
QWORD = re.compile(r"^\s*(qui|quel|quelle|quels|quelles|quand|où|combien|en quelle année|de quand)\b", re.I)
EXPL = re.compile(r"\b(cherche|recherche|google|sur internet|sur le web|vérifie)\b", re.I)

def make_ctx():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    for d in ('/apex/com.android.conscrypt/cacerts', '/system/etc/security/cacerts'):
        if os.path.isdir(d):
            try:
                ctx.load_verify_locations(capath=d)
            except Exception:
                pass
    return ctx


CTX = make_ctx()

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


# ---------- Internet (Wikipédia, DuckDuckGo, Bing) avec mémoire des recherches ----------
UA = {'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36 Julia/1.0',
      'Accept-Language': 'fr,en;q=0.8'}


def err_short(e):
    return ('%s %s' % (type(e).__name__, str(e)[:70])).strip()


def tags(x):
    return html.unescape(re.sub(r'<[^>]+>', '', x)).strip()


def http_get(url, data=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode('utf-8', 'ignore')


def wiki_search(q):
    for lang in ('fr', 'en'):
        url = ('https://%s.wikipedia.org/w/api.php?action=query&format=json&generator=search&gsrlimit=2'
               '&prop=extracts&exintro=1&explaintext=1&exchars=600&redirects=1&gsrsearch=%s'
               % (lang, urllib.parse.quote(q)))
        d = json.loads(http_get(url))
        pages = sorted(((d.get('query') or {}).get('pages') or {}).values(), key=lambda p: p.get('index', 9))
        res = [['Wikipédia - ' + p['title'], p['extract'].strip()] for p in pages if p.get('extract')]
        if res:
            return res
    return []


def ddg_search(q):
    page = http_get('https://html.duckduckgo.com/html/',
                    data=urllib.parse.urlencode({'q': q, 'kl': 'fr-fr'}).encode())
    titles = [tags(x) for x in re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)]
    snips = [tags(x) for x in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)]
    return [['Web - ' + t, s] for t, s in zip(titles, snips)][:3]


def bing_search(q):
    page = http_get('https://www.bing.com/search?setlang=fr&cc=FR&q=' + urllib.parse.quote(q))
    out = []
    for blk in re.findall(r'<li class="b_algo".*?</li>', page, re.S)[:3]:
        t = re.search(r'<h2[^>]*>.*?<a[^>]*>(.*?)</a>', blk, re.S)
        sn = re.search(r'<p[^>]*>(.*?)</p>', blk, re.S)
        if t and sn:
            out.append(['Web - ' + tags(t.group(1)), tags(sn.group(1))])
    return out


def entity(text):
    t = text.replace('’', "'")
    t = re.sub(r"(?i)(c'est qui|qui est|qui était|qui sont|qu'est-ce que|qu'est ce que|c'est quoi|parle-moi de|"
               r"parle moi de|cherche|recherche|sur internet|sur le web|dis-moi)", ' ', t)
    t = re.sub(r"[?!.,]", ' ', t)
    return re.sub(r'\s+', ' ', t).strip() or text


def web_mode(text, model):
    if EXPL.search(text) or FRESH.search(text):
        return 'fresh'
    if ENCYC.search(text) or (QWORD.search(text) and model != 'code'):
        return 'enc'
    return None


def fmt_web(res):
    return '\n'.join('[%s] %s' % (t, sn[:600]) for t, sn in res[:2])[:1100]


def web_lookup(text, mode):
    q = entity(text)
    key = q.lower()
    ttl = 1800 if mode == 'fresh' else 7 * 86400
    row = sql('SELECT ts,result FROM cache WHERE query=?', (key,), fetch=True)
    stale = None
    if row:
        stale = (row[0]['ts'], json.loads(row[0]['result']))
        if time.time() - stale[0] < ttl:
            return {'ctx': fmt_web(stale[1]), 'sources': [r[0] for r in stale[1][:2]], 'status': 'ok', 'error': '', 'age': 0}
    order = [('web', ddg_search), ('bing', bing_search), ('wiki', wiki_search)]
    if mode != 'fresh':
        order = [order[2], order[0], order[1]]
    errors = []
    for name, fn in order:
        try:
            res = fn(q)
        except Exception as e:
            errors.append(name + ': ' + err_short(e))
            continue
        if res:
            sql('INSERT OR REPLACE INTO cache(query,ts,result) VALUES(?,?,?)', (key, time.time(), json.dumps(res)))
            return {'ctx': fmt_web(res), 'sources': [r[0] for r in res[:2]], 'status': 'ok', 'error': '', 'age': 0}
        errors.append(name + ': vide')
    err = ' ; '.join(errors)[:160]
    if stale:
        return {'ctx': fmt_web(stale[1]), 'sources': [r[0] for r in stale[1][:2]], 'status': 'stale',
                'error': err, 'age': time.time() - stale[0]}
    return {'ctx': '', 'sources': [], 'status': 'fail', 'error': err, 'age': 0}


def netcheck(q):
    out = {}
    for name, fn in (('wiki', wiki_search), ('ddg', ddg_search), ('bing', bing_search)):
        t0 = time.time()
        try:
            r = fn(q)
            out[name] = {'ok': True, 'n': len(r), 'first': r[0][0] if r else '', 's': round(time.time() - t0, 1)}
        except Exception as e:
            out[name] = {'ok': False, 'error': err_short(e), 's': round(time.time() - t0, 1)}
    return out


# ---------- Cerveau (deux serveurs llama : Général 8080, Coder 8081) ----------
def system_prompt(lang, facts, code):
    if lang == 'EN':
        s = ("You are Julia, a warm and direct local voice assistant. Always answer in English. "
             "Usually answer in 1 to 3 short spoken sentences, no lists, no emojis. If asked for a story, an "
             "explanation or details, you may expand in one short paragraph (6 sentences max). "
             "If you don't know, say so instead of guessing.")
        if code:
            s += " For code, give a short block then explain in one sentence what it does."
        if facts:
            s += "\nWhat you know about the user: " + " ; ".join(facts) + "."
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
    return s


SHOT_FR = [{'role': 'user', 'content': 'Salut, tu peux te présenter ?'},
           {'role': 'assistant', 'content': "Salut ! Moi c'est Julia, ton assistante vocale. Dis-moi ce dont tu as besoin."}]
SHOT_EN = [{'role': 'user', 'content': 'Hi, can you introduce yourself?'},
           {'role': 'assistant', 'content': "Hi! I'm Julia, your voice assistant. Tell me what you need."}]
NOWEB_FR = ("Je n'arrive pas à consulter Internet pour vérifier ça, donc je préfère ne pas te répondre de tête : "
            "cette information a peut-être changé.")
NOWEB_EN = "I can't reach the Internet to check that, and it may have changed, so I'd rather not answer from memory."


def user_content(text, lang, web, note, code):
    if lang == 'EN':
        head = ("Information found on the Internet:\n%s\n\nQuestion: " % web) if web else ''
        tail = "(Answer in a few short sentences" + (", relying on this information" if web else "") + ".)"
    else:
        head = ("Informations trouvées sur Internet :\n%s\n\nQuestion : " % web) if web else ''
        tail = ("(Réponds en français, en me tutoyant." if code else
                "(Réponds en quelques phrases courtes, en me tutoyant" + (", en t'appuyant sur ces informations" if web else "") + ".")
        tail += ")"
    return head + text + "\n" + tail + (("\n" + note) if note else '')


def build_messages(text, lang, mem, web, note, code):
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
    return [{'role': 'system', 'content': system_prompt(lang, facts, code)}] + shot + hist + \
           [{'role': 'user', 'content': user_content(text, lang, web, note, code)}]


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
    mode = web_mode(text, model) if web else None
    info = {'ctx': '', 'sources': [], 'status': 'none', 'error': '', 'age': 0}
    if mode:
        GEN['stage'] = 'search'
        try:
            info = web_lookup(text, mode)
        except Exception as e:
            info = {'ctx': '', 'sources': [], 'status': 'fail', 'error': err_short(e), 'age': 0}
        if info['ctx']:
            LASTWEB.update(ts=time.time(), ctx=info['ctx'], sources=info['sources'])
    elif web and LASTWEB['ctx'] and time.time() - LASTWEB['ts'] < 600:
        info = {'ctx': LASTWEB['ctx'], 'sources': LASTWEB['sources'], 'status': 'ok', 'error': '', 'age': 0}
    if GEN['id'] != gid:
        return
    GEN.update(web_status=info['status'], web_error=info['error'], sources=info['sources'])
    if mode == 'fresh' and info['status'] == 'fail':
        out = NOWEB_EN if lang == 'EN' else NOWEB_FR
        GEN.update(text=out, brain=True, truncated=False, stage='')
        if voice:
            threading.Thread(target=speak_text, args=(out, 1.0, 1.0, lang, ''), daemon=True).start()
        GEN['done'] = True
        return
    GEN['stage'] = 'think'
    note = ''
    if info['status'] == 'stale':
        note = ("(Attention : ces informations ont été enregistrées il y a %d jour(s) et peuvent ne plus être à jour.)"
                % max(1, int(info['age'] / 86400)))
    msgs = build_messages(text, lang, mem, info['ctx'], note, model == 'code')

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
    GEN.update(text='', done=False, brain=True, stage='', truncated=False, web_status='none', web_error='', sources=[])
    threading.Thread(target=generate, daemon=True,
                     args=(gid, str(data.get('text', '')), data.get('lang', 'FR'), data.get('voice', 0),
                           data.get('mem', 1), data.get('web', 1), data.get('model', 'auto'))).start()
    return {'ok': True, 'id': gid}


def gen_state():
    t = GEN['text'] if GEN['done'] else clean_think(GEN['text'])
    return {'text': t, 'done': GEN['done'], 'brain': GEN['brain'], 'id': GEN['id'],
            'stage': GEN['stage'], 'model': GEN['model'], 'truncated': GEN['truncated'],
            'web_status': GEN['web_status'], 'web_error': GEN['web_error'], 'sources': GEN['sources']}


def memory_info():
    n = sql("SELECT COUNT(*) AS n FROM conv WHERE role='user'", fetch=True)[0]['n']
    facts = sql('SELECT id,text FROM facts ORDER BY id DESC LIMIT 50', fetch=True)
    recent = sql('SELECT role,text FROM (SELECT id,role,text FROM conv ORDER BY id DESC LIMIT 8) ORDER BY id', fetch=True)
    webk = []
    for r in sql('SELECT query,ts,result FROM cache ORDER BY ts DESC LIMIT 30', fetch=True):
        try:
            first = json.loads(r['result'])[0]
            webk.append({'q': r['query'], 'title': first[0], 'snippet': first[1][:140], 'ts': r['ts']})
        except Exception:
            pass
    return {'count': n, 'facts': facts, 'recent': recent, 'web': webk}


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
        if u.path == '/api/netcheck':
            return self._json(netcheck(q.get('q', ['zeus'])[0]))
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
        if self.path == '/api/memory/forget':
            sql('DELETE FROM cache WHERE query=?', (str(data.get('q', '')),))
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
