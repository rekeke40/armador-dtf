"""Disenos de prueba que reproducen los casos reales del taller.

Cada archivo existe por un defecto que se encontro en produccion:

- logo.png ............ PNG RGBA con sombra difuminada y un trazo de 2 px.
- foto.jpg ............ JPG sin transparencia, a 150 dpi.
- photoshop_cmyk_blanco.tif
                        TIFF CMYK de Photoshop con la base blanca como 5o canal
                        y LZW: Pillow tira ese canal (el recorte del arte) y
                        ni siquiera abre el archivo.
- sin_unidad.tif ...... TIFF con ResolutionUnit=1: se leia como 1 dpi y la
                        pieza media 15 metros.
- gris16.png .......... PNG en escala de grises de 16 bits.
- roto.png ............ Basura con extension valida: debe dar un error claro.

Uso desde la terminal:  python tests/muestras.py carpeta_destino
"""

import os
import sys

import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFilter

NOMBRES = ["logo.png", "foto.jpg", "photoshop_cmyk_blanco.tif",
           "sin_unidad.tif", "gris16.png", "roto.png"]


def generar(carpeta):
    os.makedirs(carpeta, exist_ok=True)
    ruta = lambda n: os.path.join(carpeta, n)

    arte = Image.new("RGBA", (1200, 900), (0, 0, 0, 0))
    dr = ImageDraw.Draw(arte)
    dr.ellipse((100, 100, 1100, 800), fill=(20, 60, 200, 255))
    dr.line((0, 450, 1200, 450), fill=(255, 0, 0, 255), width=2)
    sombra = Image.new("RGBA", arte.size, (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rectangle((50, 50, 1150, 850), fill=(0, 0, 0, 160))
    sombra = sombra.filter(ImageFilter.GaussianBlur(40))
    sombra.alpha_composite(arte)
    sombra.save(ruta("logo.png"), dpi=(300, 300))

    Image.new("RGB", (600, 600), (240, 120, 10)).save(ruta("foto.jpg"), dpi=(150, 150))

    a = np.zeros((500, 700, 5), np.uint8)
    a[100:400, 100:600, :4] = (200, 30, 0, 10)
    a[100:400, 100:600, 4] = 255
    tifffile.imwrite(ruta("photoshop_cmyk_blanco.tif"), a, photometric="separated",
                     extrasamples=[0], compression="lzw",
                     resolution=(300, 300), resolutionunit="INCH")

    tifffile.imwrite(ruta("sin_unidad.tif"), np.full((600, 600, 3), 128, np.uint8),
                     photometric="rgb", resolution=(1, 1), resolutionunit="NONE")

    Image.fromarray(np.linspace(0, 65535, 400 * 400).reshape(400, 400)
                    .astype(np.uint16)).save(ruta("gris16.png"))

    with open(ruta("roto.png"), "wb") as f:
        f.write(b"no soy un png")
    return carpeta


if __name__ == "__main__":
    destino = generar(sys.argv[1] if len(sys.argv) > 1 else "muestras")
    print("Muestras en", os.path.abspath(destino))
