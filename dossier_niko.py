"""Dossier Niko : tout fichier déposé dans /sdcard/Niko est pris par Niko.

Tout reste dans le téléphone. Les fichiers traités sont DÉPLACÉS (pas copiés)
dans Niko/traites, donc pas de stockage doublé.

Utilisation :
  - service.py : dossier_niko.start(on_text=..., on_media=..., log=...)
  - main.py (appli, pas le service) : si pas d'accès -> dossier_niko.ask_access()
"""
import os
import shutil
import threading
import time

ROOT = "/storage/emulated/0/Niko"
DONE = os.path.join(ROOT, "traites")
REVIEW = os.path.join(ROOT, "a_revoir")

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log"}
MEDIA_EXT = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mkv", ".mov",
             ".mp3", ".m4a", ".wav"}


def has_access():
    """True si Niko a l'accès à tous les fichiers (Android 11+)."""
    try:
        from jnius import autoclass
        env = autoclass("android.os.Environment")
        return bool(env.isExternalStorageManager())
    except Exception:
        return False


def ask_access():
    """Ouvre l'écran Android pour autoriser l'accès aux fichiers.
    À appeler depuis l'appli (main.py), pas depuis le service."""
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        intent = autoclass("android.content.Intent")(
            autoclass("android.provider.Settings")
            .ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
        intent.setData(autoclass("android.net.Uri")
                       .parse("package:" + activity.getPackageName()))
        activity.startActivity(intent)
        return True
    except Exception:
        return False


def prepare():
    for d in (ROOT, DONE, REVIEW):
        os.makedirs(d, exist_ok=True)


def _stable(path, wait=2.0):
    """Fichier fini d'être copié ? (taille inchangée et non vide)"""
    try:
        a = os.path.getsize(path)
        time.sleep(wait)
        b = os.path.getsize(path)
        return a == b and a > 0
    except OSError:
        return False


def _move(path, dest_dir):
    base = os.path.basename(path)
    dst = os.path.join(dest_dir, base)
    if os.path.exists(dst):
        nom, ext = os.path.splitext(base)
        dst = os.path.join(dest_dir, "%s_%d%s" % (nom, int(time.time()), ext))
    shutil.move(path, dst)
    return dst


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read(2000000)  # 2 Mo max
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _read_pdf(path):
    """Texte d'un PDF si pypdf est dans le build, sinon None."""
    try:
        from pypdf import PdfReader
    except Exception:
        return None
    try:
        pages = [(p.extract_text() or "") for p in PdfReader(path).pages]
        return "\n".join(pages).strip() or None
    except Exception:
        return None


def scan_once(on_text, on_media=None, log=print):
    """Traite les fichiers présents. on_text(nom, texte) ; on_media(nom, chemin)."""
    for name in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        if not _stable(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        try:
            if ext in TEXT_EXT or ext == ".pdf":
                text = _read_text(path) if ext in TEXT_EXT else _read_pdf(path)
                if not text:
                    _move(path, REVIEW)
                    log("Niko: illisible -> a_revoir : " + name)
                    continue
                on_text(name, text)
                _move(path, DONE)
                log("Niko: appris " + name)
            elif ext in MEDIA_EXT:
                new = _move(path, DONE)
                if on_media:
                    on_media(name, new)
                log("Niko: rangé (media) " + name)
            else:
                _move(path, REVIEW)
                log("Niko: type non pris en charge -> a_revoir : " + name)
        except Exception as e:
            log("Niko: erreur sur %s : %s" % (name, e))


def start(on_text, on_media=None, log=print, every=10):
    """Lance la surveillance en fond (un passage toutes les `every` secondes)."""
    def run():
        while True:
            try:
                if has_access():
                    prepare()
                    scan_once(on_text, on_media, log)
            except Exception as e:
                log("Niko dossier: " + str(e))
            time.sleep(every)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
