"""Runner in-process para yt-dlp en Android (Chaquopy).

En Android no existe binario `yt-dlp` ni es util lanzar subprocess, asi que se
ejecuta la propia libreria en el proceso: [python, -m, yt_dlp, args...] se
convierten en yt_dlp.main(args).

Correctitud:
- Un solo yt-dlp a la vez (no es reentrante), pero con TIMEOUT efectivo: si se
  cuelga (red movil), el lock se libera y la API deja de bloquearse.
- La salida se captura igual que un CompletedProcess.
- v1.3.0: `cancel=` (threading.Event) aborta la descarga en curso. Se usa la API
  de yt-dlp (parse_options + YoutubeDL) con un progress hook que lanza
  DownloadCancelled; sin `cancel` se sigue usando yt_dlp.main() tal cual.
"""
import contextlib
import io
import logging
import os
import threading
import time

logger = logging.getLogger("ytdlp_inproc")

_LOCK = threading.Lock()  # yt-dlp no es reentrante: serializamos
DEFAULT_TIMEOUT = 300     # segundos; sin timeout, un cuelgue bloquea toda la API


class _Result:
    def __init__(self, returncode, stdout, stderr, cancelled=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cancelled = cancelled


def _strip_prefix(args):
    a = [args] if isinstance(args, str) else list(args)
    if len(a) >= 3 and a[1] == "-m":
        return [str(x) for x in a[3:]]
    if a and os.path.basename(str(a[0])) in ("yt-dlp", "yt_dlp", "yt_dlp.py", "python", "python3"):
        return [str(x) for x in a[1:]]
    return [str(x) for x in a]


def _api_download(argv, cancel, err, holder, pace=None):
    """Descarga con la API para poder ABORTARLA (progress hook -> DownloadCancelled)
    y/o PAUSARLA al ritmo de reproduccion (`pace`).

    Devuelve el returncode como yt_dlp.main()."""
    import yt_dlp
    from yt_dlp import parse_options, YoutubeDL
    from yt_dlp.utils import DownloadCancelled, YoutubeDLError

    _parser, _opts, urls, ydl_opts = parse_options(argv)
    hooks = list(ydl_opts.get("progress_hooks") or [])

    def _ph(_status):
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("cancelado por el usuario")
        if pace is not None:
            inf = _status.get("info_dict") or {}
            # v1.3.0: bloquea aqui (pausa real de la descarga) si ya hay mas audio
            # del que hace falta para la posicion actual de reproduccion
            pace(_status.get("downloaded_bytes") or 0,
                 _status.get("total_bytes") or _status.get("total_bytes_estimate") or 0,
                 inf.get("duration") or 0)

    hooks.append(_ph)
    ydl_opts["progress_hooks"] = hooks
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ret = ydl.download(urls)
    except DownloadCancelled:
        holder["cancelled"] = True
        err.write("[download] cancelado por el usuario\n")
        return 1
    except YoutubeDLError as e:
        err.write("ERROR: %s\n" % e)
        return 1
    return int(ret) if ret else 0


def run_command(args, timeout=None, check=False, capture_output=True, stderr=None, text=False, cancel=None, pace=None):
    import yt_dlp

    argv = _strip_prefix(args)
    tmo = timeout or DEFAULT_TIMEOUT
    out, err = io.StringIO(), io.StringIO()
    holder = {"code": None, "cancelled": False}
    released = {"v": False}

    def _release():
        if not released["v"]:
            released["v"] = True
            try:
                _LOCK.release()
            except RuntimeError:
                pass

    def _job():
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                if cancel is None and pace is None:
                    r = yt_dlp.main(argv)
                    holder["code"] = r if isinstance(r, int) else 0
                else:
                    holder["code"] = _api_download(argv, cancel, err, holder, pace=pace)
        except SystemExit as e:  # yt-dlp sale con sys.exit
            holder["code"] = e.code if isinstance(e.code, int) else 0
        except BaseException as e:  # nunca tumbar el server por un fallo de yt-dlp
            err.write("ERROR: %s\n" % e)
            holder["code"] = 1
        finally:
            _release()

    # Esperar el lock: si una descarga esta en curso, la reproduccion no debe
    # fallar al instante; se espera hasta PLAYME_LOCK_WAIT (60s por defecto).
    # Con `cancel` la espera se corta en cuanto el usuario cambia de tema.
    wait = int(os.environ.get("PLAYME_LOCK_WAIT", "60") or 60)
    deadline = time.time() + max(tmo, wait)
    got = False
    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            err.write("ERROR: cancelado antes de ejecutar\n")
            return _Result(1, b"", err.getvalue().encode(), cancelled=True)
        if _LOCK.acquire(timeout=0.25):
            got = True
            break
    if not got:
        err.write("ERROR: yt-dlp ocupado mas de %ss\n" % max(tmo, wait))
        return _Result(124, b"", err.getvalue().encode())

    t = threading.Thread(target=_job, daemon=True)
    t.start()
    # join por tramos: si el hilo aborta por cancel, se sale antes
    end = time.time() + tmo
    while t.is_alive() and time.time() < end:
        t.join(0.25)
    if t.is_alive():
        # Se colgo. NO liberamos el lock: el hilo sigue dentro de yt-dlp y
        # liberar aqui permitiria comandos en paralelo sobre la misma libreria
        # (se corrompe y todo queda en "resolving"). El _job libera al morir;
        # mientras, los siguientes comandos fallan rapido con "ocupado".
        err.write("ERROR: timeout de yt-dlp (%ss); comando colgado\n" % tmo)
        holder["code"] = 124

    so, se = out.getvalue(), err.getvalue()
    code = holder["code"] if holder["code"] is not None else 0
    if not text:
        so, se = so.encode("utf-8", "replace"), se.encode("utf-8", "replace")
    if stderr is not None and se:
        try:
            stderr.write(se.decode("utf-8", "replace") if not text else se)
        except Exception:
            pass
    res = _Result(code, so, se, cancelled=bool(holder["cancelled"]))
    if code:
        _e = se.decode("utf-8", "replace") if isinstance(se, bytes) else str(se)
        logger.warning("inproc rc=%s args=%s | %s" % (code, " ".join([str(a) for a in argv[:8]]), _e.strip()[-300:]))
    if check and code != 0:
        raise RuntimeError("yt-dlp rc=%s: %s" % (code, (se[:200] if isinstance(se, bytes) else se[:200])))
    return res
