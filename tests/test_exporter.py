"""Lectura de disenos y exportacion TIFF para el RIP (engine/exporter.py)."""

import numpy as np
import pytest
import tifffile
from PIL import Image, ImageCms, ImageDraw

from engine import exporter

PACKBITS = 32773
SEPARATED = 5


# --------------------------------------------------------------------------
# Lectura de los archivos del cliente
# --------------------------------------------------------------------------

def test_tiff_de_photoshop_conserva_la_base_blanca(muestras):
    """El 5o canal es el recorte del arte: sin el, entraba un rectangulo opaco."""
    img = exporter.load_design(str(muestras / "photoshop_cmyk_blanco.tif"))
    assert img.mode == "RGBA" and img.size == (700, 500)
    a = np.asarray(img.getchannel("A"))
    assert a[0, 0] == 0 and a[250, 350] == 255
    assert (a > 0).mean() == pytest.approx(300 * 500 / (700 * 500), abs=0.01)


def test_tiff_sin_unidad_no_mide_quince_metros(muestras):
    img = exporter.load_design(str(muestras / "sin_unidad.tif"))
    assert exporter.design_dpi(img) == 300


def test_dpi_declarado_del_png(muestras):
    img = exporter.load_design(str(muestras / "logo.png"))
    assert exporter.design_dpi(img) == pytest.approx(300, abs=1)


@pytest.mark.parametrize("nombre", ["foto.jpg", "gris16.png"])
def test_formatos_sin_transparencia_entran_opacos(muestras, nombre):
    img = exporter.load_design(str(muestras / nombre))
    assert img.mode == "RGBA"
    assert np.asarray(img.getchannel("A")).min() == 255


def test_archivo_roto_lanza_error(muestras):
    with pytest.raises(Exception):
        exporter.load_design(str(muestras / "roto.png"))


def test_detecta_sombra_difuminada(muestras):
    img = exporter.load_design(str(muestras / "logo.png"))
    assert exporter.porcentaje_borde_suave(img) > 10


# --------------------------------------------------------------------------
# Ajustes de DTF
# --------------------------------------------------------------------------

def _rampa():
    a = np.tile(np.linspace(0, 255, 256).astype(np.uint8), (8, 1))
    img = Image.new("RGBA", (256, 8), (200, 20, 20, 0))
    img.putalpha(Image.fromarray(a, "L"))
    return img


def test_limpiar_bordes_borra_lo_casi_transparente():
    out = exporter.prepare_for_dtf(_rampa(), alpha_threshold=20)
    a = np.asarray(out.getchannel("A"))
    assert a[:, :51].max() == 0 and a[:, 52:].min() > 0


def test_espejo_invierte_la_pelicula():
    img = _rampa()
    out = exporter.prepare_for_dtf(img, mirror=True)
    assert np.array_equal(np.asarray(out)[:, ::-1], np.asarray(img))


def test_choke_no_deja_sin_blanco_los_trazos_finos():
    alpha = Image.new("L", (200, 200), 0)
    dr = ImageDraw.Draw(alpha)
    dr.rectangle((20, 20, 120, 120), fill=255)      # zona solida
    dr.line((150, 10, 150, 190), fill=255, width=1)  # trazo de 1 px
    dr.line((170, 10, 170, 190), fill=255, width=2)  # trazo de 2 px
    blanco = np.asarray(exporter._contraer_blanco(alpha, 2))
    assert blanco[20, 70] == 0 and blanco[70, 70] == 255   # el solido se contrae
    assert (blanco[10:191, 150] == 255).all()               # el trazo fino no
    assert (blanco[10:191, 170] == 255).all()


# --------------------------------------------------------------------------
# Exportacion
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def plancha(muestras):
    """Plancha de 30 x 25 cm a 150 dpi: cruza varias franjas de 512 filas."""
    src = {"a": exporter.load_design(str(muestras / "logo.png")),
           "b": exporter.load_design(str(muestras / "photoshop_cmyk_blanco.tif"))}
    pl = [{"id": "ab"[i % 2], "x": 0.5 + (i % 3) * 9.7, "y": 0.5 + (i // 3) * 6.1,
           "w": 9.2, "h": 5.8, "rotated": False} for i in range(12)]
    return exporter.compose_sheet(pl, src, 30, 25, 150)


def _referencia(hoja, choke, con_blanco):
    """Conversion de la plancha entera de un golpe, como antes de las franjas."""
    perfil = exporter.find_cmyk_profile()
    rgb = hoja.convert("RGB")
    if perfil:
        cmyk = ImageCms.profileToProfile(
            rgb, exporter.SRGB_PROFILE, perfil, outputMode="CMYK",
            renderingIntent=ImageCms.Intent.PERCEPTUAL,
            flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
    else:
        cmyk = rgb.convert("CMYK")
    a = np.asarray(hoja.getchannel("A"), dtype=np.float64) / 255
    tinta = np.round(np.asarray(cmyk, dtype=np.float64) * a[..., None])
    if not con_blanco:
        return tinta.astype(np.uint8)
    blanco = np.asarray(exporter._contraer_blanco(hoja.getchannel("A"), choke))
    return np.dstack([tinta.astype(np.uint8), blanco])


def test_tiff_cmyk_mas_white_para_maintop(plancha, tmp_path):
    out = tmp_path / "plancha.tif"
    exporter.export_tiff(plancha, str(out), 150, choke_px=1)
    with tifffile.TiffFile(out) as tf:
        p = tf.pages[0]
        assert p.samplesperpixel == 5
        assert int(p.photometric) == SEPARATED
        assert int(p.compression) == PACKBITS        # MainTop no lee LZW ni deflate
        assert p.tags["NumberOfInks"].value == 5
        assert "White" in p.tags["InkNames"].value
        assert 34377 in p.tags                        # recursos de Photoshop
        x = p.tags["XResolution"].value
        assert round(x[0] / x[1]) == 150
        if exporter.find_cmyk_profile():
            assert 34675 in p.tags                    # perfil ICC incrustado
        arr = p.asarray()
    assert arr.shape == (plancha.height, plancha.width, 5)
    assert arr[0, 0].max() == 0                       # fuera del arte: sin tinta


def test_tiff_cmyk_sin_white(plancha, tmp_path):
    out = tmp_path / "plancha.tif"
    exporter.export_tiff(plancha, str(out), 150, con_blanco=False)
    with tifffile.TiffFile(out) as tf:
        assert tf.pages[0].samplesperpixel == 4
        assert int(tf.pages[0].compression) == PACKBITS


@pytest.mark.parametrize("choke", [0, 1, 3])
def test_franjas_dan_el_mismo_resultado_que_la_plancha_entera(plancha, tmp_path, choke):
    """Convertir por franjas ahorra 4 GB por metro; el archivo debe ser el mismo."""
    out = tmp_path / "plancha.tif"
    exporter.export_tiff(plancha, str(out), 150, choke_px=choke)
    nuevo = tifffile.imread(out).astype(int)
    ref = _referencia(plancha, choke, True).astype(int)
    assert nuevo.shape == ref.shape
    assert np.array_equal(nuevo[..., 4], ref[..., 4])            # blanco identico
    assert np.abs(nuevo[..., :4] - ref[..., :4]).max() <= 1     # redondeo del CMM
    a = np.asarray(plancha.getchannel("A"))
    solido = (a == 0) | (a == 255)
    assert np.array_equal(nuevo[solido][:, :4], ref[solido][:, :4])


def test_vista_previa_del_white():
    alpha = Image.new("L", (100, 50), 0)
    ImageDraw.Draw(alpha).rectangle((10, 10, 90, 40), fill=255)
    img = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    img.putalpha(alpha)
    mask = exporter.white_preview_mask(img, (1000, 500))
    assert mask.mode == "RGBA" and max(mask.size) <= 320
    assert np.asarray(mask.getchannel("A")).max() > 0
