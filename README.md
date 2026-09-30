# Sistema Maestro NEMET

Aplicación web interna (Streamlit) para el control de inventario, directorio de clientes,
prospección comercial y cotizaciones de productos químicos y sistemas epóxicos de NEMET. Usa
`Sistema_Inventario_NEMET_Final.xlsx` como base de datos y `nemet_usuarios.db` (SQLite) para
las cuentas y el acceso.

## Módulos

| Menú | Descripción | Roles con acceso |
|---|---|---|
| 📊 Dashboard & Resumen | Métricas generales (SKUs, clientes, valor del inventario, SKUs por reabastecer). | Admin · Editor · Usuario |
| 📦 Control de Inventario y Edición | Edición directa de la hoja `Inventario`. `StockActual`, `AlertaStock`, `PrecioBaseSinIVA`, `IVA 16%` y `ValorInventario` se calculan automáticamente al guardar. | Admin · Editor |
| 👥 Gestión de Clientes | Alta/edición de clientes en la hoja `Clientes`. | Admin · Editor |
| 🎯 Prospección Comercial | **NEMET PROSPECTOR:** búsqueda, mapa, puntuación explicable, borrador de WhatsApp y agenda comercial en la hoja `Prospectos`; también admite CSV. | Admin · Editor |
| 📏 Cotizador por Área y Milimétrico | Calcula el material necesario (`m² × mm × rendimiento`, con el rendimiento y la proporción A:B de la hoja `Cat_Productos`) y recomienda la presentación más económica. | Admin · Editor · Usuario |
| 📝 Cotizador Comercial Profesional | Carrito manual por SKU. | Admin · Editor · Usuario |
| 📋 Historial de Cotizaciones | Consulta, búsqueda y borrado (con confirmación) de folios `COT-AAAA-NNN`. El borrado total es solo para administradores. | Admin · Editor · Usuario |
| 🛡️ Administración de Usuarios | Alta, edición, roles, desactivación, restablecimiento de contraseñas y bitácora de actividad. | Solo Admin |

Ambos cotizadores generan el PDF, lo envían por correo y registran el folio en la hoja
`Historial_Cotizaciones` (una sola vez por carrito, aunque se descargue o envíe varias veces).

## 🎯 NEMET PROSPECTOR

**Buscar → Detectar → Calificar → Contactar → Dar seguimiento → Vender.** Disponible para
Administrador y Editor en el menú **🎯 Prospección Comercial**.

1. **Buscar:** escribe ciudad y estado de México (por defecto **Ciudad Obregón, Sonora**),
   selecciona un radio de 1–30 km (por defecto **30 km**) y los giros. Al pulsar Buscar se consulta
   OpenStreetMap: Ciudad Obregón y Hermosillo usan centros urbanos conocidos (sin consultar
   Nominatim); otras ciudades se ubican con Nominatim. Overpass devuelve hasta 300 fichas
   etiquetadas. Los resultados de consultas iguales se guardan en caché 30 minutos; no hay
   búsquedas en segundo plano.
   La consulta se intenta en **varios servidores públicos de Overpass, en orden** (kumi.systems,
   private.coffee, overpass-api.de, z, lz4 y VK Maps), porque la IP de salida de Streamlit Cloud
   es compartida y esos servidores limitan por IP. La interfaz indica qué servidor respondió.
   Si todos aceptan la consulta pero ninguno la termina, se reintenta una vez con la mitad del
   radio y se avisa del cambio.
   Los giros incluyen carpinterías, fabricantes de muebles/mesas, artesanos, tiendas de manualidades,
   decoradores, restauradores, aplicadores de pisos, constructoras, distribuidores, manufactura y
   arquitectura. Un artista o tienda de arte es un **posible** prospecto, no un comprador confirmado.
2. **Calificar:** cada ficha muestra segmento, razón del puntaje, productos NEMET recomendados **solo
   si están en Inventario**, contacto y enlace a la fuente. La regla suma giro (50–66 puntos),
   productos/actividad mencionados en la ficha (+12 si menciona resina/epóxico, +4 si menciona
   materiales afines), distancia al centro de la búsqueda (+5 a ≤10 km, +2 a ≤30 km), teléfono/correo
   público (+9), WhatsApp comercial **publicado expresamente** (+5) y web/redes (+5). Tamaño (+3),
   actividad comercial de los últimos 90 días (+4) y clientela empresarial/mixta (+2) **solo cuentan
   cuando el equipo registra su fuente**. Alta ≥80, Media ≥60, Exploratoria <60 (máximo 100). Lo
   desconocido no se adivina ni resta puntos. La fecha de edición de un mapa NO demuestra que el
   negocio siga activo. El puntaje no predice intención de compra.
3. **Mapear y revisar:** mapa por **ciudad → zona/colonia → negocio**, coloreado por prioridad. Filtra
   por ciudad, zona, giro, estado y prioridad, y abre una ficha desde el selector de negocios para
   revisar dirección comercial, teléfono, WhatsApp, sitio/redes, productos sugeridos y fuente. El botón
   **📍 Abrir en Google Maps** abre una búsqueda externa por dirección + ciudad, o por
   coordenadas comerciales si no hay dirección; no requiere clave de API. Aparece también en tablas
   y CSV exportados. Solo aparecen en el mapa integrado negocios con coordenadas públicas válidas;
   los demás siguen en la lista. Ni Google Maps ni la distancia confirman el local: comprueba la
   dirección antes de visitarlo. La distancia es geográfica aproximada, no una ruta.
4. **Crear la lista y guardar localmente:** revisa los candidatos en **tarjetas compactas** (ideal
   para celular), elige negocios individuales o marca «Seleccionar todos»; hay tabla detallada para
   escritorio. Además, en **➕ Agregar prospecto manualmente** puedes dar de alta uno **sin consultar
   servicios externos** (la aplicación web sí necesita conexión con su servidor):
   captura empresa, giro, ciudad, dirección y teléfono comerciales si están publicados, estado, notas,
   fechas de contacto/seguimiento y origen de los datos. Se exige confirmar que los contactos son
   comerciales publicados o aportados con autorización; sin fuente/confirmación no se guarda. El
   producto se recomienda por giro usando solo descripciones del Inventario. Puedes añadir coordenadas
   comerciales opcionales o consultar Google Maps con la dirección; nunca se geocodifican domicilios
   privados. Se omiten clientes existentes y prospectos previos por identificador, empresa/ciudad o
   contacto. Todo se guarda en el **Excel local**, hoja `Prospectos`, separada de `Clientes` y
   compatible con fichas de la versión anterior (no en el almacenamiento del navegador). Puedes
   **exportar todos** o **solo los filtrados** a CSV UTF-8 con fuente, fecha y enlace a Google Maps.
   Si faltan fichas públicas, importa tu propio CSV (hasta 300 filas / 500 KB) con `Empresa` y `Giro`
   o `Segmento`. Son opcionales `Ciudad`, `Zona`, `Dirección`, `Teléfono`, `WhatsApp`, `Correo`,
   `Sitio_web`, `Redes`, `URL_fuente`, `Latitud`, `Longitud`, `Productos_negocio`, `Tamaño`,
   `Tipo_clientela`, `Última_actividad` y `Fuente_perfil`. Sin giro reconocible se omite la fila;
   datos de perfil sin `Fuente_perfil` se conservan **sin** sumar puntos. El CSV no geocodifica.
5. **Contactar y dar seguimiento:** **Preparar mensaje de WhatsApp** deja elegir qué producto
   real del inventario mencionar (por ejemplo, EPO-FAST), crea un borrador copiable (editable en
   WhatsApp) y **solo abre wa.me si existe un número publicado como WhatsApp**. El usuario
   revisa y envía manualmente; un teléfono convencional no se convierte en WhatsApp. El CRM guarda
   notas, último contacto y **próximo seguimiento**; muestra agenda de hoy/vencidos al abrir el módulo
   y permite filtrar los pendientes. **Estados del flujo:** Nuevo → Contactado → Interesado →
   Cotización → Seguimiento → Cliente. Para conservar fichas anteriores también siguen disponibles
   Por investigar y En seguimiento (anterior); Descartado y No contactar permiten excluirlos sin
   volver a importarlos. Cliente es un estado del prospecto: **no** añade por sí solo un contacto
   sin verificar al directorio `Clientes`. Dos sesiones no
   pueden pisarse las notas/seguimiento sin recargar la ficha. La agenda **no manda avisos automáticos
   fuera de la aplicación**.

**En celular:** la barra lateral de Streamlit se pliega, formularios/filtros se apilan bajo 768 px,
las fichas se consultan de una en una, los botones principales ocupan el ancho disponible y las
tablas grandes quedan opcionales para desplazamiento horizontal. No requiere instalar otra app.

**Privacidad y límites:** usa únicamente datos de contacto empresariales publicados o compartidos
con autorización; no busques números privados ni envíes campañas no solicitadas. Las fichas OSM se
atribuyen a © OpenStreetMap contributors (ODbL) y enlazan su fuente; la cobertura, exactitud,
telefonía y actividad pueden ser limitadas. Overpass/Nominatim necesitan salida HTTPS y pueden no
estar disponibles; el CSV es alternativa para datos obtenidos lícitamente. Comprueba datos, stock y
permisos antes de contactar. El Excel/CSV puede contener notas comerciales sensibles: **mantén
privado el repositorio de respaldos**. A 30/09/2026 `nemet-source/nemet` es **público**: NO lo
uses como destino para datos comerciales ni notas. La app **bloquea todos los respaldos GitHub**
(automáticos y manuales) si la API no confirma que el repositorio de destino es privado; también
bloquea por fallo de red o falta de acceso. Esto incluye Excel y base de usuarios. En Streamlit
Community Cloud el disco es efímero: despliega desde un **repositorio privado** y configura ese
mismo repositorio (misma rama/historial Git) en `[git]` antes de usar el CRM allí, o exporta y
resguarda el CSV fuera de la aplicación. Apuntar `[git]` a un repo privado vacío o sin historia
común no permite el `fetch/rebase` del respaldo actual. Si datos sensibles ya se subieron a un
repositorio público, cambiarlo a privado no borra el acceso histórico a copias previas.

### Si todavía no funciona en la app publicada

| Síntoma | Qué comprobar y hacer |
|---|---|
| No aparece **🎯 Prospección Comercial** | Comprueba que el cambio esté incorporado en la **rama que despliega Streamlit** y que ese despliegue terminó sin errores. Si se usa `main`, fusiona primero el PR de la funcionalidad. El rol **Usuario** no ve el CRM; accede como Admin/Editor. |
| Solo aparece el login o falta administrador | Configura `[auth]` en los **Secrets privados** del despliegue. Nunca compartas contraseñas o tokens en un issue, chat o commit. |
| Al pulsar **Entrar** sale un cuadro rojo `sqlite3.OperationalError` y nadie puede pasar | La app **ya no se queda ahí**: repara sola el esquema (columnas que falten), el ingreso nunca depende de la bitácora y, si `nemet_usuarios.db` no admite escritura, avisa en pantalla y sigue con una **copia temporal** para que puedas entrar. El **motivo exacto** aparece ahora dentro de la app (Streamlit censura el mensaje del traceback) y con más detalle en *Manage app* → *Logs*. Si el aviso persiste, corrige permisos del despliegue o define `[auth] db` con una ruta escribible. |
| La búsqueda devuelve error o no encuentra negocios | **No es el tamaño de la consulta**: si falla igual con 30 km y todos los giros que con 5 km y una sola carpintería, el problema es el servidor, no el radio. La app ya prueba varios servidores públicos de Overpass y reintenta el límite por IP (HTTP 429), así que pulsa **Reintentar** en el recuadro de alternativas. Si sigue el error, la IP de Streamlit Cloud está limitada/bloqueada de forma temporal: espera unos minutos o revisa el estado en <https://overpass-api.de/api/status>. Mientras tanto usa las **alternativas sin red**: importar un CSV de negocios públicos o **Agregar manualmente**. Ninguna ficha se inventa cuando falla la red. |
| Guarda, pero desaparece tras reiniciar | El archivo `Prospectos` se crea en el Excel **del servidor**, no en el teléfono; el disco de Streamlit Cloud es efímero. Exporta CSV regularmente y configura respaldo **solo en un repositorio privado verificable**, con la misma rama/historial. |

## 🔐 Acceso, roles y administración de usuarios

La app está **cerrada por completo**: sin sesión válida no se ejecuta ni un módulo (ni el
dashboard). Todo el control vive en `auth_nemet.py` y se apoya en tres pilares:

- **Contraseñas con hash seguro:** `hashlib.scrypt` (n = 2¹⁴, r = 8, p = 1) con sal aleatoria de
  16 bytes por contraseña y comparación en tiempo constante (`hmac.compare_digest`). En la base
  solo hay hashes: nunca una contraseña en texto plano, ni en la bitácora, ni en los respaldos.
- **Sesiones firmadas:** token HMAC-SHA256 con expiración por inactividad (60 min por omisión,
  renovable mientras se trabaja). En cada carga se revalida contra la base, así que desactivar o
  degradar a alguien surte efecto de inmediato. Varios intentos fallidos bloquean la cuenta 15 minutos.
- **Regla dura de administradores:** el sistema **nunca** se queda sin al menos un administrador
  activo. No se puede desactivar, eliminar ni degradar al último administrador, y nadie puede
  desactivar ni eliminar su propia cuenta (para eso hay otro administrador).

### Roles

| Rol | Puede hacer |
|---|---|
| **Administrador** | Todo: inventario, clientes, prospección, cotizadores, historial, respaldo en GitHub y el panel de usuarios. |
| **Editor** | Inventario, clientes, prospección, cotizadores e historial. No administra cuentas ni respaldos. |
| **Usuario** | Dashboard, cotizadores e historial. No ve el inventario editable, los clientes ni los prospectos. |

### Administrador inicial (sin credenciales en GitHub)

El administrador inicial se crea con los **Secrets** (o variables de entorno `NEMET_*`), nunca
desde el código ni desde un archivo versionado:

```toml
[auth]
admin_usuario  = "jefe"
admin_password = "una-contraseña-larga-y-única"   # solo se usa al crear la cuenta
admin_nombre   = "Nombre del administrador"
admin_correo   = "admin@nemet.mx"
session_secret = "cadena-aleatoria-de-al-menos-32-caracteres"   # opcional pero recomendado
session_minutos = 60                                            # opcional (por omisión 60)
```

1. En el primer arranque la app crea esa cuenta y pide **cambiar la contraseña** al entrar.
2. Después, el secreto **ya no vuelve a leerse** (no es una puerta trasera permanente).
3. Si el administrador olvida su contraseña, se pone `admin_forzar = true` una sola vez en los
   Secrets: la app restablece esa contraseña, la marca para cambio y desbloquea la cuenta. Luego
   se vuelve a dejar en `false`.

Copia la plantilla y llénala:

```bash
cp .streamlit/secrets.toml.ejemplo .streamlit/secrets.toml
```

`.streamlit/secrets.toml` está en `.gitignore`, así que las credenciales nunca llegan al repositorio.
En Streamlit Community Cloud se capturan en **App → Settings → Secrets**.

**Instalación local o VPS (sin Secrets):** usa el asistente de terminal, que pide la contraseña de
forma oculta y la guarda ya cifrada:

```bash
python herramientas/crear_admin.py --usuario jefe                 # pide la contraseña dos veces
python herramientas/crear_admin.py --usuario jefe --generar       # o genera una temporal
python herramientas/crear_admin.py --usuario jefe --db /ruta/nemet_usuarios.db
```

Se niega a crear cuentas si ya existe algún administrador activo (usa `--forzar` solo en una
emergencia, por ejemplo si perdiste el acceso). Al entrar, la app pedirá cambiar la contraseña.

> **No hay registro abierto:** nadie puede darse de alta desde la pantalla de acceso. Las cuentas
> las crea un administrador desde el panel 🛡️ *Administración de Usuarios* (usuario, rol y contraseña
> provisional), y el primer administrador se define con los Secrets o con `herramientas/crear_admin.py`.

### Persistencia de las cuentas

La base de usuarios es SQLite (`nemet_usuarios.db`, configurable con `[auth] db`). Como el disco de
Streamlit Community Cloud es efímero, el archivo se respalda en **el mismo commit** que el Excel:
cada alta, cambio de rol, desactivación o restablecimiento dispara un respaldo automático (sin
esperar el intervalo anti-spam de un minuto). Solo contiene hashes y la bitácora.

> ⚠️ Por eso `nemet_usuarios.db` **no** se ignora en Git: es lo que permite conservar las cuentas
> entre reinicios de la nube. Si el repositorio pudiera hacerse público, define `[auth] session_secret`
> en los Secrets; las contraseñas seguirían a salvo porque solo se guardan como hashes scrypt.
>
> ℹ️ No subas una base generada en tu máquina de pruebas: en la nube la base se crea en el primer
> arranque a partir de los Secrets y el respaldo automático la mantiene actualizada.
>
> 🛟 **Si la base no se puede escribir** (archivo o carpeta de solo lectura, permisos del servidor o
> esquema viejo), la app no deja a nadie fuera: al arrancar comprueba la escritura, repara las
> columnas que falten, permite entrar aunque la bitácora falle y, cuando el archivo del repositorio
> es de solo lectura, trabaja sobre una copia temporal en la carpeta del sistema (`<tmp>/nemet_datos/`)
> avisándolo en pantalla. Los cambios de usuarios de esa sesión se pierden al reiniciar el servidor:
> lo correcto es arreglar los permisos o fijar `[auth] db` (Secrets) a una ruta escribible.

### Panel de administración

En **🛡️ Administración de Usuarios** un administrador puede:

- **Crear cuentas** de administrador, editor o usuario. La contraseña inicial puede generarse
  (temporal y legible) o definirla el administrador; se muestra **una sola vez** en pantalla.
- **Editar** nombre y correo, **cambiar el rol** y **restablecer contraseñas** (con cambio
  obligatorio al primer ingreso).
- **Desactivar y reactivar** cuentas sin borrar su historial, y **eliminar** cuentas con
  confirmación escrita (la bitácora conserva sus acciones).
- **Consultar la bitácora**: inicios de sesión, intentos fallidos, bloqueos, altas, cambios de rol,
  desactivaciones, cambios de contraseña, accesos denegados y respaldos.
- **Cerrar todas las sesiones** (rota la clave de firma) si se sospecha de un acceso indebido.

Cualquier persona con sesión puede **cambiar su propia contraseña** desde la barra lateral
(`🔑 Cambiar mi contraseña`), indicando siempre la contraseña actual.

## Identidad de marca NEMET

Los tres assets de la app viven en `assets/` y se generan a partir del logo original
con la herramienta del repo (auto-detecta el fondo de la imagen de entrada):

| Asset | Uso |
|---|---|
| `assets/logo_claro.png` | Logotipo en relieve (transparente) — cabecera y barra lateral. |
| `assets/favicon.png` | Isotipo sobre pastilla crema — icono de pestaña. |
| `assets/hero_claro.png` | Portada crema 1600×900 — apertura del dashboard. |

```bash
# Regenerar los 3 assets desde el logo original (el script auto-detecta el fondo)
python herramientas/procesar_logo.py /ruta/logo_claro.png   # -> logo_claro + favicon + hero_claro
```

La app usa **solo el logo claro**: el letterpress oscuro (`logo_oscuro.png` /
`hero_oscuro.png`) se retiró de la interfaz y ya no se genera.

La paleta (`PALETA_NEMET` en `app.py`) y el tema (`assets` en `.streamlit/config.toml`) comparten
los colores de los logos: crema `#F5EFE6`, tinta `#26231F`, barro `#B4552D`, verde `#2F5D3A`.

## Ejecución local

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.ejemplo .streamlit/secrets.toml   # y captura [auth] admin_*
streamlit run app.py

# Baterías de pruebas
python tests_bateria.py        #  24/24  identidad de marca (assets, tema)
python tests_autenticacion.py  #  55/55  hashes, roles, sesiones, bases dañadas o de solo lectura, asistente CLI
python tests_prospeccion.py    #  22/22 giros, Google Maps, CSV, alta manual y privacidad (sin red)
python tests_flujo_app.py      #  17/17 acceso + CRM y alta manual (AppTest, copia temporal)
```

Sin `[auth]` en los Secrets la app arranca, pero muestra la pantalla de acceso y avisa que no hay
administrador configurado: nadie puede entrar hasta definir `admin_usuario` y `admin_password`.

## Secrets (opcionales)

Además de `[auth]` (arriba), la plantilla `.streamlit/secrets.toml.ejemplo` documenta:

```toml
[email]                      # Envío de cotizaciones por Gmail
remitente = "correo@gmail.com"
password = "contraseña-de-aplicación"

[git]                        # Respaldo de Excel + usuarios en GitHub PRIVADO
token = "github_pat_..."     # Token con permiso de escritura en ese repo
repo = "tu-organizacion/nemet-datos-privados"
```

> En Streamlit Community Cloud el disco es efímero. Si los Secrets `[git]` apuntan a un **repositorio
> privado verificado**, la app respalda automáticamente el Excel y la base de usuarios tras cada guardado
> (inventario, clientes, prospectos, cuentas y cada folio emitido; los prospectos no esperan el intervalo anti-spam).
> Si GitHub indica que es público, inaccesible o no responde, **no sube ningún dato**: guarda localmente
> y muestra un aviso. La verificación requiere HTTPS hacia la API de GitHub.
> El botón **☁️ Respaldar datos en GitHub** (solo administradores) fuerza un respaldo manual. Cada push
> reinicia la app brevemente.
>
> Si dos personas editan a la vez, el segundo guardado detecta el conflicto y ofrece sobrescribir
> o reintentar después de sincronizar. Los folios se asignan releyendo el historial bajo cerrojo,
> así que dos sesiones ya no pueden registrar el mismo folio.
