"""
TokenManager: lee SAPISID de token.txt, construye cookies.txt.
NO sobrescribe cookies adicionales (SID, HSID, etc).
"""
import logging, os, threading, time

logger = logging.getLogger(__name__)
COOKIES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")

class TokenManager:
    def __init__(self):
        self.sapisid = None
        self._load()

    def _load(self):
        path = os.path.join(os.path.dirname(os.path.dirname(COOKIES)), "token.txt")
        if os.path.isfile(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.sapisid = line
                        self._ensure()
                        return

    def set(self, sapisid):
        if not sapisid or len(sapisid) < 20:
            return False
        self.sapisid = sapisid.strip()
        path = os.path.join(os.path.dirname(os.path.dirname(COOKIES)), "token.txt")
        with open(path, "w") as f:
            f.write(f"# PlayMe SAPISID\n{self.sapisid}\n")
        self._ensure()
        return True

    def has(self):
        return self.sapisid is not None and len(self.sapisid) > 20

    def _ensure(self):
        """Asegura que cookies.txt tenga SAPISID, pero no borra otras cookies."""
        s = self.sapisid
        if not s:
            return
        existing = {}
        if os.path.isfile(COOKIES) and os.path.getsize(COOKIES) > 0:
            try:
                import http.cookiejar
                cj = http.cookiejar.MozillaCookieJar(COOKIES)
                cj.load()
                for c in cj:
                    existing[c.name] = c
            except:
                pass

        sapisid_cookies = {
            "SAPISID": ("FALSE", s),
            "__Secure-1PAPISID": ("TRUE", s),
            "__Secure-3PAPISID": ("TRUE", s),
        }

        lines = ["# Netscape HTTP Cookie File", "# PlayMe YouTube cookies"]
        seen = set()

        for name, c in sorted(existing.items(), key=lambda x: x[0]):
            if name in sapisid_cookies:
                continue
            if name in seen:
                continue
            seen.add(name)
            lines.append(f".youtube.com\tTRUE\t/\t{'TRUE' if c.secure else 'FALSE'}\t{int(c.expires) if c.expires else 0}\t{c.name}\t{c.value}")

        for name, (secure, val) in sapisid_cookies.items():
            if name not in seen:
                lines.append(f".youtube.com\tTRUE\t/\t{secure}\t1818928972\t{name}\t{val}")
                seen.add(name)

        with open(COOKIES, "w") as f:
            f.write("\n".join(lines) + "\n")

    def info(self):
        return {
            "has_token": self.has(),
            "sapisid_prefix": self.sapisid[:8] + "..." if self.sapisid else None,
        }

    def refresh_loop(self):
        while True:
            time.sleep(6 * 3600)
            if self.has():
                self._ensure()
