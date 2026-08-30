"""
firefox_cookies.py — Extrae cookies de YouTube/Google desde el perfil activo
de Firefox (cookies.sqlite) y escribe cookies.txt en formato Netscape.

Seguridad de lectura:
- NO toca el perfil en vivo: copia cookies.sqlite a /tmp y abre la copia con
  sqlite en modo `immutable=1`. Asi funciona con Firefox CORRIENDO (wal activo)
  sin bloquear ni corromper nada.
- Permite inyectar un `copy_fn` / `connect_fn` para tests sin depender de FS real.

Formato Netscape generado (compatible con Python 3.12 http.cookiejar y yt-dlp):
    dominio  flag_domain  path  secure  expiry  nombre  valor
"""
import logging
import os
import shutil
import sqlite3
import tempfile

logger = logging.getLogger(__name__)

# Dominios cuyas cookies interesan para YouTube (yt-dlp / streaming).
TARGET_HOSTS = (
    ".youtube.com",
    ".google.com",
    "www.youtube.com",
    "youtube.com",
    ".googlevideo.com",
    ".ytimg.com",
)

# Orden de preferencia: cookies esenciales de sesion primero (por si hay colision
# de nombres) y criticas al inicio para diagnostico rapido.
PREFERRED_ORDER = (
    "SID",
    "SAPISID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "HSID",
    "SSID",
    "APISID",
    "PREF",
    "LOGIN_INFO",
    "VISITOR_INFO1_LIVE",
    "YSC",
    "CONSENT",
    "ST-1PAPISID",
)


class FirefoxCookiesExtractor:
    """Extrae cookies de youtube/google desde cookies.sqlite de Firefox."""

    def __init__(self, profile_db, copy_fn=None, connect_fn=None, tmpdir=None):
        self.profile_db = profile_db
        # stdlib defaults (inyectables en tests)
        self._copy = copy_fn or shutil.copy2
        self._connect = connect_fn or (lambda path: sqlite3.connect("file:%s?immutable=1" % path, uri=True))
        self._tmpdir = tmpdir or tempfile.gettempdir()

    # ── helpers ────────────────────────────────────────────────────────────
    def _snapshot(self):
        """Copia la db del perfil (en uso) a un archivo temporal seguro."""
        if not (os.path.isfile(self.profile_db) and os.path.getsize(self.profile_db) > 0):
            return None
        dst = os.path.join(self._tmpdir, "playme_firefox_cookies.sqlite")
        try:
            self._copy(self.profile_db, dst)
            os.chmod(dst, 0o600)
            return dst
        except OSError as e:
            logger.warning("No se pudo copiar cookies.sqlite: %s", e)
            return None

    def _query(self, snap):
        """Lee filas de youtube/google desde la copia inmutable."""
        con = self._connect(snap)
        try:
            cur = con.cursor()
            # Verificar que la tabla existe (el esquema de cookies.sqlite).
            cur.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='moz_cookies'"
            )
            if cur.fetchone()[0] == 0:
                logger.warning("moz_cookies no encontrado; perfil sin cookies manuales")
                return []
            placeholders = ",".join("?" * len(TARGET_HOSTS))
            cur.execute(
                "SELECT host,name,value,path,isSecure,expiry,isHttpOnly "
                "FROM moz_cookies "
                "WHERE host IN (%s)" % placeholders,
                TARGET_HOSTS,
            )
            return cur.fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("Error leyendo moz_cookies: %s", e)
            return []
        finally:
            con.close()

    # ── filtrado / normalizacion ──────────────────────────────────────────
    def _dedupe(self, rows):
        """Quita duplicados (mismo name) quedandose con el dominio preferido.

        Firefox almacena cookies superpuestas/particionadas; queremos el valor
        vigente. Preferimos el de dominio '.youtube.com' y luego '.google.com'.
        """
        best = {}
        order = {n: i for i, n in enumerate(PREFERRED_ORDER)}
        for host, name, value, path, is_secure, expiry, is_http in rows:
            rank = 0 if host == ".youtube.com" else 1 if host == ".google.com" else 2
            prio = (order.get(name, 999), rank)  # nombre preferido + dominio
            current = best.get(name)
            if current is None or prio < current[0]:
                best[name] = (prio, (host, name, value, path, is_secure, expiry, is_http))
        # devolver dict nombre -> cookie (sin el prio)
        return {k: v[1] for k, v in best.items()}

    def _netscape_line(self, cookie):
        host, name, value, path, is_secure, expiry, _is_http = cookie
        domain_flag = "TRUE" if host.startswith(".") else "FALSE"
        secure_flag = "TRUE" if is_secure else "FALSE"
        expires = int(expiry) if (expiry and int(expiry) > 0) else 0
        return "\t".join([host, domain_flag, path or "/", secure_flag, str(expires), name, value])

    def _required_present(self, cookies):
        names = set(cookies.keys())
        missing = [n for n in ("SID", "SAPISID", "__Secure-1PAPISID") if n not in names]
        if missing:
            logger.warning("Faltan cookies de sesion youtube en Firefox: %s", ",".join(missing))
        return len(missing) == 0

    # ── API ───────────────────────────────────────────────────────────────
    def extract(self):
        """Lee cookies de youtube/google del perfil. Retorna dict {nombre: cookie}.

        Retorna {} si no hay perfil/util. No escribe archivo (lo hace write_file).
        """
        snap = self._snapshot()
        if snap is None:
            logger.warning("No hay cookies.sqlite en %s", self.profile_db)
            return {}
        try:
            rows = self._query(snap)
            dedup = self._dedupe(rows)
            logger.info("Firefox: %d filas youtube/google, %d cookies unicas", len(rows), len(dedup))
            return dedup
        finally:
            try:
                os.unlink(snap)  # limpieza best-effort de la copia temporal
            except OSError:
                pass

    def write_file(self, path, allowed=("SID", "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID",
                                        "HSID", "SSID", "APISID", "PREF", "LOGIN_INFO",
                                        "VISITOR_INFO1_LIVE", "CONSENT", "ST-1PAPISID", "YSC", "NID")):
        """Escribe el archivo Netscape. Retorna lista de nombres escritos."""
        cookies = self.extract()
        if not cookies:
            return []
        keys = [k for k in PREFERRED_ORDER if k in cookies]
        extra = sorted(set(cookies) - set(keys))
        extra = [k for k in extra if k in allowed]
        chosen = keys + extra

        lines = ["# Netscape HTTP Cookie File",
                 "# Extraido de Firefox (%s)" % os.path.basename(self.profile_db)]
        for k in chosen:
            lines.append(self._netscape_line(cookies[k]))

        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        logger.info("cookies.txt actualizado desde Firefox: %d cookies", len(chosen))
        return chosen
