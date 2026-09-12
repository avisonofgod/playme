"""Tests de las mejoras de robustez de la Fase 3:
- rate limiting por IP (server._rate_limited)
- limite de _conversions con evict (server._evict_conversions)
- metadata REAL en /api/queue/add cuando el titulo es placeholder
- kill de grupo en runner.run_command sobre timeout

Sin red ni YouTube real; se mockean resolver/titulos.
"""
import io
import json
import sys
import tempfile
import time
import unittest
from unittest import mock

import server as server_mod
from server import Handler, err_res, json_res, _rate_limited, _evict_conversions


# ── Reutilizamos el helper de test_server para construir handlers ──
def _fake_headers(d):
    class H:
        def get(self, k, default=None):
            return d.get(k, default)
        def __getitem__(self, k):
            return d[k]
    return H()


def make_handler(path="/", body=b"", headers=None):
    state = {"code": None, "headers": {}, "buffer": io.BytesIO()}
    h = Handler.__new__(Handler)
    h.path = path
    hdrs = dict(headers or {})
    if body:
        hdrs.setdefault("Content-Length", str(len(body)))
    h.headers = _fake_headers(hdrs)
    h.rfile = io.BytesIO(body)
    h.wfile = state["buffer"]
    h.client_address = ("203.0.113.99", 54321)
    h.send_response = lambda c: state.__setitem__("code", c)
    h.send_header = lambda k, v: state["headers"].__setitem__(k, v)
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    return h, state


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        # resetea estado del rate para aislamiento
        from server import _RATE, _RATE_LOCK
        with _RATE_LOCK:
            for cfg in _RATE.values():
                cfg["hits"].clear()

    def test_permite_hasta_el_limite(self):
        ip = "1.1.1.1"
        for _ in range(15):
            self.assertFalse(_rate_limited("/api/search", ip))
        self.assertTrue(_rate_limited("/api/search", ip))

    def test_convert_limite_menor(self):
        ip = "2.2.2.2"
        for _ in range(10):
            self.assertFalse(_rate_limited("/api/convert", ip))
        self.assertTrue(_rate_limited("/api/convert", ip))

    def test_ips_distintas_son_independientes(self):
        self.assertFalse(_rate_limited("/api/search", "a"))
        self.assertFalse(_rate_limited("/api/search", "b"))
        # saturar una no afecta a la otra
        for _ in range(20):
            _rate_limited("/api/search", "a")
        self.assertTrue(_rate_limited("/api/search", "a"))
        self.assertFalse(_rate_limited("/api/search", "b"))

    def test_search_429_al_exceder(self):
        ip = "3.3.3.3"
        handler, state = make_handler(
            "/api/search", body=json.dumps({"query": "jazz"}).encode(), headers={})
        handler.client_address = (ip, 1)
        # saturar el ip
        with mock.patch("server.resolver.search", return_value=[]):
            for _ in range(15):
                h2, _s2 = make_handler("/api/search", body=json.dumps({"query": "jazz"}).encode())
                h2.client_address = (ip, 1)
                h2.do_POST()
            # la 16a debe dar 429
            handler.do_POST()
        self.assertEqual(state["code"], 429)


class ConversionsEvictionTest(unittest.TestCase):
    def setUp(self):
        from server import _conversions, _conv_lock
        with _conv_lock:
            _conversions.clear()

    def test_evita_evictar_convert_en_curso(self):
        from server import _conversions
        # llema el dict con 50 terminadas + 1 en curso (51 > 50)
        with server_mod._conv_lock:
            for i in range(server_mod._MAX_CONVERSIONS):
                _conversions[f"done{i}"] = {"status": "ready"}
            _conversions["in_progress"] = {"status": "converting"}
        _evict_conversions()
        with server_mod._conv_lock:
            self.assertIn("in_progress", _conversions)   # nunca se evicta en curso
            self.assertEqual(len(_conversions), server_mod._MAX_CONVERSIONS)

    def test_evicta_la_terminada_mas_antigua(self):
        from server import _conversions
        # 50 terminadas + 1 error (term) (51 > 50) -> se evicta la INSERTADA
        # PRIMERO (done0), que es la mas antigua terminada; quedan 49 done + error.
        with server_mod._conv_lock:
            for i in range(server_mod._MAX_CONVERSIONS):
                _conversions[f"x{i}"] = {"status": "ready"}
            _conversions["term"] = {"status": "error"}
        _evict_conversions()
        with server_mod._conv_lock:
            self.assertNotIn("x0", _conversions)          # la mas antigua se fue
            self.assertNotIn("in_progress", _conversions) # (no hay en curso aqui)
            self.assertLessEqual(len(_conversions), server_mod._MAX_CONVERSIONS)


class QueueAddMetadataTest(unittest.TestCase):
    @mock.patch("server.resolver.get_info", return_value={
        "id": "dV", "title": "Titulo Real del Video", "duration": 777, "uploader": "CanalReal"})
    def test_queue_add_resuelve_metadata_cuando_title_placeholder(self, mock_info):
        handler, state = make_handler(
            "/api/queue/add",
            body=json.dumps({"video_id": "dV", "title": "YouTube dV"}).encode(),
            headers={})
        handler.do_POST()
        body = json.loads(state["buffer"].getvalue())
        self.assertEqual(state["code"], 200)
        mock_info.assert_called_once_with("dV")

    @mock.patch("server.resolver.get_info", return_value={"id": "v9", "title": "T", "duration": 50})
    def test_queue_add_title_vacio_resuelve(self, mock_info):
        handler, state = make_handler(
            "/api/queue/add", body=json.dumps({"video_id": "v9"}).encode(), headers={})
        handler.do_POST()
        body = json.loads(state["buffer"].getvalue())
        self.assertEqual(state["code"], 200)
        mock_info.assert_called_once_with("v9")

    @mock.patch("server.resolver.get_info", return_value=None)
    def test_queue_add_get_info_falla_mantiene_fallback(self, mock_info):
        player = server_mod.player
        with mock.patch.object(player, "add_queue") as add_q:
            handler, state = make_handler(
                "/api/queue/add", body=json.dumps({"video_id": "f", "title": "YouTube f"}).encode(), headers={})
            handler.do_POST()
        add_q.assert_called_once()
        # con get_info=None, se conserva el title placeholder
        self.assertEqual(add_q.call_args.args[1], "YouTube f")


class RunnerKillGroupTest(unittest.TestCase):
    def test_timeout_lanza_TimeoutExpired(self):
        import subprocess
        import runner
        with self.assertRaises(subprocess.TimeoutExpired):
            runner.run_command(["sleep", "5"], timeout=0.3)

    def test_ok_devuelve_resultado(self):
        import runner
        r = runner.run_command(["echo", "hola"], timeout=5, check=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "hola")

    def test_check_lanza_called_process_error(self):
        import subprocess
        import runner
        with self.assertRaises(subprocess.CalledProcessError):
            runner.run_command(["false"], timeout=5, check=True)


if __name__ == "__main__":
    unittest.main()
