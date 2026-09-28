# Sistema Maestro NEMET

Aplicación web interna (Streamlit) para el control de inventario, directorio de clientes y
cotizaciones de productos químicos y sistemas epóxicos de NEMET. Usa el archivo
`Sistema_Inventario_NEMET_Final.xlsx` como base de datos.

## Módulos

| Menú | Descripción |
|---|---|
| 📊 Dashboard & Resumen | Métricas generales (SKUs, clientes, valor del inventario, SKUs por reabastecer). |
| 📦 Control de Inventario y Edición | Edición directa de la hoja `Inventario`. `StockActual`, `AlertaStock`, `PrecioBaseSinIVA`, `IVA 16%` y `ValorInventario` se calculan automáticamente al guardar. |
| 👥 Gestión de Clientes | Alta/edición de clientes en la hoja `Clientes`. |
| 📏 Cotizador por Área y Milimétrico | Calcula el material necesario (`m² × mm × rendimiento`, con el rendimiento y la proporción A:B de la hoja `Cat_Productos`) y recomienda la presentación más económica. |
| 📝 Cotizador Comercial Profesional | Carrito manual por SKU. |
| 📋 Historial de Cotizaciones | Consulta, búsqueda y borrado (con confirmación) de folios `COT-AAAA-NNN`. |

Ambos cotizadores generan el PDF, lo envían por correo y registran el folio en la hoja
`Historial_Cotizaciones` (una sola vez por carrito, aunque se descargue o envíe varias veces).

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
streamlit run app.py

# Batería de pruebas del sistema de marca (24/24)
python tests_bateria.py
```

## Secrets (opcionales)

Configura `.streamlit/secrets.toml` (local) o los *Secrets* de Streamlit Community Cloud:

```toml
[email]                      # Envío de cotizaciones por Gmail
remitente = "correo@gmail.com"
password = "contraseña-de-aplicación"

[git]                        # Botón "☁️ Respaldar Excel en GitHub"
token = "github_pat_..."     # Token con permiso de escritura en el repo
repo = "nemet-source/nemet"
```

> En Streamlit Community Cloud el disco es efímero. Si los Secrets `[git]` están configurados, la app
> **respalda automáticamente** el Excel en GitHub después de cada guardado (inventario, clientes y cada
> folio emitido; empuja como máximo una vez por minuto y sesión). El botón **☁️ Respaldar Excel en GitHub**
> fuerza un respaldo manual. Cada push reinicia la app brevemente.
>
> Si dos personas editan a la vez, el segundo guardado detecta el conflicto y ofrece sobrescribir
> o reintentar después de sincronizar. Los folios se asignan releyendo el historial bajo cerrojo,
> así que dos sesiones ya no pueden registrar el mismo folio.
