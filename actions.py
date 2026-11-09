# Niko : actions (lampe, appli, minuteur, alarme). Toute action est confirmee a voix haute avant d'etre executee.
import os, re, json, time, difflib, unicodedata, datetime, urllib.request

LOG = print
DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
LLM_URL = 'http://127.0.0.1:8080/v1/chat/completions'
TIMERS_FILE = os.path.join(DATA, 'niko_minuteurs.json')


def norm(t):
    t = unicodedata.normalize('NFD', t.lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', t)).strip()


# ---------- nombres en francais ----------
_U = {'zero': 0, 'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5, 'six': 6, 'sept': 7, 'huit': 8,
      'neuf': 9, 'dix': 10, 'onze': 11, 'douze': 12, 'treize': 13, 'quatorze': 14, 'quinze': 15, 'seize': 16,
      'vingt': 20, 'vingts': 20, 'trente': 30, 'quarante': 40, 'cinquante': 50, 'soixante': 60}


def is_num(t):
    return t in _U or t in ('cent', 'cents') or t.isdigit()


def fr_num(tk):
    cur, seen, i = 0, False, 0
    while i < len(tk):
        t = tk[i]
        if t == 'et':
            i += 1
            continue
        if t == 'quatre' and i + 1 < len(tk) and tk[i + 1] in ('vingt', 'vingts'):
            cur, seen, i = cur + 80, True, i + 2
            continue
        if t in ('cent', 'cents'):
            cur, seen = (cur or 1) * 100, True
        elif t in _U:
            cur, seen = cur + _U[t], True
        elif t.isdigit():
            cur, seen = cur + int(t), True
        else:
            return None
        i += 1
    return cur if seen else None


def num_before(tk, idx):
    j = idx
    while j > 0 and (is_num(tk[j - 1]) or tk[j - 1] == 'et'):
        j -= 1
    while j < idx and tk[j] == 'et':
        j += 1
    return fr_num(tk[j:idx])


def num_after(tk, idx):
    j = idx
    while j < len(tk) and (is_num(tk[j]) or (tk[j] == 'et' and j > idx)):
        j += 1
    while j > idx and tk[j - 1] == 'et':
        j -= 1
    return fr_num(tk[idx:j])


# ---------- durees et heures ----------
def parse_duree(n):
    if re.search(r'\bquart (?:d )?heure\b', n):
        return 900
    if re.search(r'\bdemie? heure\b', n):
        return 1800
    tk = n.split()
    units = {'seconde': 1, 'secondes': 1, 'minute': 60, 'minutes': 60, 'heure': 3600, 'heures': 3600}
    total, found = 0, False
    for i, t in enumerate(tk):
        if t not in units:
            continue
        v = num_before(tk, i)
        if v is None:
            continue
        total += v * units[t]
        found = True
        nx = tk[i + 1:i + 3]
        if t in ('heure', 'heures') and len(nx) == 2 and nx[0] == 'et':
            if nx[1] in ('demie', 'demi'):
                total += 1800
            elif nx[1] == 'quart':
                total += 900
    return total if (found and 0 < total <= 86400) else None


def parse_heure(n):
    h = m = None
    if re.search(r'\bminuit\b', n):
        h, m = 0, 0
    elif re.search(r'\bmidi\b', n):
        h, m = 12, 0
    if h is not None:
        if re.search(r'\b(midi|minuit) et demi', n):
            m = 30
    else:
        mm = re.search(r'\b(\d{1,2}) ?h ?(\d{2})?\b', n)
        if mm:
            h, m = int(mm.group(1)), int(mm.group(2) or 0)
        else:
            tk = n.split()
            for i, t in enumerate(tk):
                if t in ('heure', 'heures'):
                    h = num_before(tk, i)
                    if h is None:
                        continue
                    rest = tk[i + 1:]
                    m = 0
                    if rest[:2] in (['et', 'demie'], ['et', 'demi']):
                        m = 30
                    elif rest[:2] == ['et', 'quart']:
                        m = 15
                    elif rest[:3] == ['moins', 'le', 'quart']:
                        h, m = h - 1, 45
                    elif rest[:1] == ['moins']:
                        v = num_after(rest, 1)
                        if v is not None and 0 < v < 60:
                            h, m = h - 1, 60 - v
                    else:
                        v = num_after(rest, 0)
                        if v is not None and 0 <= v < 60:
                            m = v
                    break
    if h is None or m is None:
        return None
    if 1 <= h < 12 and re.search(r'\b(soir|apres midi)\b', n):
        h += 12
    h %= 24
    if not (0 <= m < 60):
        return None
    return h, m


def next_time(h, m, n='', now=None):
    now = now or datetime.datetime.now()
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        t += datetime.timedelta(days=1)
    elif 'demain' in n.split() and t.date() == now.date():
        t += datetime.timedelta(days=1)
    return t.timestamp()


def fmt_duree(s):
    h, r = divmod(int(s), 3600)
    mi, se = divmod(r, 60)
    p = []
    if h:
        p.append('%d heure%s' % (h, 's' if h > 1 else ''))
    if mi:
        p.append('%d minute%s' % (mi, 's' if mi > 1 else ''))
    if se:
        p.append('%d seconde%s' % (se, 's' if se > 1 else ''))
    return ' '.join(p) or '0 seconde'


def fmt_heure(h, m):
    return '%d heure%s' % (h, 's' if h > 1 else '') + ((' %02d' % m) if m else '')


# ---------- comprendre la demande (regles : instantanees et sures) ----------
_OPEN = ('ouvre', 'ouvrir', 'lance', 'lancer', 'demarre', 'demarrer')
_ART = {'l', 'le', 'la', 'les', 'un', 'une', 'application', 'appli', 'app', 'moi', 'mon', 'ma', 'mes', 'de', 'd', 'du', 'des'}


def parse(text):
    n = norm(text)
    if not n:
        return None
    tk = n.split()
    if re.search(r'\b(lampe|torche|flash)\b', n):
        if re.search(r'\b(eteins|eteint|eteindre|etein|coupe|couper|desactive|desactiver|arrete|arreter|ferme|fermer)\b', n):
            return {'action': 'torche', 'etat': 'off'}
        if re.search(r'\b(allume|allumer|active|activer|mets|met|mettre|ouvre|ouvrir|lance|lancer)\b', n):
            return {'action': 'torche', 'etat': 'on'}
    if re.search(r'\b(minuteur|minuterie|compte a rebours|chrono|timer)\b', n):
        s = parse_duree(n)
        if s:
            return {'action': 'minuteur', 'secondes': s}
    if re.search(r'\b(alarme|reveil|reveille|reveiller)\b', n):
        t = parse_heure(n)
        if t:
            return {'action': 'alarme', 'heure': t[0], 'minute': t[1], 'demain': 'demain' in tk}
    for i, t in enumerate(tk[:4]):
        if t in _OPEN:
            rest = tk[i + 1:]
            while rest and rest[0] in _ART:
                rest = rest[1:]
            if rest and len(rest) <= 4:
                return {'action': 'ouvrir_app', 'app': ' '.join(rest), 'fort': t in ('ouvre', 'ouvrir')}
            break
    return None


_YES = ('oui', 'ouais', 'ouai', 'ok', 'okay', 'd accord', 'dac', 'vas y', 'allez', 'go', 'c est bon', 'confirme',
        'exactement', 'parfait', 'yes', 'bien sur', 'carrement', 'volontiers', 'fais le')
_NO = ('non', 'nan', 'nope', 'annule', 'annuler', 'laisse tomber', 'surtout pas', 'pas du tout', 'jamais', 'stop', 'arrete')


def _has(n, words):
    return any(re.search(r'\b%s\b' % re.escape(w), n) for w in words)


def is_no(n):
    return _has(n, _NO)


def is_yes(n):
    return _has(n, _YES) and not _has(n, _NO)


# ---------- comprendre la demande (modele : pour les phrases que les regles ne reconnaissent pas) ----------
SCHEMA = {'type': 'object', 'properties': {
    'action': {'type': 'string', 'enum': ['torche', 'ouvrir_app', 'minuteur', 'alarme', 'aucune']},
    'etat': {'type': 'string', 'enum': ['on', 'off']},
    'app': {'type': 'string'},
    'secondes': {'type': 'integer'},
    'heure': {'type': 'integer'},
    'minute': {'type': 'integer'}}, 'required': ['action']}

_SYS = ("Tu transformes une demande vocale en commande JSON pour un téléphone. Actions : torche (etat on ou off), "
        "ouvrir_app (app = nom de l'application), minuteur (secondes), alarme (heure de 0 à 23, minute de 0 à 59), "
        "aucune (si la demande ne correspond à aucune de ces actions). Réponds uniquement par le JSON.\n"
        "Exemples :\n"
        "allume la lumière de poche -> {\"action\":\"torche\",\"etat\":\"on\"}\n"
        "mets un minuteur de dix minutes -> {\"action\":\"minuteur\",\"secondes\":600}\n"
        "lance youtube -> {\"action\":\"ouvrir_app\",\"app\":\"YouTube\"}\n"
        "réveille-moi à six heures et demie -> {\"action\":\"alarme\",\"heure\":6,\"minute\":30}\n"
        "quelle heure est-il -> {\"action\":\"aucune\"}")


def _valide(d):
    if not isinstance(d, dict):
        return None
    a = d.get('action')
    try:
        if a == 'torche':
            return {'action': 'torche', 'etat': 'off' if d.get('etat') == 'off' else 'on'}
        if a == 'ouvrir_app' and str(d.get('app') or '').strip():
            return {'action': 'ouvrir_app', 'app': str(d['app']).strip(), 'fort': False}
        if a == 'minuteur':
            s = int(d.get('secondes') or 0)
            if 1 <= s <= 86400:
                return {'action': 'minuteur', 'secondes': s}
        if a == 'alarme':
            h, m = int(d.get('heure')), int(d.get('minute') or 0)
            if 0 <= h < 24 and 0 <= m < 60:
                return {'action': 'alarme', 'heure': h, 'minute': m, 'demain': False}
    except Exception:
        pass
    return None


def _post(msgs, schema):
    body = {'messages': msgs, 'max_tokens': 70, 'temperature': 0, 'stream': False, 'cache_prompt': True}
    if schema:
        body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'action', 'strict': True, 'schema': SCHEMA}}
    req = urllib.request.Request(LLM_URL, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())['choices'][0]['message']['content'] or ''


def llm_action(text):
    msgs = [{'role': 'system', 'content': _SYS}, {'role': 'user', 'content': text}]
    out = None
    for schema in (True, False):
        try:
            out = _post(msgs, schema)
            break
        except Exception as e:
            LOG('  action (modele) : echec %s ; %s' % ('avec grammaire' if schema else 'sans grammaire', repr(e)[:120]))
    if not out:
        return None
    try:
        d = json.loads(out[out.index('{'):out.rindex('}') + 1])
    except Exception:
        LOG('  action (modele) : reponse illisible : ' + out[:80])
        return None
    return _valide(d)


# ---------- Android ----------
def _service():
    from jnius import autoclass
    return autoclass('org.kivy.android.PythonService').mService


def _apps():
    from jnius import autoclass
    pm = _service().getPackageManager()
    Intent = autoclass('android.content.Intent')
    it = Intent(Intent.ACTION_MAIN)
    it.addCategory(Intent.CATEGORY_LAUNCHER)
    lst = pm.queryIntentActivities(it, 0)
    out = []
    for i in range(lst.size()):
        ri = lst.get(i)
        out.append((str(ri.loadLabel(pm).toString()), str(ri.activityInfo.packageName)))
    return out


def match_app(name, apps):
    q = norm(name)
    qs = q.replace(' ', '')
    best, bs = None, 0.0
    for label, pkg in apps:
        l = norm(label)
        if not l:
            continue
        ls = l.replace(' ', '')
        if l == q or ls == qs:
            s = 1.0
        elif q in l.split() or l in q.split():
            s = 0.95
        elif len(l) >= 3 and len(q) >= 3 and (q in l or l in q):
            s = 0.85
        else:
            s = max(difflib.SequenceMatcher(None, q, l).ratio(), difflib.SequenceMatcher(None, qs, ls).ratio())
        if s > bs:
            best, bs = (label, pkg), s
    return best if bs >= 0.62 else None


def prepare(a):
    # Renvoie (action prete, message). (None, message) : on le dit tel quel. (None, None) : ce n'etait pas une action.
    if a['action'] != 'ouvrir_app':
        return a, None
    try:
        apps = _apps()
    except Exception as e:
        LOG('applis : ' + repr(e))
        apps = []
    if not apps:
        return None, "Je n'arrive pas à lire la liste des applications."
    m = match_app(a['app'], apps)
    if not m:
        LOG('  appli introuvable : "%s" (%d applis)' % (a['app'], len(apps)))
        return None, ("Je ne trouve pas l'application %s." % a['app']) if a.get('fort') else None
    a = dict(a)
    a['label'], a['pkg'] = m
    return a, None


def confirm_text(a):
    k = a['action']
    if k == 'torche':
        return ("J'allume" if a.get('etat', 'on') == 'on' else "J'éteins") + " la lampe torche, d'accord ?"
    if k == 'ouvrir_app':
        return "J'ouvre %s, d'accord ?" % (a.get('label') or a.get('app'))
    if k == 'minuteur':
        return "Je lance un minuteur de %s, d'accord ?" % fmt_duree(a['secondes'])
    return "Je règle une alarme à %s, d'accord ?" % fmt_heure(a['heure'], a['minute'])


def _torche(on):
    err = None
    try:
        from jnius import autoclass, cast
        Context = autoclass('android.content.Context')
        cm = cast('android.hardware.camera2.CameraManager', _service().getSystemService(Context.CAMERA_SERVICE))
        for cid in list(cm.getCameraIdList()):
            try:
                cm.setTorchMode(cid, bool(on))
                LOG('  torche %s (camera %s)' % ('allumee' if on else 'eteinte', cid))
                return 'Lampe allumée.' if on else 'Lampe éteinte.'
            except Exception as e:
                err = e
    except Exception as e:
        err = e
    LOG('  torche : echec ' + repr(err))
    return "Je n'ai pas réussi à utiliser la lampe."


def _ouvrir(a):
    try:
        from jnius import autoclass
        Intent = autoclass('android.content.Intent')
        svc = _service()
        it = svc.getPackageManager().getLaunchIntentForPackage(a['pkg'])
        it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        svc.startActivity(it)
        LOG('  appli lancee : %s (%s)' % (a['label'], a['pkg']))
        return "J'ouvre %s." % a['label']
    except Exception as e:
        LOG('  ouvrir : echec ' + repr(e))
        return "Je n'ai pas réussi à ouvrir %s." % a.get('label', "l'application")


# ---------- minuteurs et alarmes (geres par Niko lui-meme, sauvegardes dans un fichier) ----------
_T = {'items': None}


def _tload():
    if _T['items'] is None:
        try:
            _T['items'] = json.load(open(TIMERS_FILE))
        except Exception:
            _T['items'] = []
    return _T['items']


def _tsave():
    try:
        tmp = TIMERS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(_T['items'], f)
        os.replace(tmp, TIMERS_FILE)
    except Exception as e:
        LOG('minuteurs : sauvegarde impossible ' + repr(e))


def add_timer(kind, t, label):
    _tload().append({'k': kind, 't': t, 'x': label})
    _tsave()


def due():
    items = _tload()
    now, out, keep = time.time(), [], []
    for it in items:
        if it['t'] > now:
            keep.append(it)
        elif now - it['t'] > 600:
            LOG('minuteurs : %s manque de %d s, oublie' % (it['k'], now - it['t']))
        else:
            msg = ('Ton minuteur de %s est terminé.' if it['k'] == 'minuteur' else "C'est l'heure ! Ton alarme de %s sonne.") % it['x']
            out.append(msg + ' ' + msg)
    if len(keep) != len(items):
        _T['items'] = keep
        _tsave()
    return out


def run(a):
    k = a['action']
    if k == 'torche':
        return _torche(a.get('etat', 'on') == 'on')
    if k == 'ouvrir_app':
        return _ouvrir(a)
    if k == 'minuteur':
        add_timer('minuteur', time.time() + a['secondes'], fmt_duree(a['secondes']))
        return 'Minuteur lancé.'
    if k == 'alarme':
        t = next_time(a['heure'], a['minute'], 'demain' if a.get('demain') else '')
        add_timer('alarme', t, fmt_heure(a['heure'], a['minute']))
        quand = datetime.datetime.fromtimestamp(t)
        jour = " demain" if quand.date() > datetime.date.today() else ''
        return 'Alarme réglée à %s%s.' % (fmt_heure(a['heure'], a['minute']), jour)
    return "Je ne sais pas faire ça."
