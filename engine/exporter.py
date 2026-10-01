"""Composicion de la plancha y exportacion de archivos para produccion DTF.

Exporta TIFF CMYK + White (5 canales con tinta plana). La conversion de
color usa el perfil de perfiles/cmyk.icc (Japan Web Coated por defecto)
con intent Perceptual y BPC, igualando la conversion de Photoshop.

MainTop 6.1 NO decodifica el LZW que escribe tifffile/imagecodecs — usar
siempre PACKBITS. NO cambiar a lzw ni a deflate.
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

# El perfil propio del usuario (perfiles/cmyk.icc) manda sobre el SWOP de
# Windows. Empaquetado como .exe hay dos sitios donde mirar: junto al programa
# —para poder cambiarlo sin rearmar nada— y dentro del propio paquete.
_PERFILES_PROPIOS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "perfiles", "cmyk.icc"),
]
_PERFILES_SISTEMA = [
    r"C:\Windows\System32\spool\drivers\color\USWebCoatedSWOP.icc",
    r"C:\Windows\System32\spool\drivers\color\RSWOP.icm",
]


def usar_carpeta_de_perfiles(*carpetas):
    """Agrega carpetas donde buscar `perfiles/cmyk.icc`, con prioridad."""
    for carpeta in reversed(carpetas):
        ruta = os.path.join(str(carpeta), "perfiles", "cmyk.icc")
        if ruta not in _PERFILES_PROPIOS:
            _PERFILES_PROPIOS.insert(0, ruta)


def find_cmyk_profile():
    for p in _PERFILES_PROPIOS + _PERFILES_SISTEMA:
        if os.path.isfile(p):
            return p
    return None


def is_custom_cmyk_profile():
    """True si el usuario puso su propio perfil en perfiles/cmyk.icc."""
    return any(os.path.isfile(p) for p in _PERFILES_PROPIOS)


def cm_to_px(cm, dpi):
    return int(round(cm / 2.54 * dpi))


def io_bytes(data):
    import io as _io
    return _io.BytesIO(data)


# --------------------------------------------------------------------------
# Lectura de archivos de diseno
#
# Pillow se queda corto justo con los TIFF que manda una imprenta:
#   * CMYK + un 5o canal (la base blanca de Photoshop): lee solo CMYK y tira
#     el canal extra, asi que el arte llega SIN transparencia — un rectangulo
#     solido. Es el mismo archivo que iCore abria bien.
#   * Ese mismo archivo comprimido con LZW: Pillow ni siquiera lo abre
#     ("cannot identify image file").
#   * alpha premultiplicado (associated): deja los bordes suaves oscurecidos.
# Por eso, cuando el TIFF trae canales de mas se lee con tifffile y se arma
# el RGBA a mano. Todo lo demas sigue pasando por Pillow, que es mas rapido.
# --------------------------------------------------------------------------

_SEPARATED = 5      # photometric CMYK
_MINISWHITE = 0
_MINISBLACK = 1

# nombres de canal que en un archivo de DTF significan "base blanca"
_NOMBRES_BLANCO = ("white", "blanco", "spot", "base")


def _a_8bits(arr):
    """Normaliza a uint8 cualquier profundidad (8, 16, 32 bits o flotante)."""
    if arr.dtype == np.uint8:
        return arr
    if arr.dtype == np.uint16:
        return (arr >> 8).astype(np.uint8)
    if arr.dtype == np.uint32:
        return (arr >> 24).astype(np.uint8)
    if np.issubdtype(arr.dtype, np.floating):
        top = float(arr.max()) if arr.size else 1.0
        escala = 255.0 if top <= 1.001 else 255.0 / top
        return np.clip(arr * escala, 0, 255).astype(np.uint8)
    return np.clip(arr, 0, 255).astype(np.uint8)


def _nombres_de_canal(page):
    """Nombres de los canales extra que Photoshop guarda en el tag 34377."""
    try:
        datos = page.tags[34377].value
        if isinstance(datos, str):
            datos = datos.encode("latin-1", "ignore")
        nombres = []
        i = 0
        while i + 12 <= len(datos):
            if datos[i:i + 4] != b"8BIM":
                break
            res_id = struct.unpack(">H", datos[i + 4:i + 6])[0]
            j = i + 6
            largo_nombre = datos[j]
            j += 1 + largo_nombre
            j += j % 2
            largo = struct.unpack(">I", datos[j:j + 4])[0]
            cuerpo = datos[j + 4:j + 4 + largo]
            if res_id == 1006:              # nombres de canales extra
                k = 0
                while k < len(cuerpo):
                    n = cuerpo[k]
                    nombres.append(cuerpo[k + 1:k + 1 + n].decode("latin-1", "ignore"))
                    k += 1 + n
            i = j + 4 + largo + (largo % 2)
        return nombres
    except Exception:
        return []


def _canal_es_mascara(canal):
    """True si el canal parece cobertura de arte (mezcla de vacio y tinta)."""
    vacio = float((canal < 8).mean())
    return 0.01 < vacio < 0.999


def _tiff_con_canales_extra(path):
    """Lee un TIFF que Pillow no sabe interpretar. Devuelve RGBA o None.

    Solo se mete cuando hace falta: archivos con mas canales de los que lleva
    su espacio de color (CMYK+blanco, RGB+spot) o con alpha premultiplicado.
    """
    try:
        with tifffile.TiffFile(path) as tf:
            page = tf.pages[0]
            photo = int(page.photometric)
            spp = int(page.samplesperpixel)
            extra = tuple(int(e) for e in (page.extrasamples or ()))
            base = 4 if photo == _SEPARATED else 1 if photo in (_MINISBLACK, _MINISWHITE) else 3
            extra0 = extra[0] if extra else 0
            # Pillow ya lee bien lo normal: sin canales de mas, o un unico
            # alpha sin premultiplicar sobre RGB o gris. Lo raro es lo que
            # baja por aqui.
            if spp <= base or (spp == base + 1 and extra0 == 2
                               and photo != _SEPARATED):
                return None
            arr = _a_8bits(page.asarray())
            icc = page.tags.get(34675)
            icc = icc.value if icc is not None else None
            nombres = _nombres_de_canal(page)
            invertir = photo == _MINISWHITE
    except Exception:
        return None

    if arr.ndim == 2:
        arr = arr[..., None]
    color, resto = arr[..., :base], arr[..., base:]

    if photo == _SEPARATED:
        cmyk = Image.fromarray(np.ascontiguousarray(color), "CMYK")
        rgb = _cmyk_a_srgb(cmyk, icc)
    elif base == 1:
        gris = color[..., 0]
        if invertir:
            gris = 255 - gris
        rgb = Image.fromarray(gris, "L").convert("RGB")
    else:
        rgb = Image.fromarray(np.ascontiguousarray(color[..., :3]), "RGB")

    alpha = None
    premultiplicado = False
    for i in range(resto.shape[2]):
        etiqueta = extra[i] if i < len(extra) else 0
        nombre = nombres[i].lower() if i < len(nombres) else ""
        if etiqueta in (1, 2):                                   # alpha declarado
            alpha, premultiplicado = resto[..., i], etiqueta == 1
            break
        if any(n in nombre for n in _NOMBRES_BLANCO):            # base blanca
            alpha = resto[..., i]
            break
    if alpha is None and resto.shape[2] and _canal_es_mascara(resto[..., 0]):
        # canal extra sin etiquetar: en un archivo de DTF es la base blanca,
        # y usarla como transparencia es lo unico que recupera el recorte
        alpha = resto[..., 0]

    out = rgb.convert("RGBA")
    if alpha is not None:
        if premultiplicado:
            out = _desmultiplicar(out, alpha)
        out.putalpha(Image.fromarray(np.ascontiguousarray(alpha), "L"))
    return out


def _desmultiplicar(rgba, alpha):
    """Deshace el alpha premultiplicado (si no, los bordes salen oscuros)."""
    arr = np.asarray(rgba, dtype=np.float32)
    a = alpha.astype(np.float32) / 255.0
    seguro = np.maximum(a, 1e-3)[..., None]
    rgb = np.clip(arr[..., :3] / seguro, 0, 255)
    return Image.fromarray(np.dstack([rgb, arr[..., 3]]).astype(np.uint8), "RGBA")


def _cmyk_a_srgb(img, icc_bytes=None):
    """CMYK -> sRGB usando el perfil incrustado si lo trae."""
    origen = None
    if icc_bytes:
        try:
            origen = ImageCms.ImageCmsProfile(io_bytes(icc_bytes))
        except Exception:
            origen = None
    if origen is None:
        p = find_cmyk_profile()
        if p:
            try:
                origen = ImageCms.ImageCmsProfile(p)
            except Exception:
                origen = None
    if origen is None:
        return img.convert("RGB")
    try:
        return ImageCms.profileToProfile(
            img, origen, SRGB_PROFILE, outputMode="RGB",
            renderingIntent=ImageCms.Intent.PERCEPTUAL)
    except Exception:
        return img.convert("RGB")


def _dpi_de_tiff(path):
    """Resolucion declarada por el TIFF, en puntos por pulgada."""
    try:
        with tifffile.TiffFile(path) as tf:
            page = tf.pages[0]
            xres = page.tags.get("XResolution")
            unidad = page.tags.get("ResolutionUnit")
            if xres is None:
                return None
            valor = xres.value
            num, den = valor if isinstance(valor, tuple) else (valor, 1)
            dpi = float(num) / float(den or 1)
            u = int(getattr(unidad, "value", 2) or 2)
            if u == 3:               # centimetros
                dpi *= 2.54
            elif u != 2:             # 1 = sin unidad: es solo una proporcion
                return None
            return (dpi, dpi) if 10 <= dpi <= 6000 else None
    except Exception:
        return None


def design_dpi(img, defecto=300):
    """DPI creible del archivo, o `defecto`.

    Un TIFF con ResolutionUnit=1 ("ninguna") guarda solo una proporcion, y
    Pillow la entrega como dpi=(1,1). Tomarla al pie de la letra convertia un
    diseno de 600 px en una pieza de 15 metros de ancho, asi que se descarta.
    """
    valor = img.info.get("dpi") or img.info.get("resolution")
    try:
        dpi = float(valor[0] if isinstance(valor, (tuple, list)) else valor)
    except (TypeError, ValueError, IndexError):
        return defecto
    return dpi if 10 <= dpi <= 6000 else defecto


def load_design(path):
    """Abre un archivo de diseno y lo devuelve como RGBA (sRGB)."""
    if os.path.splitext(path)[1].lower() in (".tif", ".tiff"):
        especial = _tiff_con_canales_extra(path)
        if especial is not None:
            especial.info["dpi"] = _dpi_de_tiff(path) or (300, 300)
            return especial

    img = Image.open(path)
    info = dict(img.info)
    if img.mode == "CMYK":
        img = _cmyk_a_srgb(img, info.get("icc_profile"))
    elif img.mode not in ("RGBA", "RGB", "LA", "L", "P"):
        img = img.convert("RGBA")
    # respetar el perfil incrustado del archivo si trae uno distinto de sRGB
    icc = info.get("icc_profile")
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
    img = img.convert("RGBA")
    for clave in ("dpi", "resolution"):
        if clave in info:
            img.info[clave] = info[clave]
    return img


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
# Medicion de bordes suaves (aviso en la interfaz)
# --------------------------------------------------------------------------

def porcentaje_borde_suave(rgba, umbral_pct=20):
    """Cuanto del arte cae en la franja casi transparente, en % del arte.

    Son los pixeles que "Limpiar bordes" quitaria con ese umbral: reciben poca
    tinta, retienen poco polvo adhesivo y son los que se despegan o se lavan.
    Sirve para avisar antes de imprimir, no para tocar nada.
    """
    a = np.asarray(rgba.getchannel("A"))
    arte = int((a > 0).sum())
    if not arte:
        return 0.0
    corte = int(round(min(max(umbral_pct, 0), 100) * 255 / 100))
    suave = int(((a > 0) & (a < corte)).sum())
    return round(suave / arte * 100, 1)


# --------------------------------------------------------------------------
# Salidas: RGB con alpha, el RIP hace color y blanco
# --------------------------------------------------------------------------

def _aplanar_sobre_blanco(sheet_rgba):
    """Compone el arte sobre blanco dejando el canal alpha intacto.

    MainTop 6.1 no usa el alpha como transparencia: pinta los canales de color
    tal como vienen. Componer sobre un lienzo transparente deja esos pixeles
    en negro, asi que la plancha entera se cargaba como un rectangulo negro y
    los bordes difuminados salian a plena tinta con canto duro.

    Sobre blanco, "sin arte" equivale a "sin tinta" en cuanto el RIP convierte
    a CMYK, y un borde al 40 % de opacidad le llega al RIP como un 40 % de
    tinta en vez de como tinta plena. El alpha no se toca: es el canal del que
    sale la base blanca.
    """
    arr = np.asarray(sheet_rgba, dtype=np.float32)
    a = arr[..., 3:4] / 255.0
    rgb = np.rint(arr[..., :3] * a + 255.0 * (1.0 - a))
    return Image.fromarray(
        np.dstack([rgb, arr[..., 3]]).astype(np.uint8), "RGBA")


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


def _contraer_blanco(alpha, choke_px):
    """Contrae la base blanca sin borrarla debajo de los trazos finos.

    El choke evita que el blanco asome fuera del color en el borde de las
    zonas solidas. Aplicado a secas se come la base entera de cualquier trazo
    mas delgado que el propio choke: medido, con choke=2 px las lineas de 1 y
    2 px quedaban con tinta de color y CERO blanco debajo, y sin base blanca
    esa tinta no transfiere a la prenda.

    Se contrae el blanco y se devuelve completo solo donde hace falta, que es
    justo lo que la apertura morfologica (erosion + dilatacion del mismo
    tamano) deja fuera. Las zonas solidas conservan su choke.
    """
    if choke_px <= 0:
        return alpha
    size = 2 * int(choke_px) + 1
    erosionado = alpha.filter(ImageFilter.MinFilter(size))
    apertura = erosionado.filter(ImageFilter.MaxFilter(size))
    a = np.asarray(alpha)
    fino = (a > 0) & (np.asarray(apertura) == 0)
    return Image.fromarray(
        np.where(fino, a, np.asarray(erosionado)).astype(np.uint8), "L")


def export_tiff(sheet_rgba, out_path, dpi, choke_px=0, icc_path=None,
                intent="perceptual", con_blanco=True):
    """Convierte la plancha RGBA a TIFF CMYK de 5 canales con White spot.

    Modo predeterminado. Usa el perfil de perfiles/cmyk.icc (Japan Web Coated
    por defecto) con Perceptual + BPC para igualar la conversion de Photoshop.
    """
    W, H = sheet_rgba.size
    canales = 5 if con_blanco else 4

    icc_bytes = None
    transform = None
    profile = icc_path or find_cmyk_profile()
    if profile:
        intents = {"perceptual": ImageCms.Intent.PERCEPTUAL,
                   "relative": ImageCms.Intent.RELATIVE_COLORIMETRIC,
                   "saturation": ImageCms.Intent.SATURATION}
        # una sola transformacion para todas las franjas
        transform = ImageCms.buildTransform(
            SRGB_PROFILE, profile, "RGB", "CMYK",
            renderingIntent=intents.get(intent, ImageCms.Intent.PERCEPTUAL),
            flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
        with open(profile, "rb") as f:
            icc_bytes = f.read()

    # Por franjas y directo a 8 bits. Convertir la plancha entera de un golpe
    # en float32 se comia 5.3 GB por metro de plancha (medido): una plancha
    # de metro y medio tumbaba el programa en una PC de 8 GB. Por franjas el
    # pico es la plancha RGBA mas el archivo final.
    data = np.empty((H, W, canales), np.uint8)
    franja = 512
    for y0 in range(0, H, franja):
        trozo = sheet_rgba.crop((0, y0, W, min(H, y0 + franja)))
        rgb = trozo.convert("RGB")
        cmyk = (ImageCms.applyTransform(rgb, transform) if transform
                else rgb.convert("CMYK"))
        tinta = np.asarray(cmyk, dtype=np.uint16)                 # 0-255 = tinta
        a = np.asarray(trozo.getchannel("A"), dtype=np.uint16)
        # Sin tinta de color fuera del arte; bordes suavizados llevan tinta
        # parcial. (x*a + 127) // 255 es x*a/255 redondeado.
        data[y0:y0 + franja, :, :4] = (tinta * a[..., None] + 127) // 255

    if con_blanco:
        # Canal blanco = cobertura del arte, opcionalmente contraida (choke).
        # Va por franjas con un margen de 2*choke_px filas arriba y abajo (la
        # apertura es erosion + dilatacion, cada una mira choke_px filas) para
        # que el resultado sea identico al de la plancha entera.
        pad = 2 * max(0, int(choke_px))
        for y0 in range(0, H, franja):
            y1 = min(H, y0 + franja)
            a0, a1 = max(0, y0 - pad), min(H, y1 + pad)
            alpha = sheet_rgba.crop((0, a0, W, a1)).getchannel("A")
            blanco = np.asarray(_contraer_blanco(alpha, choke_px))
            data[y0:y1, :, 4] = blanco[y0 - a0:y0 - a0 + (y1 - y0)]
        resources = _photoshop_resources("White")
        extratags = [
            (34377, 7, len(resources), resources, True),   # recursos Photoshop
            (332, 3, 1, 1, True),                          # InkSet = CMYK
            (334, 3, 1, 5, True),                          # NumberOfInks = 5
            (333, 2, None, "Cyan\0Magenta\0Yellow\0Black\0White\0", True),
        ]
        extrasamples = [0]      # el 5o canal no es alpha: sin especificar
    else:
        extratags = [
            (332, 3, 1, 1, True),
            (334, 3, 1, 4, True),
            (333, 2, None, "Cyan\0Magenta\0Yellow\0Black\0", True),
        ]
        extrasamples = None
    if icc_bytes:
        extratags.append((34675, 7, len(icc_bytes), icc_bytes, True))

    tifffile.imwrite(
        out_path, data,
        photometric="separated",
        extrasamples=extrasamples,
        # PACKBITS y no LZW: MainTop 6.1 no decodifica el LZW de imagecodecs.
        compression="packbits",
        resolution=(dpi, dpi),
        resolutionunit="INCH",
        extratags=extratags,
        rowsperstrip=16,
        metadata=None,
        software=None,
    )


# --------------------------------------------------------------------------
# Vista previa del canal White
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
        ch = 1
    a = _contraer_blanco(a, ch)
    a.thumbnail((max_side, max_side))
    tint = Image.eval(a, lambda v: int(v * 0.55))
    r = Image.new("L", a.size, 235)
    g = Image.new("L", a.size, 45)
    b = Image.new("L", a.size, 45)
    return Image.merge("RGBA", (r, g, b, tint))
