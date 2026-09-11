"""Composicion de la plancha y exportacion de archivos para produccion DTF.

ESTRATEGIA DE COLOR (ver ANALISIS-COLOR.md)
-------------------------------------------
El modo recomendado para DTF es entregar RGB (sRGB) con alpha y dejar que el
RIP (MainTop) haga la conversion a las tintas de la impresora con SU perfil,
que conoce el limite de tinta, la pelicula y la base blanca.

Pre-convertir a CMYK SWOP destruye justo los colores que el DTF imprime bien
(azules, verdes y naranjas saturados: dE de 20 a 50 medido), y esa perdida es
irreversible: MainTop ya no puede recuperar lo que el archivo no trae.

El modo CMYK + canal de tinta plana "White" se conserva como compatibilidad
con el flujo antiguo de Photoshop, pero deja de ser el predeterminado.
"""

import os
import struct

import numpy as np
from PIL import Image, ImageCms, ImageFilter
import tifffile

# Limite alto pero finito: permite planchas grandes sin dejar que un archivo
# corrupto o descomunal agote la memoria.
Image.MAX_IMAGE_PIXELS = 300_000_000

SRGB_PROFILE = ImageCms.createProfile("sRGB")
SRGB_BYTES = ImageCms.ImageCmsProfile(SRGB_PROFILE).tobytes()

_DEFAULT_CMYK_PROFILES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "perfiles", "cmyk.icc"),
    r"C:\Windows\System32\spool\drivers\color\USWebCoatedSWOP.icc",
    r"C:\Windows\System32\spool\drivers\color\RSWOP.icm",
]


def find_cmyk_profile():
    for p in _DEFAULT_CMYK_PROFILES:
        if os.path.isfile(p):
            return p
    return None


def is_custom_cmyk_profile():
    """True si el usuario puso su propio perfil en perfiles/cmyk.icc."""
    return os.path.isfile(_DEFAULT_CMYK_PROFILES[0])


def cm_to_px(cm, dpi):
    return int(round(cm / 2.54 * dpi))


def load_design(path):
    """Abre un archivo de diseño y lo devuelve como RGBA (sRGB)."""
    img = Image.open(path)
    if img.mode == "CMYK":
        profile = find_cmyk_profile()
        if profile:
            img = ImageCms.profileToProfile(
                img, profile, SRGB_PROFILE,
                renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="RGB")
        else:
            img = img.convert("RGB")
    elif img.mode not in ("RGBA", "RGB", "LA", "L", "P"):
        img = img.convert("RGBA")
    # respetar el perfil incrustado del archivo si trae uno distinto de sRGB
    icc = img.info.get("icc_profile")
    if icc and img.mode in ("RGB", "RGBA"):
        try:
            src = ImageCms.ImageCmsProfile(io_bytes(icc))
            alpha = img.getchannel("A") if img.mode == "RGBA" else None
            conv = ImageCms.profileToProfile(
                img.convert("RGB"), src, SRGB_PROFILE, outputMode="RGB",
                renderingIntent=ImageCms.Intent.PERCEPTUAL)
            img = conv.convert("RGBA")
            if alpha is not None:
                img.putalpha(alpha)
        except Exception:
            pass  # perfil ilegible: se asume sRGB
    return img.convert("RGBA")


def io_bytes(data):
    import io as _io
    return _io.BytesIO(data)


def compose_sheet(placements, sources, sheet_w_cm, sheet_len_cm, dpi):
    """Compone la plancha completa en RGBA a la resolucion final."""
    W = cm_to_px(sheet_w_cm, dpi)
    H = cm_to_px(sheet_len_cm, dpi)
    sheet = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cache = {}
    for p in placements:
        key = (p["id"], round(p["w"], 3), round(p["h"], 3), p["rotated"])
        if key not in cache:
            img = sources[p["id"]]
            if p["rotated"]:
                img = img.transpose(Image.Transpose.ROTATE_90)
            target = (max(1, cm_to_px(p["w"], dpi)), max(1, cm_to_px(p["h"], dpi)))
            cache[key] = img.resize(target, Image.Resampling.LANCZOS)
        tile = cache[key]
        x, y = cm_to_px(p["x"], dpi), cm_to_px(p["y"], dpi)
        sheet.alpha_composite(tile, (x, y))
    return sheet


def prepare_for_dtf(sheet_rgba, mirror=False, alpha_threshold=0):
    """Ajustes propios de DTF sobre la plancha ya compuesta.

    mirror: espejo horizontal. En DTF la pelicula se imprime al reves porque
        el diseño se transfiere. Solo activar si el RIP NO esta espejeando ya.
    alpha_threshold: 0-100. Elimina los pixeles con menos de ese % de opacidad.
        En DTF una zona con poca tinta retiene poco polvo adhesivo y termina
        despegandose o lavandose; recortar esos bordes fantasma evita el
        problema. 0 = desactivado.
    """
    out = sheet_rgba
    if alpha_threshold > 0:
        t = int(round(min(max(alpha_threshold, 0), 100) * 255 / 100))
        a = np.asarray(out.getchannel("A"))
        out = out.copy()
        out.putalpha(Image.fromarray(np.where(a < t, 0, a).astype(np.uint8), "L"))
    if mirror:
        out = out.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return out


# --------------------------------------------------------------------------
# Salidas recomendadas: RGB con alpha, el RIP hace color y blanco
# --------------------------------------------------------------------------

def export_rgb_tiff(sheet_rgba, out_path, dpi):
    """TIFF RGBA 8 bits, sRGB incrustado, LZW. Modo recomendado para MainTop."""
    sheet_rgba.save(out_path, "TIFF", compression="tiff_lzw",
                    dpi=(dpi, dpi), icc_profile=SRGB_BYTES)


def export_png(sheet_rgba, out_path, dpi):
    """PNG RGBA con sRGB incrustado. Equivalente al TIFF RGB, archivo mas chico."""
    sheet_rgba.save(out_path, "PNG", dpi=(dpi, dpi), icc_profile=SRGB_BYTES)


# --------------------------------------------------------------------------
# Salida de compatibilidad: CMYK + canal de tinta plana White
# --------------------------------------------------------------------------

def _photoshop_resources(spot_name="White"):
    """Construye el bloque de recursos de imagen de Photoshop (tag 34377)."""
    def resource(res_id, data):
        if len(data) % 2:
            data += b"\x00"
        return b"8BIM" + struct.pack(">H", res_id) + b"\x00\x00" + \
               struct.pack(">I", len(data)) + data

    # 1006: nombres de canales extra (cadenas Pascal)
    name = spot_name.encode("ascii")
    names_data = struct.pack("B", len(name)) + name

    # 1007: DisplayInfo clasico — 14 bytes por canal extra
    #   colorSpace=0 (RGB), color blanco, solidez 0, kind=2 (tinta plana)
    display = struct.pack(">H4HHBB", 0, 65535, 65535, 65535, 0, 0, 2, 0)

    return resource(1006, names_data) + resource(1007, display)


def export_tiff(sheet_rgba, out_path, dpi, choke_px=0, icc_path=None,
                intent="perceptual"):
    """Convierte la plancha RGBA a TIFF CMYK de 5 canales con White spot.

    Modo de compatibilidad con el flujo de Photoshop. Pierde gamut respecto a
    entregar RGB: usar solo si MainTop lo exige, y de preferencia con un perfil
    CMYK propio en perfiles/cmyk.icc, no con SWOP.
    """
    rgb = sheet_rgba.convert("RGB")
    alpha = sheet_rgba.getchannel("A")

    icc_bytes = None
    profile = icc_path or find_cmyk_profile()
    if profile:
        intents = {"perceptual": ImageCms.Intent.PERCEPTUAL,
                   "relative": ImageCms.Intent.RELATIVE_COLORIMETRIC,
                   "saturation": ImageCms.Intent.SATURATION}
        cmyk = ImageCms.profileToProfile(
            rgb, SRGB_PROFILE, profile, outputMode="CMYK",
            renderingIntent=intents.get(intent, ImageCms.Intent.PERCEPTUAL),
            flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
        with open(profile, "rb") as f:
            icc_bytes = f.read()
    else:
        cmyk = rgb.convert("CMYK")

    cmyk_arr = np.asarray(cmyk, dtype=np.float32)          # (H, W, 4) 0-255 = tinta
    alpha_arr = np.asarray(alpha, dtype=np.float32) / 255  # (H, W)

    # Sin tinta de color fuera del arte; bordes suavizados llevan tinta parcial
    cmyk_arr *= alpha_arr[..., None]

    # Canal blanco = cobertura del arte, opcionalmente contraido (choke)
    white = alpha
    if choke_px > 0:
        size = 2 * int(choke_px) + 1
        white = white.filter(ImageFilter.MinFilter(size))
    white_arr = np.asarray(white, dtype=np.float32)

    data = np.dstack([cmyk_arr, white_arr]).astype(np.uint8)  # (H, W, 5)

    resources = _photoshop_resources("White")
    extratags = [
        (34377, 7, len(resources), resources, True),   # recursos Photoshop
        (332, 3, 1, 1, True),                          # InkSet = CMYK
        (334, 3, 1, 5, True),                          # NumberOfInks = 5
        (333, 2, None, "Cyan\0Magenta\0Yellow\0Black\0White\0", True),
    ]
    if icc_bytes:
        extratags.append((34675, 7, len(icc_bytes), icc_bytes, True))

    tifffile.imwrite(
        out_path, data,
        photometric="separated",
        extrasamples=[0],           # el 5o canal no es alpha: sin especificar
        compression="lzw",
        resolution=(dpi, dpi),
        resolutionunit="INCH",
        extratags=extratags,
        rowsperstrip=256,
    )


def export_white_tiff(sheet_rgba, out_path, dpi, choke_px=0):
    """Archivo separado solo con el blanco (escala de grises, negro = tinta)."""
    alpha = sheet_rgba.getchannel("A")
    if choke_px > 0:
        white = alpha.filter(ImageFilter.MinFilter(2 * int(choke_px) + 1))
    else:
        white = alpha
    # invertido: en un archivo de escala de grises la tinta se representa oscura
    inv = Image.eval(white, lambda v: 255 - v)
    inv.save(out_path, dpi=(dpi, dpi), compression="tiff_lzw")


# --------------------------------------------------------------------------
# Vista previa del canal White (secundaria)
# --------------------------------------------------------------------------

def white_preview_mask(rgba, target_px, choke_px=0, alpha_threshold=0, max_side=320):
    """Miniatura RGBA (rojo translucido) con la cobertura real de tinta blanca."""
    alpha = rgba.getchannel("A")
    tw, th = max(1, int(target_px[0])), max(1, int(target_px[1]))
    scale = min(1.0, 1600 / max(tw, th))
    size = (max(1, int(tw * scale)), max(1, int(th * scale)))
    a = alpha.resize(size, Image.Resampling.LANCZOS)
    if alpha_threshold > 0:
        t = int(round(min(max(alpha_threshold, 0), 100) * 255 / 100))
        arr = np.asarray(a)
        a = Image.fromarray(np.where(arr < t, 0, arr).astype(np.uint8), "L")
    ch = int(round(choke_px * scale))
    if choke_px > 0 and ch < 1:
        ch = 1  # que el choke nunca desaparezca por el reescalado
    if ch > 0:
        a = a.filter(ImageFilter.MinFilter(2 * ch + 1))
    a.thumbnail((max_side, max_side))
    tint = Image.eval(a, lambda v: int(v * 0.55))
    r = Image.new("L", a.size, 235)
    g = Image.new("L", a.size, 45)
    b = Image.new("L", a.size, 45)
    return Image.merge("RGBA", (r, g, b, tint))
