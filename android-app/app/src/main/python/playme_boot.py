"""Arranque de PlayMe dentro de Android (Chaquopy).

La app llama a start(filesDir): configura rutas escribibles del dispositivo,
arranca el servidor HTTP del backend en 127.0.0.1 y espera a que escuche.
yt-dlp se ejecuta como modulo de Python (no hay binario en Android).
"""
import os
import socket
import sys
import threading
import time

_started = False
_lock = threading.Lock()


def start(data_dir, cache_dir=None):
    """Arranca el backend local. Devuelve True si el puerto quedo escuchando.

    data_dir: datos de la app (cookies, logs, mp3).
    cache_dir: cache de Android (getCacheDir()) para el audio; si no se pasa,
    se usa data_dir/cache (compatibilidad).
    """
    global _started
    with _lock:
        if _started:
            return True

        cache = cache_dir or os.path.join(data_dir, "cache")
        logs = os.path.join(data_dir, "logs")
        mp3 = os.path.join(data_dir, "mp3")
        for d in (data_dir, cache, logs, mp3):
            os.makedirs(d, exist_ok=True)

        # tempfile debe apuntar a un directorio escribible ANTES de importar el backend
        os.environ["TMPDIR"] = cache
        import tempfile
        tempfile.tempdir = cache

        os.environ.setdefault("PORT", "8191")
        os.environ["PLAYME_HOST"] = "127.0.0.1"
        os.environ["PLAYME_COOKIES_FILE"] = os.path.join(data_dir, "cookies.txt")
        os.environ["PLAYME_COOKIES_BACKUP"] = os.path.join(data_dir, "cookies_master.txt")
        # Android no tiene /tmp escribible: ruta temp explicita para la copia que usa yt-dlp
        os.environ["PLAYME_COOKIES_TEMP"] = os.path.join(cache, "playme_cookies.txt")
        os.environ["PLAYME_MP3_DIR"] = mp3
        os.environ["PLAYME_CACHE_DIR"] = cache
        os.environ["PLAYME_LOG_DIR"] = logs
        os.environ["PLAYME_STATIC"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        os.environ["PLAYME_YTDLP_INPROC"] = "1"
        os.environ["PLAYME_NO_FIREFOX"] = "1"
        os.environ["PLAYME_COOKIE_FALLBACK"] = "1"
        os.environ["PLAYME_NO_BG_DOWNLOAD"] = "1"
        os.environ["PLAYME_PREFER_FILE"] = "1"
        os.environ["PLAYME_NO_CONVERT"] = "1"

        # Android: si el resolver nativo de Python falla (habitual en algunos moviles),
        # se cae a java.net.InetAddress.
        try:
            import dns_java

            dns_java.install()
        except Exception as e:
            print("boot: dns_java no aplicado: %s" % e)

        import server  # importa el backend (resolver/player/transcoder/token_manager)

        # Diagnostico de rutas (visible en logcat con tag PlayMeBoot)
        try:
            try:
                from java import jclass
                _Log = jclass("android.util.Log")

                def logi(m):
                    _Log.i("PlayMeBoot", str(m))
            except Exception:
                def logi(m):
                    print("PlayMeBoot:", m)

            import tempfile as _tf
            import resolver as _res
            logi("TMPDIR=%s tempdir=%s" % (os.environ.get("TMPDIR"), _tf.gettempdir()))
            logi("LIVE=%s exists=%s" % (_res.COOKIES_LIVE, os.path.isfile(_res.COOKIES_LIVE)))
            logi("TEMP=%s exists=%s" % (_res.COOKIES_TEMP, os.path.isfile(_res.COOKIES_TEMP)))
            logi("BACKUP=%s" % _res.COOKIES_BACKUP)
        except Exception as e:
            print("PlayMeBoot: diag error %s" % e)
        threading.Thread(target=server.main, daemon=True, name="playme-server").start()

        port = int(os.environ.get("PORT", "8191"))
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    _started = True
                    return True
            except OSError:
                time.sleep(0.3)
        return False


def ytdlp_version():
    """Version de yt-dlp embebido (para diagnostico)."""
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception as e:
        return "error: %s" % e


def python_version():
    return sys.version.split()[0]
