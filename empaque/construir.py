"""Arma el programa como aplicacion de Windows (.exe).

    python empaque/construir.py            las dos versiones
    python empaque/construir.py carpeta    solo la carpeta portable
    python empaque/construir.py unico      solo el .exe suelto

Deja en `dist/`:

    Armador DTF/Armador DTF.exe   carpeta portable — abre en 1-2 s
    Armador DTF.exe               un solo archivo  — abre en 5-15 s

Las dos guardan su PIN, sus cargas y sus planchas junto al .exe, asi que la
carpeta portable se puede llevar en una USB con toda su configuracion.

Requiere:  pip install pyinstaller pywebview waitress
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EMPAQUE = RAIZ / "empaque"
NOMBRE = "Armador DTF"

# Peso muerto: codecs de imagecodecs que no aparecen jamas en un TIFF de
# imprenta (AVIF, JPEG XL, HEIF, compresores cientificos...). Son 35 MB de
# los 50 que pesa el paquete. Si algun dia llega un archivo raro que los
# necesite, se quitan de esta lista y se vuelve a armar.
CODECS_DE_MAS = [
    "_avif", "_jpegxl", "_brunsli", "_heif", "_ultrahdr", "_openzl",
    "_blosc", "_blosc2", "_sz3", "_pcodec", "_zfp", "_jetraw", "_dds",
    "_bcn", "_sperr", "_szip", "_lzfse", "_lzham", "_zopfli", "_snappy",
    "_brotli", "_bz2", "_lzo", "_qoi", "_rgbe", "_cms",
]

# Librerias pesadas que nada de esto usa, pero que PyInstaller arrastra si
# estan instaladas en la maquina donde se arma.
PAQUETES_DE_MAS = [
    "matplotlib", "scipy", "pandas", "IPython", "notebook", "pytest",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "gi", "cv2",
    "setuptools", "pip", "numpy.f2py", "numpy.distutils",
]


def argumentos(uno_solo):
    args = [
        "--noconfirm", "--clean", "--windowed",
        "--name", NOMBRE,
        "--icon", str(EMPAQUE / "armador.ico"),
        "--add-data", f"{RAIZ / 'static'}{';'}static",
        "--add-data", f"{RAIZ / 'perfiles'}{';'}perfiles",
        # pywebview carga su backend por nombre: hay que decirselo a mano
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "waitress",
        "--collect-all", "webview",
        # imagecodecs carga cada codec por su nombre en tiempo de ejecucion y
        # PyInstaller no tiene forma de adivinarlo: sin esto se empaqueta el
        # paquete vacio y el TIFF con LZW o PACKBITS deja de funcionar.
        "--collect-submodules", "imagecodecs",
        "--distpath", str(RAIZ / "dist"),
        "--workpath", str(RAIZ / "build"),
        "--specpath", str(RAIZ / "build"),
    ]
    for m in CODECS_DE_MAS:
        args += ["--exclude-module", f"imagecodecs.{m}"]
    for m in PAQUETES_DE_MAS:
        args += ["--exclude-module", m]
    args.append("--onefile" if uno_solo else "--onedir")
    args.append(str(RAIZ / "app.py"))
    return args


def pesar(ruta):
    if ruta.is_file():
        return ruta.stat().st_size
    return sum(f.stat().st_size for f in ruta.rglob("*") if f.is_file())


def armar(uno_solo):
    etiqueta = "un solo .exe" if uno_solo else "carpeta portable"
    print(f"\n=== Armando: {etiqueta} ===")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", "PyInstaller"] + argumentos(uno_solo),
                       cwd=str(RAIZ))
    if r.returncode != 0:
        print(f"!! Fallo al armar {etiqueta}")
        return None
    destino = RAIZ / "dist" / (f"{NOMBRE}.exe" if uno_solo else NOMBRE)
    print(f"   {destino}  ({pesar(destino) / 1e6:.0f} MB, {time.time() - t0:.0f} s)")
    return destino


def main():
    if not (EMPAQUE / "armador.ico").is_file():
        print("Falta el icono; generandolo...")
        subprocess.run([sys.executable, str(EMPAQUE / "hacer_icono.py")], check=True)

    que = (sys.argv[1] if len(sys.argv) > 1 else "todo").lower()
    hechos = []
    if que in ("todo", "carpeta"):
        # la version de un solo archivo se arma despues para que no se pisen
        hechos.append(armar(uno_solo=False))
    if que in ("todo", "unico"):
        suelto = RAIZ / "dist" / NOMBRE          # la carpeta ya armada
        respaldo = None
        if suelto.is_dir():                      # --onefile borraria la carpeta
            respaldo = RAIZ / "dist" / f"{NOMBRE}__portable"
            shutil.rmtree(respaldo, ignore_errors=True)
            suelto.rename(respaldo)
        hechos.append(armar(uno_solo=True))
        if respaldo is not None:
            respaldo.rename(suelto)

    print("\nListo:")
    for h in hechos:
        if h:
            print(f"  {h}")


if __name__ == "__main__":
    main()
