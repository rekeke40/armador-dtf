"""Armador DTF — servidor local.

Sirve la interfaz para armar planchas DTF. Escucha en todas las interfaces de
red para poder usarse tambien desde el celular o el iPad de la misma WiFi.
"""

import base64
import hmac
import io
import ipaddress
import json
import os
import secrets
import socket
import time
import uuid
import webbrowser
from collections import Counter, defaultdict
from functools import wraps
from pathlib import Path
from threading import Timer

from flask import (Flask, jsonify, redirect, request, send_from_directory,
                   session, url_for)
from werkzeug.utils import secure_filename

from engine import packer, exporter

APP_DIR = Path(__file__).parent
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
for _old in UPLOAD_DIR.iterdir():  # limpiar cargas de sesiones anteriores
    _old.unlink(missing_ok=True)

PORT = int(os.environ.get("ARMADOR_PORT", 8765))
HOST = os.environ.get("ARMADOR_HOST", "0.0.0.0")

app = Flask(__name__, static_folder=str(APP_DIR / "static"), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024


# ==========================================================================
# Seguridad
#
# El programa puede publicarse en internet con un tunel de Cloudflare, asi que
# no puede confiar en "la red local es de confianza". Tres reglas:
#   1. Todo pasa por un PIN. La sesion dura 30 dias por dispositivo.
#   2. Los archivos solo se leen y escriben dentro de la carpeta de salida.
#   3. Una peticion que llega por el tunel NUNCA cuenta como local, aunque
#      cloudflared se conecte desde 127.0.0.1.
# ==========================================================================

CONFIG_FILE = APP_DIR / "config.json"


def load_config():
    """Lee (o crea) la configuracion local con el PIN y la llave de sesion."""
    if CONFIG_FILE.is_file():
        try:
            cfg = json.loads(CONFIG_FILE.read_text("utf-8"))
            if cfg.get("pin") and cfg.get("secret_key"):
                return cfg
        except (json.JSONDecodeError, OSError):
            pass
    cfg = {"pin": f"{secrets.randbelow(10**6):06d}",
           "secret_key": secrets.token_hex(32)}
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), "utf-8")
    return cfg


CONFIG = load_config()
PIN = str(os.environ.get("ARMADOR_PIN") or CONFIG["pin"])
app.secret_key = CONFIG["secret_key"]
app.config.update(PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
                  SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax")

_login_fails = defaultdict(list)   # ip -> timestamps de intentos fallidos


def client_is_local():
    """True solo si el navegador esta en esta misma PC.

    cloudflared se conecta desde 127.0.0.1, asi que la direccion de origen no
    basta: si vienen cabeceras de proxy, la peticion llego desde fuera.
    """
    if request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For"):
        return False
    return request.remote_addr in ("127.0.0.1", "::1")


def client_key():
    return (request.headers.get("CF-Connecting-IP")
            or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            or request.remote_addr or "?")


def login_blocked(key):
    """Bloqueo temporal tras varios intentos fallidos (anti fuerza bruta)."""
    now = time.time()
    recent = [t for t in _login_fails[key] if now - t < 900]
    _login_fails[key] = recent
    if len(recent) < 5:
        return 0
    espera = min(300, 30 * (len(recent) - 4))
    restante = espera - (now - recent[-1])
    return max(0, int(restante))


@app.before_request
def require_login():
    ruta = request.path
    if ruta in ("/login", "/api/login", "/login.html", "/style.css") or ruta.startswith("/static/"):
        return None
    if session.get("ok"):
        return None
    if ruta.startswith("/api/"):
        return jsonify({"error": "Sesión expirada", "login": True}), 401
    return redirect(url_for("login_page"))


@app.get("/login")
def login_page():
    return send_from_directory(app.static_folder, "login.html")


@app.post("/api/login")
def api_login():
    key = client_key()
    espera = login_blocked(key)
    if espera:
        return jsonify({"error": f"Demasiados intentos. Espera {espera} s."}), 429
    pin = str((request.json or {}).get("pin", ""))
    if hmac.compare_digest(pin, PIN):
        _login_fails.pop(key, None)
        session.permanent = True
        session["ok"] = True
        return jsonify({"ok": True})
    _login_fails[key].append(time.time())
    return jsonify({"error": "PIN incorrecto"}), 401


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})

DESIGNS = {}      # id -> {path, name, w_px, h_px}
MASK_CACHE = {}   # (maskKey, dpi, choke, thr) -> dataurl

ALLOWED = {".png", ".tif", ".tiff", ".jpg", ".jpeg", ".webp", ".bmp"}


def lan_ips():
    """IPs de esta PC en la red local, la mas probable primero.

    Ojo: con una VPN activa (NordVPN, etc.) la ruta por defecto apunta al tunel,
    asi que no basta con preguntar "por donde saldria a internet" — esa direccion
    no es alcanzable desde el celular. Por eso se enumeran todas y se ordenan:
    192.168.x.x primero (router tipico), y al final los adaptadores virtuales,
    que casi siempre se asignan a si mismos el .1
    """
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # no envia nada: solo resuelve la ruta
        found.add(s.getsockname()[0])
    except Exception:
        pass
    finally:
        s.close()

    ranked = []
    for ip in found:
        try:
            a = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if not a.is_private or a.is_loopback or a.is_link_local:
            continue
        rank = (0 if ip.startswith("192.168.") else 1 if ip.startswith("172.") else 2,
                1 if ip.rsplit(".", 1)[1] == "1" else 0, ip)
        ranked.append((rank, ip))
    ranked.sort()
    return [ip for _, ip in ranked] or ["127.0.0.1"]


def default_output_dir():
    for cand in [Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"]:
        if cand.is_dir():
            return str(cand / "Planchas DTF")
    return str(Path.home() / "Planchas DTF")


OUTPUT_BASE = Path(os.environ.get("ARMADOR_SALIDA") or default_output_dir()).resolve()

TUNNEL_FILE = APP_DIR / "tunnel_url.txt"


def read_tunnel_url():
    """URL publica del tunel, que el lanzador escribe al arrancar cloudflared."""
    try:
        # utf-8-sig: PowerShell escribe el archivo con BOM y sin esto la URL
        # llegaria con un caracter invisible delante
        url = TUNNEL_FILE.read_text("utf-8-sig").strip()
        return url if url.startswith("https://") else None
    except OSError:
        return None


def safe_output_dir(requested=None):
    """Resuelve la carpeta de salida sin dejar salir de OUTPUT_BASE.

    Sin esto, un parametro `output_dir` o `dir` enviado por el navegador podria
    escribir o leer cualquier ruta de la PC — inaceptable con el programa
    publicado en internet.
    """
    if not requested:
        return OUTPUT_BASE
    try:
        p = Path(requested).resolve()
    except (OSError, ValueError):
        return OUTPUT_BASE
    if p == OUTPUT_BASE or OUTPUT_BASE in p.parents:
        return p
    return OUTPUT_BASE


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def config():
    profile = exporter.find_cmyk_profile()
    ips = lan_ips()
    return jsonify({
        "output_dir": str(OUTPUT_BASE),
        "cmyk_profile": os.path.basename(profile) if profile else None,
        "cmyk_profile_custom": exporter.is_custom_cmyk_profile(),
        "lan_url": f"http://{ips[0]}:{PORT}",
        "lan_alternativas": [f"http://{ip}:{PORT}" for ip in ips[1:]],
        "is_server_pc": client_is_local(),
        "url_publica": read_tunnel_url(),
    })


@app.post("/api/upload")
def upload():
    results = []
    for f in request.files.getlist("files"):
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED:
            results.append({"name": f.filename, "error": "Formato no soportado"})
            continue
        did = uuid.uuid4().hex[:12]
        dest = UPLOAD_DIR / f"{did}{ext}"
        try:
            f.save(dest)
            img = exporter.load_design(str(dest))
        except Exception as e:
            dest.unlink(missing_ok=True)
            results.append({"name": f.filename, "error": f"No se pudo abrir: {e}"})
            continue
        w_px, h_px = img.size
        dpi_info = img.info.get("dpi") or (300, 300)
        try:
            dpi = float(dpi_info[0]) or 300
        except (TypeError, ValueError):
            dpi = 300
        thumb = img.copy()
        thumb.thumbnail((260, 260))
        buf = io.BytesIO()
        thumb.save(buf, "PNG")
        DESIGNS[did] = {"path": str(dest), "name": f.filename,
                        "w_px": w_px, "h_px": h_px}
        results.append({
            "id": did, "name": f.filename, "w_px": w_px, "h_px": h_px,
            "native_w_cm": round(w_px / dpi * 2.54, 2),
            "native_h_cm": round(h_px / dpi * 2.54, 2),
            "thumb": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
        })
    return jsonify(results)


def _js_num(v):
    """Formatea un numero igual que JavaScript (10, no 10.0) para claves compartidas."""
    return int(v) if float(v) == int(v) else v


def _sheet_opts(payload):
    """Lee los ajustes de la plancha tolerando campos faltantes o invalidos."""
    s = payload.get("sheet") or {}

    def num(key, default, lo, hi):
        try:
            return min(max(float(s.get(key, default)), lo), hi)
        except (TypeError, ValueError):
            return default

    return {
        "width_cm": num("width_cm", 57, 5, 200),
        "spacing_cm": num("spacing_cm", 0.5, 0, 20),
        "margin_cm": num("margin_cm", 0.5, 0, 20),
        "dpi": int(num("dpi", 300, 72, 1200)),
        "choke_px": int(num("choke_px", 0, 0, 20)),
        "alpha_threshold": int(num("alpha_threshold", 0, 0, 100)),
        "allow_rotate": bool(s.get("allow_rotate", True)),
        "mirror": bool(s.get("mirror", False)),
        "intent": s.get("intent", "perceptual"),
    }


def _clean_fixed(payload, sheet_w, margin):
    """Valida las piezas fijadas: descarta ids eliminados y las mantiene en la plancha."""
    fixed = []
    for f in payload.get("fixed") or []:
        try:
            if f.get("id") not in DESIGNS:
                continue
            w, h = float(f["w"]), float(f["h"])
            x = min(max(float(f["x"]), margin), max(margin, sheet_w - margin - w))
            y = max(float(f["y"]), margin)
        except (TypeError, ValueError, KeyError):
            continue
        fixed.append({"key": f.get("key"), "uid": f.get("uid"), "id": f["id"],
                      "x": x, "y": y, "w": w, "h": h,
                      "rotated": bool(f.get("rotated"))})
    return fixed


def _run_layout(payload):
    s = _sheet_opts(payload)
    fixed = _clean_fixed(payload, s["width_cm"], s["margin_cm"])
    lock_counts = Counter(f["uid"] for f in fixed)

    items = []
    for r in payload.get("items") or []:
        if r.get("id") not in DESIGNS:
            continue
        try:
            qty = int(r.get("qty", 0)) - lock_counts.get(r.get("uid"), 0)
            w_cm, h_cm = float(r["w_cm"]), float(r["h_cm"])
        except (TypeError, ValueError, KeyError):
            continue
        if qty > 0 and w_cm > 0 and h_cm > 0:
            items.append({"uid": r.get("uid"), "id": r["id"],
                          "w": w_cm, "h": h_cm, "qty": qty})

    placements, length, too_big = packer.pack(
        items, sheet_w=s["width_cm"], spacing=s["spacing_cm"],
        margin=s["margin_cm"], allow_rotate=s["allow_rotate"], fixed=fixed)

    all_p = ([{**f, "locked": True} for f in fixed] +
             [{**p, "locked": False} for p in placements])
    return all_p, length, too_big, s


@app.post("/api/layout")
def layout():
    try:
        placements, length, too_big, _ = _run_layout(request.json or {})
        return jsonify({"placements": placements, "length_cm": round(length, 2),
                        "too_big": too_big})
    except Exception as e:
        return jsonify({"error": f"No se pudo calcular el acomodo: {e}"}), 500


@app.post("/api/preview-white")
def preview_white():
    """Mascaras rojas translucidas con la cobertura real del canal White."""
    try:
        payload = request.json or {}
        dpi = int(payload.get("dpi", 300))
        choke = int(payload.get("choke_px", 0))
        thr = int(payload.get("alpha_threshold", 0))
        masks = {}
        for it in payload.get("items") or []:
            did = it.get("id")
            if did not in DESIGNS:
                continue
            w_cm, h_cm = round(float(it["w_cm"]), 2), round(float(it["h_cm"]), 2)
            mask_key = f"{did}_{_js_num(w_cm)}x{_js_num(h_cm)}"
            cache_key = (mask_key, dpi, choke, thr)
            if cache_key not in MASK_CACHE:
                img = exporter.load_design(DESIGNS[did]["path"])
                target = (exporter.cm_to_px(w_cm, dpi), exporter.cm_to_px(h_cm, dpi))
                mask = exporter.white_preview_mask(img, target, choke_px=choke,
                                                   alpha_threshold=thr)
                buf = io.BytesIO()
                mask.save(buf, "PNG")
                if len(MASK_CACHE) > 200:
                    MASK_CACHE.clear()
                MASK_CACHE[cache_key] = ("data:image/png;base64," +
                                         base64.b64encode(buf.getvalue()).decode())
            masks[mask_key] = MASK_CACHE[cache_key]
        return jsonify({"masks": masks})
    except Exception as e:
        return jsonify({"error": f"No se pudo generar la vista del canal White: {e}"}), 500


@app.post("/api/export")
def export():
    try:
        payload = request.json or {}
        placements, length, too_big, s = _run_layout(payload)
        # por si un diseño fue eliminado entre el acomodo y la exportacion
        skipped = len([p for p in placements if p["id"] not in DESIGNS])
        placements = [p for p in placements if p["id"] in DESIGNS]
        if not placements:
            return jsonify({"error": "No hay diseños que exportar"}), 400

        dpi = s["dpi"]
        out_dir = safe_output_dir(payload.get("output_dir"))
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return jsonify({"error": f"No se pudo crear la carpeta de salida: {e}"}), 400

        sources = {}
        for p in placements:
            if p["id"] not in sources:
                sources[p["id"]] = exporter.load_design(DESIGNS[p["id"]]["path"])

        sheet = exporter.compose_sheet(placements, sources,
                                       s["width_cm"], length, dpi)
        sheet = exporter.prepare_for_dtf(sheet, mirror=s["mirror"],
                                         alpha_threshold=s["alpha_threshold"])

        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        mode = payload.get("format", "rgb_tiff")
        files = []
        if mode == "rgb_tiff":
            out = out_dir / f"plancha_{stamp}.tif"
            exporter.export_rgb_tiff(sheet, str(out), dpi)
            files.append(out)
        elif mode == "png":
            out = out_dir / f"plancha_{stamp}.png"
            exporter.export_png(sheet, str(out), dpi)
            files.append(out)
        elif mode == "cmyk_spot":
            out = out_dir / f"plancha_{stamp}_CMYK.tif"
            exporter.export_tiff(sheet, str(out), dpi,
                                 choke_px=s["choke_px"], intent=s["intent"])
            files.append(out)
        elif mode == "cmyk_dos":
            color = out_dir / f"plancha_{stamp}_COLOR.tif"
            white = out_dir / f"plancha_{stamp}_BLANCO.tif"
            exporter.export_tiff(sheet, str(color), dpi,
                                 choke_px=s["choke_px"], intent=s["intent"])
            exporter.export_white_tiff(sheet, str(white), dpi, choke_px=s["choke_px"])
            files += [color, white]
        else:
            return jsonify({"error": f"Formato desconocido: {mode}"}), 400

        return jsonify({
            "files": [{"path": str(f), "name": f.name,
                       "mb": round(f.stat().st_size / 1e6, 1)} for f in files],
            "dir": str(out_dir),
            "length_cm": round(length, 2),
            "size_px": [sheet.width, sheet.height],
            "mirror": s["mirror"],
            "too_big": too_big, "skipped": skipped,
        })
    except Exception as e:
        return jsonify({"error": f"No se pudo exportar la plancha: {e}"}), 500


@app.get("/api/descargar")
def descargar():
    """Descarga un archivo ya exportado (util desde el celular o el iPad)."""
    folder = safe_output_dir(request.args.get("dir"))
    name = secure_filename(request.args.get("name") or "")
    if not name or not (folder / name).is_file():
        return jsonify({"error": "Archivo no encontrado"}), 404
    return send_from_directory(str(folder), name, as_attachment=True)


@app.post("/api/abrir-carpeta")
def abrir_carpeta():
    """Abre la carpeta en el explorador. Solo desde el navegador de esta PC."""
    if not client_is_local():
        return jsonify({"ok": False,
                        "error": "Solo disponible en la PC del programa"}), 403
    folder = safe_output_dir((request.json or {}).get("dir"))
    if folder.is_dir():
        os.startfile(str(folder))
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "La carpeta no existe"}), 404


@app.post("/api/quitar")
def quitar():
    did = (request.json or {}).get("id")
    d = DESIGNS.pop(did, None)
    if d:
        Path(d["path"]).unlink(missing_ok=True)
        for k in [k for k in MASK_CACHE if k[0].startswith(str(did) + "_")]:
            MASK_CACHE.pop(k, None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    # el lanzador con tunel lo reescribe despues; asi nunca se muestra una
    # direccion publica vieja que ya no responde
    TUNNEL_FILE.unlink(missing_ok=True)
    url_local = f"http://127.0.0.1:{PORT}"
    ips = lan_ips()
    print("\n" + "=" * 58)
    print("  ARMADOR DTF esta corriendo")
    print("=" * 58)
    print(f"  PIN de acceso ...... {PIN}")
    print(f"  En esta PC ......... {url_local}")
    print(f"  Celular / iPad ..... http://{ips[0]}:{PORT}")
    for extra in ips[1:]:
        print(f"     (si no abre, prueba  http://{extra}:{PORT})")
    print("  La misma red WiFi. Cierra esta ventana para apagarlo.")
    print("=" * 58 + "\n")
    Timer(1.2, lambda: webbrowser.open(url_local)).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
