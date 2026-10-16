import os, json, threading, time, re, sqlite3, ssl, html, math, unicodedata, datetime, hashlib, concurrent.futures
import urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import brain

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
DB = os.path.join(DATA, 'julia.db')
GENERAL = 'http://127.0.0.1:8080'
CODER = 'http://127.0.0.1:8081'
NOBRAIN = "Mon cerveau démarre, patiente quelques secondes."
ST = {'active': False, 't0': 0.0, 'err': None}
GEN = {'id': 0, 'text': '', 'done': True, 'brain': True, 'stage': '', 'model': 'general', 'truncated': False,
       'web_status': 'none', 'web_error': '', 'sources': [], 'hold': False, 'ttft': None, 'dur': None, 'web_q': '', 'web_ms': 0, 'web_n': 0, 'route': '', 'act': ''}
LASTWEB = {'ts': 0, 'ctx': '', 'sources': [], 'q': '', 'mode': None}
VOUS = re.compile(r"\b(vous|votre|vos)\b", re.I)
CODE_RE = re.compile(r"\b(code|coder|coding|python|javascript|java|html|css|bash|shell|termux|linux|script|fonction|"
                     r"variable|boucle|bug|compile|programme|programmer|programmation|api|sql|json|regex|git|github|"
                     r"algorithme|debug|déboguer)\b", re.I)
LONG_RE = re.compile(r"(raconte|explique|détaill|développe|histoire|résume|décris|comment fonctionne|pourquoi|"
                     r"continue|la suite|plus de détails|en détail)", re.I)
ENCYC = re.compile(r"(c'est qui|qui est|qui était|qui sont|qu'est-ce que|qu'est ce que|c'est quoi|parle-moi de|parle moi de|définition|définis|signifie|veut dire|ça veut dire|explique-moi|explique moi|expliquer|explique)", re.I)
DEF = re.compile(r"(définition|définis|signifie|veut dire|ça veut dire|le mot )", re.I)
FRESH = re.compile(r"\b(aujourd'hui|actualités?|dernier|derniers|dernière|récent|maintenant|en ce moment|météo|score|"
                   r"résultats?|prix|cours|combien coûte|président|premier ministre|sortie|news)\b", re.I)
QWORD = re.compile(r"^\s*(qui|quel|quelle|quels|quelles|quand|où|combien|en quelle année|de quand)\b", re.I)
DATEQ = re.compile(r"\b(aujourd'hui|demain|hier|ce soir|ce matin|cette semaine|ce mois|cette année|maintenant|actuel|en ce moment|dans \d+)", re.I)
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


PATTERNS += [
    (r"\bj'ai (\d{1,2}) ans\b", "Il a {} ans"),
    (r"\bje parle (fran[cç]ais|anglais|wolof|espagnol|italien|arabe|portugais)", "Il parle {}"),
    (r"\bje viens (?:de|du|d') ?([\w'\- ]{2,30})", "Il vient de {}"),
    (r"\bje joue (?:au |à la |à l'|en |comme )?([\w'\- ]{2,30})", "Il joue {}"),
    (r"\bmon (?:équipe|club) (?:préféré|préférée|favori|favorite) est (?:le |la |l')?([\w'\- ]{2,30})", "Son équipe préférée : {}"),
    (r"\bje veux devenir ([\w'\- ]{3,40})", "Il veut devenir {}"),
    (r"\bmon objectif (?:est|c'est) (?:de )?([\w'\- ]{3,50})", "Son objectif : {}"),
    (r"\bje suis (?:un |une )?(étudiant|étudiante|lycéen|lycéenne|basketteur|basketteuse|entrepreneur|développeur|développeuse|footballeur)\b", "Il est {}"),
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
        w = w[:-1] if len(w) > 3 and w[-1] in 'sx' else w
        out.append(w[:-1] if len(w) > 4 and w[-1] == 'e' else w)
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
    dts = [toks(d['title']) * 2 + toks(d['text']) for d in docs]
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


def wiki_exact(lang, q):
    d = json.loads(http_get('https://%s.wikipedia.org/w/api.php?action=query&format=json&prop=extracts&exintro=1&explaintext=1'
                            '&exchars=1100&redirects=1&titles=%s' % (lang, urllib.parse.quote(q))))
    for p in ((d.get('query') or {}).get('pages') or {}).values():
        ex = p.get('extract') or ''
        if 'missing' not in p and ex and not re.search(r'peut désigner|may refer to|peut faire référence', ex[:250]):
            return [['Wikipédia - ' + p['title'], c] for c in chunk_text(ex, 500)]
    return []


def wiki_snips(lang, q):
    d = json.loads(http_get('https://%s.wikipedia.org/w/api.php?action=query&format=json&list=search&srlimit=3&srprop=snippet&srsearch=%s'
                            % (lang, urllib.parse.quote(q))))
    return [['Wikipédia - ' + r['title'], tags(r.get('snippet', ''))] for r in (d.get('query') or {}).get('search', [])
            if len(tags(r.get('snippet', ''))) > 30]


def wiki_search(q):
    for lang in ('fr', 'en'):
        res0 = []
        if len(q.split()) <= 4:
            try:
                res0 = wiki_exact(lang, q)
            except Exception:
                res0 = []
        if len(toks(q)) >= 3:
            try:
                res0 += wiki_snips(lang, q)
            except Exception:
                pass
        if res0:
            return res0
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


def wikt_search(q):
    w = (q.strip().split(' ') or [q])[-1].lower()
    d = json.loads(http_get('https://fr.wiktionary.org/api/rest_v1/page/definition/' + urllib.parse.quote(w)))
    out = []
    for e in (d.get('fr') or []):
        defs = [tags(x.get('definition', '')) for x in e.get('definitions', [])][:3]
        defs = [x for x in defs if x]
        if defs:
            out.append(['Wiktionnaire - %s (%s)' % (w, str(e.get('partOfSpeech', '')).lower()), ' ; '.join(defs)[:400]])
    return out[:2]


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
               r"parle moi de|définition|définis|signifie|veut dire|ça veut dire|explique-moi|explique moi|expliquer|explique|du mot|le mot|cherche|recherche|sur internet|sur le web|dis-moi)", ' ', t)
    t = re.sub(r"[?!.,]", ' ', t)
    return re.sub(r'\s+', ' ', t).strip() or text


def web_mode(text, model):
    if EXPL.search(text) or FRESH.search(text):
        return 'fresh'
    if model != 'code' and (ENCYC.search(text) or QWORD.search(text)):
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


def rag(text, mode, year, subject=''):
    t0 = time.time()
    q = ((subject + ' ') if subject else '') + entity(text)
    maxage = 1800 if mode == 'fresh' else 7 * 86400
    hits = [h for h in bm25_rank(q, kb_docs(maxage)) if h[1] >= 0.5]
    err, status, all_failed = '', 'ok', False
    if not hits and DEF.search(text):
        try:
            ch = wikt_search(q)
        except Exception as e:
            ch, err = [], err_short(e)
        if ch:
            sql('INSERT OR REPLACE INTO cache(query,ts,result) VALUES(?,?,?)', (q.lower(), time.time(), json.dumps(ch)))
            hits = [h for h in bm25_rank(q, kb_docs(maxage)) if h[1] >= 0.5]
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
        return {'ctx': '', 'sources': [], 'status': 'fail' if all_failed else 'empty', 'error': err, 'age': 0,
                'q': q, 'ms': round(time.time() - t0, 1), 'n': 0}
    best = hits[0][0]
    top = [h for h in hits if h[0] >= 0.6 * best][:2]
    ctx = '\n'.join('[%s] %s' % (h[2]['title'][:60], h[2]['text'][:320]) for h in top)[:700]
    srcs = []
    for h in top:
        if h[2]['title'] not in srcs:
            srcs.append(h[2]['title'])
    return {'ctx': ctx, 'sources': srcs, 'status': status, 'error': err, 'q': q, 'ms': round(time.time() - t0, 1), 'n': len(top),
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


def pick_facts(text):
    rows = [r['text'] for r in sql('SELECT text FROM facts ORDER BY id DESC LIMIT 100', fetch=True)]
    core = [f for f in rows if re.match(r"Il (s'appelle|habite|parle|vient)", f)][:3]
    if len(text.split()) < 3:
        return core[:1]
    rest = [f for f in rows if f not in core]
    rel = [h[2]['text'] for h in bm25_rank(text, [{'title': '', 'text': f} for f in rest], k=3) if h[0] >= 0.2] if rest else []
    out = []
    for f in core + rel + rest[:2]:
        if f not in out:
            out.append(f)
    return out[:6]


def build_messages(text, lang, mem, web, note, code, dt):
    facts, hist = [], []
    if mem:
        for f in extract_facts(text):
            sql('INSERT OR IGNORE INTO facts(ts,text) VALUES(?,?)', (time.time(), f))
        facts = pick_facts(text)
        nu = sql("SELECT COUNT(*) AS n FROM conv WHERE role='user'", fetch=True)[0]['n']
        start = 0 if nu <= 4 else ((nu - 2) // 2) * 2
        rows = sql('SELECT role,text FROM conv ORDER BY id LIMIT 8 OFFSET ?', (start * 2,), fetch=True)
        pairs, i = [], 0
        while i + 1 < len(rows):
            if rows[i]['role'] == 'user' and rows[i + 1]['role'] == 'assistant':
                pairs.append((rows[i]['text'], rows[i + 1]['text']))
                i += 2
            else:
                i += 1
        for u, a in pairs:
            if lang == 'EN' or not VOUS.search(a):
                hist += [{'role': 'user', 'content': u}, {'role': 'assistant', 'content': a[:200]}]
    fnote = ("Ce que tu sais de l'utilisateur : " + " ; ".join(facts) + ".") if facts else ''
    shot = []
    return [{'role': 'system', 'content': system_prompt(lang, [], code, dt)}] + shot + hist + \
           [{'role': 'user', 'content': user_content(text, lang, web, ((note + '\n') if note else '') + fnote, code, dt)}]


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


def finish_direct(gid, out, voice, lang, status='none', err=''):
    if GEN['id'] != gid:
        return
    GEN.update(text=out, brain=True, truncated=False, stage='', hold=False, web_status=status, web_error=err)
    if voice:
        threading.Thread(target=speak_text, args=(out, 1.0, 1.0, lang, ''), daemon=True).start()
    GEN['done'] = True


# ---------- API gratuites : réponses directes, sans le modèle ----------
CITY0 = ('Dakar', 14.6937, -17.4441)
WMO = {0: 'ciel dégagé', 1: 'plutôt dégagé', 2: 'partiellement nuageux', 3: 'ciel couvert', 45: 'brouillard', 48: 'brouillard',
       51: 'bruine légère', 53: 'bruine', 55: 'bruine forte', 61: 'pluie légère', 63: 'pluie', 65: 'forte pluie',
       71: 'neige légère', 73: 'neige', 75: 'forte neige', 80: 'averses légères', 81: 'averses', 82: 'fortes averses',
       95: 'orage', 96: 'orage et grêle', 99: 'orage et grêle'}
METEO = re.compile(r"(météo|meteo|quel temps|va-t-il pleuvoir|il pleut|température|soleil.*(se couche|se lève|coucher|lever)|(se couche|se lève|coucher|lever).*soleil)", re.I)
NOCITY = set("demain aujourd hui ce cette la le les l maintenant venir semaine soir matin midi après apres quel quelle cet moi toi mon ma".split())
DEV = {'euro': 'EUR', 'eur': 'EUR', 'dollar': 'USD', 'usd': 'USD', 'livre sterling': 'GBP', 'livre': 'GBP', 'franc cfa': 'XOF',
       'cfa': 'XOF', 'xof': 'XOF', 'dirham': 'MAD', 'naira': 'NGN', 'yen': 'JPY', 'yuan': 'CNY', 'franc suisse': 'CHF',
       'rand': 'ZAR', 'cedi': 'GHS'}
UNITS = {'EUR': ('euro', 'euros'), 'USD': ('dollar', 'dollars'), 'GBP': ('livre sterling', 'livres sterling'),
         'XOF': ('franc CFA', 'francs CFA'), 'MAD': ('dirham', 'dirhams'), 'NGN': ('naira', 'nairas'), 'JPY': ('yen', 'yens'),
         'CNY': ('yuan', 'yuans'), 'CHF': ('franc suisse', 'francs suisses'), 'ZAR': ('rand', 'rands'), 'GHS': ('cedi', 'cedis')}
CRYPTO = {'bitcoin': 'bitcoin', 'btc': 'bitcoin', 'ethereum': 'ethereum', 'éthereum': 'ethereum', 'eth': 'ethereum',
          'solana': 'solana', 'dogecoin': 'dogecoin'}


def jget(url):
    return json.loads(http_get(url))


def nf(x, d=0):
    i, _, f = ('%.*f' % (d, x)).partition('.')
    neg = i.startswith('-')
    i = '{:,}'.format(int(i.lstrip('-'))).replace(',', ' ')
    return ('-' if neg else '') + i + (',' + f if f else '')


def hm(x):
    return '%dh%s' % (int(x[11:13]), x[14:16])


def meteo_answer(text):
    t = text.lower()
    name, lat, lon = CITY0
    done = False
    for m in re.finditer(r"(?:à|au|en|pour|sur|dans|de)\s+(?=([a-zà-ÿ'’-]+(?:[ -][a-zà-ÿ'’-]+)?))", t):
        w = m.group(1).strip(" -'’")
        if len(w) < 3 or w.split(' ')[0] in NOCITY:
            continue
        for cand in dict.fromkeys([w, w.split(' ')[0]]):
            try:
                r = (jget('https://geocoding-api.open-meteo.com/v1/search?count=1&language=fr&name=' + urllib.parse.quote(cand)).get('results') or [None])[0]
            except Exception:
                r = None
            if r:
                name, lat, lon = r['name'], r['latitude'], r['longitude']
                done = True
                break
        if done:
            break
    d = jget('https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=temperature_2m,apparent_temperature,weather_code'
             '&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset'
             '&timezone=auto&forecast_days=3' % (lat, lon))
    c, dl = d['current'], d['daily']
    i = 2 if re.search(r"apr[èe]s-demain", t) else 1 if 'demain' in t else 0
    if 'soleil' in t:
        return 'À %s, le soleil se lève à %s et se couche à %s.' % (name, hm(dl['sunrise'][i]), hm(dl['sunset'][i]))
    day = "%s, entre %d et %d degrés, risque de pluie %d pour cent." % (
        WMO.get(dl['weather_code'][i], 'temps variable'), round(dl['temperature_2m_min'][i]), round(dl['temperature_2m_max'][i]),
        dl['precipitation_probability_max'][i] or 0)
    if i == 0:
        return "À %s, il fait %d degrés, %s, ressenti %d. Aujourd'hui, entre %s" % (
            name, round(c['temperature_2m']), WMO.get(c['weather_code'], 'temps variable'), round(c['apparent_temperature']),
            day.split(', entre ', 1)[1])
    return "%s à %s : %s" % (('Demain', 'Après-demain')[i - 1], name, day)


def unit(code, n):
    return UNITS[code][0 if abs(n) < 2 else 1]


def conv_answer(text):
    t = re.sub(r"\b(euro|dollar|dirham|naira|yen|yuan|rand|cedi|livre)s\b", r"\1", re.sub(r"\bfrancs\b", "franc", text.lower()))
    t = re.sub(r"(?<=\d) (?=\d{3}\b)", "", t)
    cn = '|'.join(sorted(DEV, key=len, reverse=True))
    m = re.search(r"(?:(\d+(?:[.,]\d+)?|une?)\s*)?\b(%s)\b\s*(?:en|vers|to|contre|pour)\s*\b(%s)\b" % (cn, cn), t)
    if not m:
        return None
    amt = 1.0 if (not m.group(1) or m.group(1) in ('un', 'une')) else float(m.group(1).replace(',', '.'))
    a, b = DEV[m.group(2)], DEV[m.group(3)]
    if a == b:
        return None
    out = amt * jget('https://open.er-api.com/v6/latest/' + a)['rates'][b]
    return "%s %s, c'est environ %s %s." % (nf(amt, 0 if amt == int(amt) else 2), unit(a, amt),
                                            nf(out, 0 if (b in ('XOF', 'JPY') or out >= 100) else 2), unit(b, out))


def crypto_answer(text):
    t = text.lower()
    m = re.search(r"\b(%s)\b" % '|'.join(CRYPTO), t)
    if not m or not re.search(r"(prix|cours|vaut|valeur|combien)", t):
        return None
    cid = CRYPTO[m.group(1)]
    d = jget('https://api.coingecko.com/api/v3/simple/price?ids=%s&vs_currencies=usd,eur&include_24hr_change=true' % cid)[cid]
    ch = d.get('usd_24h_change') or 0
    return '%s : %s dollars, soit %s euros, %s %s pour cent en 24 heures.' % (
        cid.capitalize(), nf(d['usd'], 0 if d['usd'] >= 100 else 2), nf(d['eur'], 0 if d['eur'] >= 100 else 2),
        'plus' if ch >= 0 else 'moins', nf(abs(ch), 1))


def news_answer(text):
    t = text.lower()
    if not re.search(r"\b(actualités?|actu|les infos|les nouvelles|gros titres|les titres)\b", t) or len(t.split()) > 8 or ' sur ' in t:
        return None
    if re.search(r"\b(nba|basket|foot|football|tennis|économie|politique|tech|technologie|crypto|cinéma|musique|jeux?)\b", t):
        return None
    if re.search(r"(actualités?|actu|infos?|nouvelles|news)\s+(?:de |du |des |d'|sur |à propos |concernant |autour |en |à la |au )(?!jour|moment|sports?|la journée|aujourd|ce jour|l'afrique|afrique|monde|du monde|le monde|sénégal|ce matin|ce soir|hier|demain)", t):
        return None
    import xml.etree.ElementTree as ET
    sec = 'sports' if 'sport' in t else 'monde' if 'monde' in t else 'afrique'
    root = ET.fromstring(http_get('https://www.rfi.fr/fr/%s/rss' % sec))
    ts = [html.unescape((i.findtext('title') or '').strip())[:120] for i in root.iter('item')]
    ts = [x for x in ts if x][:3]
    return ('Voici les titres de RFI : ' + ' ; '.join('%d, %s' % (k + 1, x) for k, x in enumerate(ts)) + '.') if ts else None


def nba_answer(text):
    t = text.lower()
    if 'nba' not in t or not re.search(r"(score|résultat|match|joué|gagné)", t):
        return None
    out = []
    for e in jget('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard').get('events', [])[:5]:
        cs = e['competitions'][0]['competitors']
        st = e['status']['type']
        a, b = cs[0], cs[1]
        if st.get('completed'):
            out.append('%s %s, %s %s' % (a['team']['shortDisplayName'], a.get('score', 0), b['team']['shortDisplayName'], b.get('score', 0)))
        elif st.get('state') == 'in':
            out.append('%s %s, %s %s en cours' % (a['team']['shortDisplayName'], a.get('score', 0), b['team']['shortDisplayName'], b.get('score', 0)))
        else:
            out.append('%s contre %s à venir' % (a['team']['shortDisplayName'], b['team']['shortDisplayName']))
    return ('Matchs NBA : ' + ' ; '.join(out) + '.') if out else "Aucun match NBA n'est affiché aujourd'hui."


def api_answer(text):
    for fn, src, gate in ((meteo_answer, 'Open-Meteo', METEO.search(text)), (conv_answer, 'ExchangeRate-API', True),
                          (crypto_answer, 'CoinGecko', True), (news_answer, 'RFI', True), (nba_answer, 'ESPN', True)):
        if gate:
            try:
                a = fn(text)
            except Exception:
                a = None
            if a:
                return a, src
    return None


# ---------- routeur sémantique (modèle d'embeddings dans un second serveur) ----------
ROUTES = {
    'meteo': ["quel temps fait-il à Dakar", "il va pleuvoir demain ?", "la météo de ce week-end", "à quelle heure se couche le soleil",
              "quelle est la température dehors", "quel temps fera-t-il demain à Thiès", "est-ce qu'il va faire chaud à Saint-Louis",
              "il pleut à Paris en ce moment ?", "la météo d'Abidjan cette semaine", "faut-il prendre un parapluie aujourd'hui",
              "will it rain tomorrow", "what's the weather like"],
    'devise': ["combien font 100 euros en francs CFA", "le taux de change du dollar", "convertis 50 dollars en euros", "un euro vaut combien en CFA",
               "100 000 francs CFA en euros", "combien de dirhams pour un euro", "le cours de la livre sterling", "change mes dollars en francs CFA",
               "how much is 20 dollars in euros", "taux euro naira"],
    'crypto': ["prix du bitcoin", "combien vaut l'ethereum aujourd'hui", "le cours du bitcoin", "combien coûte un bitcoin en euros",
               "l'ethereum monte ou descend", "valeur de la solana", "bitcoin price today", "le bitcoin a combien baissé", "cours des cryptomonnaies"],
    'actu_generale': ["les actualités du jour", "quelles sont les infos du monde", "donne-moi les titres de l'actualité", "les nouvelles de l'Afrique",
                      "les infos du moment", "quoi de neuf dans le monde aujourd'hui", "les actualités du sport", "what's the news today"],
    'sport_scores': ["les scores de la NBA", "qui a gagné les Lakers hier soir", "résultat du match de basket", "le score du match de ce soir",
                     "quel est le résultat de la NBA", "les Celtics ont gagné ?", "scores NBA d'hier", "classement du championnat", "NBA scores last night"],
    'recherche_web': ["quelle est l'actualité de la NBA", "qui est le président du Sénégal", "dernières nouvelles sur la politique en France",
                      "le prix de l'or aujourd'hui", "que s'est-il passé à Dakar cette semaine", "qui a gagné la dernière coupe d'Afrique",
                      "quand sort le prochain film Marvel", "le prix du dernier iPhone", "qui est le meilleur joueur de la NBA en ce moment",
                      "les infos sur le président du Sénégal", "où en est la situation en Ukraine", "qui est le champion du monde de football",
                      "les résultats de l'élection", "est-ce que Messi joue encore", "latest news about the NBA",
                      "recherche sur internet les offres de clubs de basket en Italie"],
    'encyclopedie': ["c'est quoi la blockchain", "explique-moi la photosynthèse", "définition du mot amère", "définition du mot discipline",
                     "qui est Léopold Sédar Senghor", "parle-moi de l'histoire du Sénégal", "pourquoi le ciel est bleu", "comment fonctionne un moteur",
                     "qui était Napoléon", "quelle est la capitale du Japon", "que veut dire le mot résilience", "explique-moi ce qu'est l'inflation",
                     "combien y a-t-il de joueurs dans une équipe de basket", "what is photosynthesis"],
    'conversation': ["salut ça va ?", "tu t'appelles comment", "raconte-moi une histoire", "merci beaucoup", "peux-tu me motiver ?", "je suis fatigué",
                     "bonne nuit", "raconte-moi une blague", "qu'est-ce que tu penses de moi", "tu peux m'aider ?", "donne-moi un conseil pour mieux dormir",
                     "je me sens stressé avant mon match", "qui es-tu ?", "hello how are you", "tu fais quoi ?", "dis-moi quelque chose de gentil"],
    'code': ["écris une fonction python qui trie une liste", "pourquoi mon script bash ne marche pas", "explique ce code javascript",
             "corrige cette erreur dans mon programme", "comment faire une boucle en python", "écris un script pour renommer des fichiers",
             "write a python function"],
    'action_telephone': ["mets une alarme à six heures", "réveille-moi à 7 heures", "appelle maman", "appelle mon frère", "ouvre YouTube",
                         "mets un minuteur de dix minutes", "envoie un message à Moussa", "lance de la musique", "allume la lampe torche",
                         "emmène-moi à la gare", "mets le volume à fond", "set an alarm for 6 am"],
}
RT = {'ready': False, 'vecs': {}, 'err': ''}


def emb_vec(text):
    req = urllib.request.Request('http://127.0.0.1:%d/v1/embeddings' % brain.EMB_PORT, json.dumps({'input': text, 'model': 'x'}).encode(),
                                 {'Content-Type': 'application/json'})
    v = json.loads(urllib.request.urlopen(req, timeout=20).read())['data'][0]['embedding']
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def router_init():
    while True:
        try:
            while brain.EMB['stage'] != 'ready':
                time.sleep(3)
            sig = hashlib.md5(json.dumps(ROUTES, sort_keys=True).encode()).hexdigest()
            path = os.path.join(DATA, 'routes_cache.json')
            try:
                c = json.load(open(path))
                if c.get('sig') == sig:
                    RT['vecs'], RT['ready'] = c['vecs'], True
                    return
            except Exception:
                pass
            vecs = {k: [emb_vec(t) for t in v] for k, v in ROUTES.items()}
            json.dump({'sig': sig, 'vecs': vecs}, open(path, 'w'))
            RT['vecs'], RT['ready'] = vecs, True
            return
        except Exception as e:
            RT['err'] = repr(e)
            time.sleep(10)


def route(text):
    if not RT['ready']:
        return None
    try:
        v = emb_vec(text[:200])
    except Exception:
        return None
    sc = sorted(((max(sum(a * b for a, b in zip(v, e)) for e in es), k) for k, es in RT['vecs'].items()), reverse=True)
    (s1, k1), (s2, k2) = sc[0], sc[1]
    return {'intent': k1, 'score': round(s1, 2), 'second': k2, 'margin': round(s1 - s2, 2), 'conf': s1 >= 0.45 and (s1 - s2) >= 0.04}


def apply_route(rt, mode):
    if not rt or not rt['conf']:
        return mode
    it = rt['intent']
    if it in ('recherche_web', 'actu_generale', 'sport_scores'):
        return 'fresh'
    if it == 'encyclopedie':
        return mode or 'enc'
    if it in ('conversation', 'code', 'action_telephone') and mode == 'enc' and rt['score'] >= 0.6:
        return None
    return mode


# ---------- actions du téléphone (phase C1) ----------
PENDING = {'act': None, 'ts': 0}
YES = re.compile(r"^\W*(oui|ouais|ok|okay|d'accord|vas-y|go|confirme|c'est bon|yes)\b", re.I)
NO = re.compile(r"^\W*(non|annule|laisse tomber|stop|pas maintenant|nope)\b", re.I)
UN_ = {'zéro': 0, 'zero': 0, 'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5, 'six': 6, 'sept': 7, 'huit': 8, 'neuf': 9,
       'dix': 10, 'onze': 11, 'douze': 12, 'treize': 13, 'quatorze': 14, 'quinze': 15, 'seize': 16}
TENS_ = {'vingt': 20, 'vingts': 20, 'trente': 30, 'quarante': 40, 'cinquante': 50, 'soixante': 60}
WORDS_ = '|'.join(sorted(list(UN_) + list(TENS_), key=len, reverse=True))
TORCH = {'on': False}


def nw(s):
    s = s.replace('quatre-vingts', '80').replace('quatre-vingt', '80').replace('quatre vingt', '80')
    n = 0
    for w in re.split(r"[\s-]+", s):
        n += UN_.get(w) if w in UN_ else TENS_.get(w) if w in TENS_ else int(w) if w.isdigit() else 0
    return n


def digits(t):
    return re.sub(r"\b(?:%s)(?:[ -](?:et[ -])?(?:%s))*\b" % (WORDS_, WORDS_), lambda m: str(nw(m.group(0).replace(' et ', ' '))), t)


def parse_time(t):
    m = re.search(r"\b(?:à|a|pour)\s+(\d{1,2})\s*(?:heures?|h|:)\s*(?:(\d{1,2})|(et quart)|(et demie?)|(moins le quart))?", t)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 15 if m.group(3) else 30 if m.group(4) else 0
        if m.group(5):
            h, mi = h - 1, 45
    elif re.search(r"\bà midi\b", t):
        h, mi = 12, 0
    elif re.search(r"\bà minuit\b", t):
        h, mi = 0, 0
    else:
        return None
    if re.search(r"(du soir|de l'après-midi|de l'apres-midi)", t) and h < 12:
        h += 12
    return (h % 24, mi) if (0 <= h <= 24 and 0 <= mi < 60) else None


def parse_duration(t):
    sec = 0
    for n, u in re.findall(r"(\d+)\s*(secondes?|minutes?|min|heures?|h)\b", t):
        sec += int(n) * (1 if u.startswith('s') else 60 if u.startswith('m') else 3600)
    m = re.search(r"(\d+)\s*(?:heures?|h)\s*(\d{1,2})\b(?!\s*(?:secondes?|minutes?|min|heures?|h)\b)", t)
    if m:
        sec += int(m.group(2)) * 60
    if re.search(r"heures? et demie?", t):
        sec += 1800
    if "quart d'heure" in t:
        sec += 900
    return sec


def hm_txt(h, m):
    return '%d h %02d' % (h, m) if m else '%d h' % h


def dur_txt(sec):
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    p = ([('%d heure%s' % (h, 's' if h > 1 else ''))] if h else []) + ([('%d minute%s' % (m, 's' if m > 1 else ''))] if m else []) + ([('%d seconde%s' % (s, 's' if s > 1 else ''))] if s else [])
    return ' et '.join(p)


def parse_action(text, dt):
    s = text.lower().replace('’', "'").strip(' .!?')
    t = digits(s)
    if re.search(r"\b(appelle|téléphone à|envoie (?:un |1 )?(?:message|sms|texto)|écris à)\b", t):
        return {'kind': 'say', 'msg': "Je ne sais pas encore appeler ni envoyer de messages, c'est prévu bientôt."}
    if re.search(r"\b(alarme|réveille|réveil)\b", t):
        m = re.search(r"\bdans\s+(\d+)\s*(minutes?|min|heures?|h)\b", t)
        if m:
            d = dt + datetime.timedelta(minutes=int(m.group(1)) * (60 if m.group(2).startswith('h') else 1))
            hh, mi = d.hour, d.minute
        else:
            tm = parse_time(t)
            if not tm:
                return {'kind': 'say', 'msg': "À quelle heure veux-tu l'alarme ? Dis par exemple : mets une alarme à 6 h 30."}
            hh, mi = tm
        return {'kind': 'alarm', 'h': hh, 'm': mi, 'confirm': True, 'label': 'une alarme à ' + hm_txt(hh, mi)}
    if re.search(r"\b(minuteur|minuterie|chrono|compte à rebours|timer)\b", t):
        sec = parse_duration(t)
        if not sec:
            return {'kind': 'say', 'msg': 'Combien de temps ? Dis par exemple : minuteur de dix minutes.'}
        return {'kind': 'timer', 'sec': sec, 'confirm': True, 'label': 'un minuteur de ' + dur_txt(sec)}
    m = re.search(r"\b(allume|éteins|éteindre|active|désactive|coupe)\s+(?:la |le |l')?(lampe|torche|flash|lumière)", s)
    if m:
        on = m.group(1) in ('allume', 'active')
        return {'kind': 'torch', 'on': on, 'label': 'la lampe torche ' + ('allumée' if on else 'éteinte')}
    m = re.search(r"(?:cherche|recherche|trouve|mets|joue|lance)\s+(.+?)\s+sur youtube", s)
    if m:
        return {'kind': 'youtube', 'q': m.group(1), 'label': 'YouTube : ' + m.group(1)}
    m = (re.search(r"(?:emmène|amène|conduis|guide)[- ]moi\s+(?:à la |à l'|au |aux |à |vers |chez )?(.+)", s) or re.search(r"itinéraire\s+(?:pour |vers |jusqu'à |à )?(.+)", s)
         or re.search(r"navigue\s+(?:vers |à )?(.+)", s) or re.search(r"comment (?:aller|se rendre|j'y vais)\s+(?:à la |à l'|au |aux |à )?(.+)", s))
    if m:
        return {'kind': 'nav', 'dest': m.group(1).strip(), 'label': 'itinéraire vers ' + m.group(1).strip()}
    m = re.search(r"\b(?:ouvre|ouvrir|lance|lancer|démarre)\s+(?:l'application |l'appli |l'app |l'|la |le |les )?([a-zà-ÿ0-9' -]{2,30})$", s)
    if m:
        return {'kind': 'app', 'name': m.group(1).strip(), 'label': m.group(1).strip()}
    return None


def droid():
    from jnius import autoclass
    return autoclass('org.kivy.android.PythonActivity').mActivity


def find_app(name):
    from jnius import autoclass
    Intent = autoclass('android.content.Intent')
    ctx_ = droid()
    pm = ctx_.getPackageManager()
    i = Intent(Intent.ACTION_MAIN)
    i.addCategory(Intent.CATEGORY_LAUNCHER)
    apps = pm.queryIntentActivities(i, 0)
    best, bs = None, 0
    want = fold(name)
    for k in range(apps.size()):
        ri = apps.get(k)
        label = fold(ri.loadLabel(pm).toString())
        sc = 3 if label == want else 2 if (want in label or label in want) else 0
        if sc > bs:
            best, bs = ri.activityInfo.packageName, sc
    return pm.getLaunchIntentForPackage(best) if best else None


def do_action(a):
    try:
        from jnius import autoclass, cast
        Intent, Uri = autoclass('android.content.Intent'), autoclass('android.net.Uri')
        AC = autoclass('android.provider.AlarmClock')
        ctx_, k = droid(), a['kind']
        if k == 'alarm':
            i = Intent(AC.ACTION_SET_ALARM)
            i.putExtra(AC.EXTRA_HOUR, int(a['h']))
            i.putExtra(AC.EXTRA_MINUTES, int(a['m']))
            i.putExtra(AC.EXTRA_MESSAGE, 'Julia')
            i.putExtra(AC.EXTRA_SKIP_UI, True)
            ctx_.startActivity(i)
        elif k == 'timer':
            i = Intent(AC.ACTION_SET_TIMER)
            i.putExtra(AC.EXTRA_LENGTH, int(a['sec']))
            i.putExtra(AC.EXTRA_SKIP_UI, True)
            ctx_.startActivity(i)
        elif k == 'torch':
            cm = cast('android.hardware.camera2.CameraManager', ctx_.getSystemService('camera'))
            cm.setTorchMode(cm.getCameraIdList()[0], bool(a['on']))
            TORCH['on'] = a['on']
        elif k == 'youtube':
            ctx_.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse('https://www.youtube.com/results?search_query=' + urllib.parse.quote(a['q']))))
        elif k == 'nav':
            i = Intent(Intent.ACTION_VIEW, Uri.parse('google.navigation:q=' + urllib.parse.quote(a['dest'])))
            i.setPackage('com.google.android.apps.maps')
            try:
                ctx_.startActivity(i)
            except Exception:
                ctx_.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse('geo:0,0?q=' + urllib.parse.quote(a['dest']))))
        elif k == 'app':
            i = find_app(a['name'])
            if not i:
                return "Je n'ai pas trouvé l'application %s." % a['name']
            ctx_.startActivity(i)
        return 'C\'est fait : %s.' % a['label'] if k in ('alarm', 'timer', 'torch') else 'Voilà.'
    except Exception as e:
        brain.log('action ' + repr(e))
        return "Je n'ai pas réussi à le faire sur le téléphone."


def act_flow(gid, text, dt, voice, lang):
    pend = PENDING['act'] and time.time() - PENDING['ts'] < 90
    a, PENDING['act'] = PENDING['act'], None
    if pend and YES.search(text):
        return finish_direct(gid, do_action(a), voice, lang) or True
    if pend and NO.search(text):
        return finish_direct(gid, "D'accord, j'annule.", voice, lang) or True
    a = parse_action(text, dt)
    if not a:
        return False
    if a['kind'] == 'say':
        finish_direct(gid, a['msg'], voice, lang)
    elif a.get('confirm'):
        PENDING.update(act=a, ts=time.time())
        GEN['act'] = '1'
        finish_direct(gid, 'Je mets %s. Tu confirmes ?' % a['label'], voice, lang)
    else:
        finish_direct(gid, do_action(a), voice, lang)
    return True


def generate(gid, text, lang, voice, mem, web, pref, dt):
    t0 = time.time()
    if lang != 'EN' and CLOCK.search(text):
        return finish_direct(gid, clock_answer(text, dt), voice, lang)
    if lang != 'EN' and act_flow(gid, text, dt, voice, lang):
        return
    if lang != 'EN' and web:
        r = api_answer(text)
        if r:
            GEN['sources'] = [r[1]]
            return finish_direct(gid, r[0], voice, lang, 'ok')
    model = pick(text, pref)
    mode = web_mode(text, model) if web else None
    follow = bool(web and FOLLOW.search(text) and LASTWEB['ctx'] and time.time() - LASTWEB['ts'] < 600)
    rt = route(text) if (web and not follow) else None
    mode = apply_route(rt, mode)
    GEN['route'] = ('%s %.2f%s' % (rt['intent'], rt['score'], '' if rt['conf'] else ' (incertain, 2e : %s)' % rt['second'])) if rt else ''
    info = {'ctx': '', 'sources': [], 'status': 'none', 'error': '', 'age': 0}
    if mode:
        GEN.update(stage='search', hold=(mode == 'fresh'))
        try:
            info = rag(text, mode, dt.year)
        except Exception as e:
            info = {'ctx': '', 'sources': [], 'status': 'fail', 'error': err_short(e), 'age': 0}
        if info['ctx']:
            LASTWEB.update(ts=time.time(), ctx=info['ctx'], sources=info['sources'], q=info.get('q', ''), mode=mode)
    elif web and FOLLOW.search(text) and LASTWEB['ctx'] and time.time() - LASTWEB['ts'] < 600:
        info = {'ctx': LASTWEB['ctx'], 'sources': LASTWEB['sources'], 'status': 'ok', 'error': '', 'age': 0}
        if LASTWEB.get('q'):
            GEN.update(stage='search', hold=(LASTWEB.get('mode') == 'fresh'))
            try:
                i2 = rag(text, LASTWEB.get('mode') or 'enc', dt.year, subject=LASTWEB['q'])
                if i2['ctx']:
                    info = i2
                    LASTWEB.update(ts=time.time(), ctx=i2['ctx'], sources=i2['sources'])
            except Exception:
                pass
    if GEN['id'] != gid:
        return
    GEN.update(web_status=info['status'], web_error=info['error'], sources=info['sources'] if info['ctx'] else [],
                   web_q=info.get('q', ''), web_ms=info.get('ms', 0), web_n=info.get('n', 0))
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
               sources=[], hold=False, ttft=None, dur=None, web_q='', web_ms=0, web_n=0, route='', act='')
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
            'ttft': GEN['ttft'], 'dur': GEN['dur'], 'web_q': GEN['web_q'], 'web_ms': GEN['web_ms'], 'web_n': GEN['web_n'], 'route': GEN['route'], 'act': GEN['act']}


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
        if u.path == '/api/brain':
            try:
                import brain
                return self._json(dict(brain.ST, emb=brain.EMB))
            except Exception as e:
                return self._json({'stage': 'error', 'err': repr(e), 'pct': 0})
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

if not os.environ.get('JULIA_NOSERVER'):
    import brain
    threading.Thread(target=brain.ensure, daemon=True).start()
    threading.Thread(target=router_init, daemon=True).start()
    threading.Thread(target=warmup, daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1', 5000), H).serve_forever()
