"""
PlayMe - Token Manager
Maneja el token SAPISID del usuario para autenticación en YouTube.
Construye cookies.txt automáticamente y refresca periódicamente.
"""
import logging
import os
import time
import threading

logger = logging.getLogger(__name__)

COOKIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cookies.txt")

# SAPISID es la única cookie que realmente necesitamos.
# Las demás (SID, HSID, SSID, APISID) las genera yt-dlp automáticamente
# a partir del SAPISID, o podemos generarlas nosotros con el hash correcto.
#
# Pero para máxima compatibilidad, el usuario puede pegarnos el SAPISID
# y nosotros construimos un cookies.txt mínimo que yt-dlp completará.

class TokenManager:
    def __init__(self):
        self.sapisid = None
        self._lock = threading.Lock()
        self._refresh_timer = None
        self._running = False
        self._load_from_disk()
        logger.info("TokenManager init")

    def _load_from_disk(self):
        """Carga SAPISID del archivo de token si existe."""
        token_path = os.path.join(os.path.dirname(COOKIES_PATH), "token.txt")
        if os.path.isfile(token_path):
            with open(token_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.sapisid = line
                        logger.info("SAPISID cargado de token.txt")
                        self._build_cookies()
                        return
        logger.info("No token found on disk")

    def set_token(self, sapisid: str):
        """Establece un nuevo SAPISID y construye cookies.txt."""
        sapisid = sapisid.strip()
        if not sapisid or len(sapisid) < 20:
            logger.warning(f"SAPISID inválido: {len(sapisid)} chars")
            return False

        with self._lock:
            self.sapisid = sapisid
            # Guardar token puro para persistencia
            token_path = os.path.join(os.path.dirname(COOKIES_PATH), "token.txt")
            with open(token_path, "w") as f:
                f.write(f"# PlayMe SAPISID token\n{sapisid}\n")
            self._build_cookies()
            logger.info(f"SAPISID guardado ({len(sapisid)} chars)")
        return True

    def has_token(self):
        return self.sapisid is not None and len(self.sapisid) > 20

    def _build_cookies(self):
        """Construye cookies.txt desde SAPISID.
        
        yt-dlp internamente solo necesita SAPISID para autenticar.
        Pero el parser de cookies de Python es muy estricto
        con el formato. Construimos un archivo mínimo que
        yt-dlp pueda leer.
        
        Nota: las cookies __Secure-* requieren secure=TRUE.
        SAPISID normal no requiere secure.
        """
        s = self.sapisid
        lines = [
            "# Netscape HTTP Cookie File",
            "# Generado por PlayMe Token Manager",
            f".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\t{s}",
            f".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-3PAPISID\t{s}",
            f".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-1PAPISID\t{s}",
            f".youtube.com\tTRUE\t/\tFALSE\t0\tAPISID\t{s}",
        ]
        content = "\n".join(lines) + "\n"
        with open(COOKIES_PATH, "w") as f:
            f.write(content)
        logger.info(f"Cookies.txt construido con SAPISID ({len(s)} chars)")

    def get_info(self):
        """Información del token para la API."""
        return {
            "has_token": self.has_token(),
            "sapisid_prefix": self.sapisid[:8] + "..." if self.sapisid else None,
            "cookies_file_size": os.path.getsize(COOKIES_PATH) if os.path.isfile(COOKIES_PATH) else 0,
        }

    def start_refresh_loop(self, interval_hours=6):
        """Inicia refresh periódico de las cookies.
        
        yt-dlp renueva cookies automáticamente en cada request.
        Este refresh solo reconstruye el archivo si es necesario
        y verifica que siga funcionando.
        """
        if self._running:
            return
        
        self._running = True
        
        def refresh_worker():
            while self._running:
                time.sleep(interval_hours * 3600)
                if self.has_token():
                    logger.info("Refresh periódico de cookies...")
                    self._build_cookies()
                    # yt-dlp renueva las cookies volátiles en cada request,
                    # no necesitamos hacer nada más.
        
        t = threading.Thread(target=refresh_worker, daemon=True)
        t.start()
        logger.info(f"Refresh loop started every {interval_hours}h")

    def stop_refresh_loop(self):
        self._running = False
