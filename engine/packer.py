"""Acomodo automatico de diseños en la plancha (algoritmo de estantes/shelf).

Todas las medidas se manejan en centimetros. El origen (0,0) es la esquina
superior izquierda de la plancha; la plancha crece hacia abajo (largo del rollo).

Soporta "piezas fijadas" (fixed): rectangulos ya colocados por el usuario que
el algoritmo trata como obstrucciones — el cursor de cada estante las salta
horizontalmente, y los estantes nuevos se prueban en TODOS los huecos libres
de arriba hacia abajo, no solo debajo del ultimo estante.
"""

_EPS = 1e-9
_MAX_SCAN = 200


def _blocking(x, y, w, h, rects, gap):
    """Devuelve el rect fijo que invade (x,y,w,h) con separacion `gap`, o None."""
    for r in rects:
        if (x + w + gap > r["x"] + _EPS and r["x"] + r["w"] + gap > x + _EPS and
                y + h + gap > r["y"] + _EPS and r["y"] + r["h"] + gap > y + _EPS):
            return r
    return None


def pack(items, sheet_w, spacing=0.5, margin=0.5, allow_rotate=True, fixed=None):
    """Acomoda los diseños y devuelve (colocaciones, largo_total_cm, no_caben).

    items: lista de dicts {uid, id, w, h, qty} en cm (solo las copias automaticas).
    fixed: lista de dicts {x, y, w, h, ...} ya colocados que hay que esquivar.
    Devuelve colocaciones como dicts {uid, id, x, y, w, h, rotated}.
    """
    fixed = fixed or []
    usable_w = sheet_w - 2 * margin
    copies = []
    too_big = []
    for it in items:
        w, h = float(it["w"]), float(it["h"])
        fits_normal = w <= usable_w
        fits_rotated = allow_rotate and h <= usable_w
        if not fits_normal and not fits_rotated:
            too_big.append(it["id"])
            continue
        for _ in range(int(it["qty"])):
            copies.append({"uid": it.get("uid"), "id": it["id"], "w": w, "h": h})

    # Los mas altos primero para que los estantes queden parejos
    copies.sort(key=lambda c: max(c["w"], c["h"]), reverse=True)

    shelves = []  # cada uno: {y, h, x} — siempre ordenados por y
    placements = []

    def scan_x(y, start_x, w, h):
        """Primera x >= start_x donde (w,h) cabe sin chocar con piezas fijas."""
        x = start_x
        for _ in range(_MAX_SCAN):
            if x + w > margin + usable_w + _EPS:
                return None
            b = _blocking(x, y, w, h, fixed, spacing)
            if b is None:
                return x
            x = b["x"] + b["w"] + spacing
        return None

    def band_free(y, h):
        """La franja [y, y+h) no pisa ningun estante ya creado."""
        return not any(y < s["y"] + s["h"] - _EPS and s["y"] < y + h - _EPS
                       for s in shelves)

    def try_place(c):
        orients = [(c["w"], c["h"], False)]
        if allow_rotate and (c["w"] != c["h"]):
            orients.append((c["h"], c["w"], True))

        # 1) estantes existentes: el que menos altura desperdicie
        best = None
        for shelf in shelves:
            for (w, h, rot) in orients:
                if w > usable_w or h > shelf["h"] + _EPS:
                    continue
                x = scan_x(shelf["y"], shelf["x"], w, h)
                if x is None:
                    continue
                waste = shelf["h"] - h
                if best is None or waste < best[0]:
                    best = (waste, shelf, x, w, h, rot)
        if best:
            _, shelf, x, w, h, rot = best
            placements.append({"uid": c["uid"], "id": c["id"], "x": x,
                               "y": shelf["y"], "w": w, "h": h, "rotated": rot})
            shelf["x"] = x + w + spacing
            return

        # 2) estante nuevo: se prueban todos los huecos de arriba hacia abajo,
        #    incluidos los que dejan las piezas fijadas. Sin esto, una pieza
        #    fijada lejos empujaba el resto al fondo y estiraba la plancha.
        cand = sorted((h, w, rot) for (w, h, rot) in orients if w <= usable_w)
        h, w, rot = cand[0]
        ys = {margin}
        ys.update(s["y"] + s["h"] + spacing for s in shelves)
        ys.update(r["y"] + r["h"] + spacing for r in fixed)
        for y in sorted(ys):
            if not band_free(y, h):
                continue
            x = scan_x(y, margin, w, h)
            if x is not None:
                shelves.append({"y": y, "h": h, "x": x + w + spacing})
                shelves.sort(key=lambda s: s["y"])
                placements.append({"uid": c["uid"], "id": c["id"], "x": x,
                                   "y": y, "w": w, "h": h, "rotated": rot})
                return

        # 3) ultimo recurso: debajo de absolutamente todo
        y = max([margin] +
                [s["y"] + s["h"] + spacing for s in shelves] +
                [r["y"] + r["h"] + spacing for r in fixed])
        shelves.append({"y": y, "h": h, "x": margin + w + spacing})
        shelves.sort(key=lambda s: s["y"])
        placements.append({"uid": c["uid"], "id": c["id"], "x": margin,
                           "y": y, "w": w, "h": h, "rotated": rot})

    for c in copies:
        try_place(c)

    bottom = 0.0
    for p in placements:
        bottom = max(bottom, p["y"] + p["h"])
    for r in fixed:
        bottom = max(bottom, r["y"] + r["h"])
    total_len = (bottom + margin) if (placements or fixed) else 0.0
    return placements, total_len, too_big


def overlaps(a, b, gap=0.0):
    """True si dos rectangulos {x,y,w,h} se pisan (con separacion opcional)."""
    return (a["x"] + a["w"] + gap > b["x"] + _EPS and
            b["x"] + b["w"] + gap > a["x"] + _EPS and
            a["y"] + a["h"] + gap > b["y"] + _EPS and
            b["y"] + b["h"] + gap > a["y"] + _EPS)
