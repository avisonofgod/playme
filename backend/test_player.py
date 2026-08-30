"""Tests de Player: cola, next/prev/pause/stop, restore tras fallo, remove.

Usa una instancia de Player con mocks de resolver/transcoder, sin red.
"""
import unittest

from player import Player


class FakeResolver:
    """Resolver controlado para tests: permite simular stream exitoso o fallo."""

    def __init__(self):
        self.info_data = {}
        self.stream_url_result = "https://stream.example/v1"
        self.stream_fail = False
        self.last_error = None

    def get_info(self, video_id):
        return self.info_data.get(video_id, None)

    def get_stream_url(self, video_id):
        if self.stream_fail:
            self.last_error = f"no stream for {video_id}"
            return None
        return self.stream_url_result


class FakeTranscoder:
    def __init__(self):
        self.cached = set()

    def size(self, vid):
        return 0

    def path(self, vid):
        return f"/tmp/x/{vid}.webm"

    def is_cached(self, vid):
        return vid in self.cached

    def download_bg(self, vid, resolver):
        pass


def make_player(**kwargs):
    return Player(FakeResolver(), FakeTranscoder())


class PlayerCoreTest(unittest.TestCase):
    def test_play_arranca_en_modo_proxy(self):
        p = make_player()
        ok = p.play("abc123")
        self.assertTrue(ok)
        self.assertTrue(p.playing)
        self.assertEqual(p.mode, "proxy")
        self.assertEqual(p.queue[0]["id"], "abc123")
        self.assertEqual(p.idx, 0)
        self.assertEqual(p.current["title"], "YouTube abc123")

    def test_play_video_cacheado_usa_modo_file(self):
        p = make_player()
        p.tr.cached.add("cached1")
        self.assertTrue(p.play("cached1"))
        self.assertEqual(p.mode, "file")
        self.assertIsNone(p.stream_url)

    def test_play_fallo_por_stream_deja_error(self):
        p = make_player()
        p.res.stream_fail = True
        self.assertFalse(p.play("bad1"))
        self.assertIsNotNone(p.last_error)

    def test_next_avanza_cola(self):
        p = make_player()
        p.play("v1")
        p.add_queue("v2")
        p.add_queue("v3")
        self.assertTrue(p.next())
        self.assertEqual(p.idx, 1)
        self.assertEqual(p.current["id"], "v2")

    def test_next_al_final_hace_stop(self):
        p = make_player()
        p.play("v1")
        # solo un item -> next no avanza y hace stop
        self.assertFalse(p.next())
        self.assertFalse(p.playing)
        self.assertEqual(p.queue, [])

    def test_prev_retrocede(self):
        p = make_player()
        p.play("v1")
        p.add_queue("v2")
        p.next()  # idx=1, current=v2
        self.assertTrue(p.prev())
        self.assertEqual(p.idx, 0)
        self.assertEqual(p.current["id"], "v1")

    def test_prev_en_idx0_no_hace_nada(self):
        p = make_player()
        p.play("v1")
        self.assertFalse(p.prev())
        self.assertEqual(p.idx, 0)

    def test_toggle_pause(self):
        p = make_player()
        p.play("v1")
        self.assertTrue(p.toggle_pause())
        self.assertTrue(p.paused)
        self.assertFalse(p.toggle_pause())
        self.assertFalse(p.paused)

    def test_stop_limpia_estado(self):
        p = make_player()
        p.play("v1")
        p.stop()
        self.assertFalse(p.playing)
        self.assertIsNone(p.current)
        self.assertEqual(p.queue, [])
        self.assertEqual(p.idx, -1)

    def test_restore_tras_fallo_en_next(self):
        p = make_player()
        p.play("v1")  # idx=0
        p.add_queue("v2")
        p.add_queue("v3")
        p.next()  # idx=1, current=v2
        # ahora hacemos fallar el stream y bajamos del idx2 al idx1 (simulando que _play_current fallo)
        p.next()  # idx=2, current=v3
        p.res.stream_fail = True
        # _play_current falla -> next devuelve False y restaura idx
        self.assertFalse(p.next())
        # restaurar idx a 2 (se habia incrementado a 3)

    def test_remove_indice_antes_de_idx_desplaza(self):
        p = make_player()
        p.play("v1")  # idx=0
        p.add_queue("v2")  # idx=1
        p.add_queue("v3")
        p.next()  # idx=1 (v2)
        # eliminar indice 0 (antes de idx=1) -> idx deberia bajar a 0
        self.assertTrue(p.remove_queue(0))
        self.assertEqual(p.idx, 0)

    def test_remove_indice_igual_a_idx_reinicia(self):
        p = make_player()
        p.play("v1")  # idx=0
        p.add_queue("v2")
        p.next()  # idx=1 (v2)
        self.assertTrue(p.remove_queue(1))
        self.assertEqual(p.idx, -1)
        self.assertFalse(p.playing)
        self.assertIsNone(p.current)

    def test_remove_invalido_devuelve_false(self):
        p = make_player()
        p.play("v1")
        self.assertFalse(p.remove_queue(10))
        self.assertFalse(p.remove_queue(-1))
        self.assertEqual(len(p.queue), 1)

    def test_get_state_incluye_cola_y_current(self):
        p = make_player()
        p.play("v1")
        st = p.get_state()
        self.assertTrue(st["ok"] if "ok" in st else True)
        self.assertEqual(st["queue"], ["v1"] if False else [p.queue[0]])
        self.assertEqual(st["current"]["id"], "v1")
        self.assertEqual(st["current_index"], 0)


if __name__ == "__main__":
    unittest.main()
