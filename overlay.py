# Popup flottant de Julia : petit visage affiche quand l'appli n'est plus au premier plan
import os, time, traceback, threading

DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
STATE = {'s': 'init'}
import sys

def _why():
    e = sys.exc_info()
    t, ln = e[2], 0
    while t is not None:
        if t.tb_frame.f_code.co_filename.endswith('overlay.py'):
            ln = t.tb_lineno
        t = t.tb_next
    return 'L%d %s' % (ln, str(e[1])[:110])


HTML_T = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<body style="margin:0;background:transparent;overflow:hidden">
<div id=f style="width:100vw;height:100vh;background:#000;border-radius:12px;border:1px solid #3a2f12;box-sizing:border-box"></div>
<script>%JS%</script>
<script>var F=JuliaFace.mount(document.getElementById('f'));F.setHue(%HUE%);
var _s='idle';function pop(s){if(s===_s)return;_s=s;if(s==='listening'){F.setExpression('surprised',120);setTimeout(function(){F.clearExpression()},650)}F.setState(s)}</script>"""

POP_W = 56  # largeur du popup en dp (la hauteur suit)

def _html():
    js = ''
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        js = open(os.path.join(base, 'web', 'julia_visage.js'), encoding='utf-8').read()
    except Exception:
        STATE['s'] = 'julia_visage.js absent'
    return HTML_T.replace('%JS%', js).replace('%HUE%', '265')


def _signal(mem):
    """État de Julia vu depuis le service : idle | listening | thinking | speaking.
    Lit /api/gen de l'appli (réflexion, réponse) et le dernier réveil des oreilles."""
    now = time.time()
    try:
        import json, urllib.request
        c = json.loads(urllib.request.urlopen('http://127.0.0.1:5000/api/conv', timeout=0.8).read())
        if c.get('active'):
            return {'thinking': 'thinking', 'speaking': 'speaking'}.get(c.get('phase'), 'listening')
    except Exception:
        pass
    try:
        import json, urllib.request
        g = json.loads(urllib.request.urlopen('http://127.0.0.1:5000/api/gen', timeout=0.8).read())
        gid = g.get('id') or 0
        if gid and not g.get('done'):
            mem['gid'], mem['until'] = gid, 0
            return 'speaking' if (g.get('text') or '').strip() else 'thinking'
        if gid and gid == mem.get('gid') and not mem.get('until'):
            mem['until'] = now + max(2.5, len(g.get('text') or '') * 0.07)   # même durée que l'interface
        if mem.get('until', 0) > now:
            return 'speaking'
    except Exception:
        pass
    try:
        import ears
        if now - ears.STATE.get('last_wake', 0) < 8:
            return 'listening'
    except Exception:
        pass
    return 'idle'


def err(txt):
    try:
        with open(os.path.join(DATA, 'service_err.txt'), 'w') as f:
            f.write(txt[-300:])
    except Exception:
        pass

def run():
    try:
        _run()
    except Exception:
        STATE['s'] = 'erreur init: ' + _why()
        err('overlay: ' + traceback.format_exc())

def _run():
    from jnius import autoclass, cast, PythonJavaClass, java_method
    ctx = autoclass('org.kivy.android.PythonService').mService
    Settings = autoclass('android.provider.Settings')
    Handler = autoclass('android.os.Handler')
    Looper = autoclass('android.os.Looper')
    WebView = autoclass('android.webkit.WebView')
    try:
        WebView.setDataDirectorySuffix('julia_svc')
    except Exception:
        pass
    LP = autoclass('android.view.WindowManager$LayoutParams')
    pkg = str(ctx.getPackageName())
    wm = ctx.getSystemService('window')
    am = ctx.getSystemService('activity')
    main = Handler(Looper.getMainLooper())
    keep = []
    st = {'wv': None, 'lp': None, 'x0': 0, 'y0': 0, 'rx': 0.0, 'ry': 0.0, 'moved': False}

    class Run(PythonJavaClass):
        __javainterfaces__ = ['java/lang/Runnable']
        __javacontext__ = 'app'
        def __init__(self, fn):
            super().__init__()
            self.fn = fn
        @java_method('()V')
        def run(self):
            try:
                self.fn()
            except Exception:
                STATE['s'] = 'erreur ui: ' + _why()
                err('overlay ui: ' + traceback.format_exc())

    def ui(fn):
        r = Run(fn)
        keep.append(r)
        if len(keep) > 30:
            del keep[:10]
        main.post(r)

    def open_app():
        i = ctx.getPackageManager().getLaunchIntentForPackage(pkg)
        i.addFlags(0x10000000)
        ctx.startActivity(i)

    class Touch(PythonJavaClass):
        __javainterfaces__ = ['android/view/View$OnTouchListener']
        __javacontext__ = 'app'
        @java_method('(Landroid/view/View;Landroid/view/MotionEvent;)Z')
        def onTouch(self, v, e):
            try:
                a = e.getAction()
                lp = st['lp']
                if a == 0:
                    st.update(x0=lp.x, y0=lp.y, rx=e.getRawX(), ry=e.getRawY(), moved=False)
                elif a == 2:
                    dx = e.getRawX() - st['rx']
                    dy = e.getRawY() - st['ry']
                    if abs(dx) > 10 or abs(dy) > 10:
                        st['moved'] = True
                    if st['moved']:
                        lp.x = int(st['x0'] + dx)
                        lp.y = int(st['y0'] + dy)
                        wm.updateViewLayout(v, lp)
                elif a == 1 and not st['moved']:
                    open_app()
            except Exception:
                err('overlay touch: ' + traceback.format_exc())
            return True

    touch = Touch()

    def show():
        if st['wv'] is not None:
            return
        d = ctx.getResources().getDisplayMetrics()
        dens = d.density
        w, h = int(POP_W * dens), int(POP_W * 0.61 * dens)
        lp = LP(w, h, 2038, 8 | 32 | 512, -3)
        lp.gravity = 51
        lp.x = int(d.widthPixels - w - 12 * dens)
        lp.y = int(140 * dens)
        v = WebView(ctx)
        v.setBackgroundColor(0)
        v.getSettings().setJavaScriptEnabled(True)
        v.setOnTouchListener(touch)
        v.loadDataWithBaseURL(None, _html(), 'text/html', 'utf-8', None)
        wm.addView(v, lp)
        st['wv'], st['lp'] = v, lp

    def hide():
        v = st['wv']
        if v is None:
            return
        st['wv'] = None
        try:
            wm.removeView(v)
        except Exception:
            pass
        try:
            v.destroy()
        except Exception:
            pass

    def app_fg():
        lst = am.getRunningAppProcesses()
        if lst is None:
            return False
        for i in range(lst.size()):
            p = cast('android.app.ActivityManager$RunningAppProcessInfo', lst.get(i))
            if str(p.processName) == pkg:
                return int(p.importance) <= 100
        return False

    def sync():
        mem, last = {}, None
        while True:
            try:
                v = st['wv']
                if v is not None:
                    sig = _signal(mem)
                    if sig != last or v is not st.get('sent'):
                        last, st['sent'] = sig, v
                        ui(lambda sig=sig, v=v: v.evaluateJavascript("pop('%s')" % sig, None))
            except Exception:
                err('overlay sync: ' + traceback.format_exc())
            time.sleep(0.6)
    threading.Thread(target=sync, daemon=True).start()

    while True:
        try:
            perm = bool(Settings.canDrawOverlays(ctx))
            fg = app_fg()
            want = perm and not fg
            if want and st['wv'] is None:
                STATE['s'] = 'creation'
                ui(show)
            elif (not want) and st['wv'] is not None:
                ui(hide)
            if not perm:
                STATE['s'] = 'permission manquante'
            elif fg:
                STATE['s'] = 'masque (appli visible)'
            elif st['wv'] is not None:
                STATE['s'] = 'affiche'
        except Exception:
            STATE['s'] = 'erreur boucle: ' + _why()
            err('overlay boucle: ' + traceback.format_exc())
        time.sleep(2)
