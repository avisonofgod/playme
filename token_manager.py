"""
TokenManager: lee SAPISID de token.txt, construye cookies.txt.
"""
import logging, os, threading, time

logger = logging.getLogger(__name__)
COOKIES = os.path.join(os.path.dirname(__file__), "cookies.txt")

class TokenManager:
    def __init__(self):
        self.sapisid = None
        self._load()

    def _load(self):
        path = os.path.join(os.path.dirname(COOKIES), "token.txt")
        if os.path.isfile(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.sapisid = line
                        self._build()
                        return

    def set(self, sapisid):
        if not sapisid or len(sapisid) < 20:
            return False
        self.sapisid = sapisid.strip()
        path = os.path.join(os.path.dirname(COOKIES), "token.txt")
        with open(path, "w") as f:
            f.write(f"# PlayMe SAPISID\n{self.sapisid}\n")
        self._build()
        return True

    def has(self):
        return self.sapisid is not None and len(self.sapisid) > 20

    def _build(self):
        s = self.sapisid
        lines = [
            "# Netscape HTTP Cookie File",
            ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\t" + s,
            ".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-3PAPISID\t" + s,
            ".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-1PAPISID\t" + s,
        ]
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
                self._build()
