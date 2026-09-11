"""Resolver DNS via Java para Chaquopy/Android.

Algunos dispositivos no resuelven nombres desde el getaddrinfo nativo de Python
(EAI_NODATA / "No address associated with hostname") aunque Android si resuelva.
Este modulo instala un wrapper que cae a java.net.InetAddress cuando el resolver
nativo falla.
"""

import socket

_orig_getaddrinfo = socket.getaddrinfo
_installed = False
_last_error = None


def _resolve_java(host, port, type_=0, proto=0):
    from java.net import InetAddress

    try:
        port = int(port)
    except Exception:
        port = 0
    out = []
    for a in InetAddress.getAllByName(host):
        ip = str(a.getHostAddress())
        if "%" in ip:
            ip = ip.split("%")[0]
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        out.append((fam, type_ or socket.SOCK_STREAM, proto or 6, "", (ip, port)))
    return out


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    global _last_error
    try:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except Exception as e:
        _last_error = str(e)
        try:
            res = _resolve_java(host, port, type, proto)
            print("DNSJAVA: resuelto %s via Java (%d direcciones)" % (host, len(res)))
            return res
        except Exception as e2:
            print("DNSJAVA: fallo tambien via Java: %s" % e2)
            raise e


def install():
    global _installed
    if _installed:
        return True
    try:
        from java.net import InetAddress  # noqa: F401
    except Exception as e:
        print("DNSJAVA: no disponible (%s)" % e)
        return False
    socket.getaddrinfo = _getaddrinfo
    _installed = True
    print("DNSJAVA: wrapper instalado")
    return True


def diag(host="www.youtube.com", port=443):
    import time

    lines = []
    t0 = time.time()
    try:
        r = socket.getaddrinfo(host, port)
        lines.append("getaddrinfo OK %d addrs %.2fs %s" % (len(r), time.time() - t0, r[0][4][0]))
    except Exception as e:
        lines.append("getaddrinfo FAIL %s: %s" % (type(e).__name__, e))
    try:
        import urllib.request

        t1 = time.time()
        with urllib.request.urlopen("https://www.youtube.com/robots.txt", timeout=20) as resp:
            data = resp.read(200)
        lines.append("https OK %d bytes %.2fs" % (len(data), time.time() - t1))
    except Exception as e:
        lines.append("https FAIL %s: %s" % (type(e).__name__, e))
    txt = " | ".join(lines)
    print("NETDIAG: " + txt)
    return txt
