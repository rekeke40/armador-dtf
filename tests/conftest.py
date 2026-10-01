import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import muestras as _muestras  # noqa: E402


@pytest.fixture(scope="session")
def muestras(tmp_path_factory):
    """Carpeta con los disenos de prueba (ver tests/muestras.py)."""
    return Path(_muestras.generar(str(tmp_path_factory.mktemp("muestras"))))


@pytest.fixture(scope="session")
def salida(tmp_path_factory):
    return tmp_path_factory.mktemp("planchas")


@pytest.fixture(scope="session")
def armador(salida):
    """El modulo app con la carpeta de salida y el PIN fijados para la prueba."""
    os.environ["ARMADOR_SALIDA"] = str(salida)
    os.environ["ARMADOR_PIN"] = "246810"
    import app
    app.app.config["TESTING"] = True
    return app


@pytest.fixture()
def cliente(armador):
    """Navegador en la propia PC: Host localhost y origen 127.0.0.1."""
    armador._login_fails.clear()
    return armador.app.test_client()
