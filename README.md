# Sistema Maestro NEMET

Aplicación web interna (Streamlit) para el control de inventario, directorio de clientes y
cotizaciones de productos químicos y sistemas epóxicos de NEMET. Usa el archivo
`Sistema_Inventario_NEMET_Final.xlsx` como base de datos y `nemet_usuarios.db` (SQLite) para
las cuentas y el acceso.

## Módulos

| Menú | Descripción | Roles con acceso |
|---|---|---|
| 📊 Dashboard & Resumen | Métricas generales (SKUs, clientes, valor del inventario, SKUs por reabastecer). | Admin · Editor · Usuario |
| 📦 Control de Inventario y Edición | Edición directa de la hoja `Inventario`. `StockActual`, `AlertaStock`, `PrecioBaseSinIVA`, `IVA 16%` y `ValorInventario` se calculan automáticamente al guardar. | Admin · Editor |
| 👥 Gestión de Clientes | Alta/edición de clientes en la hoja `Clientes`. | Admin · Editor |
| 📏 Cotizador por Área y Milimétrico | Calcula el material necesario (`m² × mm × rendimiento`, con el rendimiento y la proporción A:B de la hoja `Cat_Productos`) y recomienda la presentación más económica. | Admin · Editor · Usuario |
| 📝 Cotizador Comercial Profesional | Carrito manual por SKU. | Admin · Editor · Usuario |
| 📋 Historial de Cotizaciones | Consulta, búsqueda y borrado (con confirmación) de folios `COT-AAAA-NNN`. El borrado total es solo para administradores. | Admin · Editor · Usuario |
| 🛡️ Administración de Usuarios | Alta, edición, roles, desactivación, restablecimiento de contraseñas y bitácora de actividad. | Solo Admin |

Ambos cotizadores generan el PDF, lo envían por correo y registran el folio en la hoja
`Historial_Cotizaciones` (una sola vez por carrito, aunque se descargue o envíe varias veces).

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
| **Administrador** | Todo: inventario, clientes, cotizadores, historial, respaldo en GitHub y el panel de usuarios. |
| **Editor** | Inventario, clientes, cotizadores e historial. No administra cuentas ni respaldos. |
| **Usuario** | Dashboard, cotizadores e historial. No ve el inventario editable ni el directorio de clientes. |

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

## 📱 Modo móvil

Mucho del trabajo pasa en el teléfono (almacén, obra, visita a cliente), así que la app detecta
en cada carga si viene de una pantalla chica y adapta la interfaz. Todo vive en `movil_nemet.py`
y se resuelve **antes** del gate de acceso: la pantalla de login y el cambio obligatorio de
contraseña ya se ven bien en el teléfono.

**Cómo decide** (en este orden, sin JavaScript ni cookies):

| Pista | Ejemplo | Nota |
|---|---|---|
| Ancho declarado por el navegador | `sec-ch-viewport-width: 390`, `viewport-width` | Manda sobre lo demás; el encabezado propio del despliegue (`x-nemet-viewport`) tiene prioridad. |
| Client Hint | `sec-ch-ua-mobile: ?1` | Respaldo cuando el navegador no declara el ancho. |
| `user-agent` | iPhone, Android con `Mobile`, iPad y tabletas | Último recurso; sin ninguna pista se asume escritorio. |
| Forzado manual | `?movil=1` / `?escritorio=1` | Gana sobre todo y se recuerda durante la sesión. |

El umbral son 768 px (`movil_nemet.BREAKPOINT_PX`) y una tableta cuenta como pantalla chica; si
declara 1024 px (iPad en horizontal) recibe el diseño completo. Una ventana angosta de escritorio
también recibe la vista táctil: la decisión es por espacio disponible, no por tipo de aparato.
Si nada de esto acierta (por ejemplo iPadOS moderno, que se anuncia como Mac), `?movil=1` siempre
está disponible y la barra lateral recuerda cómo usarlo.

**Qué cambia en el teléfono**

- Área táctil mínima de 44 px y botones a lo ancho de la pantalla.
- Campos de texto a 16 px: con menos, iOS hace zoom al enfocar y descuadra la vista.
- Métricas del dashboard en 2×2 y los cuatro botones de cotización (PDF, correo, limpiar, folio) 2×2.
- Los campos del cotizador por área (cliente, medidas, espesor, línea) se apilan: uno por fila.
- Tablas y editores se desplazan dentro de su recuadro, y la vista rápida del inventario se recorta
  a una altura cómoda en lugar de empujar el resto del panel hacia abajo.
- Logo y lema más compactos, títulos ajustados y el hero 16:9 del dashboard se omite a propósito
  (ocupa toda la primera pantalla).
- Tema nocturno opcional con `?tema=oscuro`; el crema de marca es el tema por omisión.

**Forzar la vista desde la URL**

```
https://<la-app>/?movil=1        # vista táctil siempre (también sirve ?movil)
https://<la-app>/?escritorio=1   # diseño completo aunque sea un teléfono
https://<la-app>/?tema=oscuro    # tema nocturno de la sesión (?tema=claro lo devuelve al crema)
```

El forzado se recuerda en la sesión (las recargas de Streamlit pierden la URL), así que basta con
pedirlo una vez; la barra lateral indica cómo cambiar de vista.

> ℹ️ En el tema nocturno las tablas de datos son un lienzo aparte (`glide-data-grid`) y se tiñen con
> sus propias variables CSS, así que pueden conservar algún detalle del tema claro. El resto de la
> interfaz (fondo, tarjetas de métricas, campos, formularios, barra lateral) sí cambia por completo.

## Ejecución local

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.ejemplo .streamlit/secrets.toml   # y captura [auth] admin_*
streamlit run app.py

# Baterías de pruebas
python tests_bateria.py        #  24/24  identidad de marca (assets, tema)
python tests_autenticacion.py  #  50/50  hashes, roles, sesiones, reglas duras, asistente CLI
python tests_flujo_app.py      #  10/10  flujo real de acceso (AppTest, copia temporal)
python tests_movil.py          #  15/15  modo móvil (detección, forzado por URL y app real en móvil)
```

Sin `[auth]` en los Secrets la app arranca, pero muestra la pantalla de acceso y avisa que no hay
administrador configurado: nadie puede entrar hasta definir `admin_usuario` y `admin_password`.

## Secrets (opcionales)

Además de `[auth]` (arriba), la plantilla `.streamlit/secrets.toml.ejemplo` documenta:

```toml
[email]                      # Envío de cotizaciones por Gmail
remitente = "correo@gmail.com"
password = "contraseña-de-aplicación"

[git]                        # Respaldo de Excel + usuarios en GitHub
token = "github_pat_..."     # Token con permiso de escritura en el repo
repo = "nemet-source/nemet"
```

> En Streamlit Community Cloud el disco es efímero. Si los Secrets `[git]` están configurados, la app
> **respalda automáticamente** el Excel y la base de usuarios en GitHub después de cada guardado
> (inventario, clientes, cuentas y cada folio emitido; empuja como máximo una vez por minuto y sesión).
> El botón **☁️ Respaldar datos en GitHub** (solo administradores) fuerza un respaldo manual. Cada push
> reinicia la app brevemente.
>
> Si dos personas editan a la vez, el segundo guardado detecta el conflicto y ofrece sobrescribir
> o reintentar después de sincronizar. Los folios se asignan releyendo el historial bajo cerrojo,
> así que dos sesiones ya no pueden registrar el mismo folio.
