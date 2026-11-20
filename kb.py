import os, re, sqlite3, time, unicodedata
import brain

try:
    import router
except Exception:
    router = None

DB = os.path.join(brain.DATA, 'niko_kb.db')
DIRS = [os.path.join(brain.DATA, 'docs'), '/sdcard/Niko']
MAX_CTX = 300
SKIP = ('salut', 'commande', 'code')
STOP = set('le la les un une des du de et que qui quoi est sont pas pour dans sur avec ce cette ces mon ma mes ton ta tes son sa ses nous vous ils elles je tu il elle on ne se me te lui en au aux ou mais donc car comme plus tres tout tous fait faire peux peut veux veut dis dit parle niko quel quelle quels comment pourquoi'.split())
_SYN = {
    'nom': 'appelle prenom', 'prenom': 'appelle nom',
    'sport': 'basket foot joue joueur entrainement', 'pratique': 'joue',
    'age': 'ans ne naissance', 'taille': 'cm mesure', 'poids': 'kg',
    'habite': 'ville vit adresse', 'travail': 'metier boulot job',
    'metier': 'travail job', 'telephone': 'numero',
}
_FTS = {'ok': None}
_log = getattr(brain, 'log', lambda m: None)


def _norm(s):
    return ''.join(ch for ch in unicodedata.normalize('NFD', s.lower()) if unicodedata.category(ch) != 'Mn')


def _migrate(c):
    if c.execute('SELECT count(*) FROM fts').fetchone()[0] == 0:
        c.execute('INSERT INTO fts(rowid, txt) SELECT id, txt FROM chunks')
    c.execute('UPDATE chunks SET vec=NULL WHERE vec IS NOT NULL')
    c.commit()


def _db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, src TEXT, txt TEXT, vec BLOB, ts REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS docs (path TEXT PRIMARY KEY, mtime REAL)')
    if _FTS['ok'] is None:
        try:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(txt, tokenize='unicode61 remove_diacritics 2')")
            _FTS['ok'] = True
            _migrate(c)
        except Exception:
            _FTS['ok'] = False
    return c


def _put(c, src, txt):
    cur = c.execute('INSERT INTO chunks (src, txt, ts) VALUES (?,?,?)', (src, txt, time.time()))
    if _FTS['ok']:
        c.execute('INSERT INTO fts(rowid, txt) VALUES (?,?)', (cur.lastrowid, txt))


def _del(c, src):
    if _FTS['ok']:
        c.execute('DELETE FROM fts WHERE rowid IN (SELECT id FROM chunks WHERE src=?)', (src,))
    c.execute('DELETE FROM chunks WHERE src=?', (src,))


def add(txt, src='note'):
    txt = txt.strip()
    if not txt:
        return False
    c = _db()
    _put(c, src, txt)
    c.commit()
    c.close()
    return True


def _chunks(text, size=500):
    out, cur = [], ''
    for p in re.split(r'\n\s*\n', text):
        p = ' '.join(p.split())
        while len(p) > size:
            cut = p.rfind(' ', 0, size)
            cut = cut if cut > 100 else size
            if cur:
                out.append(cur)
                cur = ''
            out.append(p[:cut])
            p = p[cut:].strip()
        if not p:
            continue
        if cur and len(cur) + len(p) + 1 > size:
            out.append(cur)
            cur = ''
        cur = (cur + ' ' + p).strip()
    if cur:
        out.append(cur)
    return out


def purge():
    c = _db()
    for (p,) in c.execute('SELECT path FROM docs').fetchall():
        if not os.path.exists(p):
            _del(c, 'doc:' + p)
            _del(c, 'media:' + p)
            c.execute('DELETE FROM docs WHERE path=?', (p,))
    c.commit()
    c.close()


def index_docs():
    purge()
    c = _db()
    for d in DIRS:
        try:
            names = sorted(os.listdir(d))
        except Exception:
            continue
        for n in names:
            if not n.lower().endswith(('.txt', '.md')) or n.lower() == 'profil.txt':
                continue
            p = os.path.join(d, n)
            try:
                mt = os.path.getmtime(p)
                row = c.execute('SELECT mtime FROM docs WHERE path=?', (p,)).fetchone()
                if row and row[0] == mt:
                    continue
                with open(p, encoding='utf-8', errors='replace') as f:
                    parts = _chunks(f.read())
                _del(c, 'doc:' + p)
                for part in parts:
                    _put(c, 'doc:' + p, part)
                c.execute('REPLACE INTO docs (path, mtime) VALUES (?,?)', (p, mt))
                c.commit()
            except Exception:
                pass
    c.close()
    index_media()


def index_media():
    d = os.path.join(brain.DATA, 'media')
    try:
        names = sorted(os.listdir(d))
    except Exception:
        return
    kinds = {'photo': ('jpg', 'jpeg', 'png', 'webp', 'heic', 'gif'), 'video': ('mp4', 'mkv', '3gp', 'webm', 'mov'),
             'audio': ('mp3', 'm4a', 'wav', 'ogg', 'aac'), 'document': ('pdf', 'doc', 'docx', 'odt', 'xls', 'xlsx', 'ppt', 'pptx')}
    c = _db()
    for n in names:
        p = os.path.join(d, n)
        try:
            mt = os.path.getmtime(p)
            row = c.execute('SELECT mtime FROM docs WHERE path=?', (p,)).fetchone()
            if row and row[0] == mt:
                continue
            ext = n.lower().rsplit('.', 1)[-1] if '.' in n else ''
            kind = next((k for k, v in kinds.items() if ext in v), 'fichier')
            txt = 'Fichier enregistre dans la memoire de Niko : %s (%s)' % (n, kind)
            _del(c, 'media:' + p)
            _put(c, 'media:' + p, txt)
            c.execute('REPLACE INTO docs (path, mtime) VALUES (?,?)', (p, mt))
            c.commit()
        except Exception:
            pass
    c.close()


def _stem(w):
    return w[:max(4, len(w) - 2)] if len(w) >= 5 else w


def search(q, k=3):
    toks = []
    for w in re.findall(r'\w+', _norm(q)):
        if len(w) >= 3 and w not in STOP and w not in toks:
            toks.append(w)
    if not toks:
        return []
    ext = []
    for w in toks:
        for s in _SYN.get(w, '').split():
            if s not in toks and s not in ext:
                ext.append(s)
    real = [_stem(w) for w in toks]
    syn = [_stem(w) for w in ext]
    c = _db()
    try:
        if _FTS['ok']:
            m = ' OR '.join('"%s"*' % s for s in real + syn)
            rows = c.execute('SELECT txt, -bm25(fts) FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?',
                             (m, k * 8)).fetchall()
        else:
            rows = [(t, 0.0) for (t,) in c.execute('SELECT txt FROM chunks').fetchall()]
    finally:
        c.close()
    res, seen = [], set()
    for t, s in rows:
        nt = _norm(t)
        if nt in seen:
            continue
        seen.add(nt)
        score = sum(1 for st in real if st in nt) + 0.5 * min(2, sum(1 for st in syn if st in nt))
        if score > 0:
            res.append((score, s, t))
    if not res:
        return []
    res.sort(reverse=True)
    best = res[0][0]
    return [(sc, t) for sc, s, t in res if sc >= best * 0.5][:k]


def fichiers(q, k=3):
    c = _db()
    out = []
    try:
        for sc, t in search(q, k * 3):
            r = c.execute('SELECT src FROM chunks WHERE txt=? LIMIT 1', (t,)).fetchone()
            if r:
                n = os.path.basename(r[0].split(':', 1)[1]) if ':' in r[0] else 'notes'
                if n not in out:
                    out.append(n)
    finally:
        c.close()
    return out[:k]


def context(q, k=3):
    q = (q or '').strip()
    if len(q) < 4:
        return ''
    t0 = time.time()
    try:
        if router is not None and router.qtype(q) in SKIP:
            return ''
        out, used = [], 0
        fi = _fiche(q)
        if fi:
            out.append('- ' + fi)
        for s, t in search(q, k):
            if used and used + len(t) > MAX_CTX:
                continue
            t = t.lstrip('-• ')
            out.append('- ' + (t if len(t) <= MAX_CTX else t[:MAX_CTX].rsplit(' ', 1)[0]))
            used += len(t)
        _log('kb: %d extrait(s), %d car., %d ms' % (len(out), used, (time.time() - t0) * 1000))
        return '\n'.join(out)
    except Exception as e:
        _log('kb erreur: ' + repr(e))
        return ''


def _fiche(q):
    if not set(re.findall(r'\w+', _norm(q))) & {'mon', 'ma', 'mes', 'je', 'suis'}:
        return ''
    try:
        with open(os.path.join(DIRS[-1], 'profil.txt'), encoding='utf-8', errors='replace') as f:
            return ' '.join(f.read().split())[:300]
    except Exception:
        return ''


_SYN.update({
    'poste': 'ailier pivot meneur arriere', 'club': 'equipe academie basket',
    'langue': 'parle francais anglais', 'langues': 'parle francais anglais',
    'objectif': 'veut devenir', 'but': 'veut devenir',
    'classe': 'terminale', 'etudes': 'terminale diplome universite',
})
STOP.update({'quelles', 'suis', 'etre', 'avoir', 'ai', 'ete'})
_IRR = {'est': 'es', 'a': 'as', 'fait': 'fais', 'va': 'vas', 'veut': 'veux', 'peut': 'peux', 'doit': 'dois',
        'sait': 'sais', 'vient': 'viens', 'prend': 'prends', 'dit': 'dis', 'voit': 'vois', 'vit': 'vis',
        'sort': 'sors', 'part': 'pars', 'met': 'mets', 'connait': 'connais'}
_DET = {'une', 'le', 'la', 'les', 'un', 'des', 'du', 'de', 'sa', 'son', 'ses', 'mon', 'ma', 'mes', 'ce', 'cette', 'ces', 'quelque'}


def _v2(w):
    lw = w.lower()
    if lw in _IRR:
        return _IRR[lw]
    if lw.endswith('ait') or lw.endswith('it'):
        return w[:-1] + 's'
    if lw.endswith('ra') or lw.endswith('e'):
        return w + 's'
    return w


def adapter(s):
    s = s.strip(' -\u2022\t')
    pos = {'son': 'ton', 'sa': 'ta', 'ses': 'tes'}
    poss = lambda x: re.sub(r"\b(son|sa|ses)\b", lambda m: pos[m.group(1).lower()].capitalize() if m.group(1)[0].isupper() else pos[m.group(1).lower()], x, flags=re.I)
    rx = r"(ne\s+|n['\u2019])?(se\s+|s['\u2019])?([^\W\d_]+)(.*)$"
    m = re.match(r"(?:l['\u2019]utilisateur|il|elle)\s+" + rx, s, re.I | re.S)
    if not m and s[:1].islower():
        m = re.match(rx, s, re.S)
        if m and (m.group(3).lower() in _DET or not (m.group(3).lower() in _IRR or m.group(3).lower().endswith(('e', 'it', 'ra')))):
            m = None
    if not m:
        return poss(s)
    ne, se, v, rest = m.groups()
    out = 'Tu '
    if ne:
        out += ne.lower().replace('\u2019', "'")
    if se:
        out += 'te ' if se.lower().startswith('se') else "t'"
    return out + _v2(v) + poss(rest)


def direct(q):
    w = re.findall(r'\w+', _norm(q))
    if not set(w) & {'mon', 'ma', 'mes', 'je', 'suis'} or set(w) & {'ton', 'ta', 'tes', 'tu'}:
        return None
    toks = []
    for x in w:
        if len(x) >= 3 and x not in STOP and x not in toks:
            toks.append(x)
    if not toks:
        return None
    cands = []
    try:
        with open(os.path.join(DIRS[-1], 'profil.txt'), encoding='utf-8', errors='replace') as f:
            cands += re.split(r'(?<=[.!?])\s+|\n+', f.read())
    except Exception:
        pass
    for sc, t in search(q, 8):
        cands += re.split(r'(?<=[.!?])\s+|\s-\s|\n+', t)
    seen, sents = set(), []
    for x in cands:
        x = x.strip(' -\u2022\t')
        n = _norm(x)
        if len(x) < 8 or n in seen or 'assistant' in n or 'niko' in n:
            continue
        seen.add(n)
        sents.append(x)

    def cov(x):
        ws = re.findall(r'\w+', _norm(x))
        got = set()
        for t in toks:
            for st in [_stem(t)] + [_stem(s) for s in _SYN.get(t, '').split()]:
                if (len(st) >= 3 and any(a.startswith(st) for a in ws)) or st in ws:
                    got.add(t)
                    break
        return got

    left, chosen = set(toks), []
    for _ in range(2):
        best = max(((cov(x) & left, x) for x in sents), key=lambda r: (len(r[0]), -len(r[1])), default=(set(), ''))
        if not best[0]:
            break
        chosen.append((min(toks.index(t) for t in best[0]), best[1]))
        left -= best[0]
    if not chosen or len(left) > len(toks) * 0.4:
        return None
    chosen.sort()
    outs = [adapter(x) for _, x in chosen]
    return ' '.join(o if o[-1:] in '.!?' else o + '.' for o in outs)
