"""Runner in-process para yt-dlp en Android (Chaquopy).

En Android no existe binario `yt-dlp` ni es util lanzar subprocess, asi que se
ejecuta la propia libreria en el proceso: [python, -m, yt_dlp, args...] se
convierten en yt_dlp.main(args).

Correctitud:
- Un solo yt-dlp a la vez (no es reentrante), pero con TIMEOUT efectivo: si se
  cuelga (red movil), el lock se libera y la API deja de bloquearse.
- La salida se captura igual que un CompletedProcess.
"""
import contextlib
import io
import os
import threading

_LOCK = threading.Lock()  # yt-dlp no es reentrante: serializamos
DEFAULT_TIMEOUT = 300     # segundos; sin timeout, un cuelgue bloquea toda la API


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _strip_prefix(args):
    a = [args] if isinstance(args, str) else list(args)
    if len(a) >= 3 and a[1] == "-m":
        return [str(x) for x in a[3:]]
    if a and os.path.basename(str(a[0])) in ("yt-dlp", "yt_dlp", "yt_dlp.py", "python", "python3"):
        return [str(x) for x in a[1:]]
    return [str(x) for x in a]


def run_command(args, timeout=None, check=False, capture_output=True, stderr=None, text=False):
    import yt_dlp

    argv = _strip_prefix(args)
    tmo = timeout or DEFAULT_TIMEOUT
    out, err = io.StringIO(), io.StringIO()
    holder = {"code": None}
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
                r = yt_dlp.main(argv)
                holder["code"] = r if isinstance(r, int) else 0
        except SystemExit as e:  # yt-dlp sale con sys.exit
            holder["code"] = e.code if isinstance(e.code, int) else 0
        except BaseException as e:  # nunca tumbar el server por un fallo de yt-dlp
            err.write("ERROR: %s\n" % e)
            holder["code"] = 1
        finally:
            _release()

    if not _LOCK.acquire(timeout=tmo):
        err.write("ERROR: yt-dlp ocupado mas de %ss\n" % tmo)
        return _Result(124, b"", err.getvalue().encode())

    t = threading.Thread(target=_job, daemon=True)
    t.start()
    t.join(tmo)
    if t.is_alive():
        # Se colgo: liberamos el lock para no bloquear el resto de la API y
        # devolvemos timeout (el hilo queda como daemon hasta que muera).
        _release()
        err.write("ERROR: timeout de yt-dlp (%ss); comando abortado\n" % tmo)
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
    res = _Result(code, so, se)
    if check and code != 0:
        raise RuntimeError("yt-dlp rc=%s: %s" % (code, (se[:200] if isinstance(se, bytes) else se[:200])))
    return res
