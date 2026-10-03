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

## Segunda vuelta de mejoras (30-sep-2026): menos espera y memoria de servidores

Con la lista de servidores ya en producción quedaban tres desperdicios medibles, todos del
mismo origen (la IP compartida del despliegue). Se corrigieron así:

### 5. Un HTTP 429 ya no hace esperar a nadie

Antes, el primer servidor que contestaba «429» costaba hasta 5 s de espera **y** una segunda
petición al mismo servidor antes de probar otro. Si esa IP está limitada, esa espera casi
nunca sirve. Ahora la consulta va en **dos vueltas**:

1. **Primera vuelta, sin pausas:** cada servidor se pregunta **una** vez. Un 429 pasa al
   siguiente al instante, porque otro servidor puede tener turno libre ahora mismo.
2. **Segunda vuelta, solo si todos limitaron:** se vuelve **una sola vez** a cada servidor
   que dio 429, después de la pausa que pidió en `Retry-After` y empezando por el que pidió
   la espera más corta. Así se respeta la política de Overpass (nunca insistir antes del
   turno) sin cobrarle esa espera a quien está buscando.

`Retry-After` se entiende ahora en sus **dos formatos** válidos (segundos y fecha HTTP); si
no viene o viene mal, se usa una pausa corta y cortés en vez de suponer turno libre.

### 6. La app recuerda qué servidor funciona

`_orden_servidores()` guarda, **solo en memoria del proceso**, el último servidor que
entregó resultados y cuáles acaban de fallar:

- el que respondió se pregunta **primero** en la búsqueda siguiente;
- el que limitó por IP pasa **al final de la cola** 5 minutos (2 minutos si fue caída de red);
- **ningún servidor se descarta jamás**: si todos están enfriándose se preguntan igual,
  empezando por el que antes queda libre. Una búsqueda pedida por una persona siempre se
  intenta completa.

La memoria guarda únicamente URLs públicas y marcas de tiempo —nunca consultas, resultados
ni credenciales—, se borra al reiniciar la app y no crece: los enfriamientos vencidos se
olvidan solos.

**Efecto medible:** una búsqueda cuyo primer servidor está bloqueado pasaba de ~5 s perdidos
+ 2 peticiones inútiles a **0 s de espera y 1 petición**; y la búsqueda siguiente arranca ya
por el servidor que sí responde (1 petición en vez de recorrer la lista otra vez).

### 7. En pantalla: qué contestó cada servidor

Cuando la búsqueda falla, el recuadro de alternativas incluye **🔎 Qué contestó cada
servidor** con una línea por servidor (`overpass.kumi.systems: límite de peticiones (HTTP
429)`), y el mensaje de error queda corto y legible. Se mantiene la regla de seguridad: solo
**host + motivo redactado por la app**, nunca rutas, parámetros ni el texto de la excepción
de red (que puede incrustar credenciales de un proxy).

---

## Cómo se probó

`tests_flujo_app.py::test_16_busqueda_desplegada_contra_un_servidor_overpass_local` ejecuta la
**app real** (AppTest sobre una copia temporal del proyecto) contra un servidor HTTP local que
imita a Overpass, incluidos sus fallos. Solo se sustituye la lista de servidores; la consulta,
el reintento, el parseo, la clasificación, el filtro por radio, el Excel y la interfaz son los de
producción:

| Escenario | Resultado comprobado |
|---|---|
| 429 con `Retry-After` y luego 200 | vuelve **una** vez tras el turno pedido, encuentra 3 fichas y dice qué servidor respondió |
| 429 siempre | error claro que nombra CSV y alta manual, con el detalle **por servidor** y sin fichas anteriores en pantalla |
| botón **Reintentar** | relanza la búsqueda y muestra resultados |
| el servidor no completa la consulta | baja de 12 km a 6 km, avisa y filtra por el radio nuevo |

Y sin red, en `tests_prospeccion.py::TestVariosServidoresOverpass`:

| Escenario | Resultado comprobado |
|---|---|
| un servidor limita y otro está libre | se cambia de servidor **sin esperar** (`time.sleep` no se llama) |
| todos limitan por IP | una sola segunda oportunidad, empezando por el `Retry-After` más corto |
| `Retry-After` en segundos, fecha HTTP, ausente o absurdo | espera acotada, nunca negativa ni infinita |
| búsqueda tras un fallo | se empieza por el servidor que respondió; el que limitó queda al final |
| todos fallaron antes | la siguiente búsqueda los intenta igual: la memoria solo reordena |

Baterías completas: `tests_prospeccion.py` 55/55 · `tests_flujo_app.py` 19/19 ·
`tests_autenticacion.py` 55/55 · `tests_bateria.py` 24/24.

### Verlo en el despliegue real (sin exponer secretos)

1. Fusiona y espera a que Streamlit Cloud termine el despliegue de la rama.
2. Entra como Admin/Editor y pulsa **🔎 Buscar negocios** con Ciudad Obregón y 5 km.
3. Si vuelve a fallar, el error ahora dice **qué servidor** falló y **por qué**; pulsa
   **Reintentar**.
4. Para diagnosticar sin tocar Secrets: en la app desplegada no hace falta ningún token; si se
   quiere ver el estado del servicio, `https://overpass-api.de/api/status` es público y se puede
   consultar desde el navegador.

---

## Actualización posterior: alternativa DENUE (INEGI)

La recomendación de este diagnóstico de importar CSV o dar de alta manualmente describe la versión
anterior. Ahora, si Overpass falla (o no encuentra fichas), la prospección puede consultar el DENUE
del INEGI como respaldo o fuente directa. La API oficial requiere un token en Secrets privados
(`[inegi].denue_token`) o `NEMET_INEGI_DENUE_TOKEN`; no lo registres en este documento ni lo
compartas en el chat. El método geográfico de DENUE admite como máximo 5 km; la interfaz informa
cuando el radio original debe limitarse. Ninguna ficha se inventa si ambas fuentes fallan.

### Diagnóstico del error de DENUE en el despliegue (2026-10)

Síntoma: con **todos los giros** la búsqueda DENUE devolvía **HTTP 400**; con un solo giro mostraba
«devolvió datos inesperados; verifica el token y reintenta». Comprobado contra el servicio real
(siempre con un token de prueba inválido, nunca con el token del despliegue):

| Prueba | Respuesta real del INEGI |
|---|---|
| Consulta corta (`Buscar/carpinteria/…/5000/…`) con clave no válida | **HTTP 200**, `text/plain`, cuerpo `No Autorizado, utilice una clave valida.` |
| Consulta con acentos (`carpinter%C3%ADa`) o espacios (`pintura%20epoxica`) | Igual: el aviso de autorización, sin error |
| Condición con 25 términos (URL de 460 caracteres) | **HTTP 400** `Bad Request - Invalid URL` |
| Condición de 200–250 caracteres | HTTP 200, página HTML `Hubo un problema con su solicitud…` |
| Condición de ~180 caracteres | HTTP 200, aviso de autorización (la URL sí se atiende) |

Conclusiones:

1. **El 400 no era del token ni de los acentos**: el borde del servicio corta las URLs largas (el límite
   medido está entre ~250 y ~290 caracteres). La consulta de los 11 giros mide **770 caracteres**, así que
   el servicio la rechaza antes de mirar la credencial. Ahora la búsqueda reparte la condición en varias
   consultas cortas (`DENUE_URL_MAX = 250`) y une los resultados sin repetir fichas por `Id`.
2. **Un rechazo de credencial llega con HTTP 200**, no con 401/403: el cuerpo es texto plano
   («No Autorizado, utilice una clave valida.»). Mirar solo `status_code` y `respuesta.json()` hacía que
   cualquier aviso del servicio cayera en «datos inesperados» y culpaba al token. Ahora la app clasifica la
   respuesta por estado, tipo de contenido y forma (lista, lista vacía, objeto, texto, nulo, HTML), explica
   cada caso en pantalla y **nunca** dice «datos inesperados» sin diagnóstico.
3. Una lista vacía o un `null` ya no son un error: si la respuesta es ambigua (`null`, cuerpo vacío u
   objeto vacío), la app hace **una** consulta de verificación (`Buscar/todos/…/250/…`) para distinguir
   «sin coincidencias» de «credencial rechazada», y lo informa.
4. **Diagnóstico seguro**: la interfaz muestra estado HTTP, tipo de contenido, bytes y forma de la
   respuesta, más la **huella del token** (8 caracteres del SHA-256 y su longitud). Nunca el token, ni la
   URL completa, ni el cuerpo crudo. El botón **🔐 Verificar credencial de DENUE** comprueba si el INEGI
   acepta la clave sin exponerla.

Si el token del despliegue aparece en una captura, un chat o un commit, **rótalo**: se solicita otro en
<https://www.inegi.org.mx/app/api/denue/v1/tokenVerify.aspx> y se actualiza `[inegi].denue_token`.
