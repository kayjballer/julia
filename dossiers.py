import os, re, shutil, time
import brain, kb

ROOT = '/sdcard/Niko'
RACINES = [ROOT, os.path.join(brain.DATA, 'docs'), os.path.join(brain.DATA, 'media')]
CORBEILLE = os.path.join(brain.DATA, 'corbeille')
TYPES = {'photos': ('jpg', 'jpeg', 'png', 'webp', 'heic', 'gif'),
         'videos': ('mp4', 'mkv', '3gp', 'webm', 'mov'),
         'audios': ('mp3', 'm4a', 'wav', 'ogg', 'aac'),
         'documents': ('pdf', 'doc', 'docx', 'odt', 'xls', 'xlsx', 'ppt', 'pptx'),
         'textes': ('txt', 'md')}


def _ok(p):
    p = os.path.realpath(p)
    return any(p == os.path.realpath(r) or p.startswith(os.path.realpath(r) + os.sep) for r in RACINES)


def _trouve(nom):
    n = kb._norm(nom).strip()
    exact, part = [], []
    for r in RACINES:
        for d, dirs, files in os.walk(r):
            for x in dirs + files:
                k = kb._norm(x)
                p = os.path.join(d, x)
                if k == n or k.rsplit('.', 1)[0] == n:
                    exact.append(p)
                elif n and n in k:
                    part.append(p)
    return exact or part


def _un(nom):
    r = _trouve(nom)
    if not r:
        return None, "Je ne trouve rien qui s'appelle %s." % nom
    if len(r) > 1:
        return None, "J'en trouve %d : %s. Precise lequel." % (len(r), ', '.join(os.path.basename(x) for x in r[:4]))
    return r[0], ''


def lister(sous=''):
    base = ROOT
    if sous:
        p, err = _un(sous)
        if not p:
            return err
        base = p
    if os.path.isfile(base):
        return '%s est un fichier.' % os.path.basename(base)
    try:
        noms = sorted(os.listdir(base))
    except Exception:
        return 'Je ne peux pas ouvrir ce dossier.'
    if not noms:
        return 'Le dossier est vide.'
    dos = [x for x in noms if os.path.isdir(os.path.join(base, x))]
    fic = [x for x in noms if x not in dos]
    s = '%d dossier(s), %d fichier(s).' % (len(dos), len(fic))
    return s + ' ' + ', '.join((dos + fic)[:8])


def creer(nom):
    p = os.path.join(ROOT, nom.strip().replace('/', ' '))
    if not _ok(p):
        return 'Refuse.'
    os.makedirs(p, exist_ok=True)
    return 'Dossier %s cree.' % nom


def deplacer(nom, dest):
    p, err = _un(nom)
    if not p:
        return err
    d, err = _un(dest)
    if not d or not os.path.isdir(d):
        d = os.path.join(ROOT, dest.strip())
        os.makedirs(d, exist_ok=True)
    if not (_ok(p) and _ok(d)):
        return 'Refuse.'
    _mv(p, os.path.join(d, os.path.basename(p)))
    kb.index_docs()
    return '%s deplace dans %s.' % (os.path.basename(p), os.path.basename(d))


def renommer(nom, nouveau):
    p, err = _un(nom)
    if not p:
        return err
    q = os.path.join(os.path.dirname(p), nouveau.strip())
    if '.' not in nouveau and '.' in os.path.basename(p) and os.path.isfile(p):
        q += '.' + os.path.basename(p).rsplit('.', 1)[1]
    if not (_ok(p) and _ok(q)) or os.path.exists(q):
        return 'Impossible de renommer.'
    _mv(p, q)
    kb.index_docs()
    return 'Renomme en %s.' % os.path.basename(q)


def supprimer(nom):
    p, err = _un(nom)
    if not p:
        return err
    if not _ok(p) or os.path.realpath(p) in [os.path.realpath(r) for r in RACINES]:
        return 'Refuse.'
    os.makedirs(CORBEILLE, exist_ok=True)
    _mv(p, os.path.join(CORBEILLE, '%d_%s' % (time.time(), os.path.basename(p))))
    kb.index_docs()
    return '%s mis a la corbeille.' % os.path.basename(p)


def restaurer(nom):
    n = kb._norm(nom)
    try:
        for x in sorted(os.listdir(CORBEILLE), reverse=True):
            if n in kb._norm(x):
                orig = x.split('_', 1)[1]
                _mv(os.path.join(CORBEILLE, x), os.path.join(ROOT, orig))
                kb.index_docs()
                return '%s restaure.' % orig
    except Exception:
        pass
    return "Rien a restaurer pour %s." % nom


def vider_vieille_corbeille(jours=30):
    try:
        for x in os.listdir(CORBEILLE):
            p = os.path.join(CORBEILLE, x)
            if time.time() - os.path.getmtime(p) > jours * 86400:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    except Exception:
        pass


def ranger():
    n = 0
    try:
        for x in os.listdir(ROOT):
            p = os.path.join(ROOT, x)
            if not os.path.isfile(p):
                continue
            ext = x.lower().rsplit('.', 1)[-1] if '.' in x else ''
            for dossier, exts in TYPES.items():
                if ext in exts:
                    os.makedirs(os.path.join(ROOT, dossier), exist_ok=True)
                    _mv(p, os.path.join(ROOT, dossier, x))
                    n += 1
                    break
    except Exception:
        return 'Je ne peux pas ranger.'
    kb.index_docs()
    return '%d fichier(s) ranges par type.' % n


def executer(texte):
    t = kb._norm(texte).strip(' .!?')
    m = re.search(r'\b(?:liste|montre|affiche)\w*\s+(?:moi\s+)?(?:mes\s+|les\s+)?(?:fichiers|dossiers)(?:\s+(?:de|du|dans)\s+(?:le\s+|la\s+)?(.+))?$', t)
    if m:
        return lister((m.group(1) or '').strip())
    m = re.search(r'\bcree\w*\s+(?:un\s+)?dossier\s+(?:nomme\s+|appele\s+)?(.+)$', t)
    if m:
        return creer(m.group(1))
    if re.search(r'\brange\w*\s+(?:mes\s+|les\s+)?(?:fichiers|dossiers)', t):
        return ranger()
    m = re.search(r'\bdeplace\w*\s+(.+?)\s+(?:dans|vers|sur)\s+(?:le\s+)?(?:dossier\s+)?(.+)$', t)
    if m:
        return deplacer(m.group(1), m.group(2))
    m = re.search(r'\brenomme\w*\s+(.+?)\s+(?:en|par)\s+(.+)$', t)
    if m:
        return renommer(m.group(1), m.group(2))
    m = re.search(r'\b(?:supprime|efface|enleve|jette)\w*\s+(?:le\s+|la\s+|les\s+)?(?:fichier\s+|dossier\s+)(.+)$', t)
    if m:
        return supprimer(m.group(1))
    m = re.search(r'\brestaure\w*\s+(.+)$', t)
    if m:
        return restaurer(m.group(1))
    return None


import json
JOURNAL = os.path.join(brain.DATA, 'journal.json')
_B = {'id': 0}
_real = __import__('shutil')


def _jload():
    try:
        with open(JOURNAL) as f:
            return json.load(f)
    except Exception:
        return []


def _mv(src, dst):
    r = _real.move(src, dst)
    j = _jload()
    j.append([_B['id'], src, dst])
    try:
        with open(JOURNAL, 'w') as f:
            json.dump(j[-200:], f)
    except Exception:
        pass
    return r


def annuler():
    j = _jload()
    if not j:
        return 'Rien a annuler.'
    bid, n = j[-1][0], 0
    while j and j[-1][0] == bid:
        _, src, dst = j.pop()
        try:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            _real.move(dst, src)
            n += 1
            pd = os.path.dirname(dst)
            if pd not in RACINES:
                os.rmdir(pd)
        except Exception:
            pass
    try:
        with open(JOURNAL, 'w') as f:
            json.dump(j, f)
    except Exception:
        pass
    kb.index_docs()
    return 'Annule : %d element(s) remis en place.' % n


def noter(texte):
    ligne = '(%s) %s' % (time.strftime('%d/%m/%Y'), texte.strip())
    try:
        os.makedirs(ROOT, exist_ok=True)
        with open(os.path.join(ROOT, 'notes.txt'), 'a', encoding='utf-8') as f:
            f.write(ligne + '\n\n')
        kb.index_docs()
    except Exception:
        kb.add(ligne, 'note')
    return 'Note enregistree.'


def lire(nom):
    p, err = _un(nom)
    if not p:
        return err
    if os.path.isdir(p):
        return lister(nom)
    if not p.lower().endswith(('.txt', '.md')):
        return 'Je ne sais lire que les fichiers texte.'
    with open(p, encoding='utf-8', errors='replace') as f:
        s = ' '.join(f.read(600).split())
    return s[:350] or 'Le fichier est vide.'


_executer0 = executer


def executer(texte):
    _B['id'] = time.time()
    t = kb._norm(texte).strip(' .!?')
    if re.match(r'^(?:annule|annuler|defais)(?:\s+(?:ca|tout|la derniere action|le dernier rangement))?$', t) and _recent():
        return annuler()
    m = re.match(r'^\W*(?:note|retiens|memorise|enregistre)\w*\s+(?:que\s+|ceci\s*:?\s*)(.+)$', texte.strip(), re.I | re.S)
    if m:
        return noter(m.group(1))
    m = re.search(r'\bquels?\s+fichiers?\s+(?:parle|contient|traite|mentionne)\w*\s+(?:de\s+|du\s+|des\s+|d\')?(.+)$', t)
    if m:
        r = kb.fichiers(m.group(1))
        return ('Regarde : ' + ', '.join(r) + '.') if r else 'Aucun fichier ne parle de ca.'
    m = re.search(r'\blis\w*(?:[\s-]+moi)?\s+(?:le\s+debut\s+(?:de\s+|du\s+))?(?:le\s+)?fichier\s+(.+)$', t)
    if m:
        return lire(m.group(1))
    return _executer0(texte)


def _recent(sec=600):
    j = _jload()
    return bool(j) and time.time() - j[-1][0] < sec
