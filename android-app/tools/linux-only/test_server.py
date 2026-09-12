"""Tests de handlers HTTP (server.Handler) y validacion de inputs, sin red.

Simulamos un handler con wfile/headers falsos para probar do_POST
y validacion (query/video_id/nombres) sin levantar el servidor ni tocar YouTube.
"""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import server as server_mod
from server import Handler, err_res, json_res


class FakeHeaders:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)

    def __getitem__(self, k):
        return self._d[k]


def make_handler(path="/", body=b"", headers=None):
    """Crea un Handler real pero con transporte inyectado (wfile + stubs HTTP).

    Se parchean send_response/send_header/end_headers para capturar el codigo
    y los headers sin necesitar un socket real. Inyecta Content-Length a partir
    del body para que do_POST pueda leerlo (igual que un request HTTP real).
    """
    state = {"code": None, "headers": {}, "buffer": io.BytesIO()}
    h = Handler.__new__(Handler)
    h.path = path
    hdrs = dict(headers or {})
    if body:
        hdrs.setdefault("Content-Length", str(len(body)))
    h.headers = FakeHeaders(hdrs)
    h.rfile = io.BytesIO(body)
    h.wfile = state["buffer"]
    h.client_address = ("127.0.0.1", 12345)
    h.send_response = lambda c: state.__setitem__("code", c)
    h.send_header = lambda k, v: state["headers"].__setitem__(k, v)
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    return h, state


class JsonHelpersTest(unittest.TestCase):
    def test_json_res_estructura(self):
        status, headers, body = json_res({"ok": True}, 200)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), {"ok": True})

    def test_err_res_default_400(self):
        status, _, body = err_res("mal")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"ok": False, "error": "mal"})


class ServerValidationTest(unittest.TestCase):
    @mock.patch.object(server_mod.resolver, "search", return_value=[{"id": "x", "title": "X"}])
    def test_search_requiere_query(self, mock_search):
        handler, state = make_handler("/api/search", body=json.dumps({}).encode())
        handler.do_POST()
        self.assertEqual(state["code"], 400)
        mock_search.assert_not_called()

    @mock.patch.object(server_mod.resolver, "search", return_value=[])
    def test_search_ok_con_query(self, mock_search):
        handler, state = make_handler(
            "/api/search", body=json.dumps({"query": "jazz"}).encode())
        handler.do_POST()
        self.assertEqual(state["code"], 200)
        mock_search.assert_called_once()
        body = state["buffer"].getvalue().decode()
        self.assertIn('"results"', body)

    @mock.patch.object(server_mod.player, "play", return_value=False)
    def test_play_falla_devuelve_500(self, mock_play):
        server_mod.player.last_error = "no stream"
        handler, state = make_handler(
            "/api/play", body=json.dumps({"video_id": "abc"}).encode())
        handler.do_POST()
        self.assertEqual(state["code"], 500)
        mock_play.assert_called_once_with("abc")

    @mock.patch.object(server_mod.player, "play", return_value=True)
    def test_play_requiere_video_id(self, mock_play):
        handler, state = make_handler("/api/play", body=json.dumps({}).encode())
        handler.do_POST()
        self.assertEqual(state["code"], 400)
        mock_play.assert_not_called()

    def _serve_mp3(self, video_id, title=None):
        """Ejecuta _serve_mp3 con un mp3 temporal y devuelve Content-Disposition."""
        path = f"/api/download/mp3/{video_id}" + (f"?title={title}" if title else "")
        handler, state = make_handler(path, headers={})
        mp3 = os.path.join(server_mod.MP3_DIR, f"{video_id}.mp3")
        with open(mp3, "wb") as f:
            f.write(b"ID3...audio")
        try:
            handler._serve_mp3(video_id)
            return state["headers"].get("Content-Disposition", "")
        finally:
            if os.path.isfile(mp3):
                os.unlink(mp3)

    def test_nombre_archivo_seguro(self):
        disc = self._serve_mp3("vid123", 'Cancion/Con;Espacios! y "Comillas"')
        self.assertIn("filename=", disc)
        inner = disc.split("filename=")[1].strip('"').replace(".mp3", "")
        for ch in inner:
            self.assertTrue(ch.isalnum() or ch in "._-")

    def test_nombre_archivo_con_injection_se_limpia(self):
        disc = self._serve_mp3("vid456", "secure;chmod 777")
        inner = disc.split("filename=")[1].strip('"')
        self.assertNotIn(";", inner)
        self.assertNotIn("\n", inner)



class IpEndpointTest(unittest.TestCase):
    def _ip_valida(self, ip):
        if not ip or not isinstance(ip, str):
            return False
        partes = ip.split(".")
        if len(partes) != 4:
            return False
        for x in partes:
            if not x.isdigit():
                return False
            if not (0 <= int(x) <= 255):
                return False
        return True

    def test_ip_endpoint_responde(self):
        """GET /api/ip responde JSON con ip de formato valido (sin red)."""
        import server as srv
        handler, state = make_handler("/api/ip", headers={})
        with mock.patch.object(srv, "_detect_public_ip", return_value="127.0.0.1"):
            handler.do_GET()
        body = state["buffer"].getvalue().decode()
        d = json.loads(body)
        self.assertTrue(d.get("ok"))
        self.assertTrue(self._ip_valida(d.get("ip")))


if __name__ == "__main__":
    unittest.main()
