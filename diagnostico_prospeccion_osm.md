# Diagnóstico — «OpenStreetMap/Overpass no respondió» en NEMET PROSPECTOR

**Fecha:** 30-sep-2026 · **Alcance:** módulo 🎯 Prospección Comercial (`app.py` + `prospeccion.py`)
desplegado en Streamlit Community Cloud.
**Regla de trabajo:** este documento no contiene ni reproduce credenciales. Los tokens de GitHub,
las contraseñas de `[auth]` y el `[email] password` viven solo en los **Secrets privados** del
despliegue (o en variables de entorno) y nunca deben copiarse a un issue, chat o commit.

---

## Síntoma

Al pulsar **🔎 Buscar negocios** la app muestra:

> OpenStreetMap/Overpass no respondió. Reintenta luego o importa un CSV de negocios.

Y ocurre **igual** con 30 km + los 11 giros que con 5 km + solo «Carpinterías y ebanisterías».
En la computadora del desarrollador la búsqueda sí funciona.

---

## Causa real

**La app dependía de un único servidor de Overpass (`https://overpass-api.de/api/interpreter`) y
ese servidor estaba rechazando las peticiones que llegan desde la IP de salida del despliegue.**

No es la consulta, no es el radio, no es el catálogo ni la contraseña: es el **transporte**. Tres
datos lo demuestran:

1. **El mensaje salía de una sola rama del código.** En `buscar_osm` todo fallo de red
   (`requests.RequestException`, que incluye `ConnectionError`, `Timeout` **y** `HTTPError` de
   `raise_for_status`) se convertía en el mismo texto: «OpenStreetMap/Overpass no respondió».
   Un HTTP 429, un 403/406 y un corte de TLS eran indistinguibles para el usuario.
2. **Fallaba igual con una consulta trivial.** 5 km + `craft=carpenter|cabinet_maker|woodworker`
   es una consulta que cualquier servidor público de Overpass responde en menos de un segundo.
   Si una consulta pequeña falla igual que una grande, el problema está **antes** de ejecutar la
   consulta.
3. **Funcionaba en local.** La IP doméstica/ofimática no está limitada; la del servidor sí.

### Por qué la IP del despliegue está limitada

Streamlit Community Cloud ejecuta las apps en IPs de salida **compartidas** entre muchas apps y
usuarios. Overpass reparte sus «slots» **por dirección IP** y responde **HTTP 429** cuando se
agotan ([Overpass API — Commons/Quotas](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html));
además, desde 2026 la instancia principal endureció el cumplimiento de su política de uso y
**bloquea IP y User-Agent que abusan**, y banee a quien acumula 429 repetidos
([Overpass API performance issues, hilo de la comunidad OSM](https://community.openstreetmap.org/t/overpass-api-performance-issues/140598)).
El User-Agent de NEMET (`NEMET-Prospeccion/1.0 (https://github.com/nemet-source/nemet)`) es
válido y cumple la política: **no hay que cambiarlo**, el bloqueo es por IP compartida.

Consecuencia: la búsqueda se volvía inservible aunque la consulta fuera mínima, y el único
«plan B» que ofrecía la app era importar un CSV.

---

## Qué se cambió

### 1. Varios servidores públicos de Overpass, en orden (`prospeccion.py`)

`OVERPASS_URLS` ahora es una lista de instancias públicas globales tomada de la wiki de OSM
(<https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances>):
kumi.systems, private.coffee, overpass-api.de, z, lz4 y VK Maps. `_consultar_overpass()` recorre
la lista y:

- ante **HTTP 429** espera lo que pida `Retry-After` (acotado a 5 s) y reintenta **una vez** el
  mismo servidor, como pide la política de Overpass; si insiste, pasa al siguiente;
- ante **sin conexión / TLS / tiempo de espera / 4xx / 5xx** pasa directamente al siguiente;
- **nunca** repite la misma petición en bucle: hay un presupuesto total de 75 s para que la
  interfaz no se quede colgada;
- si ninguno responde, lanza `_FalloOverpass` con el motivo de cada servidor.

### 2. Degradación honesta cuando el servicio no termina la consulta

Si todos los servidores **aceptan** la consulta pero ninguno la completa (su `[timeout:25]`
interno), se reintenta **una sola vez con la mitad del radio** y la app lo dice en pantalla:
«Ningún servidor completó la búsqueda con 30 km; se repitió con 15 km». El origen del listado
muestra el radio realmente usado.

### 3. Mensajes que distinguen la causa y no filtran credenciales

`_FalloOverpass` **no** arrastra el texto de la excepción de red: un error de proxy puede
incrustar `usuario:token@host` en su mensaje y eso nunca debe llegar a la pantalla. El resumen
solo nombra **host + motivo** (`overpass.kumi.systems: límite de peticiones (HTTP 429)`), y la
interfaz muestra «Servidor consultado: <host>» sin ruta ni parámetros.

### 4. Alternativa segura cuando el servicio falla (`app.py`)

Si la búsqueda falla, aparece un recuadro **🛟 OpenStreetMap no respondió: alternativas sin red**
con **Reintentar** (que relanza la búsqueda con la lista de servidores) y se abren solos los
bloques **➕ Agregar prospecto manualmente** e **📂 Importar un CSV**. **Ninguna ficha se inventa
ni se guarda** cuando falla la red, y los resultados anteriores se descartan para no
confundirlos con un intento fallido.

---

## Cómo se probó

`tests_flujo_app.py::test_16_busqueda_desplegada_contra_un_servidor_overpass_local` ejecuta la
**app real** (AppTest sobre una copia temporal del proyecto) contra un servidor HTTP local que
imita a Overpass, incluidos sus fallos. Solo se sustituye la lista de servidores; la consulta,
el reintento, el parseo, la clasificación, el filtro por radio, el Excel y la interfaz son los de
producción:

| Escenario | Resultado comprobado |
|---|---|
| 429 con `Retry-After` y luego 200 | reintenta una vez, encuentra 3 fichas y dice qué servidor respondió |
| 429 siempre | error claro que nombra CSV y alta manual, sin fichas anteriores en pantalla |
| botón **Reintentar** | relanza la búsqueda y muestra resultados |
| el servidor no completa la consulta | baja de 12 km a 6 km, avisa y filtra por el radio nuevo |

Baterías completas: `tests_prospeccion.py` 29/29 · `tests_flujo_app.py` 16/16 ·
`tests_autenticacion.py` 50/50 · `tests_bateria.py` 24/24.

### Verlo en el despliegue real (sin exponer secretos)

1. Fusiona y espera a que Streamlit Cloud termine el despliegue de la rama.
2. Entra como Admin/Editor y pulsa **🔎 Buscar negocios** con Ciudad Obregón y 5 km.
3. Si vuelve a fallar, el error ahora dice **qué servidor** falló y **por qué**; pulsa
   **Reintentar**.
4. Para diagnosticar sin tocar Secrets: en la app desplegada no hace falta ningún token; si se
   quiere ver el estado del servicio, `https://overpass-api.de/api/status` es público y se puede
   consultar desde el navegador.
