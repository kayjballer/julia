import os, json, threading, time, re, sqlite3, ssl, html, math, unicodedata, datetime, concurrent.futures
import urllib.request, urllib.parse, ctypes, hashlib
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
DB = os.path.join(DATA, 'julia.db')
GENERAL = 'http://127.0.0.1:8080'
CODER = 'http://127.0.0.1:8081'
NOBRAIN = "Mon cerveau n'est pas encore disponible."
QWEN_LIB = None
QWEN_READY = False


QWEN_MODEL_NAME = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
QWEN_MODEL_URL = (
    "https://huggingface.co/DhruvalLabs/"
    "Qwen3-4B-Instruct-2507-GGUF/resolve/main/"
    "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
)
QWEN_MODEL_SHA256 = (
    "1571ec5115bcfed4b4327fc27b5f44ea284806caf5331eef89326191c9b031d6"
)
QWEN_MODEL_LOCK = threading.Lock()

def ensure_qwen_model():
    model_dir = os.path.join(DATA, "models")
    model_path = os.path.join(model_dir, QWEN_MODEL_NAME)
    part_path = model_path + ".part"

    os.makedirs(model_dir, exist_ok=True)

    if os.path.isfile(model_path):
        return model_path

    with QWEN_MODEL_LOCK:
        if os.path.isfile(model_path):
            return model_path

        print("Julia: téléchargement du modèle Qwen...")

        downloaded = os.path.getsize(part_path) if os.path.isfile(part_path) else 0

        headers = {
            "User-Agent": "Julia-Android/1.0",
            "Accept": "*/*",
        }

        if downloaded > 0:
            headers["Range"] = "bytes=%d-" % downloaded

        req = urllib.request.Request(
            QWEN_MODEL_URL,
            headers=headers,
            method="GET",
        )

        try:
            response = urllib.request.urlopen(req, timeout=30)
        except Exception as exc:
            raise RuntimeError(
                "Téléchargement Qwen impossible: %s" % exc
            )

        status = getattr(response, "status", 200)

        if downloaded > 0 and status == 206:
            mode = "ab"
            total = downloaded
            content_range = response.headers.get("Content-Range", "")
            try:
                total_size = int(content_range.split("/")[-1])
            except Exception:
                total_size = 0
        else:
            mode = "wb"
            downloaded = 0
            total = 0
            try:
                total_size = int(response.headers.get("Content-Length", "0"))
            except Exception:
                total_size = 0

        last_report = -1

        with open(part_path, mode) as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break

                f.write(chunk)
                total += len(chunk)

                mb = total // (1024 * 1024)
                if mb >= last_report + 100:
                    last_report = mb
                    if total_size:
                        pct = (100.0 * total) / total_size
                        print(
                            "Julia: Qwen %.1f%% (%d MB)"
                            % (pct, mb)
                        )
                    else:
                        print("Julia: Qwen %d MB" % mb)

        response.close()

        print("Julia: vérification SHA-256...")

        h = hashlib.sha256()

        with open(part_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)

        digest = h.hexdigest()

        if digest != QWEN_MODEL_SHA256:
            try:
                os.remove(part_path)
            except OSError:
                pass

            raise RuntimeError(
                "SHA-256 Qwen invalide: %s" % digest
            )

        os.replace(part_path, model_path)

        print("Julia: modèle Qwen prêt.")

        return model_path

def init_qwen_native():
    global QWEN_LIB, QWEN_READY
    QWEN_LIB = None
    QWEN_READY = False

    try:
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        native_dir = activity.getApplicationInfo().nativeLibraryDir

        libs = [
            "libggml-base.so",
            "libggml-cpu.so",
            "libggml.so",
            "libllama.so",
            "libjulia_qwen.so",
        ]

        loaded = {}

        for name in libs:
            path = os.path.join(native_dir, name)

            if not os.path.isfile(path):
                raise RuntimeError("Bibliothèque absente: " + path)

            loaded[name] = ctypes.CDLL(
                path,
                mode=ctypes.RTLD_GLOBAL,
            )

        QWEN_LIB = loaded["libjulia_qwen.so"]

        QWEN_LIB.julia_qwen_generate.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]

        QWEN_LIB.julia_qwen_generate.restype = ctypes.c_int
        QWEN_READY = True

        print("Julia: moteur Qwen natif chargé depuis " + native_dir)

    except Exception as exc:
        QWEN_LIB = None
        QWEN_READY = False
        print("Julia: erreur chargement Qwen natif:", repr(exc))

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


# ---------- Date et heure (horloge du téléphone) ----------
JOURS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre',
        'novembre', 'décembre']
CLOCK = re.compile(r"(quelle heure (est-il|il est)|quel jour (est-on|sommes[- ]nous|on est|c'est|est-ce)|"
                   r"quelle année (est-on|sommes[- ]nous|on est)|quel mois (est-on|sommes[- ]nous|on est)|"
                   r"quelle est la date|la date d'aujourd'hui|on est le combien)", re.I)
FOLLOW = re.compile(r"^\s*(et\b|il\b|elle\b|son\b|sa\b|ses\b|leur|combien de temps|depuis quand|quel âge|"
                    r"où est né|quand est né|et lui|et elle)", re.I)


def local_now(data):
    try:
        return datetime.datetime.utcfromtimestamp(float(data['ts']) / 1000.0 + float(data.get('tz', 0)) * 60)
    except Exception:
        return datetime.datetime.now()


def fr_date(d):
    return '%s %s %s %d' % (JOURS[d.weekday()], '1er' if d.day == 1 else d.day, MOIS[d.month - 1], d.year)


def clock_answer(text, d):
    t = text.lower()
    if 'heure' in t:
        return "Il est %dh%02d." % (d.hour, d.minute)
    if 'année' in t:
        return "Nous sommes en %d." % d.year
    if 'mois' in t:
        return "Nous sommes en %s %d." % (MOIS[d.month - 1], d.year)
    return "Nous sommes le %s." % fr_date(d)


# ---------- RAG : connaissances stockées + recherche Internet ----------
STOP = set("""le la les un une des de du d l et ou a au aux en dans sur pour par avec sans ce cet cette ces qui que quoi
quel quelle quels quelles est sont etait etre ont il elle ils elles je tu nous vous on me te se son sa ses leur leurs
mon ma mes ton ta tes y ne pas plus tres comme mais donc or ni car si quand ou combien comment pourquoi qu c s j n m t
aujourd hui actuellement actuel actuelle maintenant dis cherche recherche internet web""".split())


def fold(s):
    s = unicodedata.normalize('NFD', s.lower())
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def toks(s):
    out = []
    for w in re.findall(r"[a-z0-9]+", fold(s)):
        if w in STOP or len(w) < 2:
            continue
        out.append(w[:-1] if len(w) > 3 and w[-1] in 'sx' else w)
    return out


def chunk_text(t, n=450):
    out, cur = [], ''
    for sent in re.split(r'(?<=[.!?])\s+', t.strip()):
        if len(cur) + len(sent) > n and cur:
            out.append(cur.strip())
            cur = ''
        cur += sent + ' '
    if cur.strip():
        out.append(cur.strip())
    return out


def bm25_rank(query, docs, k=4):
    qt = set(toks(query))
    if not qt or not docs:
        return []
    dts = [toks(d['title'] + ' ' + d['text']) for d in docs]
    n = len(docs)
    avg = (sum(len(x) for x in dts) / n) or 1
    df = {t: sum(1 for x in dts if t in x) for t in qt}
    res = []
    for d, tk in zip(docs, dts):
        sc, st, L = 0.0, set(tk), len(tk)
        for t in qt:
            f = tk.count(t)
            if f:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                sc += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * L / avg))
        if sc > 0:
            res.append((sc, sum(1 for t in qt if t in st) / len(qt), d))
    res.sort(key=lambda x: -x[0])
    return res[:k]


def kb_docs(maxage=None):
    now, docs = time.time(), []
    for r in sql('SELECT query,ts,result FROM cache ORDER BY ts DESC LIMIT 200', fetch=True):
        if maxage and now - r['ts'] > maxage:
            continue
        try:
            for t, x in json.loads(r['result']):
                docs.append({'title': t, 'text': x, 'ts': r['ts'], 'q': r['query']})
        except Exception:
            continue
    return docs


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
               '&prop=extracts&exintro=1&explaintext=1&exchars=1100&redirects=1&gsrsearch=%s'
               % (lang, urllib.parse.quote(q)))
        d = json.loads(http_get(url))
        pages = sorted(((d.get('query') or {}).get('pages') or {}).values(), key=lambda p: p.get('index', 9))
        res = []
        for p in pages:
            for c in chunk_text(p.get('extract') or '', 500):
                res.append(['Wikipédia - ' + p['title'], c])
        if res:
            return res
    return []


def ddg_search(q):
    page = http_get('https://html.duckduckgo.com/html/',
                    data=urllib.parse.urlencode({'q': q, 'kl': 'fr-fr'}).encode())
    titles = [tags(x) for x in re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)]
    snips = [tags(x) for x in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)]
    return [['Web - ' + t, s] for t, s in zip(titles, snips)][:4]


def bing_search(q):
    page = http_get('https://www.bing.com/search?setlang=fr&cc=FR&q=' + urllib.parse.quote(q))
    out = []
    for blk in re.findall(r'<li class="b_algo".*?</li>', page, re.S)[:4]:
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


def live_search(q, mode, year):
    qs = q if (mode != 'fresh' or re.search(r'\b20\d\d\b', q)) else '%s %d' % (q, year)
    order = [('web', ddg_search), ('bing', bing_search), ('wiki', wiki_search)]
    if mode != 'fresh':
        order = [order[2], order[0], order[1]]
    chunks, errors, vides = [], [], 0
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=3)
    try:
        futs = [(name, ex.submit(fn, q if name == 'wiki' else qs)) for name, fn in order]
        for name, f in futs:
            try:
                res = f.result(timeout=12)
            except Exception as e:
                errors.append(name + ': ' + err_short(e))
                continue
            if res:
                chunks += res
            else:
                vides += 1
            if chunks and mode != 'fresh':
                break
    finally:
        ex.shutdown(wait=False)
    return chunks[:8], ' ; '.join(errors)[:160], bool(errors) and not chunks and vides == 0


def rag(text, mode, year):
    q = entity(text)
    maxage = 1800 if mode == 'fresh' else 7 * 86400
    hits = [h for h in bm25_rank(q, kb_docs(maxage)) if h[1] >= 0.5]
    err, status, all_failed = '', 'ok', False
    if not hits:
        chunks, err, all_failed = live_search(q, mode, year)
        if chunks:
            sql('INSERT OR REPLACE INTO cache(query,ts,result) VALUES(?,?,?)', (q.lower(), time.time(), json.dumps(chunks)))
            hits = [h for h in bm25_rank(q, kb_docs(maxage)) if h[1] >= 0.5]
    if not hits:
        old = [h for h in bm25_rank(q, kb_docs(None)) if h[1] >= 0.6]
        if old:
            hits, status = old, 'stale'
    if not hits:
        return {'ctx': '', 'sources': [], 'status': 'fail' if all_failed else 'empty', 'error': err, 'age': 0}
    best = hits[0][0]
    top = [h for h in hits if h[0] >= 0.6 * best][:3]
    ctx = '\n'.join('[%s] %s' % (h[2]['title'][:60], h[2]['text'][:420]) for h in top)[:1200]
    srcs = []
    for h in top:
        if h[2]['title'] not in srcs:
            srcs.append(h[2]['title'])
    return {'ctx': ctx, 'sources': srcs, 'status': status, 'error': err,
            'age': time.time() - max(h[2]['ts'] for h in top) if status == 'stale' else 0}


def unsupported(answer, ctx, question):
    base = set(toks(ctx + ' ' + question))
    bad = []
    for m in re.finditer(r"[A-ZÀ-Ý][\wÀ-ÿ'’-]{2,}|\d{3,}", answer):
        w = m.group(0)
        if not w[0].isdigit():
            pre = answer[:m.start()].rstrip()
            if not pre or pre[-1] in '.!?…:':
                continue
        t = toks(w)
        if t and all(x not in base for x in t):
            bad.append(w)
    return bad


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
def system_prompt(lang, facts, code, dt):
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
NOWEB = {'FR': "Je n'arrive pas à consulter Internet pour vérifier ça, donc je préfère ne pas te répondre de tête : "
               "cette information a peut-être changé.",
         'EN': "I can't reach the Internet to check that, and it may have changed, so I'd rather not answer from memory."}
NOTFOUND = {'FR': "Je n'ai pas trouvé cette information de façon fiable dans mes sources, donc je préfère ne pas te "
                  "répondre de tête.",
            'EN': "I couldn't find that reliably in my sources, so I'd rather not answer from memory."}


def user_content(text, lang, web, note, code, dt=None):
    if lang == 'EN':
        head = ("Excerpts found (sources):\n%s\n\nQuestion: " % web) if web else ''
        tail = ("(Answer only from these excerpts, in 1 to 3 short sentences. If the answer is not in them, say "
                "you did not find it.)" if web else "(Answer in a few short sentences.)")
    else:
        head = ("Extraits trouvés (sources) :\n%s\n\nQuestion : " % web) if web else ''
        if web:
            tail = ("(Réponds uniquement avec ces extraits, en 1 à 3 phrases courtes, en me tutoyant. Si la réponse "
                    "n'y figure pas, dis que tu ne l'as pas trouvée.)")
        elif code:
            tail = "(Réponds en français, en me tutoyant.)"
        else:
            tail = "(Réponds en quelques phrases courtes, en me tutoyant.)"
    dl = ''
    if dt is not None and (web or DATEQ.search(text) or FRESH.search(text)):
        dl = ("\n(Now: %s.)" % dt.strftime('%A %B %d %Y, %H:%M')) if lang == 'EN' else \
             ("\n(Nous sommes le %s, %dh%02d.)" % (fr_date(dt), dt.hour, dt.minute))
    return head + text + "\n" + tail + dl + (("\n" + note) if note else '')


def build_messages(text, lang, mem, web, note, code, dt):
    facts, hist = [], []
    if mem:
        for f in extract_facts(text):
            sql('INSERT OR IGNORE INTO facts(ts,text) VALUES(?,?)', (time.time(), f))
        facts = [r['text'] for r in sql('SELECT text FROM facts ORDER BY id DESC LIMIT 12', fetch=True)]
        nu = sql("SELECT COUNT(*) AS n FROM conv WHERE role='user'", fetch=True)[0]['n']
        start = 0 if nu <= 8 else ((nu - 4) // 4) * 4
        rows = sql('SELECT role,text FROM conv ORDER BY id LIMIT 16 OFFSET ?', (start * 2,), fetch=True)
        pairs, i = [], 0
        while i + 1 < len(rows):
            if rows[i]['role'] == 'user' and rows[i + 1]['role'] == 'assistant':
                pairs.append((rows[i]['text'], rows[i + 1]['text']))
                i += 2
            else:
                i += 1
        for u, a in pairs:
            if lang == 'EN' or not VOUS.search(a):
                hist += [{'role': 'user', 'content': u}, {'role': 'assistant', 'content': a}]
    shot = [] if code else (SHOT_EN if lang == 'EN' else SHOT_FR)
    return [{'role': 'system', 'content': system_prompt(lang, facts, code, dt)}] + shot + hist + \
           [{'role': 'user', 'content': user_content(text, lang, web, note, code, dt)}]


TUTOIE = [(r"\bpuis-je vous aider\b", "puis-je t'aider"), (r"\bvous aider\b", "t'aider"), (r"\bje vous\b", "je te"),
          (r"\bpour vous\b", "pour toi"), (r"\bavec vous\b", "avec toi"), (r"\bvous avez\b", "tu as"),
          (r"\bvous êtes\b", "tu es"), (r"\bvous pouvez\b", "tu peux"), (r"\bvous voulez\b", "tu veux"),
          (r"\bvous devez\b", "tu dois"), (r"\bvous allez\b", "tu vas"), (r"\bvous souhaitez\b", "tu souhaites"),
          (r"\bvous cherchez\b", "tu cherches")]


def tutoie(t):
    for pat, rp in TUTOIE:
        t = re.sub(pat, lambda m, rp=rp: (rp[0].upper() + rp[1:]) if m.group(0)[0].isupper() else rp, t, flags=re.I)
    return t


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
    if not QWEN_READY or QWEN_LIB is None:
        raise RuntimeError("Qwen natif indisponible: " + QWEN_ERROR)

    system = messages[0]['content'] if messages and messages[0].get('role') == 'system' else ''
    history = messages[1:-1] if messages else []
    question = messages[-1]['content'] if messages else text

    parts = []
    if system:
        parts.append(system)

    for m in history:
        role = m.get('role', 'user')
        content = m.get('content', '')
        parts.append(role + ": " + content)

    parts.append("user: " + question)
    prompt = "\n".join(parts)

    model_path = ensure_qwen_model()

    out = ctypes.create_string_buffer(32768)

    rc = QWEN_LIB.julia_qwen_generate(
        model_path.encode("utf-8"),
        prompt.encode("utf-8"),
        out,
        len(out),
    )

    if rc != 0:
        raise RuntimeError("Qwen natif erreur %d" % rc)

    answer = out.value.decode("utf-8", "replace").strip()
    if answer:
        on_text(answer)

    return "stop"
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


def finish_direct(gid, out, voice, lang, status='none', err=''):
    if GEN['id'] != gid:
        return
    GEN.update(text=out, brain=True, truncated=False, stage='', hold=False, web_status=status, web_error=err)
    if voice:
        threading.Thread(target=speak_text, args=(out, 1.0, 1.0, lang, ''), daemon=True).start()
    GEN['done'] = True


def generate(gid, text, lang, voice, mem, web, pref, dt):
    t0 = time.time()
    if lang != 'EN' and CLOCK.search(text):
        return finish_direct(gid, clock_answer(text, dt), voice, lang)
    model = pick(text, pref)
    mode = web_mode(text, model) if web else None
    info = {'ctx': '', 'sources': [], 'status': 'none', 'error': '', 'age': 0}
    if mode:
        GEN.update(stage='search', hold=(mode == 'fresh'))
        try:
            info = rag(text, mode, dt.year)
        except Exception as e:
            info = {'ctx': '', 'sources': [], 'status': 'fail', 'error': err_short(e), 'age': 0}
        if info['ctx']:
            LASTWEB.update(ts=time.time(), ctx=info['ctx'], sources=info['sources'])
    elif web and FOLLOW.search(text) and LASTWEB['ctx'] and time.time() - LASTWEB['ts'] < 600:
        info = {'ctx': LASTWEB['ctx'], 'sources': LASTWEB['sources'], 'status': 'ok', 'error': '', 'age': 0}
    if GEN['id'] != gid:
        return
    GEN.update(web_status=info['status'], web_error=info['error'], sources=info['sources'] if info['ctx'] else [])
    if mode == 'fresh' and info['status'] in ('fail', 'empty'):
        msg = (NOWEB if info['status'] == 'fail' else NOTFOUND)['EN' if lang == 'EN' else 'FR']
        return finish_direct(gid, msg, voice, lang, info['status'], info['error'])
    GEN['stage'] = 'think'
    note = ''
    if info['status'] == 'stale':
        note = ("(Attention : ces informations ont été enregistrées il y a %d jour(s) et peuvent ne plus être à jour.)"
                % max(1, int(info['age'] / 86400)))
    msgs = build_messages(text, lang, mem, info['ctx'], note, model == 'code', dt)
    hold = GEN['hold']
    buf = []

    def sink(d):
        if GEN['id'] == gid:
            if GEN['ttft'] is None:
                GEN['ttft'] = round(time.time() - t0, 1)
            buf.append(d)
            if not hold:
                GEN['text'] = ''.join(buf) if lang == 'EN' else tutoie(''.join(buf))

    def call(ms):
        for m in (model, 'general' if model == 'code' else 'code'):
            try:
                GEN['model'] = m
                return stream_brain(m, ms, sink, text), True
            except Exception:
                if buf:
                    break
        return None, False

    finish, ok = call(msgs)
    out = clean_think(''.join(buf))
    if lang != 'EN':
        out = tutoie(out)
    key = 'EN' if lang == 'EN' else 'FR'
    if ok and mode == 'fresh' and info['ctx'] and out and unsupported(out, info['ctx'], text):
        del buf[:]
        strict = list(msgs)
        strict[-1] = {'role': 'user', 'content': msgs[-1]['content'] +
                      "\n(Ne cite aucun nom, aucune date, aucun chiffre qui ne figure pas dans les extraits.)"}
        finish, ok = call(strict)
        out = clean_think(''.join(buf))
        if ok and (not out or unsupported(out, info['ctx'], text)):
            out = NOTFOUND[key]
    if GEN['id'] != gid:
        return
    truncated = bool(ok and finish == 'length' and out)
    if truncated:
        out = trim_sentence(out)
    if not out:
        out = "Je n'ai pas de réponse pour l'instant." if ok else NOBRAIN
    GEN.update(text=out, brain=ok, truncated=truncated, stage='', hold=False, dur=round(time.time() - t0, 1))
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
    GEN.update(text='', done=False, brain=True, stage='', truncated=False, web_status='none', web_error='',
               sources=[], hold=False, ttft=None, dur=None)
    threading.Thread(target=generate, daemon=True,
                     args=(gid, str(data.get('text', '')), data.get('lang', 'FR'), data.get('voice', 0),
                           data.get('mem', 1), data.get('web', 1), data.get('model', 'auto'),
                           local_now(data))).start()
    return {'ok': True, 'id': gid}


def gen_state():
    t = GEN['text'] if GEN['done'] else ('' if GEN['hold'] else clean_think(GEN['text']))
    return {'text': t, 'done': GEN['done'], 'brain': GEN['brain'], 'id': GEN['id'],
            'stage': GEN['stage'], 'model': GEN['model'], 'truncated': GEN['truncated'],
            'web_status': GEN['web_status'], 'web_error': GEN['web_error'], 'sources': GEN['sources'],
            'ttft': GEN['ttft'], 'dur': GEN['dur']}


def memory_info():
    n = sql("SELECT COUNT(*) AS n FROM conv WHERE role='user'", fetch=True)[0]['n']
    facts = sql('SELECT id,text FROM facts ORDER BY id DESC LIMIT 50', fetch=True)
    recent = sql('SELECT role,text FROM (SELECT id,role,text FROM conv ORDER BY id DESC LIMIT 8) ORDER BY id', fetch=True)
    webk = []
    for r in sql('SELECT query,ts,result FROM cache ORDER BY ts DESC LIMIT 30', fetch=True):
        try:
            items = json.loads(r['result'])
            webk.append({'q': r['query'], 'title': items[0][0], 'snippet': items[0][1][:140], 'ts': r['ts'],
                         'n': len(items)})
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


def warmup():
    for _ in range(120):
        try:
            urllib.request.urlopen(GENERAL + '/health', timeout=2).read()
            break
        except Exception:
            time.sleep(5)
    else:
        return
    try:
        msgs = build_messages('Bonjour', 'FR', 1, '', '', False, datetime.datetime.now())
        msgs[-1] = {'role': 'user', 'content': '.'}
        body = json.dumps({'messages': msgs, 'max_tokens': 1, 'stream': False, 'cache_prompt': True}).encode()
        req = urllib.request.Request(GENERAL + '/v1/chat/completions', data=body,
                                     headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=300).read()
    except Exception:
        pass


if ANDROID:
    _native_make()

init_qwen_native()

if not os.environ.get('JULIA_NOSERVER'):
    ThreadingHTTPServer(('127.0.0.1', 5000), H).serve_forever()
