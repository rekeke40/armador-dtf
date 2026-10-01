"""Genera el icono del programa (empaque/armador.ico).

El dibujo es la plancha: la película amarilla del rollo con las piezas ya
acomodadas encima. A 16 px hay que reconocerlo, así que son pocas formas,
gruesas y con mucho contraste.

    python empaque/hacer_icono.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

FONDO = (20, 22, 27, 255)        # el mismo gris muy oscuro de la interfaz
PELICULA = (245, 208, 59, 255)   # amarillo de la marca
PIEZA = (20, 22, 27, 255)

AQUI = Path(__file__).parent


def dibujar(lado=1024):
    u = lado / 64.0                                  # unidad de rejilla
    img = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([0, 0, lado - 1, lado - 1], radius=12 * u, fill=FONDO)
    # la película: un rollo vertical con margen
    d.rounded_rectangle([10 * u, 6 * u, 54 * u, 58 * u], radius=3 * u, fill=PELICULA)

    # piezas acomodadas, de distinto tamaño como en una plancha real
    piezas = [(15, 11, 23, 9), (40, 11, 9, 9),
              (15, 24, 14, 13), (32, 24, 17, 13),
              (15, 41, 10, 12), (28, 41, 21, 12)]
    for x, y, w, h in piezas:
        d.rounded_rectangle([x * u, y * u, (x + w) * u, (y + h) * u],
                            radius=1.4 * u, fill=PIEZA)
    return img


def main():
    grande = dibujar()
    tamanos = [16, 24, 32, 48, 64, 128, 256]
    capas = [grande.resize((t, t), Image.Resampling.LANCZOS) for t in tamanos]
    salida = AQUI / "armador.ico"
    capas[-1].save(salida, format="ICO",
                   sizes=[(t, t) for t in tamanos])
    grande.resize((512, 512), Image.Resampling.LANCZOS).save(AQUI / "armador.png")
    print("icono:", salida)


if __name__ == "__main__":
    main()
