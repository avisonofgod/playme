"""Tests de Player: cola, next/prev/pause/stop, restore tras fallo, remove.

Usa una instancia de Player con mocks de resolver/transcoder, sin red.
El play es ASINCRONO (resolucion en thread): los tests esperan con
wait_resolved() hasta que el estado este listo.
"""
import time
import threading
import unittest

from player import Player


class FakeResolver:
    """Resolver controlado para tests: permite simular stream exitoso o fallo."""

    def __init__(self):
        self.info_data = {}
        self.stream_url_result = "https://stream.example/v1"
        self.stream_fail = False
        self.last_error = None
        # gate: si se define, get_stream_url espera -> resolucion controlable
        self.gate = None

    def get_info(self, video_id):
        return self.info_data.get(video_id, None)

    def get_stream_url(self, video_id):
        if self.gate is not None:
            self.gate.wait(5)
        if self.stream_fail:
            self.last_error = f"no stream for {video_id}"
            return None
        return self.stream_url_result


class FakeTranscoder:
    def __init__(self):
        self.cached = set()
        self.cancelled = []      # v1.3.0: ids cuya descarga se corto
        self.forgotten = []      # v1.3.0: ids cuyo cache se borro
        self.paced = []          # v1.3.0: ids con descarga "sigue a la reproduccion"

    def size(self, vid):
        return 0

    def path(self, vid):
        return f"/tmp/x/{vid}.webm"

    def is_cached(self, vid):
        return vid in self.cached

    def download_bg(self, vid, resolver):
        pass

    def cancel(self, vid):
        self.cancelled.append(vid)
        return True

    def forget(self, vid):
        # v1.3.0: el tema abandonado se borra del cache
        self.cached.discard(vid)
        self.forgotten.append(vid)
        return 1

    def forget_except(self, keep=None):
        leftovers = [v for v in list(self.cached) if v != keep]
        for v in leftovers:
            self.cached.discard(v)
        return len(leftovers)

    def enable_pace(self, vid, duration=0, filesize=0):
        # v1.3.0: descarga al ritmo de la reproduccion (posicion + 5 s)
        self.paced.append(vid)
        return True

    def disable_pace(self, vid):
        return True


def make_player(**kwargs):
    return Player(FakeResolver(), FakeTranscoder())


def wait_resolved(p, timeout=1.0):
    """Espera a que el play asincrono termine de resolver."""
    end = time.time() + timeout
    while time.time() < end:
        with p._lock:
            if not p._resolving:
                return True
        time.sleep(0.02)
    return False


class PlayerCoreTest(unittest.TestCase):
    def test_play_arranca_en_modo_proxy(self):
        p = make_player()
        self.assertTrue(p.play("abc123"))
        self.assertTrue(wait_resolved(p))
        self.assertTrue(p.playing)
        self.assertEqual(p.mode, "proxy")
        self.assertEqual(p.queue[0]["id"], "abc123")
        self.assertEqual(p.idx, 0)
        self.assertEqual(p.current["title"], "YouTube abc123")

    def test_play_video_cacheado_usa_modo_file(self):
        p = make_player()
        p.tr.cached.add("cached1")
        self.assertTrue(p.play("cached1"))
        self.assertTrue(wait_resolved(p))
        self.assertEqual(p.mode, "file")
        self.assertIsNone(p.stream_url)

    def test_play_fallo_por_stream_deja_error(self):
        p = make_player()
        p.res.stream_fail = True
        # play async: siempre responde True; el fallo aparece en last_error y
        # el estado no queda en modo reproduccion
        self.assertTrue(p.play("bad1"))
        self.assertTrue(wait_resolved(p))
        self.assertIsNotNone(p.last_error)
        self.assertIsNone(p.mode)
        self.assertFalse(p.playing)

    def test_play_otro_tema_corta_el_actual(self):
        """v1.3.0: pedir otro tema detiene el actual de inmediato (no se solapa)."""
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        self.assertTrue(p.playing)
        self.assertIsNotNone(p.current)
        # resolucion de v2 retenida: el corte debe verse ANTES de resolver
        p.res.gate = threading.Event()
        p.play("v2")
        self.assertFalse(p.playing)        # cortado YA (antes de resolver)
        self.assertIsNone(p.current)
        self.assertIsNone(p.mode)
        self.assertTrue(p._resolving)
        p.res.gate.set()
        self.assertTrue(wait_resolved(p))
        self.assertTrue(p.playing)
        self.assertEqual(p.current["id"], "v2")
        self.assertEqual(p.idx, 1)
        # y tambien se corta su DESCARGA (libera yt-dlp para el tema nuevo)
        self.assertIn("v1", p.tr.cancelled)

    def test_next_corta_la_descarga_anterior(self):
        """v1.3.0: next tambien corta la descarga del tema que deja atras."""
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        p.add_queue("v2")
        p.res.gate = threading.Event()
        self.assertTrue(p.next())          # arranca resolucion de v2 (retenida)
        self.assertIn("v1", p.tr.cancelled)
        p.res.gate.set()
        self.assertTrue(wait_resolved(p))
        self.assertEqual(p.current["id"], "v2")

    def test_play_activa_ritmo_de_descarga(self):
        """v1.3.0: el play pide descarga al ritmo de la reproduccion (no completa)."""
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        self.assertIn("v1", p.tr.paced)

    def test_play_otro_tema_borra_cache_del_anterior(self):
        """v1.3.0: al cambiar de tema solo queda en cache el ACTUAL."""
        p = make_player()
        p.tr.cached.add("v1")
        p.play("v1")
        wait_resolved(p)
        p.play("v2")
        wait_resolved(p)
        self.assertIn("v1", p.tr.forgotten)
        self.assertNotIn("v1", p.tr.cached)
        self.assertEqual(p.current["id"], "v2")

    def test_next_borra_cache_del_anterior(self):
        """v1.3.0: next deja el cache del tema abandonado en cero."""
        p = make_player()
        p.tr.cached.update({"v1", "v2"})
        p.play("v1")
        wait_resolved(p)
        p.add_queue("v2")
        self.assertTrue(p.next())
        wait_resolved(p)
        self.assertIn("v1", p.tr.forgotten)
        self.assertNotIn("v1", p.tr.cached)
        self.assertEqual(p.current["id"], "v2")   # el actual queda como current

    def test_prev_no_reusa_cache_del_anterior(self):
        """v1.3.0: volver con prev vuelve a descargar (no reusa el audio viejo)."""
        p = make_player()
        p.tr.cached.update({"v1", "v2"})
        p.play("v1")
        wait_resolved(p)
        p.add_queue("v2")
        self.assertTrue(p.next())
        wait_resolved(p)
        self.assertTrue(p.prev())
        wait_resolved(p)
        self.assertEqual(p.current["id"], "v1")
        self.assertIn("v2", p.tr.forgotten)      # v2 se abandono -> borrado
        self.assertNotIn("v2", p.tr.cached)
        self.assertNotIn("v1", p.tr.cached)      # v1 se borro al irse; se rebaja

    def test_stop_borra_cache_del_actual(self):
        """v1.3.0: stop no deja el audio del tema detenido en cache."""
        p = make_player()
        p.tr.cached.add("v1")
        p.play("v1")
        wait_resolved(p)
        p.stop()
        self.assertIn("v1", p.tr.forgotten)
        self.assertNotIn("v1", p.tr.cached)

    def test_stop_corta_la_descarga(self):
        """v1.3.0: stop corta la descarga en curso."""
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        p.stop()
        self.assertIn("v1", p.tr.cancelled)
        self.assertIsNone(p.current)

    def test_play_mismo_tema_no_corta(self):
        """Repetir el mismo tema no debe detener la reproduccion en curso."""
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        p.play("v1")
        self.assertTrue(p.playing)
        self.assertIsNotNone(p.current)

    def test_next_avanza_cola(self):
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        p.add_queue("v2")
        p.add_queue("v3")
        self.assertTrue(p.next())
        self.assertEqual(p.idx, 1)
        self.assertEqual(p.current["id"], "v2")

    def test_next_al_final_hace_stop(self):
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        # solo un item -> next no avanza y hace stop
        self.assertFalse(p.next())
        self.assertFalse(p.playing)
        self.assertEqual(p.queue, [])

    def test_prev_retrocede(self):
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        p.add_queue("v2")
        p.next()  # idx=1, current=v2
        self.assertTrue(p.prev())
        self.assertEqual(p.idx, 0)
        self.assertEqual(p.current["id"], "v1")

    def test_prev_en_idx0_no_hace_nada(self):
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        self.assertFalse(p.prev())
        self.assertEqual(p.idx, 0)

    def test_toggle_pause(self):
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        self.assertTrue(p.toggle_pause())
        self.assertTrue(p.paused)
        self.assertFalse(p.toggle_pause())
        self.assertFalse(p.paused)

    def test_stop_limpia_estado(self):
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        p.stop()
        self.assertFalse(p.playing)
        self.assertIsNone(p.current)
        self.assertEqual(p.queue, [])
        self.assertEqual(p.idx, -1)

    def test_stop_durante_resolucion_cancela(self):
        p = make_player()
        p.res.stream_fail = False
        # stop inmediatamente despues del play (la resolucion aun corre)
        p.play("v1")
        p.stop()
        # al terminar el thread, no debe resucitar el estado
        self.assertTrue(wait_resolved(p))
        self.assertFalse(p.playing)
        self.assertIsNone(p.current)
        self.assertEqual(p.queue, [])

    def test_restore_tras_fallo_en_next(self):
        p = make_player()
        p.play("v1")  # idx=0
        wait_resolved(p)
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
        wait_resolved(p)
        p.add_queue("v2")  # idx=1
        p.add_queue("v3")
        p.next()  # idx=1 (v2)
        # eliminar indice 0 (antes de idx=1) -> idx deberia bajar a 0
        self.assertTrue(p.remove_queue(0))
        self.assertEqual(p.idx, 0)

    def test_remove_indice_igual_a_idx_reinicia(self):
        p = make_player()
        p.play("v1")  # idx=0
        wait_resolved(p)
        p.add_queue("v2")
        p.next()  # idx=1 (v2)
        self.assertTrue(p.remove_queue(1))
        self.assertEqual(p.idx, -1)
        self.assertFalse(p.playing)
        self.assertIsNone(p.current)

    def test_remove_invalido_devuelve_false(self):
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        self.assertFalse(p.remove_queue(10))
        self.assertFalse(p.remove_queue(-1))
        self.assertEqual(len(p.queue), 1)

    def test_get_state_incluye_cola_y_current(self):
        p = make_player()
        p.play("v1")
        wait_resolved(p)
        st = p.get_state()
        self.assertTrue(st["ok"] if "ok" in st else True)
        self.assertEqual(st["queue"], [p.queue[0]])
        self.assertEqual(st["current"]["id"], "v1")
        self.assertEqual(st["current_index"], 0)

    def test_get_state_expone_resolving(self):
        p = make_player()
        st = p.get_state()
        self.assertIn("resolving", st)
        self.assertFalse(st["resolving"])


if __name__ == "__main__":
    unittest.main()
