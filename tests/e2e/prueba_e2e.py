"""Prueba de punta a punta contra un Armador DTF ya abierto.

Sirve para validar el programa tal como lo recibe el taller: el .exe recien
descomprimido, no el codigo fuente. Habla con el servidor por HTTP igual que
la interfaz, exporta planchas de verdad y revisa el TIFF resultante.

    python tests/e2e/prueba_e2e.py                      # http://127.0.0.1:8765
    python tests/e2e/prueba_e2e.py http://127.0.0.1:8766

Las planchas de prueba se borran al terminar.
"""
import json, os, sys, tempfile, time, uuid, urllib.parse, urllib.request, urllib.error
import tifffile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import muestras  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
MUESTRAS = muestras.generar(tempfile.mkdtemp(prefix="armador_muestras_"))
fallas = []

def ok(cond, msg):
    print(("  OK   " if cond else "  FALLA ") + msg)
    if not cond: fallas.append(msg)

def pedir(ruta, body=None, ctype="application/json", host=None):
    data = json.dumps(body).encode() if (body is not None and ctype == "application/json") else body
    req = urllib.request.Request(BASE + ruta, data=data, method="POST" if data is not None else "GET")
    if data is not None: req.add_header("Content-Type", ctype)
    if host: req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def subir(nombres):
    b = uuid.uuid4().hex
    partes = []
    for n in nombres:
        partes.append(f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{n}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                      + open(os.path.join(MUESTRAS, n), "rb").read() + b"\r\n")
    cuerpo = b"".join(partes) + f"--{b}--\r\n".encode()
    return pedir("/api/upload", cuerpo, f"multipart/form-data; boundary={b}")

print("1. Arranque")
s, r = pedir("/api/ping"); ok(s == 200 and json.loads(r)["armador"], "responde /api/ping")
s, r = pedir("/"); ok(s == 200 and b"Armador DTF" in r, "sirve la interfaz")
s, r = pedir("/app.js"); ok(s == 200 and len(r) > 1000, "sirve app.js")
s, r = pedir("/api/config"); cfg = json.loads(r)
ok(bool(cfg["cmyk_profile"]), f"perfil CMYK disponible ({cfg['cmyk_profile']})")
ok(cfg["is_server_pc"] and cfg["pin"], "en la PC entra sin PIN")

print("2. Seguridad")
s, r = pedir("/api/config", host="evil.trycloudflare.com"); ok(s == 401, "Host externo pide PIN")
s, r = pedir("/api/login", {"pin": "000000x"}, host="x.trycloudflare.com"); ok(s == 401, "PIN malo rechazado")
s, r = pedir("/api/descargar?dir=C:%5CWindows&name=win.ini"); ok(s == 404, "no deja leer fuera de la carpeta de salida")

print("3. Carga de disenos")
s, r = subir(["logo.png", "foto.jpg", "photoshop_cmyk_blanco.tif", "sin_unidad.tif", "gris16.png", "roto.png"])
ok(s == 200, f"upload HTTP {s}")
res = {d["name"]: d for d in json.loads(r)}
for n in ["logo.png", "foto.jpg", "photoshop_cmyk_blanco.tif", "sin_unidad.tif", "gris16.png"]:
    ok("id" in res[n], f"abre {n}")
ok("error" in res["roto.png"], "archivo roto da error claro, no revienta")
ok(abs(res["logo.png"]["native_w_cm"] - 10.16) < 0.05, f"logo.png a 300dpi = 10.16 cm ({res['logo.png']['native_w_cm']})")
ok(res["sin_unidad.tif"]["native_w_cm"] < 10, f"TIFF sin unidad no mide 15 m ({res['sin_unidad.tif']['native_w_cm']} cm)")
ok(res["logo.png"]["borde_suave"] > 10, f"detecta borde suave ({res['logo.png']['borde_suave']} %)")

items = [{"uid": f"u{i}", "id": res[n]["id"], "w_cm": w, "h_cm": h, "qty": q}
         for i, (n, w, h, q) in enumerate([("logo.png", 20, 15, 6), ("foto.jpg", 8, 8, 10),
                                          ("photoshop_cmyk_blanco.tif", 12, 8.57, 4),
                                          ("gris16.png", 5, 5, 3)])]
hoja = {"width_cm": 57, "spacing_cm": 0.5, "margin_cm": 0.5, "dpi": 300, "choke_px": 1,
        "alpha_threshold": 0, "allow_rotate": True, "mirror": False, "con_blanco": True, "intent": "perceptual"}

print("4. Acomodo")
s, r = pedir("/api/layout", {"items": items, "fixed": [], "sheet": hoja}); lay = json.loads(r)
ok(s == 200 and len(lay["placements"]) == 23, f"23 piezas acomodadas ({len(lay.get('placements', []))})")
ps = lay["placements"]
choques = sum(1 for i in range(len(ps)) for j in range(i+1, len(ps))
              if ps[i]["x"] < ps[j]["x"]+ps[j]["w"] and ps[j]["x"] < ps[i]["x"]+ps[i]["w"]
              and ps[i]["y"] < ps[j]["y"]+ps[j]["h"] and ps[j]["y"] < ps[i]["y"]+ps[i]["h"])
ok(choques == 0, "ninguna pieza encimada")
ok(all(p["x"] >= 0.5 - 1e-6 and p["x"] + p["w"] <= 56.5 + 1e-6 for p in ps), "todo dentro del ancho util")
fija = dict(ps[0]); fija["key"] = "k1"; fija["x"] = 30; fija["y"] = 40
s, r = pedir("/api/layout", {"items": items, "fixed": [fija], "sheet": hoja}); lay2 = json.loads(r)
ok(len(lay2["placements"]) == 23 and sum(p["locked"] for p in lay2["placements"]) == 1, "pieza fijada respetada sin sobrar piezas")
dobles = [dict(fija, key="k%d" % i) for i in range(10)]
s, r = pedir("/api/layout", {"items": items, "fixed": dobles, "sheet": hoja}); lay3 = json.loads(r)
ok(len(lay3["placements"]) == 23 and lay3["liberadas"] == 9, "fijadas repetidas vuelven al monton (cantidad exacta)")
s, r = pedir("/api/layout", {"items": [dict(items[0], qty=10**9)], "fixed": [], "sheet": hoja})
ok(s in (200, 400), f"cantidad absurda no cuelga el servidor (HTTP {s})")

print("5. Vista del canal White")
s, r = pedir("/api/preview-white", {"items": [{"id": res["logo.png"]["id"], "w_cm": 20, "h_cm": 15}], "dpi": 300, "choke_px": 1, "alpha_threshold": 0})
ok(s == 200 and json.loads(r)["masks"], "mascara White generada")

def exportar(extra):
    t = time.time()
    s, r = pedir("/api/export", {"items": items, "fixed": [], "sheet": {**hoja, **extra}})
    return s, json.loads(r), time.time() - t

print("6. Exportacion CMYK + White")
s, ex, t = exportar({})
ok(s == 200 and ex.get("files"), f"exporta ({t:.1f} s) {ex.get('error','')}")
if s == 200:
    f = ex["files"][0]["path"]
    with tifffile.TiffFile(f) as tf:
        p = tf.pages[0]
        ok(p.samplesperpixel == 5 and int(p.photometric) == 5, "TIFF 5 canales CMYK+White")
        ok(int(p.compression) == 32773, "compresion PACKBITS (la que lee MainTop)")
        ok(34675 in p.tags, "perfil ICC incrustado")
        ok(round(p.tags["XResolution"].value[0] / p.tags["XResolution"].value[1]) == 300, "300 dpi declarados")
        arr = p.asarray()
    ok(arr.shape[1] == 6732, f"ancho 57 cm = 6732 px ({arr.shape[1]})")
    ok(arr[..., 4].max() == 255 and arr[..., 4].min() == 0, "canal White con arte y con vacio")
    ok(int(arr[0, 0, :].max()) == 0, "esquina vacia sin tinta")
    ok(not os.path.exists(f + ".parcial"), "no quedan temporales")
    q = urllib.parse.urlencode({"dir": ex["dir"], "name": ex["files"][0]["name"]})
    s, r = pedir("/api/descargar?" + q)
    ok(s == 200 and r[:2] in (b"II", b"MM"), "se descarga desde el celular")
    os.remove(f)

print("7. Exportacion CMYK sin White")
s, ex, t = exportar({"con_blanco": False, "mirror": True, "alpha_threshold": 20})
ok(s == 200, f"exporta sin blanco + espejo + limpiar bordes ({t:.1f} s)")
if s == 200:
    with tifffile.TiffFile(ex["files"][0]["path"]) as tf:
        ok(tf.pages[0].samplesperpixel == 4, "TIFF 4 canales")
    os.remove(ex["files"][0]["path"])

print("8. Quitar diseno")
s, r = pedir("/api/quitar", {"id": res["gris16.png"]["id"]}); ok(s == 200, "quitar diseno")
s, ex, t = exportar({}); ok(s == 200, "exportar tras quitar un diseno no revienta")
if s == 200:
    os.remove(ex["files"][0]["path"])

print("\nRESULTADO:", "TODO OK" if not fallas else f"{len(fallas)} FALLA(S)")
for f in fallas: print("  -", f)
sys.exit(1 if fallas else 0)
