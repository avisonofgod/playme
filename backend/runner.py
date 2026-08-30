"""
runner.py - Abstraccion de ejecucion de comandos (subprocess) inyectable.

Separa la invocacion real de subprocess de la logica de yt-dlp/ffmpeg para que
Resolver/Transcoder sean testables sin red ni YouTube (mock via fake runner).
"""
import subprocess


def run_command(args, timeout=None, check=False, capture_output=True, stderr=None, text=False):
    """Wrapper estandar sobre subprocess.run manteniendo la firma de los usos
    actuales del proyecto. Si `check` es True, lanza CalledProcessError cuando
    returncode != 0 (equivalente a check_output)."""
    kwargs = {"capture_output": capture_output, "text": text}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if stderr is not None:
        kwargs["stderr"] = stderr
    result = subprocess.run(args, **kwargs)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result
