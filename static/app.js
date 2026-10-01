// Armador DTF — lógica de la interfaz

// Cada fila de la tabla es un uso de un diseño (el mismo archivo puede estar
// en dos filas con distinto tamaño/cantidad vía "duplicar").
const rows = [];   // {uid, id, name, thumb, aspect, w_cm, h_cm, qty}
// Piezas fijadas manualmente en la plancha (modo semi-automático).
const locks = [];  // {key, uid, id, x, y, w, h, rotated}

let cfg = {};
let outputDir = null;
let layoutTimer = null;
let layoutAbort = null;   // AbortController de /api/layout en curso
let masksAbort = null;
let lastData = null;      // última respuesta de /api/layout
let lastScale = 1;
let whiteView = false;
let whiteMasks = {};      // maskKey -> dataURL de la máscara roja
let selected = null;      // {p, el} pieza seleccionada en la plancha
let arrastrando = false;  // hay una pieza en la mano ahora mismo
let redibujoPendiente = false;
let uidSeq = 1, lockSeq = 1;
let pendingFocus = null;  // {i, f} celda a enfocar tras re-render (Enter)

const $ = (id) => document.getElementById(id);

// Si la sesión caduca, cualquier llamada devuelve 401: volver al PIN.
const _fetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _fetch(...args);
  if (res.status === 401 && String(args[0]).startsWith("/api/")) {
    location.href = "/login";
    throw new Error("Sesión expirada");
  }
  return res;
};

fetch("/api/config").then(r => r.json()).then(c => {
  cfg = c;
  outputDir = c.output_dir;
  if (!c.is_server_pc) $("btnCarpeta").style.display = "none";
  updateCmykWarn();
}).catch(() => {});

// ---------- pestañas (celular) ----------
$("tabbar").querySelectorAll("button").forEach(b => {
  b.onclick = () => {
    $("tabbar").querySelectorAll("button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    document.body.classList.toggle("tab-designs", b.dataset.tab === "designs");
    document.body.classList.toggle("tab-sheet", b.dataset.tab === "sheet");
    if (b.dataset.tab === "sheet" && lastData) requestAnimationFrame(drawSheet);
  };
});
$("btnSettings").onclick = () => document.body.classList.toggle("show-settings");

// ---------- carga de archivos ----------
const dropzone = $("dropzone");
const fileInput = $("fileInput");
$("btnBrowse").onclick = () => fileInput.click();
dropzone.onclick = (e) => { if (e.target === dropzone || e.target.tagName === "P") fileInput.click(); };
fileInput.onchange = () => { uploadFiles(fileInput.files); fileInput.value = ""; };

["dragenter", "dragover"].forEach(ev =>
  document.body.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add("drag"); }));
document.body.addEventListener("dragleave", e => { e.preventDefault(); dropzone.classList.remove("drag"); });
document.body.addEventListener("drop", e => {
  e.preventDefault();
  dropzone.classList.remove("drag");
  if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});

async function uploadFiles(files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  toast("Cargando diseños…");
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    if (!res.ok) throw new Error("el servidor rechazó los archivos (¿muy pesados?)");
    const list = await res.json();
    const errors = [];
    for (const d of list) {
      if (d.error) { errors.push(`${d.name}: ${d.error}`); continue; }
      const aspect = d.h_px / d.w_px;
      const w = Math.min(Math.max(d.native_w_cm || 10, 2), 28);
      rows.push({
        uid: "u" + (uidSeq++), id: d.id, name: d.name, thumb: d.thumb,
        aspect, w_cm: round2(w), h_cm: round2(w * aspect), qty: 1,
        borde_suave: d.borde_suave || 0,
      });
    }
    renderTable();
    avisoBordes();
    scheduleLayout();
    toast(errors.length ? errors.join(" · ") : `${list.length} diseño(s) agregado(s)`);
  } catch (e) {
    toast("⚠ Error al cargar archivos: " + e.message);
  }
}

// ---------- tabla de diseños ----------
function renderTable() {
  const tbody = $("designRows");
  tbody.innerHTML = "";
  rows.forEach((d, i) => {
    const tr = document.createElement("tr");
    tr.dataset.i = i;

    const tdThumb = document.createElement("td");
    const img = document.createElement("img");
    img.className = "mini";
    img.src = d.thumb;
    tdThumb.appendChild(img);

    const tdName = document.createElement("td");
    const nameDiv = document.createElement("div");
    nameDiv.className = "fname";
    nameDiv.textContent = d.name;      // sin innerHTML: a prueba de XSS
    nameDiv.title = d.name;
    tdName.appendChild(nameDiv);

    const tdAct = document.createElement("td");
    const bDup = document.createElement("button");
    bDup.className = "rowbtn";
    bDup.title = "Duplicar fila";
    bDup.textContent = "⧉";
    bDup.onclick = () => {
      rows.splice(i + 1, 0, { ...d, uid: "u" + (uidSeq++), qty: 1 });
      renderTable();
      scheduleLayout();
    };
    const bDel = document.createElement("button");
    bDel.className = "rowbtn del";
    bDel.title = "Quitar fila";
    bDel.textContent = "✕";
    bDel.onclick = () => removeRow(i);
    tdAct.append(bDup, bDel);

    tr.append(tdThumb, tdName, cellInput(d, i, "w", d.w_cm, 0.5),
              cellInput(d, i, "h", d.h_cm, 0.5), cellInput(d, i, "qty", d.qty, 1), tdAct);
    tbody.appendChild(tr);
  });
  renderTotals();
  if (pendingFocus) {
    const { i, f } = pendingFocus;
    pendingFocus = null;
    const inp = tbody.querySelector(`tr[data-i="${i}"] input[data-f="${f}"]`);
    if (inp) { inp.focus(); inp.select(); }
  }
}

function cellInput(d, i, field, value, step) {
  const td = document.createElement("td");
  const inp = document.createElement("input");
  inp.type = "number";
  inp.min = field === "qty" ? 0 : 0.5;
  inp.step = step;
  inp.value = value;
  inp.dataset.f = field;
  inp.inputMode = field === "qty" ? "numeric" : "decimal";
  if (field === "qty") inp.className = "qty";
  inp.onchange = () => {
    const v = parseFloat(inp.value) || 0;
    if (field === "w" || field === "h") {
      if (field === "w") { d.w_cm = round2(Math.max(v, 0.5)); d.h_cm = round2(d.w_cm * d.aspect); }
      else { d.h_cm = round2(Math.max(v, 0.5)); d.w_cm = round2(d.h_cm / d.aspect); }
      // una posición fijada con otro tamaño ya no es válida
      if (pruneLocks(l => l.uid !== d.uid) > 0) toast("Se liberaron las piezas fijadas de esa fila (cambió el tamaño)");
    } else {
      d.qty = Math.max(0, Math.round(v));
      trimLocksToQty(d);
    }
    renderTable();
    scheduleLayout();
  };
  inp.onkeydown = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    pendingFocus = { i: i + 1, f: field };
    inp.blur();                       // dispara change si hubo edición
    if (pendingFocus) {               // no hubo re-render: enfocar a mano
      const nxt = document.querySelector(`#designRows tr[data-i="${i + 1}"] input[data-f="${field}"]`);
      pendingFocus = null;
      if (nxt) { nxt.focus(); nxt.select(); }
    }
  };
  td.appendChild(inp);
  return td;
}

function removeRow(i) {
  const d = rows[i];
  rows.splice(i, 1);
  pruneLocks(l => l.uid !== d.uid);
  if (!rows.some(r => r.id === d.id)) {
    fetch("/api/quitar", jsonReq({ id: d.id })).catch(() => {});
    for (const k of Object.keys(whiteMasks)) if (k.startsWith(d.id + "_")) delete whiteMasks[k];
  }
  renderTable();
  avisoBordes();
  scheduleLayout();
}

function pruneLocks(keepFn) {
  const before = locks.length;
  for (let i = locks.length - 1; i >= 0; i--) if (!keepFn(locks[i])) locks.splice(i, 1);
  return before - locks.length;
}

function trimLocksToQty(d) {
  let n = locks.filter(l => l.uid === d.uid).length;
  for (let i = locks.length - 1; i >= 0 && n > d.qty; i--) {
    if (locks[i].uid === d.uid) { locks.splice(i, 1); n--; }
  }
}

function renderTotals() {
  const piezas = rows.reduce((a, d) => a + d.qty, 0);
  const m2 = rows.reduce((a, d) => a + d.w_cm * d.h_cm * d.qty, 0) / 10000;
  const largo = lastData ? `${lastData.length_cm} cm` : "—";
  $("tableTotals").innerHTML = piezas
    ? `<span><strong>${piezas}</strong> piezas · <strong>${m2.toFixed(2)}</strong> m² de arte</span>` +
      `<span>Largo: <strong>${esc(largo)}</strong></span>`
    : "";
}

// Aviso de bordes casi transparentes.
//
// En DTF esas zonas reciben poca tinta, retienen poco polvo adhesivo y son las
// que se despegan o se van en el primer lavado. El servidor mide, al subir el
// archivo, qué parte del arte cae en esa franja; aquí solo se avisa cuando la
// cifra es alta y "Limpiar bordes" sigue apagado.
const BORDE_RIESGO = 10;      // % del arte a partir del cual vale la pena avisar

function avisoBordes() {
  const caja = $("bordeWarn");
  const umbral = parseInt($("setThreshold").value) || 0;
  const malos = rows.filter(d => (d.borde_suave || 0) >= BORDE_RIESGO);
  if (!malos.length || umbral >= 15) {
    caja.classList.add("hidden");
    return;
  }
  malos.sort((a, b) => b.borde_suave - a.borde_suave);
  const peor = malos[0];
  const otros = malos.length > 1 ? ` (y ${malos.length - 1} más)` : "";
  caja.innerHTML =
    `⚠ <strong>${esc(peor.name)}</strong>${esc(otros)}: el ${peor.borde_suave} % de su arte son ` +
    `bordes casi transparentes. En DTF esas zonas retienen poco polvo y se despegan al lavar. ` +
    `<button id="btnLimpiar20" class="secondary">Limpiar bordes al 20 %</button>`;
  caja.classList.remove("hidden");
  $("btnLimpiar20").onclick = () => {
    $("setThreshold").value = 20;
    $("setThreshold").dispatchEvent(new Event("change"));
    avisoBordes();
    toast("Bordes por debajo del 20 % de opacidad: fuera de la plancha");
  };
}

// ---------- ajustes ----------
function sheetSettings() {
  return {
    width_cm: parseFloat($("setWidth").value) || 57,
    spacing_cm: parseFloat($("setSpacing").value) || 0,
    margin_cm: parseFloat($("setMargin").value) || 0,
    dpi: parseInt($("setDpi").value) || 300,
    choke_px: parseInt($("setChoke").value) || 0,
    alpha_threshold: parseInt($("setThreshold").value) || 0,
    allow_rotate: $("setRotate").checked,
    mirror: $("setMirror").checked,
    con_blanco: $("setConBlanco").checked,
    intent: $("setIntent").value,
  };
}

function payload() {
  return {
    items: rows.map(d => ({ uid: d.uid, id: d.id, w_cm: d.w_cm, h_cm: d.h_cm, qty: d.qty })),
    fixed: locks,
    sheet: sheetSettings(),
  };
}

["setWidth", "setSpacing", "setMargin", "setRotate"].forEach(id =>
  $(id).addEventListener("change", scheduleLayout));
["setDpi", "setChoke", "setThreshold"].forEach(id =>
  $(id).addEventListener("change", () => {
    whiteMasks = {};
    if (whiteView) ensureMasks();
  }));
$("setThreshold").addEventListener("change", avisoBordes);

$("setConBlanco").addEventListener("change", () => {
  const on = $("setConBlanco").checked;
  $("whiteControls").style.display = on ? "" : "none";
  if (!on && whiteView) {
    whiteView = false;
    $("btnWhite").classList.remove("active");
    if (lastData) drawSheet();
  }
});

function updateCmykWarn() {
  const warn = $("cmykWarn");
  if (!cfg.cmyk_profile) {
    warn.textContent = "⚠ No hay perfil CMYK instalado: la conversión será aproximada. " +
                       "Pon tu perfil en perfiles/cmyk.icc.";
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }
}

$("btnAcomodar").onclick = doLayout;
$("btnLiberar").onclick = () => {
  if (!locks.length) { toast("No hay piezas fijadas"); return; }
  locks.length = 0;
  toast("🔓 Todas las piezas liberadas: acomodo automático");
  doLayout();
};

$("btnInfo").onclick = async () => {
  try { cfg = await (await fetch("/api/config")).json(); } catch (_) { /* con lo que haya */ }
  modalRed();
};

// Modal de "abrir en otro dispositivo": direcciones, PIN y el túnel.
// El PIN se muestra aquí porque la app de escritorio ya no tiene ventana negra
// donde leerlo, y sin él no se puede entrar desde el celular.
function modalRed() {
  const alts = (cfg.lan_alternativas || []).map(u => `<code>${esc(u)}</code>`).join(" ");
  const pub = cfg.url_publica
    ? `<p><strong>Desde cualquier lugar</strong> (internet, datos móviles):</p>
       <p><code>${esc(cfg.url_publica)}</code></p>
       <p class="nota">Esta dirección cambia cada vez que abres el túnel.</p>`
    : `<p>Ahora mismo <strong>solo funciona en tu red</strong>.</p>`;

  let boton = "";
  if (cfg.is_server_pc && cfg.tunel_disponible) {
    boton = `<p style="margin-top:10px"><button id="btnTunel" class="secondary">${
      cfg.url_publica ? "⏹ Cerrar el túnel" : "🌐 Publicar en internet"}</button></p>`;
  } else if (cfg.is_server_pc) {
    boton = `<p class="nota" style="margin-top:10px">Para publicarlo en internet hace
             falta <code>cloudflared.exe</code> en la carpeta del programa.</p>`;
  }

  showModal("📱 Abrir en otro dispositivo", `
    ${pub}
    ${boton}
    <hr style="border-color:var(--border);margin:14px 0">
    <p><strong>En la misma WiFi</strong> que esta PC:</p>
    <p><code>${esc(cfg.lan_url || "—")}</code></p>
    ${alts ? `<p style="margin-top:8px">Si esa no abre, también responde en:<br>${alts}</p>` : ""}
    ${cfg.pin ? `<p style="margin-top:12px">PIN para entrar desde el celular o el iPad:<br>
       <code style="font-size:1.4em;letter-spacing:.14em">${esc(cfg.pin)}</code></p>` : ""}
    <p class="nota" style="margin-top:12px">En esta PC no pide PIN. La plancha siempre se
    genera y se guarda <strong>aquí</strong>, que es donde está el RIP.</p>`);

  const btn = $("btnTunel");
  if (btn) btn.onclick = () => alternarTunel(btn);
}

async function alternarTunel(btn) {
  const abriendo = !cfg.url_publica;
  if (abriendo && !confirm(
      "Se va a publicar el programa en internet con una dirección de Cloudflare.\n\n" +
      "Cualquiera que tenga el enlace Y el PIN podrá entrar mientras el túnel " +
      "esté abierto.\n\n¿Continuar?")) return;
  btn.disabled = true;
  btn.textContent = abriendo ? "Abriendo el túnel…" : "Cerrando…";
  try {
    const r = await fetch("/api/tunel", jsonReq({ accion: abriendo ? "abrir" : "cerrar" }));
    const d = await r.json();
    if (d.error) { toast("⚠ " + d.error); btn.disabled = false; modalRed(); return; }
    cfg.url_publica = d.url || null;
    toast(abriendo ? "🌐 Publicado en internet" : "⏹ Túnel cerrado");
    modalRed();
  } catch (e) {
    toast("⚠ No se pudo cambiar el túnel: " + e.message);
    btn.disabled = false;
  }
}

// ---------- acomodo ----------
function scheduleLayout() {
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(doLayout, 250);
}

async function doLayout() {
  if (layoutAbort) layoutAbort.abort();   // cancelar la petición anterior
  layoutAbort = new AbortController();
  try {
    const res = await fetch("/api/layout", { ...jsonReq(payload()), signal: layoutAbort.signal });
    const data = await res.json();
    if (data.error) { toast("⚠ " + data.error); return; }
    sincronizarFijadas(data);
    lastData = data;
    drawSheet();
    renderTotals();
    if (whiteView) ensureMasks();
  } catch (e) {
    if (e.name !== "AbortError") toast("⚠ Error al acomodar: " + e.message);
  }
}

// El servidor es el que manda: si soltó una pieza fijada (ya no cabe, se
// encimaba con otra o su fila cambió) hay que soltarla aquí también, o la
// vista previa mostraría piezas que la plancha exportada no va a llevar.
function sincronizarFijadas(data) {
  const vivas = new Map();
  for (const p of data.placements || []) if (p.locked) vivas.set(p.key, p);
  const sueltas = pruneLocks(l => vivas.has(l.key));
  for (const l of locks) {
    const p = vivas.get(l.key);
    if (p) { l.x = p.x; l.y = p.y; }   // el servidor pudo recorrerla al borde
  }
  if (sueltas) toast(`🔓 ${sueltas} pieza(s) fijada(s) volvieron al acomodo automático`);
}

// ---------- vista previa ----------
function drawSheet() {
  // redibujar en pleno arrastre destruiría la pieza que el usuario tiene
  // agarrada y el arrastre se quedaría a medias
  if (arrastrando) { redibujoPendiente = true; return; }
  const data = lastData;
  const s = sheetSettings();
  const sheet = $("sheet");
  const wrap = $("previewWrap");
  deselect();
  if (!data || !data.placements.length) {
    sheet.style.width = "0"; sheet.style.height = "0"; sheet.innerHTML = "";
    $("statInfo").textContent = rows.some(d => d.qty > 0) ? "Nada que acomodar" : "Agrega diseños para comenzar";
    $("statLen").textContent = "";
    return;
  }
  const avail = Math.max(wrap.clientWidth - 40, 160);
  const scale = Math.min(avail / s.width_cm, 14);
  lastScale = scale;
  sheet.style.width = (s.width_cm * scale) + "px";
  sheet.style.height = (data.length_cm * scale) + "px";
  sheet.innerHTML = "";
  const byId = Object.fromEntries(rows.map(d => [d.id, d]));
  for (const p of data.placements) {
    const d = byId[p.id];
    if (!d) continue;
    const el = document.createElement("div");
    el.className = "item" + (p.locked ? " locked" : "");
    el.style.left = (p.x * scale) + "px";
    el.style.top = (p.y * scale) + "px";
    el.style.width = (p.w * scale) + "px";
    el.style.height = (p.h * scale) + "px";
    if (p.rotated) el.style.overflow = "hidden";
    el.appendChild(pieceImg(d.thumb, p, scale));
    const mask = whiteView && whiteMasks[maskKeyFor(p)];
    if (mask) {
      const ov = pieceImg(mask, p, scale);
      ov.classList.add("white-overlay");
      el.appendChild(ov);
    }
    if (p.locked) {
      const pin = document.createElement("div");
      pin.className = "pin";
      pin.textContent = "📌";
      el.appendChild(pin);
    }
    el.addEventListener("pointerdown", (e) => pieceDown(e, p, el));
    sheet.appendChild(el);
  }
  const nLocked = data.placements.filter(p => p.locked).length;
  const faltan = (data.pedidas || 0) - data.placements.length;
  $("statInfo").textContent =
    `${data.placements.length}${faltan > 0 ? ` de ${data.pedidas}` : ""} piezas` +
    (nLocked ? ` · 📌 ${nLocked} fijada(s)` : "") +
    (faltan > 0 ? ` · ⚠ ${faltan} no caben en el ancho` : "");
  $("statLen").textContent = `Largo: ${data.length_cm} cm`;
}

// imagen de una pieza (o su máscara), respetando la rotación de 90°
function pieceImg(src, p, scale) {
  const img = document.createElement("img");
  img.src = src;
  img.draggable = false;
  if (p.rotated) {
    img.style.position = "absolute";
    img.style.width = (p.h * scale) + "px";
    img.style.height = (p.w * scale) + "px";
    img.style.left = "50%";
    img.style.top = "50%";
    img.style.transform = "translate(-50%,-50%) rotate(90deg)";
  }
  return img;
}

function maskKeyFor(p) {
  const w = p.rotated ? p.h : p.w;
  const h = p.rotated ? p.w : p.h;
  return `${p.id}_${round2(w)}x${round2(h)}`;
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (lastData) drawSheet(); }, 150);
});

// ---------- canal White ----------
$("btnWhite").onclick = () => {
  whiteView = !whiteView;
  $("btnWhite").classList.toggle("active", whiteView);
  if (whiteView) ensureMasks();
  else if (lastData) drawSheet();
};

async function ensureMasks() {
  const s = sheetSettings();
  const need = {};
  for (const d of rows) {
    if (d.qty > 0) need[`${d.id}_${round2(d.w_cm)}x${round2(d.h_cm)}`] = { id: d.id, w_cm: d.w_cm, h_cm: d.h_cm };
  }
  const missing = Object.entries(need).filter(([k]) => !whiteMasks[k]).map(([, v]) => v);
  if (!missing.length) { if (lastData) drawSheet(); return; }
  if (masksAbort) masksAbort.abort();
  masksAbort = new AbortController();
  try {
    const res = await fetch("/api/preview-white", {
      ...jsonReq({ items: missing, dpi: s.dpi, choke_px: s.choke_px, alpha_threshold: s.alpha_threshold }),
      signal: masksAbort.signal,
    });
    const data = await res.json();
    if (data.error) { toast("⚠ " + data.error); return; }
    Object.assign(whiteMasks, data.masks);
    if (lastData) drawSheet();
  } catch (e) {
    if (e.name !== "AbortError") toast("⚠ Error en la vista del canal White: " + e.message);
  }
}

// ---------- selección, arrastre y rotación (ratón y táctil) ----------
function pieceDown(e, p, el) {
  if (e.button !== undefined && e.button !== 0) return;
  e.preventDefault();
  // Los listeners van en window y no en la pieza: setPointerCapture falla en
  // algunos navegadores (y en iPad al re-dibujar la plancha), y sin captura la
  // pieza dejaba de recibir pointermove en cuanto el dedo salía de ella — el
  // arrastre se quedaba colgado y no se podía fijar nada.
  try { el.setPointerCapture(e.pointerId); } catch (_) { /* no crítico */ }
  const startX = e.clientX, startY = e.clientY;
  const origLeft = parseFloat(el.style.left), origTop = parseFloat(el.style.top);
  let moved = false;
  let ultima = { x: origLeft, y: origTop };

  const quitar = () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", cancelado);
    arrastrando = false;
    if (redibujoPendiente) { redibujoPendiente = false; drawSheet(); }
  };

  const onMove = (ev) => {
    if (ev.pointerId !== e.pointerId) return;
    const dx = ev.clientX - startX, dy = ev.clientY - startY;
    if (!moved && Math.hypot(dx, dy) > 6) {
      moved = true; arrastrando = true;
      el.classList.add("dragging"); hideTools();
    }
    if (!moved) return;
    ev.preventDefault();
    ultima = { x: origLeft + dx, y: origTop + dy };
    el.style.left = ultima.x + "px";
    el.style.top = ultima.y + "px";
    const pos = dropPosition(p, ultima.x, ultima.y);
    el.classList.toggle("invalid", !!collidingLock(p, pos.x, pos.y));
  };

  const soltar = () => {
    el.classList.remove("dragging", "invalid");
    const pos = dropPosition(p, ultima.x, ultima.y);
    if (collidingLock(p, pos.x, pos.y)) {
      el.style.left = origLeft + "px";
      el.style.top = origTop + "px";
      toast("⚠ Ahí ya hay otra pieza fijada");
      if (lastData) drawSheet();
      return;
    }
    upsertLock(p, pos.x, pos.y);
    doLayout();
  };

  const finish = (ev) => {
    if (ev.pointerId !== e.pointerId) return;
    quitar();
    if (!moved) { selectPiece(p, el); return; }
    soltar();
  };

  // pointercancel lo dispara el navegador cuando se queda con el gesto (pasa
  // en el iPad). Si la pieza ya se había movido, se respeta el último sitio
  // válido en vez de tirar el arrastre a la basura.
  const cancelado = (ev) => {
    if (ev.pointerId !== e.pointerId) return;
    quitar();
    if (moved) soltar();
    else { el.style.left = origLeft + "px"; el.style.top = origTop + "px"; }
  };

  window.addEventListener("pointermove", onMove, { passive: false });
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", cancelado);
}

// convierte px de pantalla a cm dentro de la plancha, con topes
function dropPosition(p, leftPx, topPx) {
  const s = sheetSettings();
  const maxX = Math.max(s.margin_cm, s.width_cm - s.margin_cm - p.w);
  const maxY = Math.max(s.margin_cm, (lastData ? lastData.length_cm : 0) + 50);
  return {
    x: round2(clamp(leftPx / lastScale, s.margin_cm, maxX)),
    y: round2(clamp(topPx / lastScale, s.margin_cm, maxY)),
  };
}

// ¿la posición pisa otra pieza ya fijada?
function collidingLock(p, x, y, w, h) {
  const s = sheetSettings();
  const a = { x, y, w: w != null ? w : p.w, h: h != null ? h : p.h };
  return locks.find(l => l.key !== p.key && rectsOverlap(a, l, s.spacing_cm * 0.5));
}
function rectsOverlap(a, b, gap) {
  return a.x + a.w + gap > b.x + 1e-9 && b.x + b.w + gap > a.x + 1e-9 &&
         a.y + a.h + gap > b.y + 1e-9 && b.y + b.h + gap > a.y + 1e-9;
}

function upsertLock(p, x, y) {
  if (p.locked) {
    const lk = locks.find(l => l.key === p.key);
    if (lk) { lk.x = x; lk.y = y; }
  } else {
    locks.push({ key: "k" + (lockSeq++), uid: p.uid, id: p.id,
                 x, y, w: p.w, h: p.h, rotated: p.rotated });
    toast("📌 Pieza fijada — el acomodo automático la respetará");
  }
}

function selectPiece(p, el) {
  deselect();
  selected = { p, el };
  el.classList.add("selected");
  const r = el.getBoundingClientRect();
  const tools = $("pieceTools");
  tools.classList.remove("hidden");
  const tw = tools.offsetWidth || 100;
  tools.style.left = clamp(r.left, 6, window.innerWidth - tw - 6) + "px";
  tools.style.top = (r.top > 56 ? r.top - 46 : r.bottom + 8) + "px";
  $("ptRelease").style.display = p.locked ? "" : "none";
}

function deselect() {
  if (selected) selected.el.classList.remove("selected");
  selected = null;
  hideTools();
}
function hideTools() { $("pieceTools").classList.add("hidden"); }

$("previewWrap").addEventListener("pointerdown", (e) => {
  if (e.target.id === "previewWrap" || e.target.id === "sheet") deselect();
});
$("previewWrap").addEventListener("scroll", hideTools, { passive: true });

$("ptRotate").onclick = rotateSelected;
$("ptRelease").onclick = () => {
  if (!selected) return;
  pruneLocks(l => l.key !== selected.p.key);
  toast("🔓 Pieza liberada");
  doLayout();
};

function rotateSelected() {
  if (!selected) return;
  const p = selected.p;
  const s = sheetSettings();
  const nw = p.h, nh = p.w;
  const x = clamp(p.x, s.margin_cm, Math.max(s.margin_cm, s.width_cm - s.margin_cm - nw));
  if (collidingLock(p, x, p.y, nw, nh)) {
    toast("⚠ No cabe girada ahí: otra pieza fijada estorba");
    return;
  }
  if (p.locked) {
    const lk = locks.find(l => l.key === p.key);
    if (!lk) return;
    lk.w = nw; lk.h = nh; lk.x = x;
    lk.rotated = !lk.rotated;
  } else {
    locks.push({ key: "k" + (lockSeq++), uid: p.uid, id: p.id,
                 x, y: p.y, w: nw, h: nh, rotated: !p.rotated });
    toast("⟳ Pieza girada y fijada 📌");
  }
  doLayout();
}

window.addEventListener("keydown", (e) => {
  const tag = (document.activeElement || {}).tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  if ((e.key === "r" || e.key === "R") && selected) { e.preventDefault(); rotateSelected(); }
  if (e.key === "Escape") deselect();
});

// ---------- exportación ----------
$("btnExportar").onclick = async () => {
  if (!rows.some(d => d.qty > 0)) { toast("Agrega al menos un diseño"); return; }
  const btn = $("btnExportar");
  btn.disabled = true;
  const original = btn.innerHTML;
  btn.innerHTML = `<span class="spinner"></span>Generando…`;
  try {
    const body = payload();
    body.output_dir = outputDir;
    const res = await fetch("/api/export", jsonReq(body));
    const data = await res.json();
    if (data.error) { toast("⚠ " + data.error); return; }
    const links = data.files.map(f =>
      `<p>📄 <a href="/api/descargar?dir=${encodeURIComponent(data.dir)}&name=${encodeURIComponent(f.name)}"
         download>${esc(f.name)}</a> <span style="opacity:.7">(${f.mb} MB)</span></p>`).join("");
    showModal("✅ Plancha exportada", `
      <p class="ok">Largo final: ${data.length_cm} cm · ${data.size_px[0]}×${data.size_px[1]} px</p>
      ${links}
      <p style="margin-top:8px">Guardada en:<br><code>${esc(data.dir)}</code></p>
      ${data.mirror ? "<p>🪞 Exportada en espejo.</p>" : ""}
      ${data.too_big.length ? "<p>⚠ Algunos diseños no cupieron por el ancho.</p>" : ""}
      ${data.skipped ? `<p>⚠ ${data.skipped} pieza(s) omitidas (su diseño ya no existe).</p>` : ""}`);
  } catch (e) {
    toast("⚠ Error al exportar: " + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
};

function showModal(title, html) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = html;
  $("modal").classList.remove("hidden");
}
$("btnCerrarModal").onclick = () => $("modal").classList.add("hidden");
$("modal").addEventListener("pointerdown", (e) => {
  if (e.target.id === "modal") $("modal").classList.add("hidden");
});
$("btnCarpeta").onclick = async () => {
  const r = await fetch("/api/abrir-carpeta", jsonReq({ dir: outputDir }));
  const d = await r.json().catch(() => ({}));
  if (!d.ok) toast("⚠ " + (d.error || "No se pudo abrir la carpeta"));
};

// ---------- utilidades ----------
function jsonReq(body) {
  return { method: "POST", headers: { "Content-Type": "application/json" },
           body: JSON.stringify(body) };
}
function round2(v) { return Math.round(v * 100) / 100; }
function clamp(v, lo, hi) { return Math.min(Math.max(v, lo), hi); }
function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3500);
}
