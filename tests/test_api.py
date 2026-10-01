"""Servidor: seguridad, carga, acomodo y exportacion de punta a punta."""

import os

import pytest
import tifffile

from muestras import NOMBRES

TUNEL = {"Host": "abc.trycloudflare.com"}


# --------------------------------------------------------------------------
# Seguridad
# --------------------------------------------------------------------------

def test_en_la_pc_entra_sin_pin(cliente):
    r = cliente.get("/api/config")
    assert r.status_code == 200
    assert r.json["is_server_pc"] and r.json["pin"] == "246810"


@pytest.mark.parametrize("cabeceras", [
    TUNEL,                                         # Host del tunel
    {"X-Forwarded-For": "8.8.8.8"},                # detras de un proxy
    {"CF-Connecting-IP": "8.8.8.8"},               # cabecera de Cloudflare
])
def test_lo_que_llega_por_la_red_pide_pin(cliente, cabeceras):
    r = cliente.get("/api/config", headers=cabeceras)
    assert r.status_code == 401


def test_desde_otra_ip_pide_pin(cliente):
    r = cliente.get("/api/config", environ_base={"REMOTE_ADDR": "192.168.1.50"},
                    headers={"Host": "192.168.1.10:8765"})
    assert r.status_code == 401


def test_pin_correcto_abre_sesion(cliente):
    r = cliente.post("/api/login", json={"pin": "246810"}, headers=TUNEL)
    assert r.status_code == 200
    r = cliente.get("/api/config", headers=TUNEL)
    assert r.status_code == 200
    assert not r.json["is_server_pc"] and r.json["pin"] is None   # no se filtra


def test_fuerza_bruta_se_bloquea(cliente):
    for _ in range(5):
        assert cliente.post("/api/login", json={"pin": "000000"},
                            headers=TUNEL).status_code == 401
    r = cliente.post("/api/login", json={"pin": "246810"}, headers=TUNEL)
    assert r.status_code == 429


def test_no_deja_leer_fuera_de_la_carpeta_de_salida(cliente):
    r = cliente.get("/api/descargar", query_string={"dir": "C:\\Windows", "name": "win.ini"})
    assert r.status_code == 404
    r = cliente.get("/api/descargar", query_string={"name": "../app.py"})
    assert r.status_code == 404


def test_abrir_carpeta_y_tunel_solo_desde_la_pc(cliente):
    cliente.post("/api/login", json={"pin": "246810"}, headers=TUNEL)
    assert cliente.post("/api/abrir-carpeta", headers=TUNEL).status_code in (401, 403)
    assert cliente.post("/api/tunel", json={"accion": "abrir"},
                        headers=TUNEL).status_code in (401, 403)


# --------------------------------------------------------------------------
# Flujo de produccion
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def disenos(armador, muestras):
    cliente = armador.app.test_client()
    archivos = [(open(muestras / n, "rb"), n) for n in NOMBRES]
    try:
        r = cliente.post("/api/upload", data={"files": archivos},
                         content_type="multipart/form-data")
    finally:
        for f, _ in archivos:
            f.close()
    assert r.status_code == 200
    return {d["name"]: d for d in r.json}


HOJA = {"width_cm": 57, "spacing_cm": 0.5, "margin_cm": 0.5, "dpi": 100,
        "choke_px": 1, "alpha_threshold": 0, "allow_rotate": True,
        "mirror": False, "con_blanco": True, "intent": "perceptual"}


def _pedido(d):
    filas = [("logo.png", 20, 15, 6), ("foto.jpg", 8, 8, 10),
             ("photoshop_cmyk_blanco.tif", 12, 8.57, 4), ("gris16.png", 5, 5, 3)]
    return [{"uid": f"u{i}", "id": d[n]["id"], "w_cm": w, "h_cm": h, "qty": q}
            for i, (n, w, h, q) in enumerate(filas)]


def test_carga_abre_los_formatos_del_cliente(disenos):
    for n in NOMBRES[:-1]:
        assert "id" in disenos[n], disenos[n]
    assert "error" in disenos["roto.png"]
    assert disenos["logo.png"]["native_w_cm"] == pytest.approx(10.16, abs=0.05)
    assert disenos["sin_unidad.tif"]["native_w_cm"] < 10
    assert disenos["logo.png"]["borde_suave"] > 10
    assert disenos["logo.png"]["thumb"].startswith("data:image/png;base64,")


def test_rechaza_formatos_no_soportados(cliente, tmp_path):
    f = tmp_path / "pedido.pdf"
    f.write_bytes(b"%PDF-1.4")
    with open(f, "rb") as fh:
        r = cliente.post("/api/upload", data={"files": [(fh, "pedido.pdf")]},
                         content_type="multipart/form-data")
    assert "error" in r.json[0]


def test_acomodo_con_la_cantidad_pedida(cliente, disenos):
    r = cliente.post("/api/layout", json={"items": _pedido(disenos), "fixed": [], "sheet": HOJA})
    assert r.status_code == 200
    assert len(r.json["placements"]) == 23 and r.json["pedidas"] == 23


def test_fijadas_de_mas_vuelven_al_monton(cliente, disenos):
    items = _pedido(disenos)
    lay = cliente.post("/api/layout", json={"items": items, "fixed": [], "sheet": HOJA}).json
    fija = {**lay["placements"][0], "x": 30, "y": 40}
    dobles = [{**fija, "key": f"k{i}"} for i in range(10)]
    r = cliente.post("/api/layout", json={"items": items, "fixed": dobles, "sheet": HOJA}).json
    assert len(r["placements"]) == 23
    assert sum(p["locked"] for p in r["placements"]) == 1 and r["liberadas"] == 9


def test_cantidades_absurdas_tienen_tope(cliente, disenos, armador):
    fila = {**_pedido(disenos)[1], "qty": 10 ** 9}
    r = cliente.post("/api/layout", json={"items": [fila], "fixed": [], "sheet": HOJA})
    assert r.status_code == 200
    assert len(r.json["placements"]) == armador.MAX_COPIAS
    fila = {**fila, "qty": 1, "w_cm": 100000}
    r = cliente.post("/api/layout", json={"items": [fila], "fixed": [], "sheet": HOJA})
    assert r.json["placements"] == []


def test_vista_previa_del_canal_white(cliente, disenos):
    r = cliente.post("/api/preview-white", json={
        "items": [{"id": disenos["logo.png"]["id"], "w_cm": 20, "h_cm": 15}],
        "dpi": 99999, "choke_px": 999, "alpha_threshold": 1})
    assert r.status_code == 200 and r.json["masks"]


@pytest.mark.parametrize("con_blanco,canales", [(True, 5), (False, 4)])
def test_exporta_la_plancha_para_el_rip(cliente, disenos, salida, con_blanco, canales):
    hoja = {**HOJA, "con_blanco": con_blanco}
    r = cliente.post("/api/export", json={"items": _pedido(disenos), "fixed": [], "sheet": hoja})
    assert r.status_code == 200, r.json
    archivo = r.json["files"][0]
    assert os.path.dirname(archivo["path"]) == str(salida)
    with tifffile.TiffFile(archivo["path"]) as tf:
        p = tf.pages[0]
        assert p.samplesperpixel == canales and int(p.compression) == 32773
        assert p.imagewidth == round(57 / 2.54 * 100)
    assert not list(salida.glob("*.parcial"))
    descarga = cliente.get("/api/descargar", query_string={"name": archivo["name"]})
    assert descarga.status_code == 200 and descarga.data[:2] in (b"II", b"MM")
    descarga.close()


def test_quitar_un_diseno_no_rompe_la_exportacion(cliente, armador, muestras):
    with open(muestras / "foto.jpg", "rb") as fh:
        d = cliente.post("/api/upload", data={"files": [(fh, "foto.jpg")]},
                         content_type="multipart/form-data").json[0]
    items = [{"uid": "q", "id": d["id"], "w_cm": 5, "h_cm": 5, "qty": 2}]
    assert cliente.post("/api/quitar", json={"id": d["id"]}).status_code == 200
    r = cliente.post("/api/export", json={"items": items, "fixed": [], "sheet": HOJA})
    assert r.status_code == 400 and "error" in r.json
