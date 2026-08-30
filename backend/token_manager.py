"""
TokenManager: fuente de cookies de YouTube.

Estrategia 2024/2025 (hasta la fecha, 30-Ago):
1. PRIORIDAD: leer cookies DIRECTAMENTE del perfil activo de Firefox de noroot
   (cookies.sqlite) — sin export manual, sin detener Firefox. Genera/renueva
   cookies.txt en formato Netscape (ver firefox_cookies.py).
2. FALLBACK (si no hay perfil/sesion en Firefox): reproducir el flujo antiguo
   con token.txt (SAPISID) via _ensure() manual.
"""
import logging
import os
import threading
import time

from firefox_cookies import FirefoxCookiesExtractor

logger = logging.getLogger(__name__)

COOKIES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")
PROFILE_DB = "/home/noroot/.mozilla/firefox/33ys3rhe.default-esr/cookies.sqlite"

REFRESH_SECONDS = int(os.environ.get("PLAYME_REFRESH_SECONDS", "6")) * 3600  # 6h por defecto


class TokenManager:
    def __init__(self, profile_db=None, copy_fn=None, connect_fn=None):
        self.sapisid = None
        self.firefox = FirefoxCookiesExtractor(
            profile_db or PROFILE_DB, copy_fn=copy_fn, connect_fn=connect_fn
        )
        self._load()

    # ── fuente primaria: Firefox ──────────────────────────────────────────
    def _extract_firefox(self):
        """Intenta llenar cookies.txt desde Firefox. Retorna True si logro
        escribir con cookies de sesion vigentes."""
        written = self.firefox.write_file(COOKIES)
        return bool(written)

    def has_firefox(self):
        """True si cookies.txt fue generado desde Firefox y contiene SID."""
        if not (os.path.isfile(COOKIES) and os.path.getsize(COOKIES) > 0):
            return False
        try:
            import http.cookiejar
            cj = http.cookiejar.MozillaCookieJar(COOKIES)
            cj.load()
            names = {c.name for c in cj}
            return "SID" in names and "SAPISID" in names
        except Exception:
            return False

    # ── carga inicial ─────────────────────────────────────────────────────
    def _load(self):
        """Carga: 1) Firefox (prioridad). 2) token.txt como fallback."""
        self.sapisid = None  # relectura limpia
        if self._extract_firefox():
            # Leer SAPISID desde las cookies de Firefox (para mantener info() util)
            sap = self._read_sapisid_cookie()
            if sap:
                self.sapisid = sap
            logger.info("TokenManager: cookies inicializadas desde Firefox")
            return
        # fallback manual (perfil no disponible/sin sesion)
        path = os.path.join(os.path.dirname(COOKIES), "token.txt")
        if os.path.isfile(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.sapisid = line
                        self._ensure()
                        return
        logger.warning("TokenManager: ni Firefox ni token.txt disponibles")

    def _read_sapisid_cookie(self):
        try:
            import http.cookiejar
            cj = http.cookiejar.MozillaCookieJar(COOKIES)
            cj.load()
            for c in cj:
                if c.name == "SAPISID":
                    return c.value
        except Exception:
            pass
        return None

    # ── API conservada (compatibilidad) ───────────────────────────────────
    def set(self, sapisid):
        """Mantiene el metodo manual legacy: escribe token.txt y aplica _ensure."""
        if not sapisid or len(sapisid) < 20:
            return False
        self.sapisid = sapisid.strip()
        path = os.path.join(os.path.dirname(COOKIES), "token.txt")
        with open(path, "w") as f:
            f.write(f"# PlayMe SAPISID\n{self.sapisid}\n")
        self._ensure()
        return True

    def has(self):
        return self.has_firefox() or (self.sapisid is not None and len(self.sapisid) > 20)

    def _ensure(self):
        """Legacy: construye cookies.txt desde token.txt (SAPISID). Solo lo usa
        el camino de fallback manual. No borra cookies adicionales."""
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
            except Exception:
                pass

        sapisid_cookies = {
            "SAPISID": ("FALSE", s),
            "__Secure-1PAPISID": ("TRUE", s),
            "__Secure-3PAPISID": ("TRUE", s),
        }
        lines = ["# Netscape HTTP Cookie File", "# PlayMe YouTube cookies (fallback token.txt)"]
        seen = set()
        for name, c in sorted(existing.items(), key=lambda x: x[0]):
            if name in sapisid_cookies or name in seen:
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
        src = "firefox" if self.has_firefox() else "token"
        return {
            "source": src,
            "has_token": self.has(),
            "sapisid_prefix": (self.sapisid[:8] + "...") if self.sapisid else None,
        }

    def refresh_loop(self):
        """Refresca cookies desde Firefox periodicamente (default 6h)."""
        while True:
            time.sleep(REFRESH_SECONDS)
            if self._extract_firefox():
                sap = self._read_sapisid_cookie()
                if sap:
                    self.sapisid = sap
                logger.info("TokenManager.refresh_loop: cookies renovadas desde Firefox")
            else:
                logger.warning("TokenManager.refresh_loop: sin cookies Firefox en este ciclo; mantenemos existentes")
