# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [1.0.0] - 2026-09-30

Primera versión publicada. Reúne el trabajo desde julio de 2026.

### Corregido
- La descarga de planchas desde el celular daba 404 en el modo CMYK + White:
  la limpieza del nombre de archivo eliminaba el `+` de `..._CMYK+W.tif`.
- Exportar consumía 5.3 GB de RAM por metro de plancha. La conversión por
  franjas lo baja a 1.26 GB, con el mismo resultado píxel por píxel en el arte
  sólido.
- Abrir el programa por segunda vez borraba los diseños de la ventana que ya
  estaba abierta.
- Una cantidad o una medida absurdas podían colgar el servidor. Ahora hay topes
  de 2000 copias por fila y 500 cm por lado.

### Agregado
- 43 pruebas automáticas (`pytest`) e integración continua en GitHub Actions.
- Prueba de punta a punta del ejecutable empaquetado.
- Documentación: manual de uso, arquitectura, flujo de color y seguridad.

## 2026-09-20

### Cambiado
- La salida pasa a ser siempre **TIFF CMYK**, con conversión sRGB → Japan Web
  Coated, Perceptual + BPC, igual a Photoshop. La casilla *Canal White* elige
  entre 5 y 4 canales.

## 2026-09-14

### Agregado
- Aplicación de escritorio portable para Windows (PyInstaller + WebView2).
- Lectura de TIFF de Photoshop con el canal extra de base blanca, LZW, float y
  alpha premultiplicado.
- Aviso de bordes casi transparentes con corrección de un clic.
- Túnel a internet controlado desde la interfaz.

### Cambiado
- Acomodo *bottom-left fill* con piezas fijadas como obstáculos, en lugar de
  estantes: hasta 28 % menos película al fijar piezas.
- El servidor garantiza la cantidad de piezas: las fijadas inválidas vuelven al
  acomodo automático.

### Corregido
- Arrastrar piezas se colgaba si fallaba `setPointerCapture`.
- Un TIFF sin unidad de resolución se leía a 1 dpi (pieza de 15 metros).
- Las exportaciones fallidas dejaban archivos `.tif` corruptos con nombre válido.

## 2026-09-11

### Agregado
- Acceso desde celular e iPad con PIN, y publicación en internet con túnel de
  Cloudflare.
- Detección de la IP correcta de la red local con una VPN activa.

### Corregido (probado en MainTop 6.1)
- MainTop no usa el alpha como transparencia: la plancha salía negra.
- MainTop no decodifica el LZW de imagecodecs: se cambió a PACKBITS.
- El choke borraba la base blanca de los trazos finos.
