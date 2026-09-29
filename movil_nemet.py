#!/usr/bin/env python3
"""Modo móvil del Sistema Maestro NEMET.

El sistema se usa mucho desde el teléfono (almacén, obra, visita a cliente), así que la
app resuelve en cada carga si el usuario viene de una pantalla chica y, en consecuencia:

1. **Detección** (``modo_movil``): con el ancho que declara el navegador (Client Hints),
   el ``user-agent`` como respaldo y el forzado manual por URL
   (``?movil=1`` / ``?escritorio=1``). Nunca usa JavaScript ni cookies.
2. **Ajustes táctiles y de espacio** (``estilos``): área táctil mínima de 44 px, botones a
   lo ancho, campos a 16 px (evita el zoom automático de iOS), métricas como tarjetas,
   columnas que se envuelven solas, títulos y rellenos contenidos. Se inyecta desde el
   arranque, así que también cubre la pantalla de acceso.
3. **Tema de la sesión** (``tema_actual``): claro por omisión (crema de marca) o nocturno
   con ``?tema=oscuro`` para revisar inventario de noche sin encandilarse.
4. **Reparto de columnas** (``columnas_apiladas``): en móvil los bloques de 3-4 columnas
   (métricas, acciones de cotización, campos del cotizador) se apilan en una o dos
   columnas por fila, sin duplicar código en ``app.py``.

El modo se resuelve **antes** del gate de acceso: la pantalla de login ya se ve bien en el
teléfono. Es a prueba de fallos: sin Streamlit corriendo (por ejemplo en las pruebas)
las funciones de detección son puras y no dependen de ``st``.
"""
from __future__ import annotations

import math
import re

import streamlit as st

__version__ = "1.0"

# ==========================================
# PARÁMETROS, CLAVES Y UMBRALES
# ==========================================
# Forzado manual desde la URL: ?movil=1 (siempre móvil) / ?escritorio=1 (siempre escritorio).
# Se recuerda en la sesión, así que sobrevive a las recargas y al botón de Streamlit.
PARAM_MOVIL = "movil"
PARAM_ESCRITORIO = "escritorio"
PARAM_TEMA = "tema"
CLAVE_TEMA = "nemet_tema"
CLAVE_FORZADO = "nemet_movil_forzado"
TEMAS = ("claro", "oscuro")

# Ancho a partir del cual se considera pantalla de teléfono (incluye tabletas: 768 px es el
# ancho típico de un iPad en vertical y de un teléfono en horizontal).
BREAKPOINT_PX = 768

# Encabezados donde el navegador puede declarar el ancho real del viewport (Client Hints).
# El primero es el que inyecta un proxy/CDN propio; los demás son estándar.
ENCABEZADOS_ANCHO = ("x-nemet-viewport", "viewport-width", "sec-ch-viewport-width", "x-device-width")
ENCABEZADO_UA = "user-agent"
ENCABEZADO_CLIENT_HINT_MOVIL = "sec-ch-ua-mobile"  # ?1 / ?0

RE_UA_MOVIL = re.compile(
    r"iphone|ipod|android[^)]*mobile|windows phone|iemobile|opera mini|opera mobi|blackberry|"
    r"bb10|palm|webos|mobile|mobi",
    re.I,
)
RE_UA_TABLET = re.compile(r"ipad|tablet|playbook|silk|kindle|android(?!.*mobile)", re.I)
RE_ANCHO = re.compile(r"^\s*(\d{2,5})\s*(?:px)?\s*$", re.I)

VERDADEROS = ("1", "si", "sí", "s", "yes", "y", "true", "verdadero", "on", "movil", "móvil")
FALSOS = ("0", "no", "n", "false", "falso", "off", "escritorio", "desktop")

# Paletas del tema de la sesión (los colores base son los de PALETA_NEMET en app.py).
PALETA_CLARA = {
    "fondo": "#F5EFE6",
    "panel": "#EFE7DB",
    "tinta": "#26231F",
    "linea": "rgba(143,139,132,0.35)",
    "acento": "#B4552D",
    "verde": "#2F5D3A",
}
PALETA_OSCURA = {
    "fondo": "#141210",
    "panel": "#1E1B18",
    "tinta": "#F5EFE6",
    "linea": "rgba(245,239,230,0.16)",
    "acento": "#CE6A45",
    "verde": "#5C9463",
}


# ==========================================
# DETECCIÓN (funciones puras: se prueban sin Streamlit)
# ==========================================
def _valor(valor):
    """Sin envoltorios: los parámetros de URL pueden llegar como lista (clave repetida)."""
    if isinstance(valor, (list, tuple)):
        return valor[-1] if valor else ""
    return valor


def _booleano(valor):
    """Interpreta 1/0, ?1/?0, sí/no, true/false… Devuelve None cuando el valor no dice nada."""
    if valor is None:
        return None
    texto = str(_valor(valor) or "").strip().lower().lstrip("?")  # los Client Hints llegan como "?1"
    if not texto:
        return None
    if texto in VERDADEROS:
        return True
    if texto in FALSOS:
        return False
    return None


def _entero(valor):
    """Entero de un encabezado o parámetro ('390', '390px'); None si no es un ancho válido."""
    coincidencia = RE_ANCHO.match(str(_valor(valor) or ""))
    return int(coincidencia.group(1)) if coincidencia else None


def ancho_declarado(encabezados):
    """Ancho del viewport declarado por el navegador (Client Hints), o None si no lo dice.

    Se revisan varios encabezados porque no todos los despliegues los mandan: el que
    inyecta el proxy propio (``x-nemet-viewport``) tiene prioridad sobre los estándar.
    """
    for nombre in ENCABEZADOS_ANCHO:
        ancho = _entero((encabezados or {}).get(nombre))
        if ancho:
            return ancho
    return None


def es_movil_por_ancho(ancho, breakpoint=BREAKPOINT_PX):
    """True si el ancho declarado cabe en el umbral. Un ancho inválido nunca es móvil."""
    valor = _entero(ancho)
    return bool(valor) and valor <= breakpoint


def es_movil_por_encabezados(encabezados, breakpoint=BREAKPOINT_PX):
    """True/False si los encabezados alcanzan para decidir; None si no son concluyentes.

    Manda el ancho (es el dato real del dispositivo) y, si no viene, el Client Hint
    ``sec-ch-ua-mobile``.
    """
    encabezados = encabezados or {}
    ancho = ancho_declarado(encabezados)
    if ancho:
        return ancho <= breakpoint
    return _booleano(encabezados.get(ENCABEZADO_CLIENT_HINT_MOVIL))


def es_movil(user_agent, breakpoint=BREAKPOINT_PX):
    """Detección por ``user-agent``: teléfonos y tabletas (iPad, Android sin 'Mobile'…)."""
    agente = str(_valor(user_agent) or "")
    if not agente:
        return False
    return bool(RE_UA_MOVIL.search(agente) or RE_UA_TABLET.search(agente))


# Nombre explícito, por si el llamador quiere dejar claro de qué pista viene la decisión.
es_movil_por_user_agent = es_movil


def interpretar_consulta(consulta):
    """Lee el forzado manual de la URL y devuelve ``(forzado, valor)``.

    ``?movil=1`` → (True, True) · ``?escritorio=1`` → (True, False) · sin parámetro o con
    un valor que no se entiende → (False, None), y entonces decide la detección normal.
    Un parámetro sin valor (``?movil``) cuenta como encendido, igual que una bandera.
    """
    consulta = consulta or {}
    for parametro, es_movil_pedido in ((PARAM_MOVIL, True), (PARAM_ESCRITORIO, False)):
        if parametro not in consulta:
            continue
        bruto = _valor(consulta.get(parametro))
        if bruto is None or not str(bruto).strip():
            return True, es_movil_pedido  # ?movil / ?escritorio sin valor: bandera encendida
        valor = _booleano(bruto)
        if valor is None:
            continue  # valor basura: se ignora y sigue la detección automática
        return True, valor if es_movil_pedido else not valor
    return False, None


def modo_movil(consulta=None, encabezados=None, user_agent=None, ancho=None, breakpoint=BREAKPOINT_PX):
    """Decide si la sesión va en modo móvil, en orden de prioridad:

    1. Forzado manual de la URL (``?movil=`` / ``?escritorio=``).
    2. Ancho declarado (argumento explícito o Client Hints del navegador).
    3. ``user-agent`` (respaldo cuando el navegador no declara el ancho).
    """
    forzado, valor = interpretar_consulta(consulta)
    if forzado:
        return bool(valor)

    if _entero(ancho):  # un ancho inválido se ignora y se sigue con las demás pistas
        return es_movil_por_ancho(ancho, breakpoint)

    pista = es_movil_por_encabezados(encabezados, breakpoint)
    if pista is not None:
        return pista

    agente = user_agent if user_agent is not None else (encabezados or {}).get(ENCABEZADO_UA)
    if agente:
        return es_movil(agente, breakpoint)
    # Sin ninguna pista (por ejemplo una prueba automatizada o un navegador muy viejo):
    # se asume escritorio, que es el diseño completo.
    return False


def preferencia_tema(consulta=None):
    """Tema pedido en la URL: ``claro`` (omisión) u ``oscuro`` (acepta dark/noche)."""
    valor = str(_valor((consulta or {}).get(PARAM_TEMA)) or "").strip().lower()
    if valor in ("oscuro", "dark", "noche"):
        return "oscuro"
    return "claro"


# ==========================================
# APLICACIÓN EN LA SESIÓN DE STREAMLIT (usa st)
# ==========================================
def cabeceras():
    """Encabezados HTTP de la petición (``st.context.headers``), en minúsculas.

    En algunos entornos (pruebas, o servidores que los recortan) no llegan: se devuelve
    un diccionario vacío y la detección cae al ``user-agent`` o al modo de escritorio.
    """
    try:
        return {str(clave).lower(): valor for clave, valor in dict(st.context.headers).items()}
    except Exception:
        return {}


def tema_actual():
    """Tema con el que se está pintando la sesión ('claro' u 'oscuro')."""
    actual = st.session_state.get(CLAVE_TEMA)
    return actual if actual in TEMAS else TEMAS[0]


def aplicar():
    """Resuelve el modo de la sesión, deja el tema en ``st.session_state`` y lo devuelve.

    El forzado manual se recuerda en la sesión: si alguien entra con ``?movil=1`` y sigue
    navegando (las recargas de Streamlit pierden la URL), la vista móvil se mantiene.
    """
    try:
        consulta = dict(st.query_params)
    except Exception:
        consulta = {}

    forzado, valor = interpretar_consulta(consulta)
    if forzado:
        st.session_state[CLAVE_FORZADO] = bool(valor)

    recuerdo = st.session_state.get(CLAVE_FORZADO)
    if forzado:
        modo = bool(valor)
    elif recuerdo is not None:
        modo = bool(recuerdo)
    else:
        encabezados = cabeceras()
        modo = modo_movil(consulta=consulta, encabezados=encabezados,
                          user_agent=encabezados.get(ENCABEZADO_UA))

    st.session_state[CLAVE_TEMA] = preferencia_tema(consulta)
    return modo


def columnas_apiladas(numero, movil, por_fila_movil=1):
    """``st.columns(numero)``, pero en móvil reparte las columnas en filas más angostas.

    Devuelve las referencias en el mismo orden, así que el código de la vista no cambia:
    las columnas que sobran vuelven a caer en las de arriba y quedan debajo (por omisión
    una sola columna por fila). En escritorio equivale a ``st.columns(numero)``.
    """
    if not movil or por_fila_movil >= numero:
        return st.columns(numero)
    por_fila = max(1, int(por_fila_movil))
    return st.columns(por_fila) * math.ceil(numero / por_fila)


def nota_sidebar(activo):
    """Recordatorio discreto en la barra lateral de cómo cambiar de vista."""
    with st.sidebar:
        if activo:
            st.caption(f"📱 Vista móvil · vuelve al diseño completo con `?{PARAM_ESCRITORIO}=1`")
        else:
            st.caption(f"🖥️ Vista de escritorio · pruébala en el teléfono con `?{PARAM_MOVIL}=1`")


# ==========================================
# HOJA DE ESTILOS
# ==========================================
def estilos(oscuro=False, breakpoint=BREAKPOINT_PX, ancho_logo_barra=150):
    """CSS del modo móvil (se inyecta con ``st.markdown`` en cada carga de la app).

    No usa JavaScript: son reglas de hoja de estilos, así que no hay nada que pueda
    romperse si Streamlit cambia su interfaz interna — en el peor caso, el teléfono ve
    el diseño de siempre.
    """
    paleta = PALETA_OSCURA if oscuro else PALETA_CLARA
    nocturno = ""
    if oscuro:
        # El tema se declara aquí dentro (no en config.toml) para que la sesión pueda alternarlo
        # sin reiniciar la app: `?tema=oscuro` para revisar inventario de noche sin encandilarse.
        nocturno = f"""
/* ===== NEMET · tema nocturno de la sesión ===== */
[data-testid="stHeader"] {{ background-color: {paleta['fondo']}; }}
[data-testid="stAppViewContainer"] h1, [data-testid="stAppViewContainer"] h2,
[data-testid="stAppViewContainer"] h3, [data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] li, [data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] span, [data-testid="stSidebar"] * {{ color: {paleta['tinta']}; }}
[data-testid="stSidebar"] {{ background-color: {paleta['panel']}; }}
.stTextInput input, .stNumberInput input, .stTextArea textarea,
[data-baseweb="select"] > div, [data-baseweb="popover"] {{
    background-color: {paleta['panel']} !important; color: {paleta['tinta']} !important;
}}
[data-testid="stBaseButton-secondary"] {{
    background-color: {paleta['panel']}; color: {paleta['tinta']}; border-color: {paleta['linea']};
}}
/* Las tablas de datos son un lienzo aparte (glide-data-grid): se tiñen con sus propias variables */
[data-testid="stDataFrame"] {{
    --gdg-bg-cell: {paleta['panel']}; --gdg-bg-header: {paleta['panel']};
    --gdg-text-dark: {paleta['tinta']}; --gdg-border-color: {paleta['linea']};
}}"""

    return f"""
<style>
/* ===== NEMET · modo móvil =====
   Tema de la sesión: crema de marca por omisión, nocturno con {PARAM_TEMA}=oscuro.
   La hoja es autocontenida: declara sus colores y no depende del tema de .streamlit/config.toml. */
.stApp, [data-testid="stAppViewContainer"] {{ background-color: {paleta['fondo']}; }}
[data-testid="stAppViewContainer"] a {{ color: {paleta['acento']}; }}
/* Área táctil mínima de 44 px y botones a lo ancho: se pulsa con el pulgar, no con el ratón */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    min-height: 44px; width: 100%;
}}
[data-testid="stExpander"] summary, [data-baseweb="select"] > div {{ min-height: 44px; }}
/* Campos a 16 px: con menos, iOS hace zoom al enfocar y descuadra la pantalla */
.stTextInput input, .stNumberInput input, .stTextArea textarea,
.stSelectbox [data-baseweb="select"] div {{ font-size: 16px; }}
/* Menos relleno y títulos contenidos: en un teléfono cada píxel cuenta */
.block-container {{ padding: 1rem 0.9rem 3.5rem 0.9rem; }}
h1 {{ font-size: 1.5rem !important; }} h2 {{ font-size: 1.25rem !important; }}
h3 {{ font-size: 1.08rem !important; }}
/* Métricas como tarjetas: se leen mejor y separan la información */
div[data-testid="stMetric"] {{
    background: {paleta['panel']}; border: 1px solid {paleta['linea']};
    border-radius: 14px; padding: 0.6rem 0.8rem;
}}
div[data-testid="stMetricValue"] {{ font-size: 1.3rem; }}
div[data-testid="stMetricLabel"] {{ font-size: 0.8rem; }}
/* Tablas y editores: nunca desbordan la pantalla */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {{ max-width: 100%; }}
/* Logo de la barra lateral a un tamaño razonable y lema más compacto */
[data-testid="stSidebar"] img {{ max-width: {ancho_logo_barra}px; height: auto; }}
.nemet-lema {{ letter-spacing: 0.22em; font-size: 0.68rem; }}
.nemet-linea {{ border-bottom-color: {paleta['linea']}; }}
@media (max-width: {breakpoint}px) {{
    /* Los bloques de columnas se envuelven en vez de aplastarse (métricas 2x2, botones 2x2) */
    [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap !important; gap: 0.6rem !important; }}
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
        flex: 1 1 8.5rem; min-width: 8.5rem;
    }}
    /* Nada de scroll horizontal accidental */
    html, body, [data-testid="stAppViewContainer"] {{ overflow-x: hidden; }}
}}
{nocturno}
</style>
"""
