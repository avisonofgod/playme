#!/usr/bin/env python3
"""Verificacion real de que Firefox alimenta cookies.txt y yt-dlp resuelve video.

Usa una COPIA TEMPORAL de cookies en /tmp y NO toca cookies.txt del proyecto
(asi la verificacion no deja el archivo de produccion reescrito por yt-dlp).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_manager import TokenManager
from firefox_cookies import FirefoxCookiesExtractor

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIES = os.path.join(PROJECT, "cookies.txt")
PROFILE_DB = "/home/noroot/.mozilla/firefox/33ys3rhe.default-esr/cookies.sqlite"


def main():
    print("== 1) Extraccion desde Firefox (perfil noroot) ==")
    ex = FirefoxCookiesExtractor(PROFILE_DB)
    written = ex.write_file(COOKIES)  # regenera cookies.txt (es la fuente de prod)
    print("cookies escritas:", written)
    assert "SID" in written and "SAPISID" in written, "faltan cookies de sesion"

    with open(COOKIES) as f:
        txt = f.read()
    for needle in ("SID", "SAPISID", "__Secure-1PAPISID"):
        assert needle in txt, "cookies.txt no contiene %s" % needle
    mod = oct(os.stat(COOKIES).st_mode & 0o777)
    print("cookies.txt: %d lineas, modo=%s" % (len([l for l in txt.splitlines() if l and not l.startswith('#')]), mod))
    assert mod == "0o600", "permisos de cookies.txt deben ser 0600"

    print("\n== 2) yt-dlp --cookies <copia temp> == (2 videos reales)")
    # Copia en /tmp para que yt-dlp no reescriba cookies.txt de produccion.
    tmp = os.path.join(tempfile.gettempdir(), "playme_verify_cookies.txt")
    shutil.copy2(COOKIES, tmp)

    urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=9bZkp7q19f0",
    ]
    ok = True
    for u in urls:
        print("\n---", u)
        r1 = subprocess.run(["yt-dlp", "--cookies", tmp, "-J", "--no-warnings", "--skip-download", u],
                            capture_output=True, timeout=40)
        print("[metadata] rc=", r1.returncode, "stderr_tail=", (r1.stderr.decode(errors="replace").strip().splitlines() or [""])[-1][:90])
        if r1.returncode != 0:
            ok = False
            continue
        d = json.loads(r1.stdout.decode())
        print("  title:", (d.get("title") or "")[:60])
        print("  id:", d.get("id"), "uploader:", (d.get("uploader") or "")[:30])

        r2 = subprocess.run(["yt-dlp", "--cookies", tmp, "--get-url", "--format", "251/bestaudio",
                             "--no-warnings", u], capture_output=True, timeout=40)
        out = r2.stdout.decode(errors="replace").strip()
        print("[stream-url] rc=", r2.returncode, "len=", len(out))
        if r2.returncode != 0 or not out:
            ok = False

    try:
        os.unlink(tmp)
    except OSError:
        pass

    print("\nRESULTADO:", "OK (sin errores de bot-check)" if ok else "FALLIDO")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
