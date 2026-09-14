import socket, struct, random, threading


def _try(fn, *a):
    try:
        return "ok:" + str(fn(*a))[:60]
    except Exception as e:
        return "err:" + str(e)[:80]


def _tcp(pair, t=6):
    s = socket.socket()
    s.settimeout(t)
    s.connect(pair)
    s.close()
    return "conectado"


def _dns(server, host="www.youtube.com", t=5):
    qid = random.randint(0, 65000)
    q = struct.pack(">HHHHHH", qid, 256, 1, 0, 0, 0)
    for p in host.split("."):
        q += bytes([len(p)]) + p.encode()
    q += bytes([0]) + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(t)
    s.sendto(q, (server, 53))
    d, _ = s.recvfrom(512)
    s.close()
    return str(len(d)) + " bytes"


def _jrun(host, out):
    try:
        from java.net import InetAddress
        out["inet"] = "ok:" + InetAddress.getByName(host).getHostAddress()
    except Exception as e:
        out["inet"] = "err:" + str(e)[:70]


def run():
    out = {}
    out["gai"] = _try(socket.getaddrinfo, "www.youtube.com", 443)
    out["tcp_goog"] = _try(_tcp, ("8.8.8.8", 53))
    out["tcp_yt"] = _try(_tcp, ("142.250.72.14", 443))
    out["dns_lan"] = _try(_dns, "192.168.5.1")
    t = threading.Thread(target=_jrun, args=("www.youtube.com", out))
    t.start()
    t.join(20)
    return out
