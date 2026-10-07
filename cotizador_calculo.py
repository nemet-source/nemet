#!/usr/bin/env python3
"""Cálculo de material del cotizador NEMET (por área, espesor y dosificación).

El manual NEMET maneja dos formas de dosificar y confundirlas infla las cotizaciones:

* **Sistemas epóxicos (resina + catalizador):** rendimiento en **kg por m² por mm** de
  espesor, `kg = área × mm × rendimiento`. Ejemplos del manual: EPOXY PRIMER
  ``0.3333`` kg/m²·mm (247 g de A + 86 g de B por m²) y EPOXY PISOS ``1.2`` kg/m²·mm
  (800 g de A + 400 g de B por m²).
* **Pigmentos (pastas, tintas, metal en polvo, fotoluminiscentes, micas):** dosificación
  en **gramos por m²**, independiente del espesor, `g = área × dosis_g_m2`. El manual da
  ``10`` g/m² para las pastas y ``8`` g/m² para los metalizados.

Aquí estuvo el error que infló la cotización COT-2026-003: la hoja ``Cat_Productos``
traía ``Rendimiento = 1`` (kg/m²·mm) en las filas de pastas y tintas, así que 16 m² de
pasta se cotizaban como 16 kg (100 veces los 160 g de la receta) y la tinta como 16 L.

Este módulo no depende de Streamlit para poder probarse directo con
``python tests_cotizador.py``; la app lo importa como ``cotizador_calculo``.
"""
import math
import re
import unicodedata

#: Rendimiento de respaldo (kg/m²·mm) si el producto no está en `Cat_Productos`.
RENDIMIENTO_DEFAULT_KG_M2_MM = 1.2

#: Dosificación de respaldo (g/m²) de un pigmento sin dosis capturada en `Cat_Productos`.
#: Es la dosis de las pastas según el manual; sirve de red de seguridad para que un
#: pigmento nunca vuelva a calcularse con un rendimiento de resina (1 kg/m²).
DOSIS_PIGMENTO_DEFAULT_G_M2 = 10.0

#: Familias que se dosifican por área (g/m²) y **nunca** por espesor.
#: Se evalúa sobre el nombre normalizado (sin acentos y en mayúsculas).
_RX_FAMILIA_DOSIFICADA = re.compile(
    r"(PASTA|TINTA|PIGMENTO|FOTOLUMIN|METAL|MICA|GLITTER|PURPURINA|ESCARCHA)"

)


def normalizar_clave(texto):
    """Mayúsculas, sin acentos y sin espacios repetidos, para comparar nombres de productos."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().upper()


def es_familia_dosificada(nombre):
    """True si el producto se dosifica en g/m² (pigmento), sin importar el espesor."""
    return bool(_RX_FAMILIA_DOSIFICADA.search(normalizar_clave(nombre)))


# ==========================================
# PRESENTACIONES DEL INVENTARIO
# ==========================================
_RE_PRESENTACION = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?|\d+\s*/\s*\d+)\s*(kg|kgs|kilo|kilos|g|gr|grs|gramos|l|lt|lts|litro|litros|ml)\.?\s*$",
    re.IGNORECASE,
)
_FACTOR_A_KG = {"kg": 1, "kgs": 1, "kilo": 1, "kilos": 1, "g": 0.001, "gr": 0.001, "grs": 0.001,
                "gramos": 0.001, "l": 1, "lt": 1, "lts": 1, "litro": 1, "litros": 1, "ml": 0.001}
FACTOR_A_KG = _FACTOR_A_KG


def parsear_presentacion_kg(presentacion):
    """'1.420 kg' -> 1.42 | '500 g' -> 0.5 | '1/2 kg' -> 0.5 | '1 L' -> 1.0 (1 L ~ 1 kg).

    Piezas ('1 Pz') y rangos de precio ('10 a 20 kg', '< 10 kg') no son presentaciones
    cotizables -> None.
    """
    coincidencia = _RE_PRESENTACION.match(str(presentacion))
    if not coincidencia:
        return None
    numero, unidad = coincidencia.group(1), coincidencia.group(2).lower()
    if "/" in numero:
        numerador, denominador = numero.split("/")
        valor = float(numerador) / float(denominador)
    else:
        valor = float(numero.replace(",", "."))
    kg = valor * _FACTOR_A_KG[unidad]
    return kg if kg > 0 else None


def unidades_para_cubrir(kg_necesarios, kg_por_unidad):
    """Unidades enteras de una presentación necesarias para cubrir los kg pedidos (0 si no aplica)."""
    try:
        kg_necesarios = float(kg_necesarios)
        kg_por_unidad = float(kg_por_unidad)
    except (TypeError, ValueError):
        return 0
    if kg_necesarios <= 0 or kg_por_unidad <= 0:
        return 0
    return max(1, math.ceil(round(kg_necesarios / kg_por_unidad, 6)))


def elegir_opcion_mas_economica(opciones, clave_precio="precio", clave_unidades="unidades"):
    """La opción más barata de la lista; si empatan, la de menos unidades (menos inventario)."""
    candidatas = [o for o in (opciones or []) if o.get(clave_precio) is not None]
    if not candidatas:
        return None
    return min(candidatas, key=lambda o: (float(o[clave_precio]), float(o.get(clave_unidades, 0) or 0)))


# ==========================================
# MATERIAL NECESARIO
# ==========================================
def calcular_material(area_m2, espesor_mm=1.0, info_producto=None, nombre="", dosis_manual_g_m2=None):
    """Material necesario para un área, según sea resina (espesor) o pigmento (dosificación).

    Devuelve un dict con ``kg_necesarios``, ``gramos``, ``modalidad`` (``"espesor"`` o
    ``"dosificacion"``), la ``dosis_g_m2`` o el ``rendimiento`` aplicados y las banderas
    ``dosis_por_defecto`` / ``rendimiento_por_defecto`` para avisar en pantalla cuando el
    valor no venía del catálogo.
    """
    info_producto = info_producto or {}
    try:
        area = max(0.0, float(area_m2 or 0.0))
    except (TypeError, ValueError):
        area = 0.0

    dosis_catalogo = info_producto.get("dosis_g_m2")

    if es_familia_dosificada(nombre) or dosis_catalogo:
        dosis = dosis_manual_g_m2 or dosis_catalogo
        por_defecto = False
        try:
            dosis = float(dosis)
        except (TypeError, ValueError):
            dosis = 0.0
        if dosis <= 0:
            dosis = DOSIS_PIGMENTO_DEFAULT_G_M2
            por_defecto = True
        gramos = area * dosis
        return {
            "kg_necesarios": gramos / 1000.0,
            "gramos": gramos,
            "modalidad": "dosificacion",
            "es_dosificacion": True,
            "dosis_g_m2": dosis,
            "dosis_por_defecto": por_defecto,
            "rendimiento": None,
            "rendimiento_por_defecto": False,
        }

    try:
        espesor = max(0.0, float(espesor_mm or 0.0))
    except (TypeError, ValueError):
        espesor = 0.0
    rendimiento = info_producto.get("rendimiento")
    try:
        rendimiento = float(rendimiento)
    except (TypeError, ValueError):
        rendimiento = 0.0
    por_defecto = rendimiento <= 0
    if por_defecto:
        rendimiento = RENDIMIENTO_DEFAULT_KG_M2_MM
    kg = area * espesor * rendimiento
    return {
        "kg_necesarios": kg,
        "gramos": kg * 1000.0,
        "modalidad": "espesor",
        "es_dosificacion": False,
        "dosis_g_m2": None,
        "dosis_por_defecto": False,
        "rendimiento": rendimiento,
        "rendimiento_por_defecto": por_defecto,
    }
