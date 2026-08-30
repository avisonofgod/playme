"""
runner.py - Abstraccion de ejecucion de comandos (subprocess) inyectable.

Separa la invocacion real de subprocess de la logica de yt-dlp/ffmpeg para que
Resolver/Transcoder sean testables sin red ni YouTube (mock via fake runner).

Kill de grupo en timeout:
- Utiliza Popen con start_new_session=True para que el hijo quede en su propio
  grupo de procesos.
- En TimeoutExpired, os.killpg mata TODA la sesion (hijo + nietos, p.ej. el
  ffmpeg que yt-dlp lanza para remux), evitando procesos huerfanos.
- Mantiene la misma firma de llamada que los usos actuales del proyecto.
"""
import os
import subprocess
import signal


def run_command(args, timeout=None, check=False, capture_output=True, stderr=None, text=False):
    """Ejecuta un comando con kill de grupo en timeout.

    - Lanza subprocess.TimeoutExpired si agota `timeout`.
    - Lanza CalledProcessError al final si `check` y returncode != 0.
    - Devuelve un objeto con .returncode/.stdout/.stderr (compat con subprocess.run).
    """
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else (stderr or None),
        start_new_session=True,  # grupo propio -> killpg mata hijo Y nietos
    )
    try:
        stdout, stderr_b = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise
    except Exception:
        _kill_group(proc)
        raise
    if text:
        if stdout is not None:
            stdout = stdout.decode(errors="replace")
        if stderr_b is not None:
            stderr_b = stderr_b.decode(errors="replace")
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, args, output=stdout, stderr=stderr_b)
    return _Result(proc.returncode, stdout, stderr_b)


def _kill_group(proc):
    """Mata el grupo de procesos del hijo (session) para eliminar tambien los
    nietos (ffmpeg de remux de yt-dlp) que quedarian huerfanos."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        # el proceso ya termino o no podemos matar el grupo
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


class _Result:
    """Objeto con la misma interfaz minima que CompletedProcess."""

    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
