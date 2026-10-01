# Flujo de color: TIFF y CMYK para tinta DTF

> **Bitácora técnica.** Este documento registra, en orden, las decisiones de
> color del Armador DTF y las mediciones que las respaldan. Las secciones 1 a 7
> describen la etapa en que la salida predeterminada fue RGB + alpha. **Desde el
> 2026-09-20 la salida es TIFF CMYK + White con Japan Web Coated**, por las
> razones de la [sección 8](#8-vuelta-a-cmyk-como-salida-única-2026-09-20). Se
> conserva el historial completo porque explica por qué cada parámetro del
> exportador tiene el valor que tiene.

Re-análisis del formato de salida del Armador DTF. Conclusión corta:
**estábamos tirando color a la basura antes de que MainTop lo viera.**

---

## 1. Qué estaba pasando

La versión anterior convertía la plancha a CMYK con `RSWOP.icm`, el perfil que
trae Windows. SWOP significa *Specifications for Web Offset Publications*: es el
estándar de **imprenta offset sobre papel couché**. Nada que ver con tinta
pigmentada sobre película PET transferida a tela.

Medí qué le hace esa conversión a los colores típicos de DTF (convertir a SWOP
y volver, midiendo la diferencia en ΔE76):

| Color | Original sRGB | Después de SWOP | ΔE |
|---|---|---|---|
| Azul rey | 20, 60, 200 | 43, 78, 132 | **51.6** |
| Morado | 110, 40, 180 | 101, 55, 126 | **35.5** |
| Verde neón | 30, 220, 70 | 72, 169, 64 | **30.2** |
| Naranja neón | 255, 110, 0 | 232, 79, 45 | **23.4** |
| Rosa mexicano | 235, 0, 140 | 224, 45, 125 | 12.0 |
| Cyan brillante | 0, 175, 240 | 1, 167, 216 | 9.2 |
| Rojo bandera | 230, 30, 40 | 222, 50, 48 | 8.6 |
| Amarillo limón | 250, 240, 0 | 249, 236, 33 | 4.6 |

Referencia: ΔE menor a 2 es imperceptible, de 2 a 5 se nota, arriba de 5 ya es
otro color.

Un **azul rey con ΔE 52 no es un azul rey apagado: es otro color**. Se convierte
en un azul pizarra sucio. El verde neón se vuelve verde olivo. El naranja neón
se vuelve rojo ladrillo. Justamente los colores por los que la gente paga DTF.

## 2. Por qué esto no se arregla en el RIP

Lo importante: **esa pérdida ocurre antes de que MainTop reciba el archivo.**

El flujo era:

```
Diseño RGB  →  [CMYK SWOP]  →  MainTop  →  [CMYK de la impresora]  →  tinta
                    ↑
            aquí se pierde el color, y ya no se recupera
```

MainTop tiene su propio perfil, su linealización y su límite de tinta para tu
impresora, tu tinta y tu película. Es el único que sabe de qué es capaz la
máquina. Pero no puede reconstruir un azul que el archivo ya no trae.

Son **dos conversiones con pérdida** donde debería haber una.

## 3. Lo que hace ahora el programa

El modo predeterminado pasó a ser **TIFF RGB + alpha**:

- RGB de 8 bits con perfil **sRGB incrustado**
- Canal **alpha** con la transparencia real del diseño
- 300 DPI, compresión LZW

```
Diseño RGB  →  TIFF RGB + alpha  →  MainTop  →  [CMYK de la impresora]  →  tinta
                                        ↑
                            una sola conversión, con el perfil correcto
```

Verificado sobre el archivo exportado: el azul rey sale **(20, 60, 200)** y el
verde neón **(30, 220, 70)** — exactamente los valores originales, sin tocar.

El canal alpha además le da a MainTop de dónde generar la base blanca, que es
como trabajan casi todos los RIP de DTF.

## 4. Los modos que quedan

| Modo | Cuándo usarlo |
|---|---|
| **TIFF RGB + alpha** | Predeterminado. Máximo color, el RIP hace su trabajo. |
| **TIFF CMYK + White** | Compatibilidad con el flujo viejo de Photoshop. Solo si MainTop exige CMYK. |
| **TIFF color + blanco** | Dos archivos separados, para RIP que quieren el blanco aparte. |

El modo **PNG se eliminó** tras la prueba: MainTop 6.1 no lo soporta.

Al elegir cualquier modo CMYK, el programa muestra un aviso naranja recordando
con qué perfil está convirtiendo y lo que eso implica.

**Si de verdad necesitas CMYK:** no uses SWOP. Consigue un perfil ICC hecho para
tu impresora + tinta + película (lo genera un servicio de perfilado con un
espectrofotómetro, o a veces lo da el proveedor de la tinta) y ponlo en
`perfiles/cmyk.icc`. El programa lo detecta solo y deja de mostrar el aviso.

## 5. Dos ajustes nuevos propios de DTF

**Espejo.** La película se imprime al revés porque el diseño se transfiere a la
prenda. Casi todos los RIP lo hacen ellos mismos. Viene **apagado**: si MainTop
ya espejea y lo activas aquí, sale al derecho y se arruina. Actívalo solo si
compruebas que MainTop *no* lo está haciendo.

**Limpiar bordes (%).** Los píxeles casi transparentes —sombras suaves,
degradados que se desvanecen, bordes muy difuminados— reciben poquísima tinta.
Poca tinta retiene poco polvo adhesivo, y esa zona termina despegándose o
saliéndose en el primer lavado. Este ajuste borra todo lo que esté por debajo
del porcentaje de opacidad que elijas. Viene en 0 (apagado); de 15 a 20 % es un
punto de partida razonable si ves bordes que se descaraman.

## 6. Qué pasó al probarlo en MainTop (2026-09-11)

Probado contra **MainTop DTP V6.1, build 20201109.0017**, con una plancha de
57 × 21 cm a 300 dpi: parches de azul rey, verde neón, naranja, morado, negro y
blanco, un degradado de piel, una mancha con borde difuminado y una pieza con
líneas de 1 a 12 px más texto de 6 pt.

**Resultado: los archivos sí se leen sin pasar por iCore ni Photoshop.** MainTop
los importa directo, con el tamaño físico exacto (569.976 × 209.973 mm para
57 × 21 cm) y los 300 dpi. Pero para llegar ahí hubo que corregir tres defectos
reales del exportador, que ya están corregidos y vueltos a verificar.

### 6.1 MainTop no usa el alpha como transparencia

Lee el TIFF RGBA como `RGB+Spot`: trata el cuarto canal como una tinta plana
aparte y pinta los canales de color tal como vengan. Como el exportador dejaba
esos píxeles en negro, **la plancha entera se cargaba como un rectángulo negro**.
Las opciones `Black/White → Transparent` del *Picture Frame Attribution* salen
deshabilitadas para este tipo de archivo, así que no hay manera de arreglarlo
desde MainTop.

Corregido: el arte se compone sobre **blanco** antes de guardar, conservando el
canal alpha intacto. Sobre blanco, "sin arte" equivale a "sin tinta" en cuanto el
RIP convierte a CMYK. De paso arregló los bordes difuminados, que antes salían a
plena tinta con canto duro y ahora llegan al RIP como el porcentaje de tinta que
les toca.

### 6.2 MainTop no decodifica el LZW que escribía el programa

Los modos CMYK salían con una caja con una X en vez de la imagen. No es el quinto
canal, ni el tamaño de tira, ni los tags de tinta plana: es el **compresor**.

| Cómo se escribe el TIFF | MainTop |
|---|---|
| LZW de Pillow/libtiff | lo lee |
| LZW de `tifffile`/imagecodecs | caja con X, sin mensaje de error |
| Sin comprimir | lo lee |
| PACKBITS | lo lee |
| Deflate | *"Unsupported file format or file format error!"* |

Los modos CMYK eran los únicos que pasaban por `tifffile`, por eso eran justo los
que fallaban. Corregido: esas salidas usan **PACKBITS**. Cuesta tamaño (8 MB →
37 MB en la plancha de prueba) pero es la única compresión que MainTop traga.
**No volver a poner `lzw` ni `deflate` ahí.**

### 6.3 El choke borraba el blanco de los trazos finos

Con choke = 2 px, las líneas de 1 y 2 px quedaban con tinta de color y **cero
blanco debajo**. Sin base blanca esa tinta no transfiere a la prenda.

Corregido: se contrae el blanco y se devuelve completo solo en las zonas finas,
que son las que la apertura morfológica deja fuera. Las zonas sólidas conservan
su choke, y el blanco sigue sin salirse del arte en ningún píxel.

### 6.4 Otros dos arreglos

- **PNG fuera del flujo de impresión.** MainTop no lo soporta: no aparece en su
  lista de formatos ni en el explorador de su diálogo de importar. (Desde el
  2026-09-14 vuelve a existir como salida secundaria; ver la sección 7.4.)
- **Modo de dos archivos.** El archivo de color llevaba también el canal White,
  además del archivo de blanco aparte. Un RIP que cargara los dos habría puesto
  dos bases blancas. Ahora el archivo de color sale con 4 canales limpios.

### 6.5 Ajuste que hay que tocar en MainTop

En `Options > Picture Display`, **activar "Images Embedded ICC"**. Viene apagado,
y apagado MainTop ignora el perfil sRGB incrustado en el TIFF, que es justamente
la pieza que hace funcionar la estrategia de color de este documento.

### 6.6 Lo que todavía falta comprobar

La PC donde se hizo la prueba tiene MainTop en **modo Trial (sin dongle) y sin
impresora DTF instalada**. Se verificó que los archivos se leen, con la geometría,
la resolución y los canales correctos. **No** se pudo verificar que el driver de
la impresora DTF genere de verdad la tinta blanca a partir del canal spot: esa
configuración vive en las propiedades de la impresora y hay que confirmarla en la
PC de impresión antes de tirar producción.

Si resultara que el RIP no toma el blanco del canal spot del modo RGB, el modo
**TIFF CMYK + White** sí lleva el canal declarado explícitamente como tinta
`White` (InkSet CMYK, NumberOfInks 5, InkNames y recursos de Photoshop), a costa
del color que documenta la sección 1.

---

## 7. Corrección de defectos del 2026-09-14

Cuatro problemas reportados desde producción, todos medidos antes y después.

### 7.1 Leer TIFF como los lee iCore

Pillow, que es con lo que se abrían los archivos, se queda corto justo con los
TIFF que manda una imprenta:

| Archivo | Antes | Ahora |
|---|---|---|
| CMYK + 5º canal (base blanca de Photoshop) | se leía **opaco**: rectángulo sólido | transparencia correcta |
| Ese mismo archivo con LZW | `cannot identify image file` | abre |
| TIFF float32 | `cannot identify image file` | abre |
| alpha premultiplicado (associated) | bordes suaves oscurecidos | se des-premultiplica |
| TIFF sin unidad de resolución | dpi = 1 → pieza de **15 metros** de ancho | 300 dpi → tamaño real |
| Resolución en cm (unidad 3) | — | convertida a dpi |

El caso grave era el primero: el 5º canal de un TIFF CMYK de Photoshop es la
base blanca, o sea el recorte del arte. Tirarlo convierte cualquier diseño en un
rectángulo con fondo. Se comprobó con las propias planchas del programa:
`FIX_2_CMYK_WHITE.tif` se leía con 0 % de transparencia y ahora se lee con
57 %, el mismo valor que su gemelo RGB `FIX_1_RGB_ALPHA.tif` (56 %).

Cuando el TIFF trae más canales de los que le corresponden a su espacio de
color, se lee con **tifffile** y se arma el RGBA a mano: el canal extra se toma
como transparencia si viene declarado como alpha, si Photoshop lo llamó
White/Blanco/Spot, o si parece una máscara de cobertura. Lo demás sigue pasando
por Pillow.

### 7.2 El acomodo perdía película al fijar piezas

El algoritmo era de **estantes**: franjas horizontales de alto uniforme. Una
pieza fijada a mano en mitad de una franja la invalidaba entera y las copias
restantes se iban al fondo de la plancha.

Se cambió por **abajo-izquierda con obstáculos** (bottom-left fill): cada pieza
entra en la posición válida más alta y, a igual altura, la más a la izquierda;
las piezas fijadas son simples obstáculos que se rellenan por los lados.

| Caso (rollo de 57 cm) | Estantes | Abajo-izquierda |
|---|---|---|
| 12 piezas de 20 × 13.3 cm | 83.5 cm | **69.7 cm** (−17 %) |
| las mismas, con 1 pieza fijada | 97.3 cm | **69.7 cm** (−28 %) |
| pedido mezclado (32 piezas) | 163 cm | **153 cm** (−6 %) |
| el mismo, con 2 piezas fijadas | 199.5 cm | **187.5 cm** (−6 %) |

Fijar una pieza ya **no cuesta película**: en el primer caso el largo es el
mismo que en automático.

Es además más rápido en los pedidos grandes (800 piezas: 40 ms → 12 ms) gracias
a que recuerda, por tamaño de pieza, desde qué altura vale la pena volver a
buscar. Comprobado con 400 casos aleatorios: ni un solape, ni una pieza fuera de
la plancha, ni una cantidad equivocada, y el resultado es siempre el mismo para
la misma entrada (lo que se ve en pantalla es lo que se exporta).

### 7.3 La cantidad no siempre era la cantidad

Las piezas fijadas las mandaba el navegador y el servidor se fiaba de ellas. Si
la lista llegaba mal, la plancha salía mal:

- más piezas fijadas que la cantidad de la fila → se imprimían **todas** (piezas
  de más);
- dos fijadas en la misma posición, o achicar el ancho del rollo hasta que dos
  se encimaban → **piezas encimadas**, que se ven como piezas de menos;
- una fijada de una fila borrada o con el tamaño viejo → pieza fantasma.

Ahora el servidor valida cada pieza fijada (que su fila exista, que conserve el
tamaño, que quepa a lo ancho, que no se encime con otra, que no pase de la
cantidad pedida) y la que no pasa **no se tira: vuelve al montón automático**.
La plancha lleva siempre exactamente la cantidad pedida. La interfaz se
sincroniza con lo que el servidor aceptó y avisa cuántas se liberaron.

### 7.4 PNG de vuelta, pero sin engañar

Se confirmó en la máquina, abriendo el diálogo *Import Graph File* de MainTop
6.1: su lista de formatos es TIFF, JPEG, JPEG2000, BMP, PCX, GIF, TGA, PSD/PSB,
PS/EPS/PDF/DCS, HP-GL2, ADI, GMF, AI, DXF, WMF, EMF, SVG y PLF. **PNG no
aparece**, y un `.png` puesto en la carpeta ni siquiera se lista.

Aun así el modo PNG vuelve como salida secundaria, porque es el formato que abre
cualquiera (mandárselo al cliente, revisarlo en el celular, pasarlo a otro RIP).
La interfaz avisa en rojo que MainTop no lo abre. A diferencia de los TIFF, el
PNG **no** se aplana sobre blanco: lleva transparencia de verdad, que es lo
único que lo hace útil fuera de MainTop.

### 7.5 Arrastrar piezas se quedaba a medias

El arrastre escuchaba los eventos en la propia pieza y dependía de
`setPointerCapture`. Si la captura fallaba, la pieza dejaba de recibir eventos en
cuanto el cursor salía de ella y el arrastre se colgaba sin fijar nada. Además,
si el acomodo terminaba de recalcularse en pleno arrastre, la pieza que el
usuario tenía agarrada se destruía al redibujar.

Ahora los eventos se escuchan en la ventana, el redibujado espera a que termine
el arrastre, y un `pointercancel` (el navegador del iPad se queda con el gesto)
respeta la última posición válida en vez de tirar el arrastre.

### 7.6 Comprobado en MainTop el 2026-09-14

La plancha nueva (`NUEVO_1_RGB.tif`, 57 × 47.66 cm, 10 piezas, una de ellas
fijada a mano) se importó en MainTop DTP 6.1. El propio diálogo de importación
la describe así:

```
Color:       RGB+Spot
Resolution:  300 x 300 dpi
Image Size:  569.976mm x 476.5886mm    6732 x 5629 pixels
```

569.976 mm × 476.5886 mm son exactamente los 57 × 47.66 cm que reporta el
Armador. Las piezas que venían de un TIFF CMYK con canal blanco entraron
recortadas, sin el rectángulo de fondo que salía antes.

---

## 8. Vuelta a CMYK como salida única (2026-09-20)

Las secciones anteriores recomendaban dejar la conversión a CMYK en manos del
RIP. En producción manda otra restricción: el flujo del taller **ya estaba
calibrado sobre archivos CMYK convertidos en Photoshop**, y el operador ajusta
MainTop, la tinta y la película contra ese resultado. Lo que hace falta no es
"el mejor color posible en abstracto", sino **el mismo color que ya sale bien**,
sin depender de la PC que tiene Photoshop.

Por eso la exportación pasó a ser siempre TIFF CMYK, replicando la conversión de
Photoshop con su configuración real:

| Parámetro | Photoshop (configuración del taller) | Armador DTF |
|---|---|---|
| Perfil de destino | Japan Web Coated (Ad) | el mismo, en `perfiles/cmyk.icc` |
| Propósito | Perceptual | Perceptual (seleccionable) |
| Compensación de punto negro | activada | activada |
| Motor | Adobe ACE | Little CMS (Pillow `ImageCms`) |
| Origen | sRGB | sRGB |

**Resultado medido:** sobre 9 colores sólidos de prueba, los valores CMYK que
produce el Armador coinciden con los de Photoshop. Entre motores de color puede
haber ±1–2 puntos por la interpolación de las tablas; no es visible en
impresión.

Lo que se conserva de la etapa anterior:

- **SWOP sigue siendo mala idea** (sección 1). Sin `cmyk.icc` el programa cae
  a `RSWOP.icm` de Windows solo para no fallar.
- **PACKBITS obligatorio** (sección 6.2). MainTop no lee el LZW de imagecodecs.
- **Canal White como tinta plana declarada**: InkSet CMYK, NumberOfInks 5,
  InkNames `Cyan, Magenta, Yellow, Black, White` y los recursos de Photoshop
  (tag 34377) para que el canal aparezca con su nombre.
- **Choke que respeta trazos finos** (sección 6.3), 1 px por omisión.

La interfaz se simplificó en consecuencia: desapareció el selector de formato y
quedó una casilla **Canal White** (5 canales) o CMYK puro (4 canales).

## 9. Exportación por franjas (2026-09-29)

Convertir la plancha completa de un golpe en `float32` consumía **5.3 GB de RAM
por metro de plancha** (medido con el contador de memoria del proceso). Una
plancha de metro y medio cerraba el programa en una PC de 8 GB, que es lo
normal en un taller.

`export_tiff` ahora convierte en franjas de 512 filas, directo a `uint8`, con
una sola transformación de color construida al inicio. El canal White se
calcula también por franjas, con un margen de `2 × choke` filas para que la
apertura morfológica dé exactamente el mismo resultado que sobre la plancha
entera.

| Plancha (57 cm, 300 dpi) | Antes | Ahora |
|---|---|---|
| 1 m | 5.35 GB | **1.26 GB** |
| 3 m | 15.23 GB | **2.69 GB** |

El tiempo de exportación no cambió. Fidelidad comprobada píxel por píxel contra
la conversión de la plancha entera:

- donde el arte es opaco o vacío (alpha 0 o 255): **0 diferencias**;
- en bordes semitransparentes: ±1 punto, porque ahora se redondea en vez de
  truncar;
- canal White: idéntico.

La prueba `test_franjas_dan_el_mismo_resultado_que_la_plancha_entera` vigila
que esto no cambie.
