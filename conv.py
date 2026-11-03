# Conversation continue de Julia (tourne dans le processus de l'appli, à côté de main.py).
# Les oreilles (service, Vosk) envoient ce qu'elles entendent à /api/conv/ear ; ici on décide :
#   - réveil « dis Julia » -> la conversation s'ouvre ; elle reste ouverte tant que tu parles ou qu'un travail tourne
#   - chaque question devient un TRAVAIL (recherche, réflexion) qui tourne en arrière-plan ;
#     tu peux continuer à parler : une nouvelle question reçoit sa propre réponse tout de suite
#   - un travail terminé pendant que tu parles d'autre chose attend un moment calme, puis Julia l'annonce
#   - tu parles pendant qu'elle parle -> elle se tait tout de suite et t'écoute
#   - sa propre voix captée par le micro est reconnue et ignorée
import re, time, threading, unicodedata, collections, difflib

CFG = {'lang': 'FR', 'mem': 1, 'web': 1, 'rate': 1.0, 'pitch': 1.0, 'vname': '', 'tz': 0, 'hf': 1,
       'pause': 'normale', 'conf': 0.45, 'mic': 'precis', 'barge': 0}
PAUSE = {'courte': 0.8, 'normale': 1.3, 'longue': 2.0}   # silence attendu avant d'envoyer la phrase au cerveau
S = {'active': False, 'phase': 'idle', 'user': '', 'reply': '', 'last': 0.0, 'wakes': 0,
     'ears': 0.0, 'say_t': 0.0, 'tts_end': 0.0, 'was_busy': False, 'cuts': 0, 'err': '',
     'buf': [], 'buf_conf': [], 'buf_t': 0.0, 'rep': 0, 'acks': 0, 'last_req': ('', 0.0)}
J = []            # travaux : {'jid','q','mode','spoken','filler','t0','st','lead','text','queued','data'}
MAX_PAR = 2       # le serveur llama a 2 emplacements
JOURNAL = collections.deque(maxlen=14)
H = {}
L = threading.RLock()
SPOKEN = collections.deque(maxlen=40)   # (heure, mots) des phrases dites : sert à reconnaitre l'écho
IDLE_S = 30.0

WAKE = re.compile(r"^\s*(?:(?:dis|salut|ok|okay|hey|hé|eh|oh|bonjour)\s+)?(?:julia|julie|giulia|jolie|joulia)\b[\s,.!?]*", re.I)
STOP = re.compile(r"^(?:ok |bon |oui )?(?:julia )?(?:stop|arrete|arrete toi|tais toi|chut|attends|attend|ca suffit|silence|pause)(?: julia)?$")
CANCEL = re.compile(r"^(?:ok |bon |oui )?(?:julia )?(?:laisse tomber|annule tout|annule|oublie ca|oublie)(?: julia)?$")
BYE = re.compile(r"\b(au revoir|a plus tard|a tout a l heure|c est tout|on s arrete|fin de la conversation|bonne nuit|merci julia)\b")
CUT_AT = re.compile(r"(?<!\d)[.!?…]+(?=\s)|\n")
ACKS = ["Une seconde, je regarde ça.", "Je cherche ça. Dis-moi si tu veux autre chose en attendant.",
        "Je vérifie, je te dis ça dans un instant."]


def norm(t):
    t = unicodedata.normalize('NFD', (t or '').lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.findall(r"[a-z0-9]+", t)


def init(**k):
    H.update(k)
    threading.Thread(target=_loop, daemon=True).start()


def set_cfg(d):
    for k in ('lang', 'vname'):
        if k in d:
            CFG[k] = str(d[k])
    if d.get('pause') in PAUSE:
        CFG['pause'] = d['pause']
    if d.get('mic') in ('precis', 'antiecho'):
        CFG['mic'] = d['mic']
    for k in ('mem', 'web', 'hf', 'barge'):
        if k in d:
            CFG[k] = 1 if d[k] else 0
    for k in ('rate', 'pitch'):
        if k in d:
            try:
                CFG[k] = float(d[k])
            except Exception:
                pass
    if 'tz' in d:
        try:
            CFG['tz'] = int(d['tz'])
        except Exception:
            pass
    return state()


def state():
    now = time.time()
    jobs = [{'q': j['q'][:48], 'st': j['st']} for j in J if not (j['mode'] == 'stream' and (j['spoken'] > 0 or j['st'] == 'prête'))]
    return {'active': S['active'], 'phase': S['phase'], 'user': S['user'], 'reply': S['reply'],
            'wakes': S['wakes'], 'cuts': S['cuts'], 'hf': CFG['hf'], 'barge': CFG['barge'], 'err': S['err'], 'mic': CFG['mic'],
            'jobs': jobs, 'ears_age': round(now - S['ears'], 1) if S['ears'] else None}


def _log(text, conf, how):
    JOURNAL.append({'t': time.strftime('%H:%M:%S'), 'text': (text or '')[:90],
                    'conf': None if conf is None else round(conf, 2), 'how': how})


def log():
    return list(JOURNAL)[::-1]


# ---------- voix ----------
def _busy():
    try:
        b = bool(H['busy']())
    except Exception:
        b = False
    return b or time.time() - S['say_t'] < 0.7


def _tail():
    # vrai tant que le micro peut encore entendre la voix de Julia (parole en cours + petite queue)
    return _busy() or time.time() - S['tts_end'] < 1.5


def _say(text):
    S['say_t'] = time.time()
    SPOKEN.append((S['say_t'], set(norm(text)), ' '.join(norm(text))))
    H['say'](text, CFG['pitch'], CFG['rate'], CFG['lang'], CFG['vname'])


def _is_echo(text):
    # Vosk transcrit mal la voix de Julia (« veux » -> « vas ») : on compare en tolérant les erreurs
    ws = norm(text)
    if not ws:
        return True
    now = time.time()
    vocab, joined = set(), []
    for t, w, jn in SPOKEN:
        if now - t < 25:
            vocab |= w
            joined.append(jn)
    if not vocab:
        return False
    vl = list(vocab)
    hit = 0
    for w in ws:
        if w in vocab or (len(w) >= 3 and difflib.get_close_matches(w, vl, n=1, cutoff=0.72)):
            hit += 1
    if hit / float(len(ws)) >= 0.6:
        return True
    t, big = ' '.join(ws), ' '.join(joined)
    m = difflib.SequenceMatcher(None, t, big).find_longest_match(0, len(t), 0, len(big))
    return m.size >= 0.55 * len(t)


# ---------- travaux ----------
def _kill(j, cancel=True):
    if cancel and j.get('jid'):
        try:
            H['job_cancel'](j['jid'])
        except Exception:
            pass
    if j.get('jid'):
        try:
            H['job_drop'](j['jid'])
        except Exception:
            pass
    if j in J:
        J.remove(j)
    S['last'] = time.time()


def _cut():
    # Julia se tait tout de suite ; le travail qui parlait est abandonné (tu l'as coupé exprès),
    # les travaux de fond qui n'ont encore rien dit continuent.
    try:
        H['stop']()
    except Exception:
        pass
    for j in list(J):
        if j.get('lead_said'):
            # une réponse de fond qu'il a coupée n'est pas perdue : on la redira à un moment calme
            j.update(spoken=0, lead_said=False, mode='hold')
        elif j['spoken'] > 0:
            _kill(j)
    S['cuts'] += 1


def _running():
    return [j for j in J if j.get('jid') and j['st'] == 'cherche']


def _launch(j):
    used = set(x.get('slot') for x in _running())
    slot = 0 if 0 not in used else 1
    r = H['start_job'](dict(j['data'], text=j['q']), slot)
    j.update(jid=r['id'], slot=slot, st='cherche', queued=False, t0=time.time())


def _new_job(text):
    data = {'lang': CFG['lang'], 'voice': 0, 'mem': CFG['mem'], 'web': CFG['web'], 'model': 'general', 'asr': 1,
            'ts': int(time.time() * 1000), 'tz': CFG['tz']}
    j = {'jid': None, 'q': text, 'mode': 'stream', 'spoken': 0, 'filler': False, 't0': time.time(), 'st': 'attente',
         'text': '', 'queued': True, 'data': data, 'lead_said': False, 'slot': None}
    # l'ancien travail de premier plan passe en arrière-plan : son résultat sera annoncé plus tard
    for o in J:
        if o['mode'] == 'stream':
            o['mode'] = 'hold'
    J.append(j)
    if len(_running()) < MAX_PAR:
        _launch(j)
    else:
        S['acks'] += 1
        _say("Je note, je m'en occupe juste après.")
    return j


# ---------- événements des oreilles ----------
def ear(kind, text, wake=False, conf=None):
    text = (text or '').strip()
    now = time.time()
    S['ears'] = now
    if not text:
        return state()
    with L:
        if not S['active']:
            if wake and CFG['hf']:
                _start(text)
            return state()
        ws = ' '.join(norm(text))
        stop = bool(STOP.match(ws) or CANCEL.match(ws))
        speaking = _busy()
        # Julia ne doit jamais se répondre à elle-même : le micro entend sa propre voix
        if not stop:
            if speaking:
                if not (CFG['barge'] and kind in ('P', 'R') and not _is_echo(text)):
                    if kind == 'R':
                        _log(text, conf, 'ignoré : Julia parlait')
                    return state()
                if kind == 'P':          # coupure de parole (option) : tu as parlé par-dessus elle
                    _cut()
                    S['phase'] = 'listening'
            elif now - S['tts_end'] < (2.2 if kind == 'R' else 0.9):
                # elle vient de finir : un écho tardif est mal reconnu (confiance basse) et ressemble à ce qu'elle a dit
                if _is_echo(text) or (conf is not None and conf < 0.75):
                    if kind == 'R':
                        _log(text, conf, 'écho ignoré')
                    return state()
        S['last'] = now
        if kind == 'P':
            S['buf_t'] = now          # il parle encore : on n'envoie rien tant qu'il n'a pas fini
            S['user'] = (' '.join(S['buf']) + ' ' + text).strip()
        elif kind == 'R':
            if stop or (BYE.search(ws) and len(ws.split()) <= 6):
                S['buf'], S['buf_conf'] = [], []
                _request(text, conf)
            else:
                # Vosk coupe à la moindre pause : on recolle les morceaux jusqu'à un vrai silence
                S['buf'].append(text)
                S['buf_conf'].append(conf)
                S['buf_t'] = now
                S['user'] = ' '.join(S['buf'])
    return state()


def _flush():
    if not S['buf']:
        return
    text = ' '.join(S['buf'])
    cs = [x for x in S['buf_conf'] if x is not None]
    conf = (sum(cs) / len(cs)) if cs else None
    S['buf'], S['buf_conf'] = [], []
    _request(text, conf)


def _start(text=''):
    S.update(active=True, phase='listening', user='', reply='', last=time.time(), err='')
    S['wakes'] += 1
    rest = WAKE.sub('', text).strip() if text else ''
    if len(norm(rest)) >= 2:
        _request(rest)


def start():
    with L:
        _start('')
    return state()


def end(say_bye=False):
    with L:
        S.update(active=False, phase='idle', buf=[], buf_conf=[])
        try:
            H['stop']()
        except Exception:
            pass
        for j in list(J):
            _kill(j)
        if say_bye:
            _say("À tout à l'heure.")
    return state()


def cut():
    # appui sur l'écran pendant qu'elle parle : elle se tait, la conversation reste ouverte
    with L:
        _cut()
        S.update(phase='listening', last=time.time())
    return state()


def text(t):
    # message écrit pendant une conversation : même chemin que la voix (la réponse est dite aussi)
    with L:
        if not S['active']:
            _start('')
        _request(t)
    return state()


def _request(text, conf=None):
    text = WAKE.sub('', text).strip()
    ws = ' '.join(norm(text))
    if not ws:
        S['phase'] = 'listening'
        return
    if CANCEL.match(ws):
        _log(text, conf, 'tout annulé')
        try:
            H['stop']()
        except Exception:
            pass
        for j in list(J):
            _kill(j)
        S.update(phase='listening', user=text, reply='')
        return
    if STOP.match(ws):
        _log(text, conf, 'stop')
        _cut()
        S.update(phase='listening', user=text)
        return
    if BYE.search(ws) and len(ws.split()) <= 6:
        S.update(user=text, reply='')
        _log(text, conf, 'au revoir')
        end(say_bye=True)
        return
    if conf is not None and conf < CFG['conf'] and S['rep'] < 2:
        # le micro n'est pas sûr : mieux vaut redemander que répondre à côté
        S['rep'] += 1
        _log(text, conf, 'à répéter')
        S.update(user=text, phase='listening', reply='')
        _say("Je n'ai pas bien compris, tu peux répéter ?")
        return
    lw, lt = S['last_req']
    if lw and time.time() - lt < 6 and difflib.SequenceMatcher(None, lw, ws).ratio() >= 0.85:
        _log(text, conf, 'doublon ignoré')
        return
    S['last_req'] = (ws, time.time())
    S['rep'] = 0
    _log(text, conf, 'envoyé')
    # si elle était en train de parler de la réponse précédente, tu l'as coupée : on l'abandonne
    if _busy():
        _cut()
    S.update(user=text, reply='', last=time.time())
    try:
        _new_job(text)
    except Exception as e:
        S.update(phase='listening', err=repr(e)[:80])
        return
    S['phase'] = 'thinking'


# ---------- voix des travaux ----------
def _speakable(t):
    return re.sub(r"```[\s\S]*?(```|$)", " (le code est à l'écran) ", t)


def _speak_new(j, st):
    # dit les phrases terminées du travail ; True si quelque chose a été dit
    done = bool(st.get('done'))
    sp = _speakable(st.get('text') or '')
    said = False
    while True:
        rest = sp[j['spoken']:]
        m = CUT_AT.search(rest)
        if m and not done:
            seg, adv = rest[:m.end()], m.end()
        elif done and rest.strip():
            seg, adv = rest, len(rest)
        else:
            break
        j['spoken'] += adv
        if len(seg.strip()) > 2:
            if not j['lead_said'] and j['mode'] == 'hold':
                j['lead_said'] = True
                _say('À propos de « %s » : ' % ' '.join(j['q'].split()[:8]))
            _say(seg.strip())
            said = True
        if done:
            break
    return said


def _quiet():
    # moment calme : il ne parle pas, elle ne parle pas
    now = time.time()
    return (not S['buf']) and now - S['buf_t'] > 1.0 and now - S['last'] > 1.2 and not _tail()


def _tick():
    now = time.time()
    # lancer les travaux en attente dès qu'un emplacement est libre
    for j in J:
        if j['queued'] and len(_running()) < MAX_PAR:
            _launch(j)
    stream_active = any(j['mode'] == 'stream' for j in J)
    for j in list(J):
        if not j.get('jid'):
            continue
        st = H['job_state'](j['jid'])
        if st is None or st.get('gone'):
            _kill(j, cancel=False)
            continue
        j['text'] = st.get('text') or ''
        done = bool(st.get('done'))
        if done and j['st'] != 'prête':
            j['st'] = 'prête'
        if j['mode'] == 'stream':
            S['reply'] = j['text'] if (j['text'] or j['spoken']) else S['reply']
            if _speak_new(j, st):
                S['phase'] = 'speaking'
            # pas de réponse après un moment : un petit mot pour ne pas laisser un silence
            waited = now - j['t0']
            if not j['text'] and not j['filler'] and not _tail() and (
                    (st.get('stage') == 'search' and waited > 1.2) or waited > 4.5):
                j['filler'] = True
                _say(ACKS[S['acks'] % len(ACKS)])
                S['acks'] += 1
            if done and j['spoken'] >= len(_speakable(j['text'])) and not _busy():
                _kill(j, cancel=False)
        else:   # travail de fond : on attend la fin puis un moment calme
            if done and _quiet() and not stream_active:
                S['reply'], S['user'] = j['text'], j['q']
                _speak_new(j, st)
                S['phase'] = 'speaking'
                if j['spoken'] >= len(_speakable(j['text'])):
                    j['mode'] = 'stream'      # fini de dire : sera retiré au prochain passage
    return stream_active


def _loop():
    while True:
        try:
            now = time.time()
            b = _busy()
            if S['was_busy'] and not b:
                S['tts_end'] = now
            S['was_busy'] = b
            with L:
                if not S['active']:
                    S['buf'], S['buf_conf'] = [], []
                    if J:
                        for j in list(J):
                            _kill(j)
                else:
                    if S['buf'] and now - S['buf_t'] >= PAUSE.get(CFG['pause'], 1.3):
                        _flush()
                    _tick()
                    if b:
                        S['phase'] = 'speaking'
                    elif any(j['mode'] == 'stream' and j['st'] != 'prête' for j in J):
                        S['phase'] = 'thinking'
                    else:
                        S['phase'] = 'listening'
                    # la conversation reste ouverte tant qu'un travail tourne ou qu'on parle
                    if not J and not b and not S['buf'] and now - S['last'] > IDLE_S:
                        S.update(active=False, phase='idle')
                    elif J:
                        S['last'] = max(S['last'], now - 5)
        except Exception as e:
            S['err'] = repr(e)[:80]
        time.sleep(0.15)
