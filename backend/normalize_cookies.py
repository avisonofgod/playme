#!/usr/bin/env python3
"""
normalize_cookies.py — Prepara un archivo de cookies exportado del navegador
("Get cookies.txt LOCALLY") para que yt-dlp/Python 3.12 lo acepten.

Problemas que resuelve:
1. El exportador incluye cookies de TODOS los sitios (a veces 2000+). yt-dlp
   solo necesita youtube/google. Filtramos esos dominios.
2. Bug de Python 3.12 http.cookiejar: `assert domain_specified == initial_dot`
   falla cuando el dominio tiene punto inicial pero el flag es FALSE (o al
   reves). Normalizamos el dominio segun la regla Netscape.

Uso:
    python3 normalize_cookies.py /ruta/cookies_exportadas.txt [salida]

Salida por defecto: cookies.txt en el directorio del proyecto (raiz).
"""
import re, sys, os

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "/root/cookies.txt"
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PROJECT, "cookies.txt")
    keep = re.compile(r"\.(youtube\.com|google\.com|ytimg\.com|googleusercontent\.com|googlevideo\.com)\t")

    out = ["# Netscape HTTP Cookie File", "# Filtrado y normalizado para PlayMe (youtube/google)"]
    kept = fixed = dropped = 0

    with open(src, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            if not keep.search(line):
                continue
            parts = line.split("\t")
            if len(parts) != 7:
                dropped += 1
                continue
            domain, flag, path, secure, expires, name, value = parts
            if not expires.isdigit():
                dropped += 1
                continue
            if flag == "TRUE" and not domain.startswith("."):
                domain = "." + domain
                fixed += 1
            elif flag == "FALSE" and domain.startswith("."):
                domain = domain.lstrip(".")
                fixed += 1
            out.append("\t".join([domain, flag, path, secure, expires, name, value]))
            kept += 1

    with open(dst, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(dst, 0o600)
    print(f"OK: {kept} cookies youtube/google -> {dst} (dominios normalizados: {fixed}, descartadas: {dropped})")

if __name__ == "__main__":
    main()
