"""Tests de Resolver con runner mockeado (sin red ni YouTube)."""
import json
import os
import tempfile
import unittest

import resolver as res_mod
from resolver import Resolver


class FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class ScriptedRunner:
    """Runner que devuelve resultados programados en orden, o lanza si vacio."""

    def __init__(self):
        self.calls = []      # args de cada llamada
        self.results = []    # cola de respuestas (FakeProc)
        self.exceptions = []  # lista de excepciones a lanzar (en orden)

    def __call__(self, args, timeout=None, check=False, capture_output=True):
        self.calls.append(args)
        if self.exceptions:
            exc = self.exceptions.pop(0)
            if exc:
                raise exc()
        if self.results:
            proc = self.results.pop(0)
            if check and proc.returncode != 0:
                raise RuntimeError("simulated fail")
            return proc
        return FakeProc()


def make_resolver(runner):
    """Crea un Resolver con runner mock. COOKIES_LIVE/TEMP del modulo quedan
    igual (mismo archivo) para que _sync_cookies no intente copiar sobre si."""
    return Resolver(runner=runner)


def _patch_cookies(testcase):
    """Fuerza COOKIES_* del modulo a un mismo path vacio para no copiar en si."""
    fd, p = tempfile.mkstemp()
    os.close(fd)
    open(p, "w").close()
    old = (res_mod.COOKIES_LIVE, res_mod.COOKIES_BACKUP, res_mod.COOKIES_TEMP)
    res_mod.COOKIES_LIVE = p
    res_mod.COOKIES_BACKUP = p
    res_mod.COOKIES_TEMP = p

    def restore():
        os.unlink(p)
        (res_mod.COOKIES_LIVE, res_mod.COOKIES_BACKUP, res_mod.COOKIES_TEMP) = old

    testcase.addCleanup(restore)
    return p


class ResolverSearchTest(unittest.TestCase):
    def setUp(self):
        self.runner = ScriptedRunner()
        _patch_cookies(self)

    def _search_payload(self):
        return {
            "entries": [
                {"id": "vid1", "title": "Tema Uno", "duration": 120, "uploader": "autor", "thumbnail": ""},
                {"id": "vid2", "title": "Tema Dos", "duration": 240, "uploader": "autor2", "thumbnail": ""},
            ]
        }

    def test_search_devuelve_resultados(self):
        self.runner.results.append(FakeProc(json.dumps(self._search_payload()).encode()))
        r = make_resolver(self.runner)
        out = r.search("query test")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["id"], "vid1")
        self.assertEqual(out[0]["title"], "Tema Uno")

    def test_search_incluye_cookies_en_args_si_existe(self):
        # cookies.txt real existe en el repo -> args debe incluir --cookies
        self.runner.results.append(FakeProc(json.dumps(self._search_payload()).encode()))
        r = make_resolver(self.runner)
        r.search("jazz")
        first = self.runner.calls[0]
        if "--cookies" in first:
            idx = first.index("--cookies")
            self.assertIsNotNone(first[idx + 1])

    def test_search_error_devuelve_lista_vacia(self):
        self.runner.exceptions.append(OSError)
        r = make_resolver(self.runner)
        self.assertEqual(r.search("x"), [])


class ResolverStreamTest(unittest.TestCase):
    def setUp(self):
        self.runner = ScriptedRunner()
        _patch_cookies(self)

    def test_get_stream_url_devuelve_url(self):
        self.runner.results.append(FakeProc(b"https://audio.example/stream.m3u8", b"", 0))
        r = make_resolver(self.runner)
        url = r.get_stream_url("vid")
        self.assertEqual(url, "https://audio.example/stream.m3u8")
        self.assertIsNone(r.last_error)

    def test_get_stream_url_cae_en_segunda_estrategia(self):
        self.runner.results.append(FakeProc(b"", b"ERROR: bot-check", 1))
        self.runner.results.append(FakeProc(b"https://audio.example/ok", b"", 0))
        r = make_resolver(self.runner)
        url = r.get_stream_url("vid")
        self.assertEqual(url, "https://audio.example/ok")

    def test_get_stream_url_todas_fallan_devuelve_none(self):
        for _ in range(5):
            self.runner.results.append(FakeProc(b"", b"ERROR: blocked", 1))
        r = make_resolver(self.runner)
        url = r.get_stream_url("vid")
        self.assertIsNone(url)
        self.assertTrue(r.last_error and "ERROR" in r.last_error)

    def test_get_stream_url_timeout_lo_maneja(self):
        class AlwaysFail:
            def __call__(self, *a, **k):
                raise TimeoutError("timeout 10s")
        r = make_resolver(AlwaysFail())
        url = r.get_stream_url("vid")
        self.assertIsNone(url)
        self.assertIsNotNone(r.last_error)


class ResolverInfoTest(unittest.TestCase):
    def setUp(self):
        self.runner = ScriptedRunner()
        _patch_cookies(self)

    def test_get_info_completo(self):
        info = {"id": "v", "title": "T", "duration": 60}
        self.runner.results.append(FakeProc(json.dumps(info).encode()))
        r = make_resolver(self.runner)
        out = r.get_info("v")
        self.assertEqual(out["title"], "T")

    def test_get_info_full_falla_degrada_a_flat(self):
        info = {"id": "v", "title": "Flat"}
        self.runner.results.append(FakeProc(b"", b"fail", 1))  # full -J falla
        self.runner.results.append(FakeProc(json.dumps(info).encode()))  # flat OK
        r = make_resolver(self.runner)
        out = r.get_info("v")
        self.assertEqual(out["title"], "Flat")

    def test_get_info_todo_falla_devuelve_none(self):
        self.runner.results.append(FakeProc(b"", b"fail", 1))
        self.runner.results.append(FakeProc(b"", b"fail", 1))
        r = make_resolver(self.runner)
        self.assertIsNone(r.get_info("v"))


if __name__ == "__main__":
    unittest.main()
