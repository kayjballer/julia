# Niko : memoire (RAG). "retiens que..." -> stocke ; chaque tour de discussion -> rappel des souvenirs proches.
import os, re, json, math, time, unicodedata, urllib.request

LOG = print
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
FILE = os.path.join(DATA, 'niko_souvenirs.json')
EMB_URL = 'http://127.0.0.1:8082/v1/embeddings'
THRESHOLD = 0.45
MAX_ITEMS = 500
_INSTR = 'Instruct: Retrouve les souvenirs utiles pour répondre à la question\nQuery: '
_S = {'items': None}


def norm(t):
    t = unicodedata.normalize('NFD', t.lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', t)).strip()


def _load():
    if _S['items'] is None:
        try:
            _S['items'] = json.load(open(FILE))
        except Exception:
            _S['items'] = []
    return _S['items']


def _save():
    try:
        tmp = FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(_S['items'], f)
        os.replace(tmp, FILE)
    except Exception as e:
        LOG('souvenirs : sauvegarde impossible ' + repr(e))


def embed(text):
    body = json.dumps({'input': text, 'model': 'emb'}).encode()
    req = urllib.request.Request(EMB_URL, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        v = json.loads(r.read())['data'][0]['embedding']
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def parse_remember(text):
    # "Niko retiens que ma sœur s'appelle Léa" -> "ma sœur s'appelle Léa"
    m = re.match(r"\s*(?:s'il (?:te|vous) plait,? |dis,? |ok,? )?(?:retiens|retenir|rappelle[- ]toi|souviens[- ]toi|memorise|memoriser|note|noter|n'oublie pas|prends note)\b[ ,:]*(?:bien )?(?:que |qu'|de |d'|ceci |ca |ça )?(.+)", text, re.I)
    if not m:
        return None
    fact = m.group(1).strip(' .!?,')
    if len(norm(fact).split()) < 2:
        return None
    return fact


def is_forget(text):
    n = norm(text)
    return bool(re.search(r'\b(oublie|oublier|efface|effacer|supprime|supprimer)\b (ca|cela|le dernier|ce souvenir|ce que je viens|ce que je t ai)', n)
                or re.fullmatch(r'(?:niko )?(?:oublie|efface) (?:ca|cela)', n))


def add(fact):
    items = _load()
    try:
        v = embed(fact)
    except Exception as e:
        LOG('souvenirs : embedding indisponible, stocke sans vecteur (%s)' % repr(e)[:80])
        v = None
    items.append({'t': time.time(), 'x': fact, 'v': v})
    if len(items) > MAX_ITEMS:
        del items[0:len(items) - MAX_ITEMS]
    _save()
    LOG('souvenirs : +1 (%d au total) "%s"' % (len(items), fact[:60]))
    return len(items)


def forget_last():
    items = _load()
    if not items:
        return None
    it = items.pop()
    _save()
    LOG('souvenirs : oublie "%s"' % it['x'][:60])
    return it['x']


def backfill():
    # Complete les souvenirs stockes sans vecteur (serveur d'embeddings pas pret au moment de l'ajout).
    items = _load()
    todo = [it for it in items if not it.get('v')]
    if not todo:
        return 0
    n = 0
    for it in todo:
        try:
            it['v'] = embed(it['x'])
            n += 1
        except Exception:
            break
    if n:
        _save()
        LOG('souvenirs : %d vecteur(s) complete(s)' % n)
    return n


def recall(query, k=3):
    items = [it for it in _load() if it.get('v')]
    if not items:
        return []
    try:
        q = embed(_INSTR + query)
    except Exception:
        return []
    sc = sorted(((sum(a * b for a, b in zip(q, it['v'])), it['x']) for it in items), reverse=True)[:k]
    LOG('  souvenirs : meilleurs scores ' + ', '.join('%.2f' % s for s, _ in sc))
    return [x for s, x in sc if s >= THRESHOLD]


def count():
    return len(_load())
