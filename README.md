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

## Ejecución local

```bash
pip install -r requirements.txt
streamlit run app.py
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

> En Streamlit Community Cloud el disco es efímero: los cambios al Excel se pierden al
> reiniciarse la app. Usa el botón **☁️ Respaldar Excel en GitHub** para subirlos al repositorio
> (esto reinicia la app al hacer push).
