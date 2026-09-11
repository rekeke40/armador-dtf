# Análisis: TIFF y CMYK para tinta DTF

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
| **PNG RGB + alpha** | Igual que el anterior pero más liviano. Si MainTop lo acepta, sirve igual. |
| **TIFF CMYK + White** | Compatibilidad con el flujo viejo de Photoshop. Solo si MainTop exige CMYK. |
| **TIFF color + blanco** | Dos archivos separados, para RIP que quieren el blanco aparte. |

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

## 6. Cómo comprobarlo en tu máquina

No tengo MainTop enfrente, así que esto hay que verificarlo con una prueba real.
Una sola plancha chica resuelve la duda:

1. Arma una plancha de prueba con: un azul rey, un verde neón, un naranja, una
   foto con tonos de piel y un negro sólido.
2. Expórtala **dos veces**, misma plancha: una en `TIFF RGB + alpha` y otra en
   `TIFF CMYK + White`.
3. Cárgalas en MainTop y revisa **antes de imprimir**:
   - ¿MainTop acepta el RGB y genera el blanco solo desde la transparencia?
   - ¿La vista previa del RGB se ve más viva que la del CMYK?
4. Imprime las dos en la misma película, mismo polvo, mismo horno, misma prensa.
5. Compara sobre la prenda.

Lo que espero que veas: el RGB claramente más saturado en azules, verdes y
naranjas, y prácticamente igual en pieles y negros.

Si MainTop **no** acepta el RGB o no genera bien el blanco, el modo CMYK sigue
ahí tal como estaba y no perdiste nada. Cuéntame el resultado y ajustamos.
