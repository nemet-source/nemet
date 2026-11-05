#!/usr/bin/env python3
"""Cotizador General de Catálogo: el presupuesto en el formato del CSV de NEMET.

Este módulo es la aritmética pura (sin Streamlit) del presupuesto por catálogo que
nació de los archivos del proveedor (`Cotizador_Catalogo_Epoxy_Art_Completo.csv` y
el `.xlsx` que era un `.xlsx` renombrado). El formato es exactamente el del CSV:

    Ítem / Producto | Unidad / Presentación | Cantidad Requerida |
    Precio Unitario ($) | Subtotal ($ MXN)   +   fila TOTAL GENERAL

La muestra del proveedor (Epoxy pisos 5K + Epoxy Primer 9KG + Jalador Epóxico
Premium + Rodillo de Picos) suma $1,800.00 y `csv_presupuesto` reproduce ese
archivo byte a byte (cp1252, saltos CRLF, comillas solo cuando hay comas).

Los precios se usan tal cual vienen en la lista del proveedor: los márgenes
flacos (EPRT 01 0.5 %, rodillos 2.6–4.2 %, jalador premium 4.45 %, cuarzo +60 kg
2.2 %) NO se reprecian (decisión registrada en cotizador_general_catalogo.md);
sus márgenes reales quedan visibles en la hoja `Escalas_Precios_KG`.
"""
import csv
import io
import os

# --------------------------------------------------------------- formato CSV
TITULO_PRESUPUESTO = "COTIZADOR GENERAL Y GENERADOR DE PRESUPUESTOS DE CATALOGO"
SUBTITULO_PRESUPUESTO = ("Selecciona cualquier producto, herramienta o complemento "
                         "para cotizaciones personalizadas a la medida")
TITULO_DESGLOSE = "Desglose de Cotización de Productos y Herramientas"
COLUMNAS_PRESUPUESTO = [
    "Ítem / Producto",
    "Unidad / Presentación",
    "Cantidad Requerida",
    "Precio Unitario ($)",
    "Subtotal ($ MXN)",
]
ETIQUETA_TOTAL = "TOTAL GENERAL"
# La plantilla del proveedor reserva 15 renglones de artículo (en la muestra: 4
# capturados + 11 huecos con $0.00 en el subtotal) antes del TOTAL GENERAL.
FILAS_ARTICULOS_PLANTILLA = 15
SUBTOTAL_HUECO = "$0.00"

HOJA_CATALOGO_PRESUPUESTOS = "Catalogo_Presupuestos"

# --------------------------------------------------------------- formato numérico
def importe_txt(valor):
    """$1,800.00 — como se escribe en el CSV de la muestra."""
    return f"${float(valor or 0):,.2f}"


def cantidad_txt(valor):
    """1.00 / 4.00 — dos decimales, como en el CSV de la muestra."""
    return f"{float(valor or 0):,.2f}"


# --------------------------------------------------------------- utilidades PDF
_TRANSLITERACION_PDF = str.maketrans({
    "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", "•": "-", "→": "->",
})


def texto_pdf(valor):
    """Las fuentes base de FPDF solo soportan latin-1: translitera lo común y sustituye lo demás."""
    return str(valor).translate(_TRANSLITERACION_PDF).encode("latin-1", "replace").decode("latin-1")


# --------------------------------------------------------------- ítems
def etiqueta_item(datos):
    """Nombre del ítem para el presupuesto: producto y, si aplica, el área.

    Misma lista blanca que la cotización impresa: jamás se filtra el costo de
    compra guardado en la columna «Detalle» del carrito.
    """
    nombre = str(datos.get("nombre", datos.get("Descripcion", "")) or "")
    area = datos.get("area_m2", datos.get("Area_m2"))
    if area is not None and float(area) > 0:
        espesor = datos.get("espesor_mm", datos.get("Espesor_mm"))
        if espesor is not None and float(espesor) > 0:
            nombre += f" [{float(area):.2f} m² x {float(espesor):g} mm]"
        else:
            nombre += f" [{float(area):.2f} m²]"
    return nombre


def normalizar_item(datos):
    """Acepta un dict con nombre/unidad/cantidad/precio (o subtotal) y lo deja canónico."""
    if isinstance(datos, dict) and "nombre" in datos:
        nombre = etiqueta_item(datos)
        unidad = str(datos.get("unidad", "") or "")
        cantidad = float(datos.get("cantidad", 0) or 0)
        precio = float(datos.get("precio_unitario", 0) or 0)
        subtotal = datos.get("subtotal")
        subtotal = round(float(subtotal), 2) if subtotal is not None else round(cantidad * precio, 2)
    else:  # renglón de carrito (SKU / descripción / presentación / subtotal)
        fila = dict(datos)
        nombre = etiqueta_item(fila)
        unidad = str(fila.get("Presentacion", fila.get("unidad", "")) or "")
        cantidad = float(fila.get("Cantidad", fila.get("cantidad", 0)) or 0)
        subtotal = float(fila.get("Subtotal", fila.get("subtotal", 0)) or 0)
        precio = subtotal / cantidad if cantidad else 0.0
    return {
        "nombre": nombre,
        "unidad": unidad,
        "cantidad": cantidad,
        "precio_unitario": precio,
        "subtotal": subtotal,
    }


def items_desde_carrito(items):
    """Carrito (DataFrame o lista de dicts) -> ítems canónicos del presupuesto."""
    if hasattr(items, "iterrows"):
        filas = [fila for _, fila in items.iterrows()]
    else:
        filas = list(items or [])
    return [normalizar_item(fila) for fila in filas]


def items_desde_catalogo(df_catalogo, selecciones):
    """[(nombre_item, cantidad), ...] contra el catálogo -> ítems canónicos.

    El precio unitario sale del catálogo tal cual (lista del proveedor) y el
    subtotal es cantidad × precio.
    """
    por_nombre = {}
    if hasattr(df_catalogo, "iterrows"):
        for _, fila in df_catalogo.iterrows():
            por_nombre[str(fila.get("Producto / Item", "")).strip()] = fila
    items = []
    for nombre, cantidad in selecciones:
        fila = por_nombre.get(str(nombre).strip())
        if fila is None:
            raise KeyError(f"El producto «{nombre}» no está en el catálogo de presupuestos")
        precio = float(fila["Precio Público Sugerido (MXN)"])
        cantidad = float(cantidad)
        items.append({
            "nombre": str(fila["Producto / Item"]),
            "unidad": str(fila["Unidad de Venta"]),
            "cantidad": cantidad,
            "precio_unitario": precio,
            "subtotal": round(cantidad * precio, 2),
        })
    return items


def totales_presupuesto(items):
    """(cantidad total, importe total) del presupuesto."""
    items = items_desde_carrito(items)
    cantidad = sum(float(it["cantidad"]) for it in items)
    total = round(sum(float(it["subtotal"]) for it in items), 2)
    return cantidad, total


# --------------------------------------------------------------- renglones CSV
def filas_presupuesto(items):
    """Todos los renglones del archivo CSV, en el formato de la muestra."""
    items = items_desde_carrito(items)
    filas = [
        [TITULO_PRESUPUESTO, "", "", "", ""],
        [SUBTITULO_PRESUPUESTO, "", "", "", ""],
        ["", "", "", "", ""],
        [TITULO_DESGLOSE, "", "", "", ""],
        list(COLUMNAS_PRESUPUESTO),
    ]
    for it in items:
        filas.append([
            it["nombre"],
            it["unidad"],
            cantidad_txt(it["cantidad"]),
            importe_txt(it["precio_unitario"]),
            importe_txt(it["subtotal"]),
        ])
    huecos = ["", "", "", "", SUBTOTAL_HUECO]
    total_huecos = max(FILAS_ARTICULOS_PLANTILLA, len(items))
    for _ in range(total_huecos - len(items)):
        filas.append(list(huecos))
    cantidad_total, importe_total = totales_presupuesto(items)
    filas.append([ETIQUETA_TOTAL, "", cantidad_txt(cantidad_total), "", importe_txt(importe_total)])
    return filas


def csv_presupuesto(items):
    """Bytes del presupuesto: idénticos al CSV de la muestra (cp1252, CRLF)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerows(filas_presupuesto(items))
    return buffer.getvalue().encode("cp1252")


# --------------------------------------------------------------- tabla en pantalla
def presupuesto_dataframe(items):
    """Tabla visual: las 5 columnas del formato + la fila TOTAL GENERAL."""
    import pandas as pd  # import local: el resto del módulo no requiere pandas

    items = items_desde_carrito(items)
    filas = []
    for it in items:
        filas.append({
            COLUMNAS_PRESUPUESTO[0]: it["nombre"],
            COLUMNAS_PRESUPUESTO[1]: it["unidad"],
            COLUMNAS_PRESUPUESTO[2]: cantidad_txt(it["cantidad"]),
            COLUMNAS_PRESUPUESTO[3]: importe_txt(it["precio_unitario"]),
            COLUMNAS_PRESUPUESTO[4]: importe_txt(it["subtotal"]),
        })
    cantidad_total, importe_total = totales_presupuesto(items)
    filas.append({
        COLUMNAS_PRESUPUESTO[0]: ETIQUETA_TOTAL,
        COLUMNAS_PRESUPUESTO[1]: "",
        COLUMNAS_PRESUPUESTO[2]: cantidad_txt(cantidad_total),
        COLUMNAS_PRESUPUESTO[3]: "",
        COLUMNAS_PRESUPUESTO[4]: importe_txt(importe_total),
    })
    return pd.DataFrame(filas, columns=COLUMNAS_PRESUPUESTO)


# --------------------------------------------------------------- catálogo
def cargar_catalogo_presupuestos(ruta_excel):
    """Hoja `Catalogo_Presupuestos` del Sistema; vacía si la hoja no existe (libros antiguos)."""
    import pandas as pd

    columnas = ["Producto / Item", "Unidad de Venta", "Min Cantidad", "Max Cantidad",
                "Costo Proveedor (MXN)", "Precio Público Sugerido (MXN)",
                "Margen Bruto ($)", "Margen Bruto (%)", "Línea"]
    try:
        with pd.ExcelFile(ruta_excel) as libro:
            if HOJA_CATALOGO_PRESUPUESTOS not in libro.sheet_names:
                return pd.DataFrame(columns=columnas)
            df = pd.read_excel(libro, sheet_name=HOJA_CATALOGO_PRESUPUESTOS)
    except Exception:
        return pd.DataFrame(columns=columnas)
    return df.dropna(how="all").reset_index(drop=True)
