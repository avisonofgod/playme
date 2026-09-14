import socket, threading


def _try(fn, *a):
    try:
        r = fn(*a)
        return "ok:" + str(r)[:70]
    except Exception as e:
        return "err:" + str(e)


def _jtry(host):
    try:
        from java.net import InetAddress
        return "ok:" + InetAddress.getByName(host).getHostAddress()
    except Exception as e:
        return "err:" + str(e)


def run():
    out = {"gai_main": _try(socket.getaddrinfo, "www.youtube.com", 443)}
    res = {}

    def _h():
        res["gai_thread"] = _try(socket.getaddrinfo, "www.youtube.com", 443)
        res["inet_thread"] = _jtry("www.youtube.com")
        res["gai_gv"] = _try(socket.getaddrinfo, "redirector.googlevideo.com", 443)

    t = threading.Thread(target=_h)
    t.start()
    t.join(30)
    out.update(res)
    return out
