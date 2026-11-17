import os, json, sqlite3, time, array, math, re, operator, urllib.request
import brain

DB = os.path.join(brain.DATA, 'niko_kb.db')
URL = 'http://127.0.0.1:%d/v1/embeddings' % brain.EMB_PORT
DIRS = [os.path.join(brain.DATA, 'docs'), '/sdcard/Niko']
MIN_SCORE = 0.40


def _db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, src TEXT, txt TEXT, vec BLOB, ts REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS docs (path TEXT PRIMARY KEY, mtime REAL)')
    return c


def embed(t, timeout=30):
    body = json.dumps({'input': t[:600]}).encode()
    req = urllib.request.Request(URL, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        v = json.loads(r.read())['data'][0]['embedding']
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array.array('f', [x / n for x in v])


def add(txt, src='note'):
    txt = txt.strip()
    if not txt:
        return False
    v = embed(txt)
    c = _db()
    c.execute('INSERT INTO chunks (src, txt, vec, ts) VALUES (?,?,?,?)', (src, txt, v.tobytes(), time.time()))
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
                c.execute('DELETE FROM chunks WHERE src=?', ('doc:' + p,))
                for part in parts:
                    v = embed(part)
                    c.execute('INSERT INTO chunks (src, txt, vec, ts) VALUES (?,?,?,?)', ('doc:' + p, part, v.tobytes(), time.time()))
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
            v = embed(txt)
            c.execute('DELETE FROM chunks WHERE src=?', ('media:' + p,))
            c.execute('INSERT INTO chunks (src, txt, vec, ts) VALUES (?,?,?,?)', ('media:' + p, txt, v.tobytes(), time.time()))
            c.execute('REPLACE INTO docs (path, mtime) VALUES (?,?)', (p, mt))
            c.commit()
        except Exception:
            pass
    c.close()


def search(q, k=3):
    qv = embed(q, timeout=8)
    c = _db()
    rows = c.execute('SELECT txt, vec FROM chunks').fetchall()
    c.close()
    res = []
    for t, b in rows:
        v = array.array('f')
        v.frombytes(b)
        s = sum(map(operator.mul, qv, v))
        if s >= MIN_SCORE:
            res.append((s, t))
    res.sort(reverse=True)
    return res[:k]


def context(q):
    if len(q.strip()) < 4:
        return ''
    try:
        return '\n'.join('- ' + t for s, t in search(q))
    except Exception:
        return ''
