# Popup flottant de Julia : petit visage affiche quand l'appli n'est plus au premier plan
import os, time, traceback

DATA = os.environ.get('ANDROID_PRIVATE') or os.path.expanduser('~')
STATE = {'s': 'init'}

HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<body style="margin:0;background:transparent;overflow:hidden">
<div style="width:100vw;height:100vh;background:#000;border-radius:22px;border:1px solid #3a2f12;box-sizing:border-box">
<svg viewBox="0 0 360 220" style="width:100%;height:100%">
<g id=l><rect x="56" y="36" width="84" height="104" rx="30" fill="#e0a93b" opacity=".13"/><rect x="64" y="44" width="68" height="88" rx="22" fill="#e0a93b"/></g>
<g id=r><rect x="220" y="36" width="84" height="104" rx="30" fill="#e0a93b" opacity=".13"/><rect x="228" y="44" width="68" height="88" rx="22" fill="#e0a93b"/></g>
<path d="M154 173Q180 182 206 173Q180 183 154 173Z" fill="none" stroke="#e0a93b" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
</svg></div>
<script>
var e=[document.getElementById('l'),document.getElementById('r')];
e.forEach(function(g){g.style.transformBox='fill-box';g.style.transformOrigin='center';g.style.transition='transform .08s';g.style.transform='scaleY(.94)'});
function b(){e.forEach(function(g){g.style.transform='scaleY(.08)'});
setTimeout(function(){e.forEach(function(g){g.style.transform='scaleY(.94)'})},130);
setTimeout(b,2500+Math.random()*4500)}
setTimeout(b,3000);
</script>"""

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
        STATE['s'] = 'erreur init: ' + traceback.format_exc().strip().splitlines()[-1][:90]
        err('overlay: ' + traceback.format_exc())

def _run():
    from jnius import autoclass, cast, PythonJavaClass, java_method
    ctx = autoclass('org.kivy.android.PythonService').mService
    Settings = autoclass('android.provider.Settings')
    Handler = autoclass('android.os.Handler')
    Looper = autoclass('android.os.Looper')
    WebView = autoclass('android.webkit.WebView')
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
                STATE['s'] = 'erreur ui: ' + traceback.format_exc().strip().splitlines()[-1][:90]
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
        w, h = int(150 * dens), int(92 * dens)
        lp = LP(w, h, 2038, 8 | 32 | 512, -3)
        lp.gravity = 51
        lp.x = int(d.widthPixels - w - 12 * dens)
        lp.y = int(140 * dens)
        v = WebView(ctx)
        v.setBackgroundColor(0)
        v.getSettings().setJavaScriptEnabled(True)
        v.setOnTouchListener(touch)
        v.loadDataWithBaseURL(None, HTML, 'text/html', 'utf-8', None)
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
            STATE['s'] = 'erreur boucle: ' + traceback.format_exc().strip().splitlines()[-1][:90]
            err('overlay boucle: ' + traceback.format_exc())
        time.sleep(2)
