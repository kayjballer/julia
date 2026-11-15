s = open('service.py', encoding='utf-8').read()

def rep(old, new):
    global s
    assert s.count(old) == 1, 'ancre introuvable : ' + old[:60]
    s = s.replace(old, new)

# 1. correction du nom quand Niko est deja reveille
rep("""            called, cmd = split_wake(text)
            cmd = cmd if called else text
""", """            text = router.fix_name(text)
            called, cmd = split_wake(text)
            w0 = text.split()[0].lower().strip(',.!?') if text.split() else ''
            if called and not cmd and w0 in ('bonjour', 'salut', 'coucou', 'hello', 'bonsoir', 'hey'):
                cmd = w0
            cmd = cmd if called else text
""")

# 2. reglage de la voix
rep("""def tts_stop():""", """def voice_set(p, v):
    try:
        TTS['eng'].setPitch(float(p))
        TTS['eng'].setSpeechRate(float(v))
    except Exception:
        pass


def tts_stop():""")

rep("""    Ears.setMuted(False)
    S['active_until'] = time.time() + ACTIVE_WINDOW""", """    Ears.setMuted(False)
    voice_set(1.0, 1.05)
    S['active_until'] = time.time() + ACTIVE_WINDOW""")

# 3. amorce vocale pendant que Gemma reflechit
rep("""        log('role: ' + router.role(cmd))
        _, spoken = ask(cmd)
""", """        kind = router.qtype(cmd)
        p, v, amo = router.amorce(kind)
        log('role: %s | voix %.2f x%.2f' % (kind, p, v))
        voice_set(p, v)
        if amo:
            Ears.setMuted(True)
            push_state(phase='parle', force=True)
            say(amo)
        _, spoken = ask(cmd)
        spoken = spoken or bool(amo)
""")
open('service.py', 'w', encoding='utf-8').write(s)
print('ok')
