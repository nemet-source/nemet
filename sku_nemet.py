#!/usr/bin/env python3
"""Generación automática de SKUs para el inventario NEMET.

El catálogo usa códigos del tipo ``PREFIJO NNN`` (``EPT 04``, ``POLIU 02``, ``CN 03``):
un prefijo por familia de producto y un consecutivo de dos dígitos por presentación,
en orden ascendente de tamaño (g → kg → L).

Este módulo concentra esa regla para que el inventario **nunca** vuelva a quedarse sin
códigos (pasó al subir al repositorio una plantilla vieja del Excel: la columna `SKU`
llegó vacía y la app mostraba *NONE* en todas las filas). La app llama a
``asignar_skus_faltantes`` al cargar la hoja `Inventario`, así que:

* una fila sin SKU recibe uno coherente con su familia (mismo prefijo que sus hermanas);
* un producto nuevo —sin ninguna hermana en la hoja— recibe el prefijo de la tabla
  ``ALIAS_FAMILIAS`` si su nombre es conocido, o un acrónimo legible si es inédito;
* los SKUs ya capturados **nunca** se modifican ni se reenumeran.

Sin dependencias de Streamlit: se prueba con ``python tests_sku.py``.
"""
import re
import unicodedata

# Palabras que no aportan al prefijo (artículos, conectores y unidades de la presentación).
PALABRAS_IGNORADAS = {
    "A", "AL", "B", "CADA", "CON", "DE", "DEL", "EL", "EN", "LA", "LAS", "LOS", "O",
    "PARA", "POR", "SIN", "SU", "UN", "UNA", "Y",
    "G", "GR", "GRAMO", "GRAMOS", "KG", "K", "KILO", "KILOS",
    "L", "LT", "LTS", "LITRO", "LITROS", "M", "ML", "PZ", "PZA", "PZAS", "PIEZA", "PIEZAS",
    "CM", "MM", "MG",
}

# Valores que el Excel/Streamlit entregan cuando en realidad no hay SKU.
VALORES_VACIOS = {"", "NONE", "NAN", "N/A", "NA", "NULL", "-", "--", "—", "S/N", "SN"}

# Familias ya codificadas en el catálogo NEMET (clave: nombre normalizado).
# Se conservan tal cual para que un producto nuevo de una familia existente herede su
# prefijo histórico en lugar de inventar uno nuevo.
ALIAS_FAMILIAS = {
    "ARENA SILICA": "AS",
    "BASCULA DIGITAL CAPACIDAD 5KG": "BD",
    "COPA DIAMANTE PARA DESBASTE 7": "CD",
    "CUNA PARA VINIL": "CV",
    "CUNA VINIL": "CV",
    "CASCARA DE NARANJA": "CN",
    "EPO DEEP TRANSPARENTE": "EDT",
    "EPO DEEP ULTRA A Y B TRANSPARENTE": "EDU",
    "EPO FAST A Y B TRANSPARENTE": "EFT",
    "EPO PAINT A Y B": "EPA",
    "EPOXI BUBLES": "EB",
    "EPOXI SPIKES UNITALLA": "ES",
    "EPOXY SPIKES": "ES",
    "EPOXY CUARZO": "EC",
    "EPOXY METAL PERLADO Y METALICOS EN POLVO": "EM",
    "EPOXY PASTA COLORES SOLIDOS": "EP",
    "EPOXY PISOS A Y B TRANSPARENTE": "EPT",
    "EPOXY PRIMER A Y B TRANSPARENTE": "EPRT",
    "EPOXY SPIKES PREMIUM": "ESP",
    "EPOXY TINTA TRASLUCIDOS LIQUIDOS": "ET",
    "EPOXY TINTA": "ET",
    "EPOXY VEINS": "EV",
    "EPOXY GRANO": "EG",
    "EPOXY GRANO COMBINACION": "EGC",
    "EPOXY HOJUELA 1 COLOR": "EH",
    "EPOXY HOJUELA COMBINACION": "EHC",
    "EPOXY MICA": "EMI",
    "EPOXY MICA COMBINADO": "EMC",
    "ESTUCO VENECIANO": "ESV",
    "FELPA": "F",
    "FELPA 4": "F",
    "FELPA 9": "F",
    "GRANO WALL": "GW",
    "JALADOR DENTADO": "JD",
    "JALADOR EPOXICO PREMIUM": "JEP",
    "JALADOR NIVELADOR EPOXICO": "JNE",
    "JALADOR PARA EPOXICOS ECO": "JEE",
    "KIT DE BORLAS PARA PULIMENTO": "KB",
    "KIT BORLAS PULIMENTO": "KB",
    "HOJUELA COLOR": "EH",
    "HOJUELA COMBINADO": "EHC",
    "LIJADORA PISO": "LPE",
    "EPOXY BUBBLES": "EB",
    "POLIURETANO": "POLIU",
    "LIJADORA DE PISO ELECTRICA": "LPE",
    "MEZCLADOR": "M",
    "PIGMENTOS FOTOLUMINISCENTES": "PF",
    "POLIURETANO A Y B TRANSPARENTE": "POLIU",
    "RODILLO": "R",
    "RODILLO 4": "R",
    "RODILLO 9": "R",
    "RODILLO DE PICOS EPOXY PISOS": "RP",
}

_RE_SKU = re.compile(r"^(?P<prefijo>[A-Z0-9]+)\s+(?P<numero>\d{1,4})$")
_RE_NUMERO_INICIAL = re.compile(r"\d+(?:[.,]\d+)?")


# ------------------------------------------------------------------ utilidades
def normalizar(texto):
    """Mayúsculas, sin acentos y sin signos: la llave estable para comparar nombres."""
    if texto is None:
        return ""
    try:
        if texto != texto:  # NaN de pandas/numpy
            return ""
    except Exception:
        pass
    limpio = unicodedata.normalize("NFKD", str(texto))
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    limpio = limpio.replace("Ñ", "N").replace("ñ", "n")
    limpio = re.sub(r"[^0-9A-Za-z]+", " ", limpio.upper())
    return re.sub(r"\s+", " ", limpio).strip()


def sku_vacio(valor):
    """True si el valor equivale a «sin SKU» (vacío, None, NaN, *NONE*, guiones…)."""
    if valor is None:
        return True
    try:
        if valor != valor:  # NaN sin importar numpy/pandas
            return True
    except Exception:
        pass
    texto = str(valor).strip()
    return texto.upper() in VALORES_VACIOS or texto == ""


def prefijo_de_sku(sku):
    """Prefijo de un SKU existente («EPT 04» → «EPT»)."""
    texto = str(sku or "").strip().upper()
    coincidencia = _RE_SKU.match(texto)
    if coincidencia:
        return coincidencia.group("prefijo")
    # SKU fuera del formato estándar: se usa su parte alfabética inicial.
    letras = re.match(r"^[A-Z]+", texto)
    return letras.group(0) if letras else ""


def numero_de_sku(sku):
    """Consecutivo de un SKU existente («EPT 04» → 4); 0 si no lo tiene."""
    coincidencia = _RE_SKU.match(str(sku or "").strip().upper())
    return int(coincidencia.group("numero")) if coincidencia else 0


def formato_sku(prefijo, numero):
    """«EPT», 4 → «EPT 04» (sin acentos ni espacios: el SKU viaja en folios y PDF)."""
    limpio = normalizar(prefijo).replace(" ", "")
    return f"{limpio} {int(numero):02d}"


def _palabras_significativas(descripcion):
    """Palabras que sí aportan al prefijo: sin conectores, unidades ni números.

    Los tokens con dígitos («5K», «+60K», «20ML») describen la presentación, no la
    familia, así que se descartan: «Epoxy pisos 5K» y «Epoxy pisos +20 k» deben caer
    en el mismo prefijo y distinguirse solo por su consecutivo.
    """
    texto = normalizar(descripcion)
    palabras = []
    for palabra in texto.split():
        if not palabra or palabra in PALABRAS_IGNORADAS or any(c.isdigit() for c in palabra):
            continue
        palabras.append(palabra)
    return palabras


def clave_familia(descripcion):
    """Llave de familia: las palabras que aportan al prefijo.

    Así «Epoxy pisos 5K», «Epoxy pisos 9K» y «Epoxy pisos +20 k» son **una** familia
    —mismo prefijo, consecutivos 01, 02, 03— aunque la presentación cambie el nombre.
    """
    palabras = _palabras_significativas(descripcion)
    if palabras:
        return " ".join(palabras)
    return normalizar(descripcion)


def _acronimo(palabras, limite=4):
    return "".join(p[0] for p in palabras[:limite])


def prefijo_alias(descripcion):
    """Prefijo histórico de una familia, si el nombre se parece a una ya codificada.

    Reconoce el nombre completo («EPOXY PISOS (A Y B) TRANSPARENTE»), su raíz
    («EPOXY PISOS») y variantes que contengan esa raíz, para que una presentación
    escrita de otra forma no estrene prefijo.
    """
    clave = normalizar(descripcion)
    if clave in ALIAS_FAMILIAS:
        return ALIAS_FAMILIAS[clave]
    raiz = clave_familia(descripcion)
    if raiz in ALIAS_FAMILIAS:
        return ALIAS_FAMILIAS[raiz]
    # Variantes: el nombre de la familia debe abrir el nombre histórico, para que
    # «EPOXY PISOS» no tome el código de «RODILLO DE PICOS EPOXY PISOS».
    palabras_raiz = raiz.split()
    coincidencias = [
        nombre for nombre in ALIAS_FAMILIAS
        if palabras_raiz and nombre.split()[:len(palabras_raiz)] == palabras_raiz
    ]
    if coincidencias:
        return ALIAS_FAMILIAS[min(coincidencias, key=len)]
    return ""


def prefijo_para(descripcion, ocupados=()):
    """Prefijo para una familia sin SKU previo.

    1. Si el nombre corresponde a una familia ya codificada se usa su código histórico.
    2. Si no, se prueban acrónimos (iniciales → primeras letras de la primera palabra)
       hasta encontrar uno que ninguna *otra* familia esté usando.
    3. Último recurso: acrónimo + consecutivo («EP2»), para no romper la unicidad.
    """
    ocupados = {str(p).strip().upper() for p in ocupados if p}
    alias = prefijo_alias(descripcion)
    if alias:  # familia conocida: conserva su código aunque otra ya lo use (es la misma)
        return alias

    palabras = _palabras_significativas(descripcion)
    candidatos = []
    for n in range(min(len(palabras), 4), 1, -1):
        candidatos.append(_acronimo(palabras, n))
    if palabras:
        candidatos.extend([palabras[0][:3], palabras[0][:4], palabras[0][:5], palabras[0]])
    candidatos.append("P")

    vistos = set()
    for candidato in candidatos:
        candidato = candidato[:5]
        if not candidato or candidato in vistos:
            continue
        vistos.add(candidato)
        if candidato not in ocupados:
            return candidato

    base = (candidatos[0] or "P")[:4]
    for sufijo in range(2, 100):
        candidato = f"{base}{sufijo}"
        if candidato not in ocupados:
            return candidato
    return base


_MULTIPLICADORES = {
    "MG": 1e-6, "G": 1e-3, "GR": 1e-3, "GRAMO": 1e-3, "GRAMOS": 1e-3,
    "KG": 1.0, "K": 1.0, "KILO": 1.0, "KILOS": 1.0,
    "ML": 1e-3, "L": 1.0, "LT": 1.0, "LTS": 1.0, "LITRO": 1.0, "LITROS": 1.0,
    "PZ": 1.0, "PZA": 1.0, "PZAS": 1.0, "PIEZA": 1.0, "PIEZAS": 1.0,
}


def _magnitud(presentacion):
    """Tamaño de la presentación en unidades base (g→kg, ml→L) para ordenarla.

    «20 ml» (0.02) queda antes que «1 L» (1.0) y «200 g» (0,2) antes que «1 kg» (1.0).
    Devuelve ``inf`` cuando la presentación no trae número (p. ej. «PZA»).
    """
    if presentacion is None:
        return float("inf")
    texto = normalizar(presentacion)
    coincidencia = _RE_NUMERO_INICIAL.search(texto.replace(",", "."))
    if not coincidencia:
        return float("inf")
    try:
        valor = float(coincidencia.group(0))
    except ValueError:
        return float("inf")
    unidad = re.search(r"[A-Z]+", texto[coincidencia.end():])
    factor = _MULTIPLICADORES.get(unidad.group(0), 1.0) if unidad else 1.0
    return valor * factor


# ------------------------------------------------------------------ núcleo
def asignar_skus_faltantes(df, col_desc="Descripcion", col_pres="Presentacion", col_sku="SKU"):
    """Rellena los SKUs vacíos de un inventario sin tocar los ya capturados.

    Devuelve ``(df, asignados)`` donde ``asignados`` es la lista de
    ``(descripcion, sku_nuevo)`` en el orden en que se generaron (vacía si no faltaba
    ninguno). El DataFrame recibido no se modifica: se trabaja sobre una copia.
    """
    if df is None or len(df) == 0:
        return df, []
    df = df.copy()
    if col_sku not in df.columns:
        df[col_sku] = ""
    if col_desc not in df.columns:
        return df, []

    df[col_sku] = ["" if sku_vacio(valor) else str(valor).strip() for valor in df[col_sku]]

    usados = set()
    prefijo_por_familia = {}
    contadores = {}
    for descripcion, sku in zip(df[col_desc], df[col_sku]):
        if not sku:
            continue
        usados.add(sku)
        prefijo = prefijo_de_sku(sku)
        if not prefijo:
            continue
        prefijo_por_familia.setdefault(clave_familia(descripcion), prefijo)
        contadores[prefijo] = max(contadores.get(prefijo, 0), numero_de_sku(sku))

    faltantes = [i for i, sku in zip(df.index, df[col_sku]) if not sku]
    if not faltantes:
        return df, []

    # Se reservan primero los prefijos históricos de las familias presentes: así un
    # acrónimo genérico («EP» de Epoxy Pisos) no le roba el código a EPOXY PASTA (EP).
    for i, descripcion in zip(df.index, df[col_desc]):
        clave = clave_familia(descripcion)
        if not clave or clave in prefijo_por_familia:
            continue
        alias = prefijo_alias(descripcion)
        if alias:
            prefijo_por_familia[clave] = alias

    # Se numeran por familia y de la presentación más chica a la más grande, para que
    # «01» sea siempre la más pequeña aunque el renglón esté en otro orden en la hoja.
    orden_familias = {}
    for i in faltantes:
        clave = clave_familia(df.at[i, col_desc])
        if clave not in orden_familias:
            orden_familias[clave] = len(orden_familias)
    faltantes.sort(key=lambda i: (
        orden_familias.get(clave_familia(df.at[i, col_desc]), len(orden_familias)),
        _magnitud(df.at[i, col_pres]) if col_pres in df.columns else float("inf"),
        i,
    ))

    asignados = []
    for i in faltantes:
        descripcion = df.at[i, col_desc]
        if normalizar(descripcion) == "":
            continue  # fila vacía o de relleno: se deja sin SKU
        clave = clave_familia(descripcion)
        prefijo = prefijo_por_familia.get(clave)
        if prefijo is None:
            ocupados = set(prefijo_por_familia.values())
            prefijo = prefijo_para(descripcion, ocupados)
            prefijo_por_familia[clave] = prefijo
        numero = contadores.get(prefijo, 0) + 1
        sku = formato_sku(prefijo, numero)
        while sku in usados:  # huecos o numeración previa irregular
            numero += 1
            sku = formato_sku(prefijo, numero)
        contadores[prefijo] = numero
        usados.add(sku)
        df.at[i, col_sku] = sku
        asignados.append((str(descripcion), sku))

    return df, asignados


def skus_duplicados(df, col_sku="SKU"):
    """SKUs repetidos (ignorando los vacíos), ordenados alfabéticamente."""
    if df is None or col_sku not in getattr(df, "columns", []):
        return []
    serie = df[col_sku].fillna("").astype(str).str.strip()
    repetidos = serie[serie.ne("") & serie.duplicated()]
    return sorted(set(repetidos))
