# Seguridad

El Armador DTF corre en la PC del taller, pero se puede usar desde el celular y
publicar en internet con un túnel de Cloudflare. Por eso no da por segura la red
local: **toda petición que no venga de la propia PC necesita el PIN**.

## Modelo de amenazas

| Activo | Riesgo | Control |
|---|---|---|
| Archivos de la PC | Leer o escribir fuera de la carpeta de salida | `safe_output_dir` resuelve la ruta y la encierra en `Planchas DTF`. Las descargas aceptan solo un nombre de archivo, sin rutas. |
| Acceso al programa | Entrar sin PIN a través del túnel | El túnel nunca cuenta como acceso local (ver abajo). |
| PIN | Fuerza bruta | 5 intentos fallidos por origen activan un bloqueo creciente, de 30 s a 5 min. La comparación es de tiempo constante (`hmac.compare_digest`). |
| Sesión | Robo de cookie por script | Cookie `HttpOnly`, `SameSite=Lax`, firmada con una llave aleatoria de 256 bits por instalación. |
| Disponibilidad | Pedidos absurdos que cuelguen el servidor | Topes de copias, medidas, dpi, choke y tamaño de carga (500 MB). |
| Interfaz | Inyección de HTML con un nombre de archivo | Los nombres se insertan como texto, nunca como HTML. |
| Acciones de la PC | Abrir carpetas o el túnel desde fuera | `/api/abrir-carpeta` y `/api/tunel` solo responden a la propia PC. |

## Qué cuenta como "la propia PC"

Entrar sin PIN es cómodo frente a la máquina, pero `cloudflared` se conecta al
servidor desde `127.0.0.1`. Con mirar solo la dirección de origen, todo lo que
llega por internet pasaría como local. `client_is_local()` exige **las tres
condiciones a la vez**:

1. Que no haya cabeceras de proxy (`CF-Connecting-IP`, `X-Forwarded-For`).
2. Que la petición salga de la interfaz loopback.
3. Que el navegador haya pedido `localhost` o `127.0.0.1` en la cabecera `Host`.

La tercera es la red de seguridad: una petición del túnel siempre trae
`Host: algo.trycloudflare.com`. Aunque alguien lograra quitarle las cabeceras
de proxy, seguiría sin pasar por local.

waitress limpia las cabeceras `X-Forwarded-*` por defecto. El servidor se
arranca con `clear_untrusted_proxy_headers=False` para que la primera condición
pueda verlas.

Las pruebas `tests/test_api.py` cubren cada caso: Host del túnel, cabeceras de
proxy, otra IP de la LAN, fuerza bruta y rutas fuera de la carpeta de salida.

## Secretos

- `config.json` contiene el PIN y la llave de sesión. Se genera en cada
  instalación, está en `.gitignore` y **nunca se incluye en el ZIP de
  distribución**.
- El PIN se puede fijar con `ARMADOR_PIN` sin tocar el archivo.
- Los perfiles ICC no se versionan: suelen tener licencia del fabricante.

## Túnel a internet

- Solo se abre a pedido explícito del operador, desde la propia PC y con
  confirmación. Nunca se abre solo.
- Se cierra al cerrar el programa.
- La dirección cambia en cada apertura y se borra al arrancar, para no mostrar
  nunca una dirección vieja.

## Reportar un problema

Si se encuentra una vulnerabilidad, conviene reportarla en privado al
mantenedor del repositorio antes de publicarla.
