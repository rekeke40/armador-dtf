"""Acomodo automatico de disenos en la plancha (abajo-izquierda con obstaculos).

Todas las medidas se manejan en centimetros. El origen (0,0) es la esquina
superior izquierda de la plancha; la plancha crece hacia abajo (largo del rollo).

Por que no estantes ("shelf"): el algoritmo de estantes reserva franjas de alto
uniforme, asi que una pieza fijada a mano en mitad de una franja la invalidaba
entera y el resto de las copias se iba al fondo. Medido: fijar dos piezas en un
pedido mezclado estiraba la plancha de 163 a 199 cm — 36 cm de pelicula tirados.

Este acomodo coloca cada pieza en la posicion valida mas alta y, a igual altura,
la mas a la izquierda ("bottom-left fill"). Las piezas fijadas son simples
obstaculos: se rellena alrededor de ellas en vez de saltarlas.
"""

import bisect

_EPS = 1e-9
_MAX_SCAN = 600      # tope de saltos horizontales por franja (anti bucle)


class _Strip:
    """Rectangulos ya colocados, ordenados por y, con consulta por franja."""

    def __init__(self):
        self.rects = []
        self._ys = []
        self._max_h = 0.0

    def add(self, r):
        i = bisect.bisect_left(self._ys, r["y"])
        self.rects.insert(i, r)
        self._ys.insert(i, r["y"])
        self._max_h = max(self._max_h, r["h"])

    def band(self, y, h, gap):
        """Los rectangulos cuya franja vertical toca [y, y+h) con separacion gap."""
        lo = bisect.bisect_left(self._ys, y - self._max_h - gap)
        hi = bisect.bisect_right(self._ys, y + h + gap)
        return [r for r in self.rects[lo:hi] if r["y"] + r["h"] + gap > y + _EPS]


def _scan_x(band, x_min, x_max, w, gap):
    """Primera x libre en la franja, o None si la pieza ya no cabe a lo ancho.

    `band` solo trae rectangulos que estorban verticalmente, asi que aqui basta
    con mirar la x: se salta al borde derecho del obstaculo mas lejano.
    """
    x = x_min
    for _ in range(_MAX_SCAN):
        if x + w > x_max + _EPS:
            return None
        tope = None
        for r in band:
            if r["x"] + r["w"] + gap > x + _EPS and x + w + gap > r["x"] + _EPS:
                borde = r["x"] + r["w"]
                if tope is None or borde > tope:
                    tope = borde
        if tope is None:
            return x
        x = tope + gap
    return None


def pack(items, sheet_w, spacing=0.5, margin=0.5, allow_rotate=True, fixed=None):
    """Acomoda los disenos y devuelve (colocaciones, largo_total_cm, no_caben).

    items: lista de dicts {uid, id, w, h, qty} en cm (solo las copias automaticas).
    fixed: lista de dicts {x, y, w, h, ...} ya colocados que hay que esquivar.
    Devuelve colocaciones como dicts {uid, id, x, y, w, h, rotated}.
    """
    fixed = fixed or []
    usable_w = sheet_w - 2 * margin
    x_min, x_max = margin, margin + usable_w

    copies = []
    too_big = []
    for it in items:
        w, h = float(it["w"]), float(it["h"])
        if w > usable_w + _EPS and not (allow_rotate and h <= usable_w + _EPS):
            too_big.append(it["id"])
            continue
        for _ in range(int(it["qty"])):
            copies.append({"uid": it.get("uid"), "id": it["id"], "w": w, "h": h})

    # Las mas grandes primero: dejan los huecos chicos para las piezas chicas.
    copies.sort(key=lambda c: (-max(c["w"], c["h"]), -c["w"] * c["h"],
                               str(c["id"]), str(c["uid"])))

    strip = _Strip()
    ys = [margin]                      # alturas candidatas, ordenadas
    ys_set = {margin}
    # Por tamano de pieza, la altura mas alta que todavia puede servir. Una
    # posicion que ya se descarto para ese tamano no vuelve a ser valida
    # (solo se agregan piezas, nunca se quitan), asi que no hay que
    # recorrer otra vez la plancha entera por cada copia.
    pisos = {}

    def anota_y(v):
        if v <= margin or v in ys_set:
            return
        ys_set.add(v)
        bisect.insort(ys, v)
        # una altura nueva por encima de un piso si hay que volver a probarla
        for k, piso in pisos.items():
            if v < piso:
                pisos[k] = v

    for r in fixed:
        rect = {"x": float(r["x"]), "y": float(r["y"]),
                "w": float(r["w"]), "h": float(r["h"])}
        strip.add(rect)
        anota_y(rect["y"])
        anota_y(rect["y"] + rect["h"] + spacing)

    placements = []
    for c in copies:
        orients = [(c["w"], c["h"], False)]
        if allow_rotate and abs(c["w"] - c["h"]) > _EPS:
            orients.append((c["h"], c["w"], True))
        orients = [o for o in orients if o[0] <= usable_w + _EPS]
        if not orients:
            continue

        elegido = None
        tam = (c["w"], c["h"])
        desde = bisect.bisect_left(ys, pisos.get(tam, margin))
        for y in ys[desde:]:            # de arriba hacia abajo: la primera sirve
            mejor = None
            for (w, h, rot) in orients:
                x = _scan_x(strip.band(y, h, spacing), x_min, x_max, w, spacing)
                if x is not None and (mejor is None or x < mejor[0] - _EPS):
                    mejor = (x, w, h, rot)
            if mejor:
                elegido = (y, *mejor)
                break
        if elegido is None:             # no cabe en ningun hueco: al fondo
            fondo = max([margin] + [r["y"] + r["h"] + spacing for r in strip.rects])
            w, h, rot = orients[0]
            elegido = (fondo, x_min, w, h, rot)

        y, x, w, h, rot = elegido
        pisos[tam] = y
        rect = {"uid": c["uid"], "id": c["id"], "x": x, "y": y,
                "w": w, "h": h, "rotated": rot}
        placements.append(rect)
        strip.add(rect)
        anota_y(y)
        anota_y(y + h + spacing)

    bottom = 0.0
    for r in strip.rects:
        bottom = max(bottom, r["y"] + r["h"])
    total_len = (bottom + margin) if strip.rects else 0.0
    return placements, total_len, too_big


def overlaps(a, b, gap=0.0):
    """True si dos rectangulos {x,y,w,h} se pisan (con separacion opcional)."""
    return (a["x"] + a["w"] + gap > b["x"] + _EPS and
            b["x"] + b["w"] + gap > a["x"] + _EPS and
            a["y"] + a["h"] + gap > b["y"] + _EPS and
            b["y"] + b["h"] + gap > a["y"] + _EPS)
