import os, re, sqlite3, time, unicodedata
import brain

DB = os.path.join(brain.DATA, 'niko_kb.db')
DIRS = [os.path.join(brain.DATA, 'docs'), '/sdcard/Niko']
MIN_HITS = 1
STOP = set('le la les un une des du de et que qui quoi est sont pas pour dans sur avec ce cette ces mon ma mes ton ta tes son sa ses nous vous ils elles je tu il elle on ne se me te lui en au aux ou mais donc car comme plus tres tout tous fait faire peux peut veux veut dis dit parle niko quel quelle quels comment pourquoi'.split())
_FTS = {'ok': None}


def _norm(s):
    return ''.join(ch for ch in unicodedata.normalize('NFD', s.lower()) if unicodedata.category(ch) != 'Mn')


def _migrate(c):
    n = c.execute('SELECT count(*) FROM fts').fetchone()[0]
    if n == 0:
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


def index_docs():
    c = _db()
    for d in DIRS:
        try:
            names = sorted(os.listdir(d))
        except Exception:
            continue
        for n in names:
            if not n.lower().endswith(('.txt', '.md')):
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
    stems = [_stem(w) for w in toks]
    c = _db()
    try:
        if _FTS['ok']:
            m = ' OR '.join('"%s"*' % s for s in stems)
            rows = c.execute('SELECT txt, -bm25(fts) FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?',
                             (m, k * 6)).fetchall()
        else:
            rows = [(t, 0.0) for (t,) in c.execute('SELECT txt FROM chunks').fetchall()]
    finally:
        c.close()
    res = []
    for t, s in rows:
        nt = _norm(t)
        hit = sum(1 for st in stems if st in nt)
        if hit >= MIN_HITS:
            res.append((hit, s, t))
    res.sort(reverse=True)
    return [(h, t) for h, s, t in res[:k]]


def context(q):
    if len(q.strip()) < 4:
        return ''
    try:
        return '\n'.join('- ' + t for s, t in search(q))
    except Exception:
        return ''
