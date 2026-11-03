# Conversation continue de Julia (tourne dans le processus de l'appli, à côté de main.py).
# Les oreilles (service, Vosk) envoient ce qu'elles entendent à /api/conv/ear ; ici on décide :
#   - réveil « dis Julia » -> la conversation s'ouvre, plus besoin de répéter le mot de réveil
#   - fin de phrase -> question au cerveau, réponse dite phrase par phrase pendant qu'elle s'écrit
#   - tu parles pendant qu'elle parle -> elle se tait tout de suite et t'écoute (coupure de parole)
#   - son propre écho (sa voix captée par le micro) est reconnu et ignoré
#   - silence prolongé ou « au revoir » -> retour à l'attente du mot de réveil
import re, time, threading, unicodedata, collections

CFG = {'lang': 'FR', 'mem': 1, 'web': 1, 'rate': 1.0, 'pitch': 1.0, 'vname': '', 'tz': 0, 'hf': 1}
S = {'active': False, 'phase': 'idle', 'user': '', 'reply': '', 'last': 0.0, 'wakes': 0, 'gid': 0,
     'ears': 0.0, 'say_t': 0.0, 'tts_end': 0.0, 'was_busy': False, 'cuts': 0, 'err': ''}
H = {}
L = threading.RLock()
SPOKEN = collections.deque(maxlen=40)   # (heure, mots) des phrases dites : sert à reconnaitre l'écho
IDLE_S = 30.0

WAKE = re.compile(r"^\s*(?:(?:dis|salut|ok|okay|hey|hé|eh|oh|bonjour)\s+)?(?:julia|julie|giulia|jolie|joulia)\b[\s,.!?]*", re.I)
STOP = re.compile(r"^(?:ok |bon |oui )?(?:julia )?(?:stop|arrete|arrete toi|tais toi|chut|attends|attend|ca suffit|silence|pause|laisse tomber)(?: julia)?$")
BYE = re.compile(r"\b(au revoir|a plus tard|a tout a l heure|c est tout|on s arrete|fin de la conversation|bonne nuit|merci julia)\b")
CUT_AT = re.compile(r"(?<!\d)[.!?…]+(?=\s)|\n")


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
    for k in ('mem', 'web', 'hf'):
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
    return {'active': S['active'], 'phase': S['phase'], 'user': S['user'], 'reply': S['reply'],
            'wakes': S['wakes'], 'cuts': S['cuts'], 'hf': CFG['hf'], 'err': S['err'],
            'ears_age': round(now - S['ears'], 1) if S['ears'] else None}


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
    SPOKEN.append((S['say_t'], set(norm(text))))
    H['say'](text, CFG['pitch'], CFG['rate'], CFG['lang'], CFG['vname'])


def _is_echo(text):
    ws = norm(text)
    if not ws:
        return True
    now = time.time()
    vocab = set()
    for t, w in SPOKEN:
        if now - t < 25:
            vocab |= w
    if not vocab:
        return False
    return sum(1 for w in ws if w in vocab) / float(len(ws)) >= 0.7


def _cut(cancel_gen=False):
    # Julia se tait tout de suite
    S['gid'] = -1
    try:
        H['stop']()
    except Exception:
        pass
    if cancel_gen:
        try:
            H['gen']['id'] += 1
        except Exception:
            pass
    S['cuts'] += 1


# ---------- événements des oreilles ----------
def ear(kind, text, wake=False):
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
        S['last'] = now
        tail = _tail()
        ws = ' '.join(norm(text))
        if kind == 'P':
            if tail and not STOP.match(ws) and _is_echo(text):
                return state()
            if S['phase'] in ('speaking',) or (tail and S['phase'] != 'listening'):
                _cut()
                S['phase'] = 'listening'
            if S['phase'] in ('listening', 'thinking'):
                S['user'] = text
        elif kind == 'R':
            if tail and not STOP.match(ws) and _is_echo(text):
                return state()
            _request(text)
    return state()


def _start(text=''):
    S.update(active=True, phase='listening', user='', reply='', last=time.time(), err='')
    S['wakes'] += 1
    rest = WAKE.sub('', text).strip() if text else ''
    if len(norm(rest)) >= 2:
        _request(rest)


def start():
    with L:
        S['ears'] = S['ears'] or 0.0
        _start('')
    return state()


def end(say_bye=False):
    with L:
        S.update(active=False, phase='idle')
        _cut(cancel_gen=True)
        if say_bye:
            _say("À tout à l'heure.")
    return state()


def cut():
    # appui sur l'écran pendant qu'elle parle : elle se tait, la conversation reste ouverte
    with L:
        _cut(cancel_gen=True)
        S.update(phase='listening', last=time.time())
    return state()


def text(t):
    # message écrit pendant une conversation : même chemin que la voix (la réponse est dite aussi)
    with L:
        if not S['active']:
            _start('')
        _request(t)
    return state()


def _tz():
    return CFG['tz']


def _request(text):
    text = WAKE.sub('', text).strip()
    ws = ' '.join(norm(text))
    if not ws:
        S['phase'] = 'listening'
        return
    if STOP.match(ws):
        _cut(cancel_gen=True)
        S.update(phase='listening', user=text)
        return
    if BYE.search(ws) and len(ws.split()) <= 6:
        S.update(user=text, reply='')
        end(say_bye=True)
        return
    _cut()
    S.update(phase='thinking', user=text, reply='', last=time.time())
    try:
        r = H['start_ask']({'text': text, 'lang': CFG['lang'], 'voice': 0, 'mem': CFG['mem'], 'web': CFG['web'],
                            'model': 'general', 'ts': int(time.time() * 1000), 'tz': _tz()})
    except Exception as e:
        S.update(phase='listening', err=repr(e)[:80])
        return
    gid = r['id']
    S['gid'] = gid
    threading.Thread(target=_speaker, args=(gid,), daemon=True).start()


# ---------- réponse dite pendant qu'elle s'écrit ----------
def _speakable(t):
    return re.sub(r"```[\s\S]*?(```|$)", " (le code est à l'écran) ", t)


def _speaker(gid):
    spoken, filler, t0 = 0, False, time.time()
    try:
        while True:
            if S['gid'] != gid or not S['active']:
                return
            g = H['gen_state']()
            if g.get('id') != gid:
                return
            text, done = g.get('text') or '', bool(g.get('done'))
            S['reply'] = text
            sp = _speakable(text)
            while True:
                rest = sp[spoken:]
                m = CUT_AT.search(rest)
                if m and not done:
                    seg, adv = rest[:m.end()], m.end()
                elif done and rest.strip():
                    seg, adv = rest, len(rest)
                else:
                    break
                spoken += adv
                if len(seg.strip()) > 2:
                    S['phase'] = 'speaking'
                    _say(seg.strip())
                if done:
                    break
            if (not text) and g.get('stage') == 'search' and not filler and time.time() - t0 > 1.5:
                filler = True
                S['phase'] = 'speaking'
                _say('Une seconde, je regarde ça.')
            if done and spoken >= len(sp):
                break
            time.sleep(0.15)
        while _busy() and S['gid'] == gid and S['active']:
            time.sleep(0.15)
        with L:
            if S['gid'] == gid and S['active']:
                S.update(phase='listening', last=time.time())
    except Exception as e:
        S['err'] = repr(e)[:80]
        with L:
            if S['gid'] == gid:
                S['phase'] = 'listening'


def _loop():
    while True:
        try:
            now = time.time()
            b = _busy()
            if S['was_busy'] and not b:
                S['tts_end'] = now
            S['was_busy'] = b
            with L:
                if S['active'] and S['phase'] == 'listening' and not b and now - S['last'] > IDLE_S:
                    S.update(active=False, phase='idle')
        except Exception:
            pass
        time.sleep(0.3)
