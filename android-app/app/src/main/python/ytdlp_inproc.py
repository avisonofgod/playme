"""Runner in-process para yt-dlp en Android (Chaquopy).

En Android no existe binario `yt-dlp` ni es util lanzar subprocess para ello,
asi que se ejecuta la propia libreria en el proceso: [python, -m, yt_dlp, ...]
se convierte en yt_dlp.main([...]) con la salida capturada.

Interfaz identica a runner.run_command (returncode/stdout/stderr).
"""
import contextlib
import io
import os
import threading

_LOCK = threading.Lock()  # yt-dlp no es reentrante: serializamos


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
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with _LOCK:
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                r = yt_dlp.main(argv)
                code = r if isinstance(r, int) else 0
        except SystemExit as e:  # yt-dlp sale con sys.exit
            code = e.code if isinstance(e.code, int) else 0
        except BaseException as e:  # nunca tumbar el server por un fallo de yt-dlp
            err.write("ERROR: %s\n" % e)
            code = 1

    so, se = out.getvalue(), err.getvalue()
    if not text:
        so = so.encode("utf-8", "replace")
        se = se.encode("utf-8", "replace")
    res = _Result(code, so, se)
    if check and code != 0:
        tail = se if isinstance(se, str) else se.decode("utf-8", "replace")
        raise RuntimeError("yt-dlp rc=%s: %s" % (code, tail[-300:]))
    return res
