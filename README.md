# Armador DTF

Arma planchas de producción DTF automáticamente: acomoda los diseños en el rollo,
calcula el largo y exporta el archivo listo para el RIP. Reemplaza el armado
manual en iCore.

Funciona en la PC, y desde el celular o el iPad conectados a la misma WiFi.

---

## Cómo se usa

1. Doble clic en el acceso directo **Armador DTF** del escritorio.
   Se abre solo en el navegador.
2. Arrastra tus PNG (con transparencia) a la zona de carga. También acepta TIFF y JPG.
3. A cada diseño ponle su **ancho o alto en cm** (el otro lado se calcula solo) y la
   **cantidad** de repeticiones.
4. La plancha se acomoda sola y ves la vista previa con el largo total.
5. **Exportar plancha** → el archivo queda en `Escritorio\Planchas DTF`.

### Desde el celular o el iPad

Toca el botón **📱** en la barra de arriba: te muestra la dirección que hay que
abrir en el navegador del dispositivo (algo como `http://192.168.1.50:8765`).
Tiene que estar en la misma WiFi que la PC.

La plancha siempre se genera y se guarda **en la PC**, que es donde está el RIP.
Desde el celular puedes armar, revisar, exportar y descargar el archivo.

> Cualquiera en esa red puede abrir el programa, así que úsalo en la WiFi de tu
> taller, no en una red pública.

## Hoja de diseños

Cada fila es un uso de un diseño: miniatura, nombre, ancho, alto y cantidad.

- **Enter** baja a la misma columna de la siguiente fila (como hoja de cálculo).
- **⧉ Duplicar** crea otra fila con el mismo archivo (el mismo diseño en dos tamaños).
- Abajo se ve el total de piezas, los m² de arte y el largo de plancha.

## Armado semi-automático

El acomodo es automático, pero puedes intervenir:

- **Arrastra** una pieza en la vista previa para fijarla 📌 donde quieras; el
  acomodo automático la respeta y acomoda el resto alrededor.
- **Toca** una pieza para seleccionarla; **⟳** (o la tecla **R**) la gira 90° y la fija.
- **🔓** libera la pieza seleccionada; **🔓 Liberar todo** vuelve al modo automático.
- Si una posición pisa otra pieza fijada, el programa la rechaza y avisa.
- Cambiar el *tamaño* de una fila libera sus piezas fijadas (la posición ya no
  sería válida); cambiar la *cantidad* las conserva.
- La exportación respeta exactamente lo que ves en pantalla.

## Ajustes

| Ajuste | Qué hace | Valor típico |
|---|---|---|
| Ancho útil | Ancho imprimible del rollo | 57 cm (rollo de 60) |
| Separación | Espacio entre diseños | 0.5 cm |
| Margen | Borde libre alrededor de la plancha | 0.5 cm |
| DPI | Resolución del archivo final | 300 |
| Limpiar bordes | Borra los píxeles por debajo de ese % de opacidad | 0, o 15–20 % |
| Girar 90° | Permite rotar diseños para aprovechar espacio | activado |
| Espejo | Espejea la película | **apagado** (ver abajo) |

**Espejo:** en DTF la película se imprime al revés porque el diseño se transfiere.
Casi todos los RIP lo hacen ellos mismos. Si MainTop ya espejea y además lo
activas aquí, el diseño sale al derecho y se arruina. Actívalo solo si
compruebas que MainTop *no* lo está haciendo.

**Limpiar bordes:** los píxeles casi transparentes reciben muy poca tinta,
retienen poco polvo adhesivo y terminan despegándose. Este ajuste los elimina.

## Formatos de salida

| Modo | Cuándo usarlo |
|---|---|
| **TIFF RGB + alpha** | Predeterminado. Máximo color: el RIP hace la conversión con su perfil. |
| **PNG RGB + alpha** | Igual pero más liviano. |
| **TIFF CMYK + White** | Compatibilidad con el flujo viejo de Photoshop (canal de tinta plana). |
| **TIFF color + blanco** | Dos archivos separados, para RIP que quieren el blanco aparte. |

Por qué el predeterminado cambió de CMYK a RGB está explicado con mediciones en
**[ANALISIS-COLOR.md](ANALISIS-COLOR.md)**. Resumen: convertir a CMYK SWOP antes
de mandar al RIP destruía los azules, verdes y naranjas saturados (ΔE de hasta
52), y esa pérdida ya no se recupera dentro de MainTop.

> **Antes de tirar producción:** exporta una plancha chica de prueba y confirma
> que MainTop la lee bien. El plan de prueba está al final de ANALISIS-COLOR.md.

## Canal White

El botón **👁 Canal White** pinta en rojo dónde llevará tinta blanca la plancha,
como Photoshop muestra un canal de tinta plana. Se calcula desde el alpha real de
cada diseño. Es una ayuda visual; el blanco lo genera el RIP.

## Perfil de color CMYK

Solo importa si usas un modo CMYK. Copia el perfil de tu impresora a
`perfiles/cmyk.icc` (instrucciones en `perfiles/LEEME.txt`). Sin eso se usa el
perfil SWOP de Windows, que **no** es adecuado para DTF.

## Instalación

Requiere Python 3.10 o superior.

```bash
pip install -r requirements.txt
python app.py
```

En Windows basta con el `.bat` incluido. El programa escucha en el puerto 8765;
se puede cambiar con la variable de entorno `ARMADOR_PORT`.

Para que solo sea accesible desde la propia PC (sin celular ni iPad):
`ARMADOR_HOST=127.0.0.1`.
