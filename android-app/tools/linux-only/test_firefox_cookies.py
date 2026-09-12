"""Tests de firefox_cookies.py sin tocar el perfil real de Firefox.

Inyectamos una db sqlite en memoria (o temp file) con el esquema de moz_cookies
y verificamos: snapshot (copy), query filtrada, dedupe, y salida Netscape.
"""
import os
import sqlite3
import tempfile
import unittest

from firefox_cookies import FirefoxCookiesExtractor, TARGET_HOSTS


def make_sqlite(rows):
    """Crea un archivo sqlite con el esquema minimo de moz_cookies."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE moz_cookies ("
        "id INTEGER PRIMARY KEY, name TEXT, value TEXT, host TEXT, path TEXT, "
        "expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER"
        ")"
    )
    for r in rows:
        con.execute(
            "INSERT INTO moz_cookies (host,name,value,path,expiry,isSecure,isHttpOnly) "
            "VALUES (?,?,?,?,?,?,?)", r
        )
    con.commit()
    con.close()
    return path


class _CopyRecorder:
    """Captura que se haya copiado el archivo fuente (prueba modo snapshot)."""
    def __init__(self):
        self.copied_src = None
        self.copied_dst = None

    def __call__(self, src, dst):
        self.copied_src = src
        self.copied_dst = dst
        import shutil
        shutil.copy2(src, dst)


def connect_immutable(path):
    """Connect wrapper estandar (misma logica que prod, pero usable en test)."""
    return sqlite3.connect("file:%s?immutable=1" % path, uri=True)


class FirefoxCookiesTest(unittest.TestCase):
    def setUp(self):
        self.src_path = make_sqlite([
            (".youtube.com", "SID", "sid-val", "/", 1820000000, 0, 1),
            (".youtube.com", "SAPISID", "sapival", "/", 1820000000, 1, 0),
            (".youtube.com", "__Secure-1PAPISID", "1papival", "/", 1820000000, 1, 0),
            (".youtube.com", "HSID", "hsidval", "/", 1820000000, 0, 0),
            (".google.com", "SID", "google-sid", "/", 1820000000, 0, 1),
            (".example.com", "IGNORE", "bad", "/", 1820000000, 0, 0),
        ])

    def tearDown(self):
        try:
            os.unlink(self.src_path)
        except OSError:
            pass

    def test_snapshot_copies(self):
        rec = _CopyRecorder()
        ex = FirefoxCookiesExtractor(self.src_path, copy_fn=rec, connect_fn=connect_immutable)
        ex.extract()
        self.assertEqual(rec.copied_src, self.src_path)
        self.assertTrue(rec.copied_dst.endswith("playme_firefox_cookies.sqlite"))
        # la copia temporal se debe limpiar tras extract
        self.assertFalse(os.path.exists(rec.copied_dst))

    def test_extract_filtra_solo_youtube_google(self):
        ex = FirefoxCookiesExtractor(
            self.src_path,
            copy_fn=lambda s, d: __import__("shutil").copy2(s, d),
            connect_fn=connect_immutable,
        )
        cookies = ex.extract()
        # example.com queda fuera
        self.assertNotIn("IGNORE", cookies)
        # Nombres presentes
        self.assertIn("SID", cookies)
        self.assertIn("SAPISID", cookies)
        self.assertIn("__Secure-1PAPISID", cookies)
        # dedupe: prefiere .youtube.com para SID (no el de google.com)
        self.assertEqual(cookies["SID"][0], ".youtube.com")

    def test_required_present(self):
        ex = FirefoxCookiesExtractor(self.src_path, copy_fn=lambda s, d: None, connect_fn=connect_immutable)
        # con filas ficticias nos valdría; comprobamos sobre las del sqlite con la sesion completa
        # insertamos también el doble camino este metodo no depende del fs, uses rows manuales:
        from firefox_cookies import FirefoxCookiesExtractor as F
        f2 = F(self.src_path)
        # moz_cookies con datos: extraer y marcar ok
        cookies = f2.extract()
        # _required_present necesita el dict name->cookie
        named = {k: v for k, v in cookies.items()}
        self.assertTrue(f2._required_present(named))

    def test_escribe_formato_netscape_valido(self):
        ex = FirefoxCookiesExtractor(self.src_path, copy_fn=lambda s,d: __import__("shutil").copy2(s,d), connect_fn=connect_immutable)
        out = tempfile.mktemp(suffix=".txt")
        chosen = ex.write_file(out)
        with open(out) as f:
            data = f.read()
        self.assertIn("SID", data)
        self.assertIn("SAPISID", data)
        self.assertIn("sapival", data)
        # todas las lineas no-comentario tienen 7 campos
        for ln in data.splitlines():
            if ln and not ln.startswith("#"):
                self.assertEqual(len(ln.split("\t")), 7)
        self.assertIn("SID", chosen)
        self.assertIn("SAPISID", chosen)
        os.unlink(out)

    def test_perfil_inexistente_devuelve_vacio(self):
        ex = FirefoxCookiesExtractor("/no/existe/foo.sqlite", copy_fn=lambda s, d: None, connect_fn=connect_immutable)
        cookies = ex.extract()
        self.assertEqual(cookies, {})


if __name__ == "__main__":
    unittest.main()
