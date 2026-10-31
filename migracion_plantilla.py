#!/usr/bin/env python3
"""Migración de la plantilla del proveedor a la estructura que entiende la app.

La plantilla que llegó al repositorio el 31-oct-2026
(`respaldos/Sistema_Inventario_2026-10-31_sin_SKU.xlsx`) trae la lista de precios del
proveedor, pero **no** en el formato del catálogo NEMET:

===================  ==========================================  =========================
Plantilla            Significado                                 Lo que espera la app
===================  ==========================================  =========================
`Producto`           nombre reescrito (`Epoxy pisos 5K`)          `Descripcion` = nombre de
                                                                  `Cat_Productos`
`Unidad de venta`    tramo de compra (`0 - 5.999 KG`)             `Presentacion` real
                                                                  (`1.5 kg`, `60 g`, `1 Pz`)
`Costo proveedor`    costo **por kg/L** en tramos                 `PrecioCompra` por
                                                                  presentación
`Precio sugerido`    precio de venta **por kg/L** en tramos       `PrecioPublicoIVA` por
                                                                  presentación (con IVA)
`Línea`              venía corrida dos filas                      línea real del producto
===================  ==========================================  =========================

Este módulo hace la conversión de forma reproducible (nada de copiar y pegar a mano):

1. **nombres amarrados a `Cat_Productos`**: cada renglón de la plantilla se identifica
   con su familia canónica (`sku_nemet.clave_familia` + prefijo histórico del SKU), así
   el cotizador por área vuelve a encontrar todos los productos;
2. **escalas $/kg y $/L → precio por presentación**: `precio = kg_de_la_presentación ×
   precio_por_kg` del tramo que le toca (idéntico a como se armó el catálogo bueno del
   07-oct: 1.5 kg × $480 = $720, 20 kg × $400 = $8 000);
3. **`PrecioCompra`** con el costo del proveedor del mismo tramo, para que el margen por
   SKU sea real (antes valía 0 en 18 familias y el margen salía en 100 %);
4. **columna `Línea`** recalculada por familia (Resina / Acabado / Complemento /
   Herramienta) en lugar de la columna corrida de la plantilla;
5. **SKUs** con `sku_nemet.asignar_skus_faltantes`: los históricos se conservan y solo se
   generan los que falten.

Además cierra los huecos del catálogo: agrega a `Cat_Productos` las familias que no
tenían `Rendimiento` ni `Dosis_g_m2` (hojuela, grano, mica, cuarzo, cáscara de naranja,
estuco, grano wall, veins, arena sílica, EPO-PAINT) y a `Escalas_Precios_KG` las escalas
del proveedor que solo vivían en la plantilla (poliuretano, estuco y grano wall).

Sin Streamlit: se prueba con ``python tests_migracion.py`` y se aplica al libro con
``python herramientas/migrar_plantilla.py``.
"""
import math
import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

import cotizador_calculo as cocalc
import sku_nemet as skun

#: IVA con el que se publican los precios (igual que `app.TASA_IVA`).
TASA_IVA = 0.16

#: Título que la app escribe en la fila 1 de la hoja `Inventario`.
TITULO_INVENTARIO = "CONTROL DE INVENTARIO Y FACTURACIÓN - NEMET"

HOJA_INVENTARIO = "Inventario"
HOJA_CATALOGO = "Cat_Productos"
HOJA_ESCALAS = "Escalas_Precios_KG"

# Formas en que la plantilla expresa el precio:
#   kg     → $/kg (o $/L) por tramo; se multiplica por el tamaño de la presentación
#   unidad → el precio ya es el de esa presentación (bolsas de g, tintas de ml, piezas)
#   rango  → la presentación ES un tramo de compra (`0 a 59.999 kg`); el precio es $/kg
MODO_KG = "kg"
MODO_UNIDAD = "unidad"
MODO_RANGO = "rango"

_LINEA_RESINA = "Resina"
_LINEA_ACABADO = "Acabado"
_LINEA_COMPLEMENTO = "Complemento"
_LINEA_HERRAMIENTA = "Herramienta"


@dataclass(frozen=True)
class Familia:
    """Una familia del inventario y cómo se lee su precio en la plantilla del proveedor."""

    prefijo: str
    nombre: str
    modo: str
    linea: str
    claves_plantilla: tuple = field(default_factory=tuple)
    #: Otras fichas de la misma familia (RODILLO 4" y 9" comparten prefijo).
    nombres_extra: tuple = field(default_factory=tuple)

    def nombres(self):
        return (self.nombre,) + tuple(self.nombres_extra)


#: Las 88 fichas del inventario, agrupadas por familia. El orden es el del catálogo.
FAMILIAS = (
    Familia("EDT", "EPO-DEEP TRANSPARENTE", MODO_KG, _LINEA_RESINA, ("EPO DEEP",)),
    Familia("EFT", "EPO-FAST (A Y B) TRANSPARENTE", MODO_KG, _LINEA_RESINA, ("EPO FAST",)),
    Familia("EPT", "EPOXY PISOS (A Y B) TRANSPARENTE", MODO_KG, _LINEA_RESINA, ("EPOXY PISOS",)),
    Familia("EPRT", "EPOXY PRIMER (A Y B) TRANSPARENTE", MODO_KG, _LINEA_RESINA, ("EPOXY PRIMER",)),
    Familia("EDU", "EPO-DEEP ULTRA (A Y B) TRANSPARENTE", MODO_KG, _LINEA_RESINA, ("EPO DEEP ULTRA",)),
    Familia("POLIU", "POLIURETANO (A Y B) TRANSPARENTE", MODO_KG, _LINEA_RESINA, ("POLIURETANO",)),
    Familia("EP", "EPOXY PASTA COLORES SÓLIDOS", MODO_UNIDAD, _LINEA_COMPLEMENTO, ("EPOXY PASTA",)),
    Familia("ET", "EPOXY TINTA TRASLÚCIDOS LÍQUIDOS", MODO_UNIDAD, _LINEA_COMPLEMENTO, ("EPOXY TINTA",)),
    Familia("PF", "PIGMENTOS FOTOLUMINISCENTES", MODO_UNIDAD, _LINEA_COMPLEMENTO),
    Familia("EM", "EPOXY METAL PERLADO Y METÁLICOS EN POLVO", MODO_UNIDAD, _LINEA_COMPLEMENTO, ("EPOXY METAL",)),
    Familia("EV", "EPOXY VEINS", MODO_UNIDAD, _LINEA_COMPLEMENTO, ("EPOXY VEINS",)),
    Familia("EH", "EPOXY-HOJUELA (1 color)", MODO_RANGO, _LINEA_COMPLEMENTO, ("HOJUELA COLOR",)),
    Familia("EHC", "EPOXY-HOJUELA (combinación)", MODO_RANGO, _LINEA_COMPLEMENTO, ("HOJUELA COMBINADO",)),
    Familia("EG", "EPOXY-GRANO", MODO_RANGO, _LINEA_COMPLEMENTO),
    Familia("EGC", "EPOXY-GRANO (combinación)", MODO_RANGO, _LINEA_COMPLEMENTO),
    Familia("EMI", "EPOXY-MICA", MODO_UNIDAD, _LINEA_COMPLEMENTO),
    Familia("EMC", "EPOXY-MICA (combinado)", MODO_UNIDAD, _LINEA_COMPLEMENTO),
    Familia("EPA", "EPO-PAINT (A y B)", MODO_UNIDAD, _LINEA_COMPLEMENTO),
    Familia("AS", "ARENA SILICA", MODO_UNIDAD, _LINEA_COMPLEMENTO),
    Familia("RP", "RODILLO DE PICOS EPOXY-PISOS", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("RODILLO PICOS",)),
    Familia("JEP", "JALADOR EPÓXICO PREMIUM", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("JALADOR EPOXICO PREMIUM",)),
    Familia("JEE", "JALADOR PARA EPÓXICOS ECO", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("JALADOR EPOXICO ECO",)),
    Familia("JNE", "JALADOR NIVELADOR EPÓXICO", MODO_UNIDAD, _LINEA_HERRAMIENTA),
    Familia("JD", "JALADOR DENTADO", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("JALADOR DENTADO",)),
    Familia("CV", "CUÑA PARA VINIL", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("CUNA VINIL",)),
    Familia("R", 'RODILLO 4"', MODO_UNIDAD, _LINEA_HERRAMIENTA, ("RODILLO",), ('RODILLO 9"',)),
    Familia("F", 'FELPA 4"', MODO_UNIDAD, _LINEA_HERRAMIENTA, ("FELPA",), ('FELPA 9"',)),
    Familia("M", "MEZCLADOR", MODO_UNIDAD, _LINEA_HERRAMIENTA),
    Familia("ES", "EPOXI SPIKES UNITALLA", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("EPOXY SPIKES",)),
    Familia("ESP", "EPOXY SPIKES PREMIUM", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("EPOXY SPIKES PREMIUM",)),
    Familia("KB", "KIT DE BORLAS PARA PULIMENTO", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("KIT BORLAS PULIMENTO",)),
    Familia("CD", 'COPA DIAMANTE PARA DESBASTE 7"', MODO_UNIDAD, _LINEA_HERRAMIENTA),
    Familia("EB", "EPOXI BUBLES", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("EPOXY BUBBLES",)),
    Familia("BD", "BÁSCULA DIGITAL CAPACIDAD 5kg", MODO_UNIDAD, _LINEA_HERRAMIENTA),
    Familia("LPE", "LIJADORA DE PISO ELÉCTRICA", MODO_UNIDAD, _LINEA_HERRAMIENTA, ("LIJADORA PISO",)),
    Familia("CN", "CÁSCARA DE NARANJA", MODO_RANGO, _LINEA_ACABADO, ("CASCARA NARANJA",)),
    Familia("ESV", "ESTUCO VENECIANO", MODO_KG, _LINEA_ACABADO, ("ESTUCO VENECIANO",)),
    Familia("GW", "GRANO WALL", MODO_KG, _LINEA_ACABADO, ("GRANO WALL",)),
    Familia("EC", "EPOXY CUARZO", MODO_RANGO, _LINEA_COMPLEMENTO, ("EPOXY CUARZO",)),
)

#: Todas las familias del inventario comparten prefijo salvo estos casos con dos fichas.
_PREFIJOS_DOBLES = ("R", "F")

#: Familias que no estaban en `Cat_Productos`: consumo por m² y proporción de mezcla.
#: Los valores sin respaldo del manual son **criterio NEMET de arranque** (ajustables en
#: la hoja); quedan anotados en `Fuente_Dosis` para que se vea de dónde salen.
VALORES_CATALOGO = {
    "CÁSCARA DE NARANJA": {
        "dosis": 100.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: texturizante al voleo ≈100 g/m² (ajustable)",
    },
    "EPOXY-HOJUELA (1 color)": {
        "dosis": 150.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: hojuela al voleo ≈150 g/m² (ajustable)",
    },
    "EPOXY-HOJUELA (combinación)": {
        "dosis": 150.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: hojuela al voleo ≈150 g/m² (ajustable)",
    },
    "EPOXY-GRANO": {
        "dosis": 2500.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: grano al voleo ≈2.5 kg/m² (ajustable)",
    },
    "EPOXY-GRANO (combinación)": {
        "dosis": 2500.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: grano al voleo ≈2.5 kg/m² (ajustable)",
    },
    "EPOXY-MICA": {
        "dosis": 8.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Manual NEMET: 8 g/m²",
    },
    "EPOXY-MICA (combinado)": {
        "dosis": 8.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Manual NEMET: 8 g/m²",
    },
    "EPOXY VEINS": {
        "dosis": 20.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: vetas ≈20 g/m² (ajustable)",
    },
    "EPOXY CUARZO": {
        "dosis": 2500.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: cuarzo al voleo ≈2.5 kg/m² (ajustable)",
    },
    "ARENA SILICA": {
        "dosis": 2000.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: arena sílica como carga ≈2 kg/m² (ajustable)",
    },
    "ESTUCO VENECIANO": {
        "rendimiento": 1.0, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: ≈1 L/m² por capa de 1 mm",
    },
    "GRANO WALL": {
        "rendimiento": 1.5, "prop_a": 100, "prop_b": 0,
        "fuente": "Criterio NEMET: ≈1.5 L/m² por capa",
    },
    "EPO-PAINT (A y B)": {
        "rendimiento": 0.2, "prop_a": 100, "prop_b": 50,
        "fuente": "Criterio NEMET: ≈0.2 kg/m² por capa",
    },
}

#: Familias a las que la plantilla sí les da escalas $/kg-L y que no estaban en la hoja
#: `Escalas_Precios_KG` (las demás se venden por presentación, sin venta a granel).
ESCALAS_NUEVAS = ("POLIU", "ESV", "GW")

_RE_RANGO = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:-|–|a|al)\s*(\d+(?:[.,]\d+)?)\s*(?:kg|kgs|kilo|kilos|l|lt|lts|litro|litros)?",
    re.IGNORECASE,
)
_RE_ABIERTO = re.compile(r"^\s*\+\s*(\d+(?:[.,]\d+)?)")
_RE_MAS_EN_NOMBRE = re.compile(r"\+\s*(\d+(?:[.,]\d+)?)")
_RE_PIEZA = re.compile(r"^\s*(?:pz|pza|pzs|pzas|pieza|piezas)\s*$", re.IGNORECASE)
_RE_DIGITOS = re.compile(r"\d+(?:[.,]\d+)?")


# ======================================================================= utilidades
def normalizar(texto):
    """Mayúsculas, sin acentos y sin espacios repetidos (igual que `sku_nemet`)."""
    return skun.normalizar(texto)


#: Índice nombre normalizado → familia (incluye las tallas de rodillo y felpa).
_FAMILIA_POR_NOMBRE = {
    normalizar(nombre): familia
    for familia in FAMILIAS
    for nombre in familia.nombres()
}


def _texto(valor):
    """Texto de una celda, tratando NaN/None como vacío (``str(nan)`` no es vacío)."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    return str(valor).strip()


def _numero(texto):
    return float(str(texto).replace(",", "."))


def parsear_rango(texto):
    """``'0 - 5.999 KG'`` → (0.0, 5.999); ``'+ 60 kg'`` → (60.0, None); si no es rango, None."""
    if texto is None:
        return None
    limpio = str(texto).strip()
    abierto = _RE_ABIERTO.match(limpio)
    if abierto:
        return (_numero(abierto.group(1)), None)
    coincidencia = _RE_RANGO.fullmatch(limpio)
    if coincidencia:
        desde, hasta = _numero(coincidencia.group(1)), _numero(coincidencia.group(2))
        if hasta > desde:
            return (desde, hasta)
    return None


def interpretar_presentacion(producto, unidad):
    """Cómo se lee una fila de la plantilla: tramo, tamaño o pieza.

    Devuelve ``{"tipo": "rango"|"tamano"|"pieza", "desde", "hasta", "magnitud"}``.
    ``magnitud`` está en kg/L (20 ml → 0.02, 60 g → 0.06) para poder cruzar la
    presentación de la plantilla con la del inventario.
    """
    rango = parsear_rango(unidad)
    if rango:
        return {"tipo": "rango", "desde": rango[0], "hasta": rango[1], "magnitud": rango[0]}
    # «EPOXY PRIMER +20 kg» trae el «+» en el nombre y el tamaño pelón en la unidad.
    mas = _RE_MAS_EN_NOMBRE.search(str(producto or ""))
    if mas:
        return {"tipo": "rango", "desde": _numero(mas.group(1)), "hasta": None,
                "magnitud": _numero(mas.group(1))}
    if _RE_PIEZA.match(str(unidad or "").strip()):
        return {"tipo": "pieza", "desde": None, "hasta": None, "magnitud": None}
    magnitud = cocalc.parsear_presentacion_kg(unidad)
    return {"tipo": "tamano", "desde": magnitud, "hasta": magnitud, "magnitud": magnitud}


def _columna(df, fragmentos, obligatoria=False):
    """Nombre real de la columna cuya versión normalizada contiene alguno de los fragmentos."""
    for columna in df.columns:
        clave = normalizar(columna)
        if any(normalizar(fragmento) in clave for fragmento in fragmentos):
            return columna
    if obligatoria:
        raise ValueError(f"No se encontró la columna {fragmentos} en {list(df.columns)}")
    return None


# ======================================================================= lectura
def leer_plantilla(ruta, hoja=HOJA_INVENTARIO):
    """Lee la plantilla del proveedor → lista de dicts con nombre, unidad, costo y precio.

    Detecta el encabezado igual que la app (primera fila que trae «SKU»).
    """
    muestra = pd.read_excel(ruta, sheet_name=hoja, header=None, nrows=6)
    fila_encabezado = 0
    for indice, fila in muestra.iterrows():
        celdas = [str(c).strip().lower() for c in fila.tolist() if pd.notna(c)]
        if any(celda == "sku" for celda in celdas):
            fila_encabezado = int(indice)
            break
    df = pd.read_excel(ruta, sheet_name=hoja, header=fila_encabezado).dropna(how="all")
    df.columns = [str(c).strip() for c in df.columns]

    col_producto = _columna(df, ("producto", "descripcion"), obligatoria=True)
    col_unidad = _columna(df, ("unidad", "presentacion"))
    col_costo = _columna(df, ("costo", "coste"), obligatoria=True)
    col_precio = _columna(df, ("precio",), obligatoria=True)
    col_linea = _columna(df, ("linea",))

    filas = []
    for _, fila in df.iterrows():
        producto = _texto(fila.get(col_producto))
        if not producto or normalizar(producto) == "":
            continue
        unidad = _texto(fila.get(col_unidad)) if col_unidad else ""
        precio = pd.to_numeric(fila.get(col_precio), errors="coerce")
        costo = pd.to_numeric(fila.get(col_costo), errors="coerce")
        filas.append({
            "producto": producto,
            "unidad": unidad,
            "costo": float(costo) if pd.notna(costo) else None,
            "precio": float(precio) if pd.notna(precio) else None,
            "linea_declarada": _texto(fila.get(col_linea)) if col_linea else "",
            "clave": skun.clave_familia(producto),
            "prefijo": skun.prefijo_para(producto),
            "presentacion": interpretar_presentacion(producto, unidad),
        })
    return filas


# ======================================================================= tramos
def _rangos(filas):
    """Convierte las filas de una familia en tramos ``(desde, hasta, precio, costo, fila)``.

    Si la plantilla ya trae tramos («0 - 5.999 kg», «+ 20 kg») se respetan tal cual; si
    trae tamaños sueltos («1 LT», «4 LT», «19 LT») se arma el tramo de cada tamaño con el
    siguiente como tope (1 → 1-3.999, 4 → 4-18.999, 19 → +19), que es exactamente cómo
    factura el proveedor.
    """
    con_rango = [f for f in filas if f["presentacion"]["tipo"] == "rango"]
    if con_rango:
        return [(f["presentacion"]["desde"], f["presentacion"]["hasta"], f["precio"], f["costo"], f)
                for f in sorted(con_rango, key=lambda f: f["presentacion"]["desde"])]

    tamanos = [f for f in filas if f["presentacion"]["tipo"] == "tamano"
               and f["presentacion"]["magnitud"] is not None]
    tamanos.sort(key=lambda f: f["presentacion"]["magnitud"])
    tramos = []
    for posicion, fila in enumerate(tamanos):
        desde = fila["presentacion"]["magnitud"]
        siguiente = tamanos[posicion + 1]["presentacion"]["magnitud"] if posicion + 1 < len(tamanos) else None
        hasta = round(siguiente - 0.001, 3) if siguiente is not None else None
        tramos.append((desde, hasta, fila["precio"], fila["costo"], fila))
    return tramos


def _tramo_para(kg, tramos):
    for desde, hasta, precio, costo, fila in tramos:
        if kg >= desde - 1e-9 and (hasta is None or kg <= hasta + 1e-9):
            return precio, costo, fila
    return None


def _rango_en_tramos(desde, hasta, tramos):
    for tramo_desde, tramo_hasta, precio, costo, fila in tramos:
        if abs(tramo_desde - desde) <= 1e-6 and (
            (hasta is None and tramo_hasta is None) or
            (hasta is not None and tramo_hasta is not None and abs(tramo_hasta - hasta) <= 1e-6)
        ):
            return precio, costo, fila
    return None


def _numeros(texto):
    return {round(_numero(n), 3) for n in _RE_DIGITOS.findall(str(texto))}


# ======================================================================= inventario
def _origen_pieza(filas_familia, descripcion):
    """Fila de la plantilla para una ficha por pieza.

    La talla de un rodillo o una felpa solo aparece en el nombre («Rodillo 9''» ↔
    «RODILLO 9"»), así que si la familia tiene varias piezas se cruzan los números.
    """
    candidatas = [f for f in filas_familia if f["presentacion"]["tipo"] == "pieza"]
    if not candidatas:
        return None
    if len(candidatas) == 1:
        return candidatas[0]
    numeros = _numeros(descripcion)
    coincidentes = [f for f in candidatas if _numeros(f["producto"]) & numeros]
    return coincidentes[0] if len(coincidentes) == 1 else None


def migrar_inventario(df_base, filas_plantilla):
    """Devuelve ``(inventario_migrado, informe)`` a partir del inventario base y la plantilla.

    El inventario base aporta el esqueleto de las 88 fichas (stock, presentaciones y SKUs
    históricos); la plantilla aporta precios y costos. Nunca se pierde un renglón: si una
    familia no viene en la plantilla, conserva su precio actual y se reporta.
    """
    df = df_base.copy().reset_index(drop=True)
    for columna in ("SKU", "Descripcion", "Presentacion"):
        if columna in df.columns:
            df[columna] = df[columna].fillna("").astype(str).str.strip()
    df = df[~((df["Descripcion"] == "") & (df["SKU"] == ""))].reset_index(drop=True)

    for columna in ("PrecioPublicoIVA", "PrecioCompra"):
        if columna not in df.columns:
            df[columna] = 0.0
        df[columna] = pd.to_numeric(df[columna], errors="coerce").fillna(0.0)

    prefijos = [skun.prefijo_de_sku(sku) for sku in df["SKU"]]
    df["_prefijo"] = prefijos

    informe = {"filas": [], "sin_dato": [], "plantilla_sin_usar": [], "errores": [],
               "lineas_corregidas": []}
    usadas = set()
    tramos_por_familia = {}
    presentes = set()

    for indice, descripcion in zip(df.index, df["Descripcion"]):
        familia = _familia_por_nombre(descripcion)
        if familia is None:
            continue
        presentes.add(familia.prefijo)
        if not familia.claves_plantilla:
            continue
        if familia.prefijo not in tramos_por_familia:
            filas_familia = [f for f in filas_plantilla if f["clave"] in familia.claves_plantilla]
            tramos_por_familia[familia.prefijo] = (filas_familia, _rangos(filas_familia))
        filas_familia, tramos = tramos_por_familia[familia.prefijo]
        if not filas_familia:
            continue

        presentacion = df.at[indice, "Presentacion"]
        antes_precio = float(df.at[indice, "PrecioPublicoIVA"])
        antes_costo = float(df.at[indice, "PrecioCompra"])
        aplicado = None

        if familia.modo == MODO_KG:
            kg = cocalc.parsear_presentacion_kg(presentacion)
            if kg:
                tramo = _tramo_para(kg, tramos)
                if tramo:
                    precio_kg, costo_kg, origen = tramo
                    aplicado = (round(kg * precio_kg), round(kg * costo_kg, 2), origen)
        elif familia.modo == MODO_RANGO:
            rango = parsear_rango(presentacion)
            if rango:
                tramo = _rango_en_tramos(rango[0], rango[1], tramos)
                if tramo:
                    # La presentación es un tramo de compra: el precio de la lista es $/kg.
                    precio_kg, costo_kg, origen = tramo
                    aplicado = (precio_kg, costo_kg, origen)
        else:  # MODO_UNIDAD
            magnitud = cocalc.parsear_presentacion_kg(presentacion)
            origen = None
            if magnitud:
                for fila in filas_familia:
                    if fila["presentacion"]["tipo"] == "tamano" and fila["presentacion"]["magnitud"] is not None \
                            and abs(fila["presentacion"]["magnitud"] - magnitud) <= 1e-6:
                        origen = fila
                        break
            if origen is None:
                origen = _origen_pieza(filas_familia, descripcion)
            if origen is not None:
                aplicado = (origen["precio"], origen["costo"], origen)

        if aplicado is None:
            informe["sin_dato"].append({
                "sku": df.at[indice, "SKU"], "familia": familia.nombre,
                "presentacion": presentacion, "precio": antes_precio,
            })
            continue

        precio, costo, origen = aplicado
        usadas.add(id(origen))
        declarada = origen.get("linea_declarada") or ""
        if declarada and declarada != familia.linea:
            # La columna `Línea` de la plantilla venía corrida dos filas: se reporta el desfase.
            informe["lineas_corregidas"].append(
                {"sku": df.at[indice, "SKU"], "declarada": declarada, "correcta": familia.linea})
        df.at[indice, "PrecioPublicoIVA"] = float(precio)
        df.at[indice, "PrecioCompra"] = float(costo if costo is not None else 0.0)
        informe["filas"].append({
            "sku": df.at[indice, "SKU"],
            "familia": familia.nombre,
            "presentacion": presentacion,
            "precio_antes": antes_precio,
            "precio": float(precio),
            "costo_antes": antes_costo,
            "costo": float(costo if costo is not None else 0.0),
            "origen": f"{origen['producto']} · {origen['unidad']}",
        })

    for familia in FAMILIAS:
        if familia.claves_plantilla and familia.prefijo not in presentes:
            informe["errores"].append(f"{familia.nombre}: la familia no tiene renglones en el inventario")

    for fila in filas_plantilla:
        if id(fila) not in usadas:
            informe["plantilla_sin_usar"].append(f"{fila['producto']} · {fila['unidad']}")

    # Línea del producto: se recalcula por familia (la de la plantilla venía corrida).
    df["Línea"] = [
        familia.linea if (familia := _familia_por_nombre(descripcion)) else _linea_de(prefijo)
        for descripcion, prefijo in zip(df["Descripcion"], df["_prefijo"])
    ]
    df = df.drop(columns=["_prefijo"])

    # PrecioCompra / PrecioPublicoIVA → columnas derivadas igual que en la app.
    df["PrecioBaseSinIVA"] = (df["PrecioPublicoIVA"] / (1 + TASA_IVA)).round(6)
    df["IVA 16%"] = (df["PrecioPublicoIVA"] - df["PrecioBaseSinIVA"]).round(6)
    if "StockActual" in df.columns:
        stock = pd.to_numeric(df["StockActual"], errors="coerce").fillna(0)
        df["ValorInventario"] = (stock * df["PrecioPublicoIVA"]).round(2)
    else:
        df["ValorInventario"] = 0.0

    # Última red de seguridad: ningún renglón se queda sin SKU.
    df, generados = skun.asignar_skus_faltantes(df, "Descripcion", "Presentacion", "SKU")
    informe["skus_generados"] = generados
    return df, informe


def _familia_por_nombre(descripcion):
    """Familia del catálogo a la que pertenece una descripción del inventario."""
    return _FAMILIA_POR_NOMBRE.get(normalizar(descripcion))


def _linea_de(prefijo):
    """Línea de una ficha cuando su descripción ya no coincide con ninguna familia."""
    for familia in FAMILIAS:
        if familia.prefijo == prefijo:
            return familia.linea
    return ""


def _orden_columnas(df):
    """Orden de columnas de la app + `Línea` al final (no rompe el editor ni los derivados)."""
    orden = ["SKU", "Descripcion", "Presentacion", "StockMinimo", "StockInicial", "Entradas",
             "Salidas", "StockActual", "AlertaStock", "PrecioCompra", "PrecioBaseSinIVA",
             "IVA 16%", "PrecioPublicoIVA", "ValorInventario", "Línea"]
    presente = [columna for columna in orden if columna in df.columns]
    resto = [columna for columna in df.columns if columna not in presente]
    return df[presente + resto]


# ======================================================================= catálogo
def ampliar_catalogo(df_catalogo, df_inventario):
    """Agrega a `Cat_Productos` las familias que no tenían rendimiento ni dosis.

    Devuelve ``(catalogo_nuevo, agregadas)``. No toca las filas existentes.
    """
    df = df_catalogo.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for columna in ("Producto", "Presentacion", "PrecioPublico", "Rendimiento",
                    "Prop_A", "Prop_B", "Dosis_g_m2", "Fuente_Dosis"):
        if columna not in df.columns:
            df[columna] = None

    existentes = {normalizar(nombre) for nombre in df["Producto"].dropna() if normalizar(nombre)}

    def en_catalogo(nombre):
        clave = normalizar(nombre)
        return any(clave == otra or clave.startswith(otra) or otra.startswith(clave) for otra in existentes)

    agregadas = []
    vistos = set()
    for nombre, presentacion in zip(df_inventario["Descripcion"], df_inventario["Presentacion"]):
        nombre = str(nombre).strip()
        if not nombre or normalizar(nombre) in vistos or en_catalogo(nombre):
            continue
        valores = VALORES_CATALOGO.get(nombre)
        if valores is None:
            continue
        vistos.add(normalizar(nombre))
        magnitud = cocalc.parsear_presentacion_kg(presentacion)
        precios = pd.to_numeric(
            df_inventario.loc[df_inventario["Descripcion"].astype(str).str.strip() == nombre, "PrecioPublicoIVA"],
            errors="coerce")
        nueva = {
            "Producto": nombre,
            "Presentacion": float(magnitud) if magnitud else 0.0,
            # Precio de referencia por presentación (el que la app cobra vive en `Inventario`).
            "PrecioPublico": float(precios.iloc[0]) if len(precios) and pd.notna(precios.iloc[0]) else None,
            "Rendimiento": valores.get("rendimiento"),
            "Prop_A": valores.get("prop_a", 100),
            "Prop_B": valores.get("prop_b", 0),
            "Dosis_g_m2": valores.get("dosis"),
            "Fuente_Dosis": valores.get("fuente"),
        }
        df = pd.concat([df, pd.DataFrame([nueva])], ignore_index=True)
        agregadas.append(nombre)
    return df, agregadas


def ampliar_escalas(df_escalas, filas_plantilla):
    """Agrega a `Escalas_Precios_KG` las escalas del proveedor que solo estaban en la plantilla."""
    df = df_escalas.copy()
    df.columns = [str(c).strip() for c in df.columns]
    ya = {normalizar(nombre) for nombre in df["Producto_NEMET"].dropna()}
    nuevas = []
    for familia in FAMILIAS:
        if familia.prefijo not in ESCALAS_NUEVAS or normalizar(familia.nombre) in ya:
            continue
        filas_familia = [f for f in filas_plantilla if f["clave"] in familia.claves_plantilla]
        if not filas_familia:
            continue
        proveedor = filas_familia[0]["clave"] or normalizar(filas_familia[0]["producto"])
        for desde, hasta, precio, costo, fila in _rangos(filas_familia):
            costo = float(costo or 0.0)
            margen = precio - costo
            nuevas.append({
                "Producto_Proveedor": proveedor,
                "Producto_NEMET": familia.nombre,
                "Rango_Desde_Kg": desde,
                "Rango_Hasta_Kg": hasta,
                "Costo_Compra_IVA_por_Kg": costo,
                "Precio_Publico_por_Kg": precio,
                "Margen_por_Kg": round(margen, 2),
                "Margen_Porcentaje_Sobre_Venta": round(margen / precio * 100, 6) if precio else 0.0,
                "Margen_Markup_Sobre_Costo": round(margen / costo * 100, 6) if costo else 0.0,
                "Rango_Texto_Original": f"{desde:g} - {hasta:g} kg" if hasta is not None else f"+ {desde:g} kg",
            })
    if nuevas:
        df = pd.concat([df, pd.DataFrame(nuevas)], ignore_index=True)
    return df, [fila["Producto_NEMET"] for fila in nuevas]


# ======================================================================= validación
def validar(df_inventario, df_catalogo, df_escalas=None):
    """Revisa que el inventario migrado cumpla lo que la app necesita para cotizar.

    Devuelve ``{"errores": [...], "avisos": [...]}``. Los errores rompen la cotización
    (SKU vacío o repetido, familia sin consumo, precio en cero); los avisos son cosas que
    conviene capturar pero que la app sabe manejar (por ejemplo, una ficha sin presentación
    porque el proveedor aún no cotiza ese producto).
    """
    errores, avisos = [], []
    df = df_inventario
    if df is None or len(df) == 0:
        return {"errores": ["el inventario está vacío"], "avisos": []}

    skus = df["SKU"].fillna("").astype(str).str.strip()
    vacios = int((skus == "").sum())
    if vacios:
        errores.append(f"{vacios} renglones sin SKU")
    duplicados = skun.skus_duplicados(df, "SKU")
    if duplicados:
        errores.append(f"SKUs repetidos: {', '.join(duplicados)}")

    if df_catalogo is None or df_catalogo.empty:
        errores.append("Cat_Productos está vacía")
        return {"errores": errores, "avisos": avisos}

    catalogo = df_catalogo.copy()
    catalogo.columns = [str(c).strip() for c in catalogo.columns]
    consumo = {}
    for _, fila in catalogo.iterrows():
        nombre = normalizar(fila.get("Producto"))
        if not nombre or nombre in consumo:
            continue
        rendimiento = pd.to_numeric(fila.get("Rendimiento"), errors="coerce")
        dosis = pd.to_numeric(fila.get("Dosis_g_m2"), errors="coerce")
        consumo[nombre] = (pd.notna(rendimiento) and rendimiento > 0, pd.notna(dosis) and dosis > 0)

    def tiene_consumo(nombre):
        clave = normalizar(nombre)
        if clave in consumo:
            return any(consumo[clave])
        candidatas = [k for k in consumo if clave.startswith(k) or k.startswith(clave)]
        if not candidatas:
            return False
        return any(consumo[max(candidatas, key=len)])

    escalas = set()
    if df_escalas is not None and len(df_escalas):
        escalas = {normalizar(nombre) for nombre in df_escalas["Producto_NEMET"].dropna()}

    for prefijo, familia in [(skun.prefijo_de_sku(sku), descripcion)
                             for sku, descripcion in zip(df["SKU"], df["Descripcion"])]:
        linea = _linea_de(prefijo)
        if linea == _LINEA_HERRAMIENTA:
            continue   # se cotiza por pieza: no necesita consumo por m²
        if not tiene_consumo(familia):
            if f"{familia}: no tiene Rendimiento ni Dosis_g_m2 en Cat_Productos" not in errores:
                errores.append(f"{familia}: no tiene Rendimiento ni Dosis_g_m2 en Cat_Productos")

    for _, fila in df.iterrows():
        presentacion = str(fila.get("Presentacion")).strip()
        precio = pd.to_numeric(fila.get("PrecioPublicoIVA"), errors="coerce")
        sku = str(fila.get("SKU")).strip()
        if presentacion in ("", "0", "0.0"):
            avisos.append(f"{sku} · {fila.get('Descripcion')}: sin presentación capturada (no se puede cotizar)")
        elif pd.isna(precio) or precio <= 0:
            errores.append(f"{sku} · {fila.get('Descripcion')}: precio público en cero")
        costo = pd.to_numeric(fila.get("PrecioCompra"), errors="coerce")
        if pd.isna(costo) or costo <= 0:
            avisos.append(f"{sku} · {fila.get('Descripcion')}: sin costo de compra (margen desconocido)")

    for familia in FAMILIAS:
        if familia.modo in (MODO_KG, MODO_RANGO) and familia.claves_plantilla \
                and normalizar(familia.nombre) not in escalas:
            avisos.append(f"{familia.nombre}: sin escalas de proveedor en {HOJA_ESCALAS}")

    return {"errores": errores, "avisos": avisos}
