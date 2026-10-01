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
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
import webbrowser
from collections import Counter, defaultdict
from functools import wraps
from pathlib import Path
from threading import Thread, Timer

from flask import (Flask, jsonify, redirect, request, send_from_directory,
                   session, url_for)

from engine import packer, exporter

# ==========================================================================
# Donde vive cada cosa
#
# Como programa suelto (.exe) el codigo corre desde una carpeta temporal de
# SOLO LECTURA que arma PyInstaller (sys._MEIPASS): ahi viven la interfaz y
# los perfiles. Lo que el programa guarda —PIN, cargas, tunel— va junto al
# .exe, para que se lo pueda llevar en una USB con todo y su configuracion.
# Si esa carpeta no deja escribir (USB protegida, "Archivos de programa"),
# se usa la carpeta del usuario.
# ==========================================================================

ES_APP = getattr(sys, "frozen", False)
RECURSOS = Path(getattr(sys, "_MEIPASS", str(Path(__file__).parent)))


def _escribible(carpeta):
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        prueba = carpeta / ".escritura"
        prueba.write_text("ok", "utf-8")
        prueba.unlink()
        return True
    except OSError:
        return False


def _carpeta_de_datos():
    junto_al_programa = (Path(sys.executable).parent if ES_APP
                         else Path(__file__).parent)
    if _escribible(junto_al_programa):
        return junto_al_programa
    respaldo = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ArmadorDTF"
    respaldo.mkdir(parents=True, exist_ok=True)
    return respaldo


DATOS = _carpeta_de_datos()
UPLOAD_DIR = DATOS / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def limpiar_cargas_viejas():
    """Borra las cargas de sesiones anteriores.

    Se llama desde main() DESPUES de comprobar que no hay otro Armador
    abierto: si se hiciera al importar, un segundo doble clic borraba los
    disenos que la primera ventana todavia estaba usando.
    """
    for viejo in UPLOAD_DIR.iterdir():
        try:
            viejo.unlink()
        except OSError:
            pass

# el perfil CMYK propio del usuario se busca junto al programa antes que el
# que venga empaquetado, para que pueda cambiarlo sin rearmar el .exe
exporter.usar_carpeta_de_perfiles(DATOS, RECURSOS)

PORT = int(os.environ.get("ARMADOR_PORT", 8765))
HOST = os.environ.get("ARMADOR_HOST", "0.0.0.0")

app = Flask(__name__, static_folder=str(RECURSOS / "static"), static_url_path="")
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

CONFIG_FILE = DATOS / "config.json"


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
    basta. Se piden tres cosas a la vez: que no vengan cabeceras de proxy, que
    la peticion salga de la loopback y que el navegador haya pedido
    "localhost" y no un dominio de fuera. Lo tercero es la red de seguridad:
    una peticion del tunel siempre trae Host = algo.trycloudflare.com, asi que
    aunque alguien lograra quitarle las cabeceras seguiria sin pasar por local
    — y pasar por local es entrar sin PIN.
    """
    if request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For"):
        return False
    host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
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


# El PIN protege lo que entra por la red (celular, iPad, tunel). En la PC
# donde corre el programa no tiene sentido: ya estas sentado frente a ella.
# Con ARMADOR_PIN_SIEMPRE=1 se vuelve a pedir tambien aqui.
PIN_TAMBIEN_LOCAL = str(os.environ.get("ARMADOR_PIN_SIEMPRE", "")).lower() in ("1", "si", "true")


@app.get("/api/ping")
def api_ping():
    """Para saber si ya hay un Armador corriendo en este puerto."""
    return jsonify({"armador": True, "puerto": PORT})


@app.before_request
def require_login():
    ruta = request.path
    if ruta in ("/login", "/api/login", "/login.html", "/style.css",
                "/api/ping") or ruta.startswith("/static/"):
        return None
    if session.get("ok"):
        return None
    if client_is_local() and not PIN_TAMBIEN_LOCAL:
        session.permanent = True
        session["ok"] = True
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

TUNNEL_FILE = DATOS / "tunnel_url.txt"


def read_tunnel_url():
    """URL publica del tunel: la del proceso vivo, o la que dejo el lanzador."""
    proc = TUNEL["proc"]
    if proc is not None:
        if proc.poll() is None:
            return TUNEL["url"]
        cerrar_tunel()          # se murio: no seguir anunciando su direccion
        return None
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


# --------------------------------------------------------------------------
# Tunel a internet (opcional)
#
# Publica el programa en una direccion https de Cloudflare. Lo arranca el
# usuario a proposito desde el boton de la interfaz, nunca solo: mientras el
# tunel esta abierto, cualquiera con el enlace Y el PIN puede entrar.
# --------------------------------------------------------------------------

TUNEL = {"proc": None, "url": None}
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PATRON_URL = re.compile(rb"https://[a-z0-9-]+\.trycloudflare\.com")


def buscar_cloudflared():
    """cloudflared.exe junto al programa, en el PATH o donde lo deja su instalador."""
    candidatos = [DATOS / "cloudflared.exe", RECURSOS / "cloudflared.exe",
                  Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
                  Path(r"C:\Program Files\cloudflared\cloudflared.exe")]
    for c in candidatos:
        if c.is_file():
            return str(c)
    return shutil.which("cloudflared")


def abrir_tunel():
    """Arranca el tunel y devuelve (url, error)."""
    if TUNEL["proc"] and TUNEL["proc"].poll() is None:
        return TUNEL["url"], None
    exe = buscar_cloudflared()
    if not exe:
        return None, ("Falta cloudflared.exe. Descárgalo de Cloudflare y "
                      "déjalo en la carpeta del programa.")
    log = DATOS / "tunnel.log"
    try:
        log.unlink(missing_ok=True)
        salida = open(log, "wb")
    except OSError as e:
        return None, f"No se pudo escribir el registro del túnel: {e}"
    try:
        TUNEL["proc"] = subprocess.Popen(
            [exe, "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
            stdout=salida, stderr=subprocess.STDOUT, creationflags=_SIN_VENTANA)
    except OSError as e:
        return None, f"No se pudo arrancar cloudflared: {e}"

    for _ in range(50):                      # hasta ~30 s
        time.sleep(0.6)
        if TUNEL["proc"].poll() is not None:
            break
        try:
            m = _PATRON_URL.search(log.read_bytes())
        except OSError:
            m = None
        if m:
            url = m.group().decode()
            TUNEL["url"] = url
            try:
                TUNNEL_FILE.write_text(url, "utf-8")
            except OSError:
                pass
            return url, None
    cerrar_tunel()
    return None, "Cloudflare no respondió. Revisa la conexión a internet."


def cerrar_tunel():
    proc = TUNEL["proc"]
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    TUNEL["proc"] = None
    TUNEL["url"] = None
    TUNNEL_FILE.unlink(missing_ok=True)


@app.post("/api/tunel")
def api_tunel():
    """Abre o cierra el tunel. Solo desde la PC donde corre el programa."""
    if not client_is_local():
        return jsonify({"error": "Solo desde la PC del programa"}), 403
    accion = (request.json or {}).get("accion")
    if accion == "cerrar":
        cerrar_tunel()
        return jsonify({"ok": True, "url": None})
    if accion == "abrir":
        url, error = abrir_tunel()
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"ok": True, "url": url, "pin": PIN})
    return jsonify({"error": "Acción desconocida"}), 400


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
        "tunel_disponible": bool(buscar_cloudflared()),
        "pin": PIN if client_is_local() else None,
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
        dpi = exporter.design_dpi(img)
        borde_suave = exporter.porcentaje_borde_suave(img)
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
            "borde_suave": borde_suave,
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
        "con_blanco": bool(s.get("con_blanco", True)),
        "intent": s.get("intent", "perceptual"),
    }


# Topes de cordura: un cero de mas en la cantidad o en la medida colgaba el
# servidor intentando acomodar millones de piezas o una plancha de kilometros.
MAX_COPIAS = 2000
MAX_LADO_CM = 500


def _items_validos(payload):
    """Filas de la tabla que se pueden acomodar, por uid."""
    items = {}
    for r in payload.get("items") or []:
        if r.get("id") not in DESIGNS:
            continue
        try:
            qty = int(r.get("qty", 0))
            w_cm, h_cm = float(r["w_cm"]), float(r["h_cm"])
        except (TypeError, ValueError, KeyError):
            continue
        qty = min(qty, MAX_COPIAS)
        if qty > 0 and 0 < w_cm <= MAX_LADO_CM and 0 < h_cm <= MAX_LADO_CM:
            items[r.get("uid")] = {"uid": r.get("uid"), "id": r["id"],
                                   "w": w_cm, "h": h_cm, "qty": qty}
    return items


def _clean_fixed(payload, items, s):
    """Valida las piezas fijadas. Devuelve (fijadas, cuantas_se_liberaron).

    Una pieza fijada que ya no es valida NO se tira: se devuelve al monton
    automatico, para que la plancha lleve SIEMPRE la cantidad que pidio el
    usuario. Antes, una fijada de mas (o encimada tras cambiar el ancho de la
    plancha) se imprimia igual, y salian piezas de mas o dos encimadas.
    """
    sheet_w, margin = s["width_cm"], s["margin_cm"]
    usable = sheet_w - 2 * margin
    holgura = s["spacing_cm"] * 0.5      # la misma que usa la interfaz al soltar
    fixed, liberadas = [], 0
    puestas = Counter()

    for f in payload.get("fixed") or []:
        fila = items.get(f.get("uid"))
        try:
            w, h = float(f["w"]), float(f["h"])
            x, y = float(f["x"]), float(f["y"])
        except (TypeError, ValueError, KeyError):
            liberadas += 1
            continue
        # la fila existe, no cambio de tamano y todavia le faltan copias
        if (fila is None or f.get("id") != fila["id"]
                or puestas[fila["uid"]] >= fila["qty"]
                or not _mismo_tamano(w, h, fila, bool(f.get("rotated")))
                or w > usable + 1e-9):
            liberadas += 1
            continue
        x = min(max(x, margin), max(margin, sheet_w - margin - w))
        y = max(y, margin)
        pieza = {"key": f.get("key"), "uid": fila["uid"], "id": fila["id"],
                 "x": x, "y": y, "w": w, "h": h,
                 "rotated": bool(f.get("rotated"))}
        if any(packer.overlaps(pieza, otra, holgura) for otra in fixed):
            liberadas += 1
            continue
        fixed.append(pieza)
        puestas[fila["uid"]] += 1
    return fixed, liberadas


def _mismo_tamano(w, h, fila, rotada):
    """True si la pieza fijada conserva el tamano que dice su fila."""
    esperado = (fila["h"], fila["w"]) if rotada else (fila["w"], fila["h"])
    return abs(w - esperado[0]) < 0.02 and abs(h - esperado[1]) < 0.02


def _run_layout(payload):
    s = _sheet_opts(payload)
    items = _items_validos(payload)
    fixed, liberadas = _clean_fixed(payload, items, s)
    fijadas = Counter(f["uid"] for f in fixed)

    autos = []
    for uid, it in items.items():
        faltan = it["qty"] - fijadas.get(uid, 0)
        if faltan > 0:
            autos.append({**it, "qty": faltan})

    placements, length, too_big = packer.pack(
        autos, sheet_w=s["width_cm"], spacing=s["spacing_cm"],
        margin=s["margin_cm"], allow_rotate=s["allow_rotate"], fixed=fixed)

    all_p = ([{**f, "locked": True} for f in fixed] +
             [{**p, "locked": False} for p in placements])
    pedidas = sum(it["qty"] for it in items.values())
    return all_p, length, too_big, s, {"pedidas": pedidas, "liberadas": liberadas}


@app.post("/api/layout")
def layout():
    try:
        placements, length, too_big, _, cuentas = _run_layout(request.json or {})
        return jsonify({"placements": placements, "length_cm": round(length, 2),
                        "too_big": too_big, **cuentas})
    except Exception as e:
        return jsonify({"error": f"No se pudo calcular el acomodo: {e}"}), 500


@app.post("/api/preview-white")
def preview_white():
    """Mascaras rojas translucidas con la cobertura real del canal White."""
    try:
        payload = request.json or {}
        dpi = min(max(int(payload.get("dpi", 300)), 72), 1200)
        choke = min(max(int(payload.get("choke_px", 0)), 0), 20)
        thr = min(max(int(payload.get("alpha_threshold", 0)), 0), 100)
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


def _escribir_archivo(destino, escritor):
    """Escribe con nombre temporal y renombra al final.

    Si algo se rompe a media escritura (un codec que falta, disco lleno, la
    memoria), sin esto quedaba un .tif de 8 bytes con nombre de plancha
    buena — y eso el RIP lo abre como si nada. Medido con una exportacion
    fallida: dos archivos corruptos indistinguibles de los correctos.
    """
    temporal = destino.with_name(destino.name + ".parcial")
    try:
        escritor(str(temporal))
        temporal.replace(destino)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
    return destino


@app.post("/api/export")
def export():
    try:
        payload = request.json or {}
        placements, length, too_big, s, cuentas = _run_layout(payload)
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
        files = []
        sufijo = "CMYK+W" if s["con_blanco"] else "CMYK"
        out = out_dir / f"plancha_{stamp}_{sufijo}.tif"
        _escribir_archivo(out, lambda r: exporter.export_tiff(
            sheet, r, dpi, choke_px=s["choke_px"], intent=s["intent"],
            con_blanco=s["con_blanco"]))
        files.append(out)

        return jsonify({
            "files": [{"path": str(f), "name": f.name,
                       "mb": round(f.stat().st_size / 1e6, 1)} for f in files],
            "dir": str(out_dir),
            "length_cm": round(length, 2),
            "size_px": [sheet.width, sheet.height],
            "mirror": s["mirror"],
            "too_big": too_big, "skipped": skipped, **cuentas,
        })
    except Exception as e:
        return jsonify({"error": f"No se pudo exportar la plancha: {e}"}), 500


@app.get("/api/descargar")
def descargar():
    """Descarga un archivo ya exportado (util desde el celular o el iPad)."""
    folder = safe_output_dir(request.args.get("dir"))
    # Nombre tal cual, sin rutas. secure_filename() no sirve aqui: le quita el
    # "+" a "..._CMYK+W.tif" y la descarga del modo predeterminado daba 404.
    pedido = request.args.get("name") or ""
    name = os.path.basename(pedido)
    if (not name or name != pedido or name in (".", "..") or ":" in name
            or not (folder / name).is_file()):
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


# --------------------------------------------------------------------------
# Arranque
# --------------------------------------------------------------------------

def puerto_libre(preferido):
    """El puerto preferido, o el primero libre despues de el."""
    for p in range(preferido, preferido + 20):
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return preferido


def instancia_viva(puerto):
    """True si en ese puerto ya responde OTRO Armador (doble clic al icono)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/ping",
                                    timeout=1.5) as r:
            return json.loads(r.read()).get("armador") is True
    except Exception:
        return False


def servir():
    """Servidor de verdad (waitress) y, si no estuviera, el de Flask."""
    try:
        from waitress import serve
    except ImportError:
        app.run(host=HOST, port=PORT, debug=False, threaded=True)
        return
    # clear_untrusted_proxy_headers=False: el programa necesita ver
    # X-Forwarded-For para distinguir lo que entra por el tunel de lo que
    # entra desde esta misma PC.
    serve(app, host=HOST, port=PORT, threads=8, ident="Armador DTF",
          channel_timeout=900, clear_untrusted_proxy_headers=False)


def esperar_servidor(puerto, segundos=20):
    for _ in range(int(segundos * 5)):
        if instancia_viva(puerto):
            return True
        time.sleep(0.2)
    return False


def abrir_ventana(url):
    """Ventana propia (WebView2). False si este equipo no puede con ella."""
    try:
        import webview
    except Exception as e:
        print(f"Sin motor de ventana ({e}); se usa el navegador.")
        return False

    def asegurar_carga(ventana):
        """WebView2 a veces se come la primera navegacion mientras arranca.

        Medido: la ventana abria en blanco y solo con F5 aparecia el programa.
        Aqui se comprueba que la pagina este cargada de verdad y, si no, se
        vuelve a pedir — sin parpadeo cuando cargo bien a la primera.
        """
        for _ in range(15):
            time.sleep(0.4)
            try:
                if ventana.evaluate_js("document.title") == "Armador DTF":
                    return
                ventana.load_url(url)
            except Exception:
                return

    try:
        ventana = webview.create_window("Armador DTF", url, width=1360,
                                        height=860, min_size=(900, 560),
                                        text_select=True)
        webview.start(asegurar_carga, ventana)   # bloquea hasta que la cierran
        return True
    except Exception as e:
        print(f"No se pudo abrir la ventana ({e}); se usa el navegador.")
        return False


def ventana_de_respaldo(url):
    """Sin WebView2: navegador y una ventanita para poder apagar el programa."""
    webbrowser.open(url)
    try:
        import tkinter as tk
        raiz = tk.Tk()
        raiz.title("Armador DTF")
        raiz.geometry("430x170")
        raiz.configure(bg="#14161b")
        tk.Label(raiz, text="Armador DTF está corriendo", bg="#14161b",
                 fg="#f5d03b", font=("Segoe UI", 13, "bold")).pack(pady=(24, 6))
        tk.Label(raiz, text=f"Abierto en el navegador:\n{url}", bg="#14161b",
                 fg="#e8eaf0", font=("Segoe UI", 9)).pack()
        tk.Button(raiz, text="Apagar el programa", command=raiz.destroy,
                  font=("Segoe UI", 9)).pack(pady=14)
        raiz.mainloop()
    except Exception:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f"Armador DTF está corriendo en {url}\n\n"
                  "Deja esta ventana abierta mientras lo usas.",
            "Armador DTF", 0x40)


def banner(url_local, ips):
    print("\n" + "=" * 58)
    print("  ARMADOR DTF esta corriendo")
    print("=" * 58)
    print(f"  PIN (celular / iPad)  {PIN}")
    print(f"  En esta PC .......... {url_local}")
    print(f"  Celular / iPad ...... http://{ips[0]}:{PORT}")
    for extra in ips[1:]:
        print(f"     (si no abre, prueba  http://{extra}:{PORT})")
    print("  La misma red WiFi. Cierra esta ventana para apagarlo.")
    print("=" * 58 + "\n")


def main():
    global PORT
    modo_ventana = ES_APP or "--ventana" in sys.argv
    if "--navegador" in sys.argv:
        modo_ventana = False

    # si ya hay un Armador abierto, no levantar otro: se enseña el que existe
    if instancia_viva(PORT):
        url = f"http://127.0.0.1:{PORT}"
        if modo_ventana and abrir_ventana(url):
            return
        webbrowser.open(url)
        return

    limpiar_cargas_viejas()
    TUNNEL_FILE.unlink(missing_ok=True)   # nunca mostrar una direccion vieja
    PORT = puerto_libre(PORT)
    url_local = f"http://127.0.0.1:{PORT}"
    Thread(target=servir, daemon=True).start()
    esperar_servidor(PORT)

    try:
        if modo_ventana:
            if not abrir_ventana(url_local):
                ventana_de_respaldo(url_local)
        else:
            banner(url_local, lan_ips())
            Timer(1.2, lambda: webbrowser.open(url_local)).start()
            while True:                    # el servidor vive en su hilo
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        cerrar_tunel()


if __name__ == "__main__":
    main()
