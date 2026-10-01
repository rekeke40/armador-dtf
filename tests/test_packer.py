"""Acomodo de piezas en el rollo (engine/packer.py)."""

import itertools
import random
import time

import pytest

from engine import packer

ANCHO, SEP, MARGEN = 57.0, 0.5, 0.5


def _sin_choques(rects, gap=0.0):
    return not any(packer.overlaps(a, b, gap - 1e-6)
                   for a, b in itertools.combinations(rects, 2))


def _dentro_del_rollo(rects, ancho=ANCHO, margen=MARGEN):
    return all(r["x"] >= margen - 1e-6 and r["x"] + r["w"] <= ancho - margen + 1e-6
               and r["y"] >= margen - 1e-6 for r in rects)


def test_cantidad_exacta_y_sin_encimar():
    items = [{"uid": "a", "id": "logo", "w": 20, "h": 15, "qty": 6},
             {"uid": "b", "id": "foto", "w": 8, "h": 8, "qty": 10},
             {"uid": "c", "id": "escudo", "w": 12, "h": 8.57, "qty": 4}]
    piezas, largo, no_caben = packer.pack(items, ANCHO, SEP, MARGEN)
    assert len(piezas) == 20 and not no_caben
    assert _sin_choques(piezas, SEP)
    assert _dentro_del_rollo(piezas)
    assert largo == pytest.approx(max(p["y"] + p["h"] for p in piezas) + MARGEN)


def test_respeta_la_separacion_entre_piezas():
    items = [{"uid": "a", "id": "x", "w": 9, "h": 9, "qty": 30}]
    piezas, _, _ = packer.pack(items, ANCHO, spacing=1.0, margin=MARGEN)
    assert _sin_choques(piezas, gap=1.0)


def test_gira_la_pieza_que_solo_cabe_acostada():
    items = [{"uid": "a", "id": "banda", "w": 10, "h": 50, "qty": 1}]
    piezas, _, no_caben = packer.pack(items, 40, SEP, MARGEN, allow_rotate=True)
    assert not no_caben and len(piezas) == 1
    assert not piezas[0]["rotated"]           # 10 de ancho ya cabe derecha
    items = [{"uid": "a", "id": "banda", "w": 50, "h": 10, "qty": 1}]
    piezas, _, no_caben = packer.pack(items, 40, SEP, MARGEN, allow_rotate=True)
    assert piezas[0]["rotated"] and piezas[0]["w"] == 10


def test_avisa_la_pieza_que_no_cabe_ni_girada():
    items = [{"uid": "a", "id": "gigante", "w": 80, "h": 70, "qty": 2}]
    piezas, largo, no_caben = packer.pack(items, ANCHO, SEP, MARGEN)
    assert piezas == [] and no_caben == ["gigante"] and largo == 0


def test_las_fijadas_son_obstaculos():
    fija = {"x": 20, "y": 10, "w": 15, "h": 15}
    items = [{"uid": "a", "id": "x", "w": 10, "h": 10, "qty": 25}]
    piezas, _, _ = packer.pack(items, ANCHO, SEP, MARGEN, fixed=[fija])
    assert len(piezas) == 25
    assert _sin_choques(piezas + [fija], SEP)


def test_fijar_una_pieza_no_cuesta_pelicula():
    """El shelf packing anterior perdia 28 cm de rollo al fijar una pieza."""
    items = [{"uid": "a", "id": "x", "w": 20, "h": 13.3, "qty": 12}]
    _, libre, _ = packer.pack(items, ANCHO, SEP, MARGEN)
    piezas, _, _ = packer.pack([{**items[0], "qty": 11}], ANCHO, SEP, MARGEN)
    fija = dict(piezas[4])
    _, con_fija, _ = packer.pack([{**items[0], "qty": 11}], ANCHO, SEP, MARGEN,
                                 fixed=[fija])
    assert con_fija <= libre + 1e-6


def test_es_determinista():
    items = [{"uid": str(i), "id": f"d{i}", "w": 3 + i % 7, "h": 4 + i % 5, "qty": 3}
             for i in range(15)]
    assert packer.pack(items, ANCHO) == packer.pack(items, ANCHO)


def test_aprovechamiento_en_pedido_mixto():
    """Pedido tipico de taller: el arte debe cubrir la mayor parte del rollo."""
    rnd = random.Random(7)
    items = [{"uid": str(i), "id": f"d{i}", "w": rnd.uniform(4, 25),
              "h": rnd.uniform(4, 25), "qty": rnd.randint(1, 8)} for i in range(20)]
    piezas, largo, _ = packer.pack(items, ANCHO, SEP, MARGEN)
    area_arte = sum(p["w"] * p["h"] for p in piezas)
    assert _sin_choques(piezas, SEP)
    assert area_arte / (ANCHO * largo) > 0.70


def test_ochocientas_piezas_en_menos_de_un_segundo():
    items = [{"uid": str(i), "id": f"d{i}", "w": 2 + i % 9, "h": 3 + i % 4, "qty": 40}
             for i in range(20)]
    t = time.perf_counter()
    piezas, _, _ = packer.pack(items, ANCHO, SEP, MARGEN)
    assert len(piezas) == 800
    assert time.perf_counter() - t < 1.0
