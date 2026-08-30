"""
cookie_parser.py — Parseador del archivo de cookies en formato Netscape.

Proporciona un helper para construir el header `Cookie` a partir de cookies.txt
(tal como lo genera firefox_cookies.py / token_manager: Netscape format).

Formato Netscape (una cookie por linea, tab-separated):
    dominio  flag_domain  path  secure  expiry  nombre  valor
    (las lineas que empiezan por '#' son comentarios)
"""
import http.cookiejar
import os

# Dominios cuyas cookies se envian al acceder a googlevideo / youtube.
STREAM_HOSTS = (".youtube.com", ".googlevideo.com", "www.youtube.com", "youtube.com")


def load_cookie_pairs(path, hosts=STREAM_HOSTS):
    """Lee el archivo Netscape y devuelve lista de tuplas (nombre, valor).

    Filtra por los dominios en `hosts`. Si el archivo no existe o esta vacio,
    retorna [].
    """
    if not (path and os.path.isfile(path) and os.path.getsize(path) > 0):
        return []
    try:
        cj = http.cookiejar.MozillaCookieJar(path)
        cj.load(ignore_discard=True, ignore_expires=True)
        return [(c.name, c.value) for c in cj if c.domain in hosts]
    except Exception:
        return []


def build_cookie_header(path, hosts=STREAM_HOSTS):
    """Construye el header 'Cookie: name1=value1; name2=value2' (o '' si no hay)."""
    pairs = load_cookie_pairs(path, hosts)
    if not pairs:
        return ""
    return "; ".join("%s=%s" % (name, val) for name, val in pairs)
