# 🔍 Auditoría de código — Sistema Maestro NEMET (`app.py`)

**Fecha:** 28-sep-2026 · **Alcance:** `app.py` (880 líneas), `Sistema_Inventario_NEMET_Final.xlsx`, comportamiento en Streamlit Community Cloud.
**Método:** lectura completa del código + pruebas ejecutadas sobre una copia del Excel (carga/guardado ida-y-vuelta, registro de folios, simulación de carrera entre sesiones, generación de PDF, cotizador por área).

---

## Resumen ejecutivo

La app está bien construida para un solo usuario: la carga de datos es defensiva, el flujo de folios evita duplicados *dentro* de una sesión, el PDF sale correcto y el guardado ida-y-vuelta **no pierde datos** del archivo actual (verificado: 78 filas íntegras, las 3 hojas conservadas).

Los riesgos serios aparecen con **más de un usuario y el disco efímero de Streamlit Cloud**: guardados concurrentes que se pisan entre sí, folios duplicados tras un reinicio, y el envío de correo que se hace *antes* de registrar el folio. Ninguno requiere reescribir la app; hay mitigaciones incrementales.

| # | Severidad | Hallazgo |
|---|-----------|----------|
| 1 | 🔴 Crítica | Guardado concurrente: dos sesiones se sobreescriben silenciosamente (pérdida de datos) |
| 2 | 🔴 Crítica | Disco efímero + respaldo manual: un reinicio borra cotizaciones y ediciones sin avisar |
| 3 | 🔴 Crítica | Folios duplicados tras reinicio (el historial vuelve a `COT-2026-001`) |
| 4 | 🟠 Alta | El correo se envía **antes** de registrar el folio → folio "quemado" y duplicable |
| 5 | 🟠 Alta | El respaldo a GitHub nunca hace *pull* → falla con *non-fast-forward* |
| 6 | 🟠 Alta | El cotizador por área recomienda productos sin considerar el stock (hoy todo está en 0) |
| 7 | 🟡 Media | `datetime.now()` = UTC en el servidor → fechas/folio con año desfasados para Sonora |
| 8 | 🟡 Media | Cada guardado destruye el formato de la hoja (negritas, anchos, formatos de celda) |
| 9 | 🟡 Media | Relectura del Excel completo + regeneración del PDF en *cada* interacción |
| 10 | 🟡 Media | Reutilizar folio por carrito idéntico impide emitir una *nueva* cotización con los mismos artículos |
| 11 | 🟢 Baja | Productos fuera de `Cat_Productos` se excluyen en silencio del cotizador por área |
| 12 | 🟢 Baja | Sin autenticación: cualquiera con el URL edita inventario y ve el directorio de clientes |
| 13 | 🟢 Baja | Detalles menores de datos (SKUs duplicados, presentaciones `0`, nombres de cliente repetidos) |

---

## ✅ Lo que está bien (verificado en ejecución)

- **Round-trip sin pérdida:** `cargar_inventario()` → `guardar_inventario()` sobre una copia: 78/78 filas, todas las columnas numéricas y de texto idénticas; `Cotizador_Rendimientos` y `Cat_Productos` intactas.
- **Carga defensiva:** detección de fila de encabezado, limpieza de columnas `Unnamed` y de la columna duplicada `PrecioBaseSinIVA` (col. Q del archivo, que es copia exacta — se descarta sin riesgo).
- **Hojas faltantes:** el Excel **no trae** `Clientes` ni `Historial_Cotizaciones`; la app las maneja con gracia y las crea al primer guardado. Primer folio correctamente `COT-2026-001`.
- **PDF válido** incluso con acentos, `²` y saltos de línea en el detalle (transliteración latin-1 correcta).
- **Editor de inventario:** las columnas derivadas se bloquean y se recalculan al guardar — buena decisión.
- **Sin inyecciones obvias:** búsquedas con `regex=False`, PDF con texto escapado, token de GitHub solo en la llamada push y limpiado de los mensajes de error.

---

## 🔴 Críticos

### 1. Guardado concurrente = pérdida silenciosa de datos
**Dónde:** `escribir_hoja()` (L348), `registrar_cotizacion_en_excel()` (L422-437), caché `st.session_state["inventario"]` (L611).

Cada visitante de la app es una sesión con **su propia copia** del inventario en `session_state` (cargada una sola vez). Guardar **reescribe la hoja completa** con esa copia. Si el usuario B guardó algo mientras A tenía la app abierta, al guardar A **revive datos viejos y borra los de B** sin ningún aviso.

**Evidencia** (simulación de dos registros de cotización "simultáneos"):
```
Tras la 'carrera', historial: [['COT-2026-002', 'B']]   # la cotización de A desapareció
```

En Streamlit Cloud esto no es hipotético: cada navegador/nueva pestaña es una sesión nueva.

**Mitigaciones incrementales:**
1. Releer el archivo **justo antes** de cada escritura y aplicar solo el delta (p. ej. agregar la fila nueva al historial releído, en vez de reescribir desde la caché de la sesión).
2. Caché con vencimiento corto (`st.cache_data(ttl=30)`) + invalidación explícita al guardar, en lugar de `session_state` perpetuo.
3. Cerrojo entre hilos (`threading.Lock` funciona dentro de una sola instancia de Cloud) o `filelock` alrededor de leer-modificar-escribir.
4. Solución de fondo: mover el historial/folios a SQLite u otra base persistente.

### 2. Disco efímero + respaldo 100 % manual
**Dónde:** botón "☁️ Respaldar Excel en GitHub" (L664).

Streamlit Cloud reinicia la app en cada deploy, crash o periodo de inactividad, y el disco vuelve al último commit. Toda cotización registrada o edición de inventario desde el último respaldo **se pierde para siempre**, sin advertencia en pantalla. El README lo documenta, pero el módulo de cotizaciones convierte esto en pérdida de documentos comerciales.

**Mitigación:** respaldar **automáticamente** después de cada guardado exitoso (inventario, clientes, alta de folio), no solo con el botón. Costo: cada push reinicia la app (brevemente), pero garantiza cero pérdida. Alternativa robusta: escribir el historial vía API de GitHub Contents o una Google Sheet, sin depender del disco local.

### 3. Folios duplicados tras un reinicio
**Dónde:** `obtener_siguiente_folio()` (L401).

El consecutivo se deriva del mayor folio en la hoja `Historial_Cotizaciones` **del disco efímero**. Si la app se reinicia después de emitir p. ej. `COT-2026-005` y el respaldo no se hizo, el siguiente folio vuelve a ser `COT-2026-001` — dos cotizaciones distintas con el mismo folio, una ya entregada al cliente por correo. Es el invariant que el sistema debe garantizar.

**Mitigación:** la auto-respaldarización del punto 2; y/o registrar el último folio emitido en los Secrets o en un commit aparte como bitácora de emergencia.

---

## 🟠 Altas

### 4. El correo se envía antes de registrar el folio
**Dónde:** `bloque_acciones_cotizacion()` L593-595:
```python
enviar_cotizacion_por_correo(...)          # 1º se manda el PDF con folio N
...
registrar_si_es_necesario(...)             # 2º se intenta registrar N
```
Si el envío tiene éxito pero el registro en Excel falla (archivo ocupado, excepción, reinicio), el cliente recibió el folio N pero el historial no lo conoce → el siguiente carrito volverá a generar N. **Invertir el orden:** registrar primero y enviar después; si el envío falla, el folio queda registrado (falso negativo inofensivo) en lugar de duplicarse.

### 5. El respaldo a GitHub nunca hace pull
**Dónde:** `guardar_cambios_github()` L162.
```python
repo.git.push(f"https://{token}@github.com/{repo_name}.git", f"HEAD:{rama}")
```
Si `main` avanzó (un PR mergeado, otro respaldo desde otra sesión) el push es rechazado por *non-fast-forward* y el usuario ve "Error al sincronizar". Agregar `repo.git.pull("--rebase", ...)` (o `fetch` + `rebase`) antes del push, manejando el conflicto trivial del binario.

### 6. El cotizador por área ignora el stock
**Dónde:** recomendación óptima (L775-790). Elige la presentación más barata sin mirar `StockActual`/`AlertaStock`. Hoy **las 78 filas tienen stock 0** y aun así recomienda "compre 17 kg". Sugerencia: advertencia visible (y en el PDF) cuando las unidades recomendadas excedan el stock disponible del SKU.

---

## 🟡 Medias

### 7. Zona horaria UTC
**Dónde:** L403 (año del folio), L428 (fecha del historial), L460 (fecha del PDF). En Cloud `datetime.now()` es UTC; para Sonora (UTC-7) una cotización de las 5 p. m. local se registra con fecha del día siguiente, y en fin de año el **año del folio** puede salir mal. Usar `ZoneInfo("America/Hermosillo")`.

### 8. El guardado destruye el formato de las hojas
`if_sheet_exists="replace"` reconstruye la hoja desde cero: negritas, anchos de columna y formatos de número se pierden en el primer guardado (verificado: encabezado deja de estar en negrita). Guardar con openpyxl conservando estilos, o aceptarlo explícitamente como decisión de diseño.

### 9. Relecturas y PDF en cada rerun
`cargar_historial()` (vía `folio_para_carrito`), `cargar_catalogo_rendimientos()` y `generar_pdf_cotizacion()` corren en **cada interacción** de UI mientras haya carrito. En el free tier se nota. Usar `st.cache_data(ttl=…)` para lecturas y generar el PDF solo cuando cambian folio/cliente/carrito.

### 10. Folio reutilizado impide nuevas cotizaciones idénticas
`firma_carrito()` (L541): el mismo cliente + mismos artículos **siempre** reutiliza el folio anterior, aunque sea un pedido nuevo. El usuario tiene que "ensuciar" el carrito para obtener folio nuevo. Agregar un botón "Nueva cotización (folio nuevo)" que limpie `{clave}_emitido`.

---

## 🟢 Bajas

11. **Filtro de familias:** productos sin entrada en `Cat_Productos` se excluyen silenciosamente del cotizador por área (L735); el mensaje del rendimiento por defecto por producto (L766) es inalcanzable cuando el catálogo existe. Incluirlos con `RENDIMIENTO_DEFAULT` o avisar qué líneas no califican.
12. **Sin autenticación:** el URL público permite editar inventario y expone el directorio (correos/teléfonos). Considerar `st.login` o un gate simple por contraseña en Secrets.
13. **Higiene de datos detectada:** SKUs duplicados (`EM 01`, `EM 02`, `EP 01` — el dashboard ya lo advierte, bien), 2 filas con `Presentacion = "0"` (29 de 78 filas no son cotizables por área: piezas y rangos "10 a 20 kg", excluidas correctamente), y nombres de cliente repetidos tomarían el primero (`seleccionar_cliente`, `.iloc[0]`).

---

## Ruta sugerida (por impacto / esfuerzo)

| Paso | Arreglo | Esfuerzo |
|------|---------|----------|
| 1 | Registrar folio **antes** de enviar correo (#4) | ~5 líneas |
| 2 | Zona horaria Hermosillo (#7) | ~5 líneas |
| 3 | Auto-respaldo tras cada guardado exitoso (#2, #3) | ~15 líneas |
| 4 | `pull --rebase` antes del push (#5) | ~10 líneas |
| 5 | Releer + cerrojo antes de cada escritura (#1) | ~30 líneas |
| 6 | Aviso de stock en recomendación (#6) | ~15 líneas |
