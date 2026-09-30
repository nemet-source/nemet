import hashlib
import math
import os
import re
import smtplib
import sqlite3
import threading
import time
import unicodedata
from datetime import date, datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import numpy as np
import pandas as pd
import streamlit as st
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

import auth_nemet as auth  # autenticación, roles y administración de usuarios (SQLite + scrypt)
import prospeccion as pros  # búsqueda pública, clasificación y deduplicación de prospectos

try:
    from PIL import Image as _PILImage
except ImportError:  # la app sigue funcionando sin Pillow; solo pierde el icono de pestaña
    _PILImage = None

# ==========================================
# IDENTIDAD DE MARCA NEMET
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIR_ASSETS = os.path.join(BASE_DIR, "assets")
LEMA_NEMET = "Naturaleza que habita"
PALETA_NEMET = {
    "crema": "#F5EFE6",    # fondo claro (muro encalado)
    "tinta": "#26231F",    # texto principal (carbon)
    "barro": "#B4552D",    # acento primario (terracota)
    "verde": "#2F5D3A",    # acento secundario (epoxi verde)
    "piedra": "#8F8B84",   # gris medio (cemento)
    "carbon": "#141210",   # negro profundo (contraste)
}


def _asset(nombre):
    """Ruta del asset de marca si existe; None en caso contrario."""
    ruta = os.path.join(DIR_ASSETS, nombre)
    return ruta if os.path.exists(ruta) else None


def _imagen_asset(nombre):
    """PIL.Image del asset de marca (o None si no está instalado Pillow o falta el archivo)."""
    ruta = _asset(nombre)
    if ruta is None or _PILImage is None:
        return None
    try:
        return _PILImage.open(ruta)
    except Exception:
        return None


def logo_marca():
    """Logo de la marca: siempre la versión clara (el letterpress oscuro se retiró de la interfaz)."""
    return _imagen_asset("logo_claro.png")


def estilos_marca():
    """Tipografía y detalles visuales de la marca sobre el tema base de Streamlit."""
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] {{ font-family: 'Montserrat', 'Source Sans Pro', sans-serif; }}
    h1, h2, h3 {{ letter-spacing: 0.02em; }}
    .stButton > button, .stDownloadButton > button {{ border-radius: 10px; font-weight: 600; letter-spacing: 0.02em; }}
    .nemet-lema {{ font-weight: 300; letter-spacing: 0.34em; text-transform: uppercase;
                   font-size: 0.72rem; opacity: 0.7; text-align: center; margin-top: -6px; }}
    .nemet-linea {{ border-bottom: 1px solid rgba(143,139,132,0.35); margin: 10px 0 22px 0; }}
    </style>
    """, unsafe_allow_html=True)


def cabecera_marca():
    """Franja de marca al inicio de cada vista (logo claro + lema). Silenciosa si faltan los assets."""
    logo = logo_marca()
    if logo is None:
        return
    col_izq, col_centro, col_der = st.columns([1, 2, 1])
    with col_centro:
        st.image(logo, width=250)
        st.markdown(f"<div class='nemet-lema'>{LEMA_NEMET}</div>", unsafe_allow_html=True)
        st.markdown("<div class='nemet-linea'></div>", unsafe_allow_html=True)


def portada_marca():
    """Portada cinematográfica del dashboard: el hero claro cubre todo el ancho.

    Devuelve True si se mostró (el hero ya lleva logo + lema, así que la vista omite
    la franja compacta para no duplicar la marca).
    """
    hero = _imagen_asset("hero_claro.png")
    if hero is None:
        return False
    with st.container(border=True):
        st.image(hero, width="stretch")
    return True


# Icono de pestaña: favicon de la marca si existe; emoji de respaldo.
ICONO_PAGINA = _imagen_asset("favicon.png") or "🧪"

# Configuración de la página (debe ser el primer comando de Streamlit)
st.set_page_config(
    page_title="Sistema Maestro NEMET",
    page_icon=ICONO_PAGINA,
    layout="wide"
)

# ==========================================
# CONSTANTES
# ==========================================
EXCEL_FILE = os.path.join(BASE_DIR, "Sistema_Inventario_NEMET_Final.xlsx")

HOJA_INVENTARIO = "Inventario"
HOJA_CLIENTES = "Clientes"
HOJA_PROSPECTOS = "Prospectos"
HOJA_HISTORIAL = "Historial_Cotizaciones"
HOJA_CATALOGO = "Cat_Productos"

TITULO_INVENTARIO = "CONTROL DE INVENTARIO Y FACTURACIÓN - NEMET"
TASA_IVA = 0.16
RENDIMIENTO_DEFAULT = 1.2  # kg por m² por mm, si el producto no está en Cat_Productos

# Sonora (MX) usa UTC-7 todo el año; el servidor de Streamlit Community Cloud vive en UTC.
try:
    ZONA_HORARIA = ZoneInfo("America/Hermosillo")
except Exception:
    ZONA_HORARIA = None

# Serializa todas las escrituras al Excel: en Streamlit cada sesión es un hilo del mismo proceso.
LOCK_EXCEL = threading.RLock()
RESPALDO_INTERVALO_SEG = 60  # anti-spam del respaldo automático por sesión

# Base de usuarios: SQLite, respaldada en GitHub junto con el Excel.
# Solo guarda hashes scrypt (nunca contraseñas en texto plano).
NOMBRE_DB_USUARIOS = "nemet_usuarios.db"
CLAVE_SESION_URL = "sesion"          # parámetro de URL con el token firmado de la sesión
REFRESCO_TOKEN_SEG = 300             # cada cuánto se renueva el token (ventana deslizante)


class ConflictoGuardado(Exception):
    """Otro usuario guardó el Excel después de que esta sesión lo cargó."""

COLUMNAS_CARRITO = ["SKU", "Descripcion", "Presentacion", "Cantidad", "Subtotal"]
COLUMNAS_CARRITO_AREA = ["SKU", "Descripcion", "Presentacion", "Area_m2", "Espesor_mm", "Kg_Necesarios", "Cantidad", "Subtotal", "Detalle"]
COLUMNAS_CLIENTES = ["Nombre", "Empresa", "Correo", "Teléfono", "Dirección"]
COLUMNAS_HISTORIAL = ["Folio", "Fecha", "Cliente", "Detalle_Productos", "Total"]

# Columnas que se capturan a mano vs. las que se calculan automáticamente al guardar
COLUMNAS_NUMERICAS_INV = ["StockMinimo", "StockInicial", "Entradas", "Salidas", "StockActual",
                          "PrecioCompra", "PrecioBaseSinIVA", "IVA 16%", "PrecioPublicoIVA", "ValorInventario"]
COLUMNAS_DERIVADAS_INV = ["StockActual", "AlertaStock", "PrecioBaseSinIVA", "IVA 16%", "ValorInventario"]

OPCION_CLIENTE_GENERAL = "Otro / Cliente General"
_RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ==========================================
# UTILIDADES GENERALES
# ==========================================
def obtener_secret(seccion, clave):
    """Lee st.secrets[seccion][clave] sin reventar cuando no existe secrets.toml."""
    try:
        return st.secrets[seccion][clave]
    except Exception:
        return None


def variable_entorno(nombre):
    """Variable de entorno no vacía (alternativa a los Secrets en VPS/contenedores)."""
    valor = os.environ.get(nombre, "")
    return valor.strip() or None


def ruta_db_usuarios():
    """Ruta del archivo SQLite de usuarios: `[auth] db`, la variable NEMET_DB_USUARIOS o la carpeta de la app."""
    configurada = obtener_secret("auth", "db") or variable_entorno("NEMET_DB_USUARIOS")
    if configurada:
        configurada = str(configurada)
        return configurada if os.path.isabs(configurada) else os.path.join(BASE_DIR, configurada)
    return os.path.join(BASE_DIR, NOMBRE_DB_USUARIOS)


def flash(mensaje, tipo="success"):
    """Guarda mensajes para mostrarlos después de un st.rerun()."""
    st.session_state.setdefault("_flash_lista", []).append((tipo, mensaje))


def mostrar_flash():
    for tipo, mensaje in st.session_state.pop("_flash_lista", []):
        getattr(st, tipo, st.info)(mensaje)


def ahora_local():
    """datetime.now() en hora de Sonora (el servidor de Streamlit Cloud está en UTC)."""
    return datetime.now(ZONA_HORARIA) if ZONA_HORARIA else datetime.now()


def _hash_hoja(nombre_hoja):
    """Huella del contenido actual de una hoja, para detectar cambios hechos por otras sesiones."""
    try:
        with LOCK_EXCEL:
            df = pd.read_excel(EXCEL_FILE, sheet_name=nombre_hoja)
        return hashlib.md5(df.to_csv(index=False).encode("utf-8")).hexdigest()
    except Exception:
        return None


def normalizar_texto(texto):
    """Mayúsculas, sin acentos y sin espacios repetidos, para comparar nombres de productos."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().upper()


_TRANSLITERACION_PDF = str.maketrans({"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "•": "-", "→": "->"})


def texto_pdf(valor):
    """Las fuentes base de FPDF solo soportan latin-1: translitera lo común y sustituye lo no representable."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return ""
    return str(valor).translate(_TRANSLITERACION_PDF).encode("latin-1", "replace").decode("latin-1")


def valor_celda(fila, columna, default=""):
    valor = fila.get(columna, default)
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return default
    return valor


def formato_cantidad(cantidad):
    try:
        cantidad = float(cantidad)
    except (TypeError, ValueError):
        return str(cantidad)
    return str(int(cantidad)) if cantidad.is_integer() else f"{cantidad:g}"


def limpiar_precio(val):
    if pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).replace("$", "").replace(",", "").strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0


def carrito_vacio(por_area=False):
    return pd.DataFrame(columns=COLUMNAS_CARRITO_AREA if por_area else COLUMNAS_CARRITO)


def agregar_al_carrito(clave, item):
    """Agrega un renglón al carrito guardado en session_state (evita concat con DataFrames vacíos)."""
    nuevo = pd.DataFrame([item])
    actual = st.session_state[clave]
    st.session_state[clave] = nuevo if actual.empty else pd.concat([actual, nuevo], ignore_index=True)


# ==========================================
# RESPALDO EN GITHUB
# ==========================================
RUTA_DB_USUARIOS = ruta_db_usuarios()


def _url_github(token, repo_name):
    return f"https://{token}@github.com/{repo_name}.git"


def _respaldar_core(mensaje, rutas_extra=None):
    """Commit + fetch/rebase + push de los datos (Excel + base de usuarios).

    Devuelve (ok, tipo_ui, mensaje). Siempre incluye la base de usuarios: así el
    respaldo la publica y, sobre todo, el árbol de trabajo queda limpio para que
    el rebase no falle por cambios sin confirmar.
    """
    token = obtener_secret("git", "token")
    repo_name = obtener_secret("git", "repo")
    if not token or not repo_name:
        return False, "warning", "Respaldo en GitHub no configurado: agrega `[git]` con `token` y `repo` en los Secrets de Streamlit."
    privado, motivo = pros.respaldo_github_privado(repo_name, token)
    if not privado:
        return False, "warning", motivo
    try:
        import git  # Import perezoso: GitPython falla al importarse si no existe el binario git

        repo = git.Repo(BASE_DIR, search_parent_directories=True)
        with repo.config_writer() as git_config:
            git_config.set_value("user", "name", "NEMET Bot")
            git_config.set_value("user", "email", "bot@nemet.app")

        # Se respalda la base que la app está usando de verdad (si el archivo del
        # repositorio no admitía escritura, la copia escribible vive fuera de él y el
        # filtro de rutas de abajo la descarta sin romper nada).
        candidatas = [EXCEL_FILE, auth.ruta_efectiva() or RUTA_DB_USUARIOS]
        if isinstance(rutas_extra, str):
            rutas_extra = [rutas_extra]
        candidatas.extend(rutas_extra or [])
        rutas_datos = []
        for ruta in candidatas:
            if not ruta or not os.path.exists(ruta):
                continue
            relativa = os.path.relpath(ruta, repo.working_tree_dir)
            if not relativa.startswith("..") and relativa not in rutas_datos:
                rutas_datos.append(relativa)

        repo.index.add(rutas_datos)
        hay_cambios = bool(repo.index.diff("HEAD", paths=rutas_datos))
        if hay_cambios:
            repo.index.commit(mensaje)
        try:
            rama = repo.active_branch.name
        except TypeError:  # HEAD desprendido (detached)
            rama = "main"

        url = _url_github(token, repo_name)
        # Traer primero lo remoto y rebasar el commit local encima: sin esto, un push
        # tras otro respaldo (o un merge en main) moría con "non-fast-forward".
        # El Excel es binario: si remoto y local lo tocaron, gana la versión local
        # (la recién guardada por el usuario) con -X theirs.
        try:
            repo.git.fetch(url, rama)
            repo.git.rebase("-X", "theirs", "FETCH_HEAD")
        except Exception:
            try:
                repo.git.rebase("--abort")
            except Exception:
                pass
            return False, "error", ("El repositorio cambió y el Excel local entra en conflicto con lo remoto. "
                                    "Intenta el respaldo de nuevo; si persiste, revisa el historial en GitHub.")
        # El token solo se usa en estas llamadas; no se guarda en .git/config.
        # Se empuja siempre para subir también commits previos que no se hayan podido enviar.
        repo.git.push(url, f"HEAD:{rama}")
        if hay_cambios:
            return True, "success", f"¡Datos guardados y respaldados en GitHub (rama `{rama}`) correctamente!"
        return True, "info", f"El Excel no tenía cambios nuevos; el repositorio (rama `{rama}`) ya está al día."
    except Exception as e:
        return False, "error", f"Error al sincronizar con GitHub: {str(e).replace(token, '***')}"


def guardar_cambios_github(mensaje="Actualización automática de datos", rutas_extra=None):
    """Versión para botón: ejecuta el respaldo y muestra el resultado en pantalla."""
    ok, tipo, texto = _respaldar_core(mensaje, rutas_extra=rutas_extra)
    getattr(st, tipo, st.info)(texto)
    return ok


def _respaldo_automatico(mensaje, rutas_extra=None, intervalo=True):
    """Tras un guardado exitoso intenta subir los datos a GitHub (si los Secrets [git] existen).
    Devuelve (tipo_ui, mensaje) para mostrar, o None si no aplica o se omitió por el intervalo."""
    token = obtener_secret("git", "token")
    repo_name = obtener_secret("git", "repo")
    if not token or not repo_name:
        return None
    if intervalo and time.time() - st.session_state.get("_ultimo_respaldo", 0) < RESPALDO_INTERVALO_SEG:
        return None
    st.session_state["_ultimo_respaldo"] = time.time()
    ok, tipo, texto = _respaldar_core(mensaje, rutas_extra=rutas_extra)
    return (tipo, texto) if ok else ("warning", f"Respaldo automático falló: {texto}")


# ==========================================
# FUNCIONES DE CARGA Y LIMPIEZA DE DATOS
# ==========================================
def detectar_fila_encabezado(nombre_hoja):
    """Busca en las primeras filas la que contiene el encabezado 'SKU' (el Excel trae un título en la fila 1)."""
    try:
        muestra = pd.read_excel(EXCEL_FILE, sheet_name=nombre_hoja, header=None, nrows=5)
    except Exception:
        return 1
    for i, fila in muestra.iterrows():
        celdas = [str(c).strip().lower() for c in fila.tolist() if pd.notna(c)]
        if any(c == "sku" or "codigo" in c or "código" in c for c in celdas):
            return int(i)
    return 1


def quitar_columnas_duplicadas(df):
    """pandas renombra encabezados repetidos como 'Columna.1'; se eliminan si son copia exacta de la original."""
    for col in list(df.columns):
        coincidencia = re.match(r"^(.+)\.\d+$", col)
        if coincidencia and coincidencia.group(1) in df.columns and df[col].equals(df[coincidencia.group(1)]):
            df = df.drop(columns=[col])
    return df


def cargar_inventario():
    """Carga la hoja Inventario detectando la fila de encabezados y normalizando nombres de columnas."""
    if not os.path.exists(EXCEL_FILE):
        st.error(f"No se encontró el archivo {os.path.basename(EXCEL_FILE)} en la carpeta.")
        return pd.DataFrame()
    try:
        try:
            df = pd.read_excel(EXCEL_FILE, sheet_name=HOJA_INVENTARIO, header=detectar_fila_encabezado(HOJA_INVENTARIO))
        except ValueError:  # la hoja no existe: se usa la primera
            df = pd.read_excel(EXCEL_FILE, header=1)

        df.columns = [str(c).strip() for c in df.columns]
        df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
        df = quitar_columnas_duplicadas(df)
        df = df.dropna(how="all")

        renombres = {}
        for col in df.columns:
            cl = col.lower()
            if cl == "sku" or "codigo" in cl or "código" in cl:
                renombres[col] = "SKU"
            elif "descripcion" in cl or "descripción" in cl:
                renombres[col] = "Descripcion"
            elif "presentacion" in cl or "presentación" in cl or "kg / l" in cl:
                renombres[col] = "Presentacion"
            elif "precio" in cl and ("venta" in cl or "publico" in cl or "público" in cl):
                renombres[col] = "PrecioPublicoIVA"
            elif "stock actual" in cl:
                renombres[col] = "StockActual"
        df = df.rename(columns=renombres)

        if "PrecioPublicoIVA" not in df.columns:
            candidatas = [c for c in df.columns if "precio" in c.lower()]
            df["PrecioPublicoIVA"] = df[candidatas[0]] if candidatas else 0.0
        df["PrecioPublicoIVA"] = df["PrecioPublicoIVA"].apply(limpiar_precio)

        for col in COLUMNAS_NUMERICAS_INV:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        if "SKU" not in df.columns:
            df.insert(0, "SKU", df.iloc[:, 0])
        if "Descripcion" not in df.columns:
            df["Descripcion"] = df.iloc[:, 1] if len(df.columns) > 1 else ""
        if "Presentacion" not in df.columns:
            df["Presentacion"] = df.iloc[:, 2] if len(df.columns) > 2 else ""
        for col in ("SKU", "Descripcion", "Presentacion"):
            df[col] = df[col].fillna("").astype(str).str.strip()

        st.session_state["inv_base_hash"] = _hash_hoja(HOJA_INVENTARIO)
        return df.reset_index(drop=True)
    except Exception as e:
        st.error(f"Error al cargar el archivo Excel: {e}")
        return pd.DataFrame()


def cargar_clientes():
    """Carga la hoja Clientes garantizando que existan las columnas esperadas."""
    df_cli = pd.DataFrame(columns=COLUMNAS_CLIENTES)
    try:
        if os.path.exists(EXCEL_FILE):
            df_cli = pd.read_excel(EXCEL_FILE, sheet_name=HOJA_CLIENTES).dropna(how="all")
    except Exception:
        pass
    for col in COLUMNAS_CLIENTES:
        if col not in df_cli.columns:
            df_cli[col] = ""
    st.session_state["cli_base_hash"] = _hash_hoja(HOJA_CLIENTES)
    return df_cli.reset_index(drop=True)


def cargar_historial():
    try:
        df_hist = pd.read_excel(EXCEL_FILE, sheet_name=HOJA_HISTORIAL)
    except Exception:
        return pd.DataFrame(columns=COLUMNAS_HISTORIAL)
    for col in COLUMNAS_HISTORIAL:
        if col not in df_hist.columns:
            df_hist[col] = ""
    return df_hist


def cargar_catalogo_rendimientos():
    """Lee Cat_Productos -> {producto_normalizado: {rendimiento, prop_a, prop_b}}."""
    try:
        df_cat = pd.read_excel(EXCEL_FILE, sheet_name=HOJA_CATALOGO)
    except Exception:
        return {}
    df_cat.columns = [str(c).strip() for c in df_cat.columns]
    if "Producto" not in df_cat.columns or "Rendimiento" not in df_cat.columns:
        return {}
    catalogo = {}
    for _, fila in df_cat.iterrows():
        nombre = normalizar_texto(valor_celda(fila, "Producto"))
        rendimiento = pd.to_numeric(fila.get("Rendimiento"), errors="coerce")
        if not nombre or pd.isna(rendimiento) or rendimiento <= 0 or nombre in catalogo:
            continue
        prop_a = pd.to_numeric(fila.get("Prop_A"), errors="coerce")
        prop_b = pd.to_numeric(fila.get("Prop_B"), errors="coerce")
        catalogo[nombre] = {
            "rendimiento": float(rendimiento),
            "prop_a": float(prop_a) if pd.notna(prop_a) and prop_a > 0 else 100.0,
            "prop_b": float(prop_b) if pd.notna(prop_b) and prop_b > 0 else 0.0,
        }
    return catalogo


def buscar_en_catalogo(descripcion, catalogo):
    """Busca el producto por nombre exacto (sin acentos/mayúsculas) o por prefijo (p. ej. 'EPOXY TINTA ...')."""
    clave = normalizar_texto(descripcion)
    if not clave:
        return None
    if clave in catalogo:
        return catalogo[clave]
    candidatos = [k for k in catalogo if clave.startswith(k) or k.startswith(clave)]
    return catalogo[max(candidatos, key=len)] if candidatos else None


_RE_PRESENTACION = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?|\d+\s*/\s*\d+)\s*(kg|kgs|kilo|kilos|g|gr|grs|gramos|l|lt|lts|litro|litros|ml)\.?\s*$",
    re.IGNORECASE,
)
_FACTOR_A_KG = {"kg": 1, "kgs": 1, "kilo": 1, "kilos": 1, "g": 0.001, "gr": 0.001, "grs": 0.001, "gramos": 0.001,
                "l": 1, "lt": 1, "lts": 1, "litro": 1, "litros": 1, "ml": 0.001}


def parsear_presentacion_kg(presentacion):
    """'1.420 kg' -> 1.42 | '500 g' -> 0.5 | '1/2 kg' -> 0.5 | '1 L' -> 1.0 (1 L ~ 1 kg).
    Piezas ('1 Pz') y rangos de precio ('10 a 20 kg', '< 10 kg') no son presentaciones cotizables -> None."""
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


def columnas_inventario(df):
    col_desc = "Descripcion" if "Descripcion" in df.columns else df.columns[1]
    col_sku = "SKU" if "SKU" in df.columns else df.columns[0]
    col_pres = "Presentacion" if "Presentacion" in df.columns else df.columns[2]
    return col_desc, col_sku, col_pres


# ==========================================
# GUARDADO EN EXCEL
# ==========================================
def escribir_hoja(df, nombre_hoja, startrow=0, titulo=None):
    """Reemplaza una hoja del Excel conservando las demás. Serializado entre hilos/sesiones."""
    with LOCK_EXCEL:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name=nombre_hoja, index=False, startrow=startrow)
            if titulo:
                writer.sheets[nombre_hoja].cell(row=1, column=1, value=titulo)


def recalcular_columnas_derivadas(df):
    """StockActual, AlertaStock, PrecioBaseSinIVA, IVA y ValorInventario se derivan de las columnas capturadas."""
    df = df.copy()
    for col in COLUMNAS_NUMERICAS_INV:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if {"StockInicial", "Entradas", "Salidas"} <= set(df.columns):
        df["StockActual"] = df["StockInicial"] + df["Entradas"] - df["Salidas"]
    if "StockActual" in df.columns:
        minimo = df["StockMinimo"] if "StockMinimo" in df.columns else 0
        df["AlertaStock"] = np.where(df["StockActual"] <= minimo, "⚠️ REABASTECER", "✅ OK")
    if "PrecioPublicoIVA" in df.columns:
        df["PrecioBaseSinIVA"] = df["PrecioPublicoIVA"] / (1 + TASA_IVA)
        df["IVA 16%"] = df["PrecioPublicoIVA"] - df["PrecioBaseSinIVA"]
        if "StockActual" in df.columns:
            df["ValorInventario"] = df["StockActual"] * df["PrecioPublicoIVA"]
    return df


def quitar_filas_vacias(df, columnas_clave):
    columnas = [c for c in columnas_clave if c in df.columns]
    if not columnas:
        return df
    vacias = np.ones(len(df), dtype=bool)
    for col in columnas:
        vacias &= df[col].fillna("").astype(str).str.strip().eq("").to_numpy()
    return df[~vacias]


def _verificar_sin_conflicto(nombre_hoja, clave_hash):
    """Lanza ConflictoGuardado si la hoja cambió en disco desde que esta sesión la cargó."""
    base = st.session_state.get(clave_hash)
    actual = _hash_hoja(nombre_hoja)
    if base and actual and actual != base:
        raise ConflictoGuardado(
            f"La hoja '{nombre_hoja}' cambió en el archivo (probablemente otro usuario guardó cambios). "
            "Tus capturas siguen en pantalla: sincroniza para revisarlas, o sobrescribe si lo prefieres.")


def guardar_inventario(df_editado, forzar=False):
    with LOCK_EXCEL:
        if not forzar:
            _verificar_sin_conflicto(HOJA_INVENTARIO, "inv_base_hash")
        df_final = quitar_filas_vacias(df_editado, ["SKU", "Descripcion"])
        df_final = recalcular_columnas_derivadas(df_final)
        if "SKU" in df_final.columns:
            df_final["SKU"] = df_final["SKU"].fillna("").astype(str).str.strip()
        escribir_hoja(df_final, HOJA_INVENTARIO, startrow=1, titulo=TITULO_INVENTARIO)
        st.session_state["inv_base_hash"] = _hash_hoja(HOJA_INVENTARIO)


def guardar_clientes(df_editado, forzar=False):
    with LOCK_EXCEL:
        if not forzar:
            _verificar_sin_conflicto(HOJA_CLIENTES, "cli_base_hash")
        escribir_hoja(quitar_filas_vacias(df_editado, ["Nombre", "Empresa", "Correo"]), HOJA_CLIENTES)
        st.session_state["cli_base_hash"] = _hash_hoja(HOJA_CLIENTES)


# ==========================================
# PROSPECCIÓN: HOJA SEPARADA DE CLIENTES
# ==========================================
def cargar_prospectos():
    """La hoja se crea al primer guardado; no se confunden oportunidades con clientes."""
    with LOCK_EXCEL:
        with pd.ExcelFile(EXCEL_FILE) as libro:
            if HOJA_PROSPECTOS not in libro.sheet_names:
                return pd.DataFrame(columns=pros.COLUMNAS)
            df = pd.read_excel(libro, sheet_name=HOJA_PROSPECTOS, dtype=str, keep_default_na=False)
    for columna in pros.COLUMNAS:
        if columna not in df.columns:
            df[columna] = ""
    return df[list(pros.COLUMNAS)].reset_index(drop=True)


def _clientes_para_prospeccion():
    """Lee los clientes *actuales* del disco antes de insertar, nunca la copia de la sesión."""
    with LOCK_EXCEL:
        with pd.ExcelFile(EXCEL_FILE) as libro:
            if HOJA_CLIENTES not in libro.sheet_names:
                return []
            clientes = pd.read_excel(libro, sheet_name=HOJA_CLIENTES, dtype=str, keep_default_na=False)
    return clientes.to_dict("records")


def nuevos_prospectos(candidatos):
    """Previsualización deduplicada contra prospectos y clientes existentes."""
    with LOCK_EXCEL:
        return pros.filtrar_nuevos(candidatos, cargar_prospectos().to_dict("records"),
                                  _clientes_para_prospeccion())


def guardar_prospectos(candidatos):
    """Relee y agrega solo nuevos bajo cerrojo; nunca sobreescribe notas de otra sesión."""
    with LOCK_EXCEL:
        guardados = cargar_prospectos()
        nuevos, repetidos, clientes = pros.filtrar_nuevos(
            candidatos, guardados.to_dict("records"), _clientes_para_prospeccion())
        if nuevos:
            filas = []
            for candidato in nuevos:
                fila = {col: pros.proteger_excel(candidato.get(col, "")) for col in pros.COLUMNAS}
                fila["Puntaje"] = int(candidato["Puntaje"])
                fila["Fecha_alta"] = ahora_local().date().isoformat()
                fila["Estado"] = candidato["Estado"] if candidato["Estado"] in pros.ESTADOS else "Nuevo"
                filas.append(fila)
            escribir_hoja(pd.concat([guardados, pd.DataFrame(filas)], ignore_index=True), HOJA_PROSPECTOS)
        return len(nuevos), repetidos, clientes


def guardar_seguimiento_prospecto(clave, esperado, cambios):
    """Actualiza una ficha y recalcula su prioridad sin sobrescribir ediciones ajenas."""
    if cambios["Estado"] not in pros.ESTADOS:
        raise ValueError("Estado de prospecto no válido.")
    if len(cambios["Notas"]) > 500:
        raise ValueError("Las notas no pueden superar 500 caracteres.")
    for campo in ("Último_contacto", "Próximo_seguimiento", "Última_actividad"):
        if cambios[campo]:
            fecha_capturada = date.fromisoformat(cambios[campo])
            if campo != "Próximo_seguimiento" and fecha_capturada > ahora_local().date():
                raise ValueError(f"{campo} no puede ser una fecha futura.")
    if cambios["Tamaño"] and cambios["Tamaño"] not in pros.TAMANOS[1:]:
        raise ValueError("Tamaño no válido.")
    if cambios["Tipo_clientela"] and cambios["Tipo_clientela"] not in pros.CLIENTELAS[1:]:
        raise ValueError("Tipo de clientela no válido.")
    with LOCK_EXCEL:
        guardados = cargar_prospectos()
        indices = guardados.index[guardados["Clave"] == clave].tolist()
        if len(indices) != 1:
            raise ConflictoGuardado("El prospecto ya no está en la lista. Recarga la página.")
        indice = indices[0]
        actual = tuple(str(guardados.at[indice, col]) for col in pros.CAMPOS_SEGUIMIENTO)
        if actual != esperado:
            raise ConflictoGuardado("Otra sesión actualizó este prospecto. Recarga antes de editarlo de nuevo.")
        valores = {col: pros.proteger_excel(cambios[col]) for col in pros.CAMPOS_SEGUIMIENTO}
        valores["Notas"] = pros.proteger_excel(pros.texto(cambios["Notas"], 500))
        valores["Productos_negocio"] = pros.proteger_excel(pros.texto(cambios["Productos_negocio"], 250))
        valores["Fuente_perfil"] = pros.proteger_excel(pros.texto(cambios["Fuente_perfil"], 250))
        if any(valores[c] != guardados.at[indice, c] for c in
               ("Productos_negocio", "Tamaño", "Tipo_clientela", "Última_actividad")) and not valores["Fuente_perfil"]:
            raise ValueError("Para modificar el perfil comercial indica una fuente pública o nota de verificación.")
        if all(valores[c] == guardados.at[indice, c] for c in pros.CAMPOS_SEGUIMIENTO):
            return False
        ficha = guardados.loc[indice].to_dict()
        ficha.update(valores)
        ficha = pros.recalcular_ficha(ficha, hoy=ahora_local().date())
        for campo in (*pros.CAMPOS_SEGUIMIENTO, "Puntaje", "Prioridad", "Motivo"):
            guardados.at[indice, campo] = str(ficha[campo]) if campo == "Puntaje" else ficha[campo]
        escribir_hoja(guardados, HOJA_PROSPECTOS)
        return True


def csv_prospectos(df):
    """Exportación UTF-8 con BOM, enlaces a Maps calculados y sin fórmulas de CSV."""
    exportar = df.copy()
    if {"Dirección", "Ciudad", "Latitud", "Longitud"} <= set(exportar.columns):
        exportar["Google_Maps"] = [pros.enlace_google_maps(fila) for fila in exportar.to_dict("records")]
    for columna in exportar.columns:
        exportar[columna] = exportar[columna].map(pros.proteger_csv)
    return exportar.to_csv(index=False).encode("utf-8-sig")


# ==========================================
# FOLIOS, HISTORIAL, PDF Y CORREO
# ==========================================
def obtener_siguiente_folio():
    """Siguiente consecutivo del año en curso (COT-AAAA-NNN) según el mayor folio ya registrado."""
    anio_actual = ahora_local().year
    patron = re.compile(rf"^COT-{anio_actual}-(\d+)$")
    ultimo = 0
    for folio in cargar_historial()["Folio"].dropna().astype(str):
        coincidencia = patron.match(folio.strip())
        if coincidencia:
            ultimo = max(ultimo, int(coincidencia.group(1)))
    return f"COT-{anio_actual}-{ultimo + 1:03d}"


def describir_item(fila):
    texto = f"{formato_cantidad(valor_celda(fila, 'Cantidad', 1))}x {valor_celda(fila, 'Descripcion')} ({valor_celda(fila, 'Presentacion')})"
    area = valor_celda(fila, "Area_m2", None)
    espesor = valor_celda(fila, "Espesor_mm", None)
    if area is not None and espesor is not None:
        texto += f" [{float(area):.2f} m² x {float(espesor):g} mm]"
    return texto


def registrar_cotizacion_en_excel(folio, cliente, items_carrito, total_general):
    """Agrega la cotización al historial de forma atómica: relee el archivo bajo cerrojo.
    Si otra sesión ya tomó el folio con otro contenido, asigna el siguiente libre.
    Devuelve (ok, mensaje_error, folio_final)."""
    try:
        detalle = ", ".join(describir_item(fila) for _, fila in items_carrito.iterrows())
        with LOCK_EXCEL:
            df_hist = cargar_historial()
            folio_final = folio
            if not df_hist.empty:
                chocan = df_hist[df_hist["Folio"].astype(str).str.strip() == folio]
                if not chocan.empty:
                    mismo_contenido = chocan.apply(
                        lambda f: str(valor_celda(f, "Cliente")).strip() == cliente.strip()
                        and str(valor_celda(f, "Detalle_Productos")) == detalle, axis=1)
                    if mismo_contenido.any():
                        return True, "", folio  # ya está registrada; no duplicar
                    folio_final = obtener_siguiente_folio()  # otra sesión lo tomó primero
            nueva_fila = pd.DataFrame({
                "Folio": [folio_final],
                "Fecha": [ahora_local().strftime("%Y-%m-%d %H:%M:%S")],
                "Cliente": [cliente],
                "Detalle_Productos": [detalle],
                "Total": [float(total_general)],
            })
            df_hist = nueva_fila if df_hist.empty else pd.concat([df_hist, nueva_fila], ignore_index=True)
            escribir_hoja(df_hist, HOJA_HISTORIAL)
        return True, "", folio_final
    except Exception as e:
        return False, str(e), folio


def generar_pdf_cotizacion(folio, cliente, items, titulo_detalle):
    """Genera el PDF de la cotización y devuelve sus bytes."""
    total = float(items["Subtotal"].sum()) if not items.empty else 0.0
    base = total / (1 + TASA_IVA)
    iva = total - base

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("helvetica", "B", 15)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 10, "SISTEMA MAESTRO NEMET", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, texto_pdf("Productos Químicos y Especialidades Epóxicas"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    pdf.set_font("helvetica", "", 11)
    pdf.set_text_color(0, 0, 0)
    for linea in (f"Folio: {folio}", f"Cliente: {cliente}", f"Fecha: {ahora_local():%Y-%m-%d}", titulo_detalle):
        pdf.cell(0, 6, texto_pdf(linea), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(50, 50, 50)
    estilo_encabezado = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=(30, 58, 138))
    with pdf.table(col_widths=(25, 75, 30, 20, 40), text_align=("CENTER", "LEFT", "CENTER", "CENTER", "RIGHT"),
                   headings_style=estilo_encabezado, line_height=5, padding=1.5) as tabla:
        encabezado = tabla.row()
        for titulo in ("SKU", "Descripcion / Sistema", "Presentacion", "Cantidad", "Subtotal"):
            encabezado.cell(titulo)
        for _, fila in items.iterrows():
            renglon = tabla.row()
            renglon.cell(texto_pdf(valor_celda(fila, "SKU")))
            renglon.cell(texto_pdf(valor_celda(fila, "Detalle", None) or valor_celda(fila, "Descripcion")))
            renglon.cell(texto_pdf(valor_celda(fila, "Presentacion")))
            renglon.cell(formato_cantidad(valor_celda(fila, "Cantidad", 1)))
            renglon.cell(f"${float(valor_celda(fila, 'Subtotal', 0)):,.2f}")

    pdf.ln(5)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 6, f"Subtotal (sin IVA): ${base:,.2f} MXN", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f"IVA (16%): ${iva:,.2f} MXN", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f"Total General: ${total:,.2f} MXN", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def enviar_cotizacion_por_correo(destinatario, cliente, folio, total, pdf_bytes):
    """Envía el PDF por Gmail. Requiere secrets [email] remitente y password (contraseña de aplicación)."""
    remitente = obtener_secret("email", "remitente")
    password = obtener_secret("email", "password")
    if not remitente or not password:
        raise RuntimeError("No están configurados los Secrets `[email]` (remitente y password).")
    if not _RE_CORREO.match(destinatario or ""):
        raise ValueError(f"El correo del cliente no es válido: '{destinatario}'.")

    msg = MIMEMultipart()
    msg["From"] = remitente
    msg["To"] = destinatario
    msg["Subject"] = f"Cotización Oficial NEMET - {folio}"
    cuerpo = (f"Hola {cliente},\n\nAdjunto encontrarás la cotización oficial {folio}.\n\n"
              f"Total: ${total:,.2f} MXN\n\nSaludos cordiales,\nEquipo NEMET")
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
    adjunto = MIMEApplication(pdf_bytes, Name=f"{folio}.pdf")
    adjunto["Content-Disposition"] = f'attachment; filename="{folio}.pdf"'
    msg.attach(adjunto)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(remitente, password)
        server.send_message(msg)


# ==========================================
# COMPONENTES DE INTERFAZ COMPARTIDOS
# ==========================================
def seleccionar_cliente(clave):
    """Selector de cliente registrado (o captura libre). Devuelve (nombre, correo)."""
    df_cli = st.session_state["clientes"]
    nombres = []
    if not df_cli.empty and "Nombre" in df_cli.columns:
        nombres = sorted({str(n).strip() for n in df_cli["Nombre"].dropna() if str(n).strip()})
    seleccion = st.selectbox("Seleccionar Cliente Registrado", [OPCION_CLIENTE_GENERAL] + nombres, key=f"{clave}_cliente_sel")

    if seleccion != OPCION_CLIENTE_GENERAL:
        fila = df_cli[df_cli["Nombre"].astype(str).str.strip() == seleccion].iloc[0]
        correo_bd = str(valor_celda(fila, "Correo")).strip()
        nombre = seleccion
        # La clave incluye el cliente para que el valor precargado cambie al cambiar de cliente
        correo = st.text_input("Correo Electrónico", value=correo_bd, key=f"{clave}_correo_{seleccion}")
    else:
        nombre = st.text_input("Nombre del Cliente", "Cliente General", key=f"{clave}_cliente_txt")
        correo = st.text_input("Correo Electrónico", "", key=f"{clave}_correo_txt")
    return (nombre.strip() or "Cliente General"), correo.strip()


def firma_carrito(items, cliente):
    if items.empty:
        return ""
    return f"{cliente}|{int(pd.util.hash_pandas_object(items.astype(str), index=False).sum())}"


def folio_para_carrito(clave, items, cliente):
    """Si este mismo carrito ya se registró, reutiliza su folio (evita duplicar folios al re-descargar)."""
    emitido = st.session_state.get(f"{clave}_emitido")
    firma = firma_carrito(items, cliente)
    if emitido and firma and emitido["firma"] == firma:
        return emitido["folio"], True
    return obtener_siguiente_folio(), False


def registrar_si_es_necesario(clave, folio, cliente, items, total):
    """Registra el folio en el historial una sola vez por carrito y guarda un mensaje para la interfaz."""
    if st.session_state.get(f"{clave}_emitido", {}).get("folio") == folio:
        return True
    ok, error, folio_final = registrar_cotizacion_en_excel(folio, cliente, items, total)
    if ok:
        st.session_state[f"{clave}_emitido"] = {"firma": firma_carrito(items, cliente), "folio": folio_final}
        if folio_final != folio:
            st.session_state[f"{clave}_msg"] = (
                "warning", f"El folio {folio} acababa de ser tomado por otra sesión; se asignó **{folio_final}**. "
                           "Vuelve a descargar el PDF para que incluya el folio correcto.")
        else:
            st.session_state[f"{clave}_msg"] = ("success", f"Cotización **{folio}** registrada en el historial.")
        respaldo = _respaldo_automatico(f"Respaldo automático: cotización {folio_final}")
        if respaldo:
            flash(respaldo[1], respaldo[0])
    else:
        st.session_state[f"{clave}_msg"] = ("error", f"No se pudo registrar el folio {folio} en el Excel: {error}")
    return ok


def bloque_acciones_cotizacion(clave, items, cliente, correo, titulo_detalle):
    """Totales, folio y botones Descargar PDF / Enviar por correo / Limpiar (compartido por ambos cotizadores)."""
    total = float(items["Subtotal"].sum()) if not items.empty else 0.0
    base = total / (1 + TASA_IVA)
    iva = total - base
    st.markdown(f"**Subtotal (sin IVA):** ${base:,.2f} MXN | **IVA (16%):** ${iva:,.2f} MXN | **Total Final:** ${total:,.2f} MXN")

    folio, ya_registrado = folio_para_carrito(clave, items, cliente)
    if ya_registrado:
        st.caption(f"Folio registrado para este carrito: **{folio}** (se reutiliza mientras no cambie el carrito)")
    else:
        st.caption(f"Folio consecutivo que se asignará: **{folio}** (se registra al descargar o enviar)")

    mensaje = st.session_state.pop(f"{clave}_msg", None)
    if mensaje:
        getattr(st, mensaje[0])(mensaje[1])

    pdf_bytes = generar_pdf_cotizacion(folio, cliente, items, titulo_detalle) if not items.empty else b""

    col_b1, col_b2, col_b3, col_b4 = st.columns(4)
    with col_b1:
        st.download_button("📄 Descargar PDF", data=pdf_bytes, file_name=f"{folio}.pdf", mime="application/pdf",
                           disabled=items.empty, key=f"{clave}_pdf",
                           on_click=registrar_si_es_necesario, args=(clave, folio, cliente, items, total))
    with col_b2:
        if st.button("📧 Enviar por Correo", disabled=items.empty, key=f"{clave}_mail"):
            try:
                # Primero se registra el folio y después se envía: si el registro falla,
                # no se manda un PDF con un folio que el historial no conocerá.
                if not registrar_si_es_necesario(clave, folio, cliente, items, total):
                    raise RuntimeError("No se pudo registrar el folio en el historial; el correo NO se envió.")
                folio_registrado = st.session_state.get(f"{clave}_emitido", {}).get("folio")
                if folio_registrado != folio:
                    raise RuntimeError(f"El folio cambió a {folio_registrado} (otra sesión lo tomó primero); "
                                       "descarga de nuevo el PDF y reenvía el correo.")
                mensaje = st.session_state.pop(f"{clave}_msg", None)
                if mensaje:
                    getattr(st, mensaje[0])(mensaje[1])
                enviar_cotizacion_por_correo(correo, cliente, folio, total, pdf_bytes)
                st.success(f"¡Correo enviado exitosamente a {correo}!")
            except Exception as e:
                st.error(f"Error al enviar correo: {e}")
    with col_b3:
        if st.button("🗑️ Limpiar Carrito", disabled=items.empty, key=f"{clave}_clear"):
            st.session_state[clave] = carrito_vacio(por_area=(clave == "carrito_area"))
            st.rerun()
    with col_b4:
        if st.button("🧾 Folio Nuevo", disabled=items.empty, key=f"{clave}_nuevo_folio",
                     help="Conserva el carrito pero fuerza un folio nuevo (p. ej. para una segunda cotización idéntica)."):
            st.session_state.pop(f"{clave}_emitido", None)
            st.rerun()


# ==========================================
# AUTENTICACIÓN, SESIONES Y PERMISOS
# ==========================================
# Módulos del menú y la clave con la que se evalúa el permiso (auth_nemet.MODULOS).
MODULOS_MENU = [
    ("📊 Dashboard & Resumen", "dashboard"),
    ("📦 Control de Inventario y Edición", "inventario"),
    ("👥 Gestión de Clientes", "clientes"),
    ("🎯 Prospección Comercial", "prospeccion"),
    ("📏 Cotizador por Área y Milimétrico", "cotizador_area"),
    ("📝 Cotizador Comercial Profesional", "cotizador_comercial"),
    ("📋 Historial de Cotizaciones (Folios)", "historial"),
    ("🛡️ Administración de Usuarios", "admin_usuarios"),
]


@st.cache_resource(show_spinner=False)
def conexion_usuarios(ruta):
    """Conexión única (cacheada) a la base de usuarios de la app.

    La ruta entra como argumento para que el caché quede ligado al archivo en uso
    (y no a la primera base que se abrió en el proceso).
    """
    conn = auth.conectar(ruta)
    auth.inicializar_db(conn)
    return conn


def modulos_visibles(rol):
    """Etiquetas del menú que el rol puede ver: los módulos sin permiso ni se listan."""
    return [etiqueta for etiqueta, clave in MODULOS_MENU if auth.puede(rol, clave)]


def fecha_legible(iso, con_hora=True):
    """Convierte una marca de tiempo ISO al horario de Sonora para mostrarla."""
    if not iso:
        return ""
    try:
        momento = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    if ZONA_HORARIA:
        momento = momento.astimezone(ZONA_HORARIA)
    return momento.strftime("%d/%m/%Y %H:%M" if con_hora else "%d/%m/%Y")


def sembrar_administrador_inicial(conn):
    """Administrador inicial desde `[auth]` en los Secrets (o variables de entorno NEMET_*).

    Sin credenciales en el repositorio: la contraseña inicial vive en los Secrets y,
    una vez creado el administrador, la app no vuelve a leerla (solo con `admin_forzar`).
    """
    def leer(clave, variable):
        return obtener_secret("auth", clave) or variable_entorno(variable)

    forzar = str(leer("admin_forzar", "NEMET_ADMIN_FORZAR") or "").strip().lower()
    origen = "los Secrets" if obtener_secret("auth", "admin_usuario") else "las variables de entorno"
    return auth.sembrar_admin_inicial(
        conn,
        leer("admin_usuario", "NEMET_ADMIN_USUARIO"),
        leer("admin_password", "NEMET_ADMIN_PASSWORD"),
        nombre=leer("admin_nombre", "NEMET_ADMIN_NOMBRE") or "",
        correo=leer("admin_correo", "NEMET_ADMIN_CORREO") or "",
        forzar=forzar in ("1", "true", "verdadero", "si", "sí", "yes"),
        zona=ZONA_HORARIA,
        origen=origen,
    )


def minutos_de_sesion():
    """Duración de la sesión (por inactividad): `[auth] session_minutos`, por omisión 60."""
    configurado = obtener_secret("auth", "session_minutos") or variable_entorno("NEMET_SESION_MINUTOS")
    try:
        return max(5, int(configurado))
    except (TypeError, ValueError):
        return auth.TTL_SESION_MINUTOS


def _fijar_token(usuario, secreto, ttl):
    """Emite el token firmado de la sesión (session_state + URL para sobrevivir recargas)."""
    token = auth.crear_token(secreto, usuario, minutos=ttl)
    st.session_state["_token"] = token
    st.session_state["_token_emitido"] = time.time()
    try:
        st.query_params[CLAVE_SESION_URL] = token
    except Exception:
        pass  # sin URL disponible la sesión sigue viva en session_state
    return token


def _borrar_token():
    st.session_state.pop("_token", None)
    st.session_state.pop("_token_emitido", None)
    try:
        if CLAVE_SESION_URL in st.query_params:
            del st.query_params[CLAVE_SESION_URL]
    except Exception:
        pass


def cerrar_sesion(conn, motivo="Cierre de sesión del usuario."):
    """Cierra la sesión: bitácora, borra el token y limpia el estado de la sesión."""
    usuario = st.session_state.get("_usuario") or {}
    if usuario:
        auth.registrar_evento(conn, "cierre_sesion", actor=usuario.get("usuario", ""),
                              actor_id=usuario.get("id"), detalle=motivo, zona=ZONA_HORARIA)
    _borrar_token()
    for clave in ("_usuario", "inventario", "clientes", "carrito_area", "carrito_comercial",
                  "inv_base_hash", "cli_base_hash", "_ultimo_respaldo"):
        st.session_state.pop(clave, None)


def _descripcion_error_base(error):
    """Explica en español, y de forma accionable, el error real de la base de usuarios.

    Streamlit reemplaza el mensaje del traceback por «the original error message is
    redacted»; por eso el motivo se muestra aquí en texto propio.
    """
    texto = f"{type(error).__name__}: {error}"
    bajo = texto.lower()
    if "no such column" in bajo or "no such table" in bajo:
        return ("La base de usuarios tiene un **esquema viejo o incompleto**. La app la repara "
                "sola al arrancar; si este aviso sigue apareciendo, restaura el último respaldo "
                "de `nemet_usuarios.db`.")
    if ("readonly" in bajo or "read-only" in bajo or "unable to open database" in bajo
            or "permission" in bajo or "disk i/o" in bajo):
        return ("La base de usuarios está en un archivo o carpeta que **no admite escritura** "
                "(permisos del servidor o disco montado como solo lectura).")
    if "locked" in bajo or "busy" in bajo:
        return ("Otra operación está usando la base de usuarios y la dejó **bloqueada**. "
                "Espera unos segundos y vuelve a intentar el acceso.")
    if "malformed" in bajo or "not a database" in bajo or "encrypted" in bajo:
        return ("El archivo de la base de usuarios parece **dañado**. Restaura el último respaldo "
                "de `nemet_usuarios.db`.")
    return "Esta es la causa exacta del fallo; pásala a soporte si el problema continúa."


def _diagnostico_base_markdown():
    """Líneas del diagnóstico de la base de usuarios (compartidas por las pantallas)."""
    info = auth.info_conexion()
    if not info:
        return None
    uso = info.get("ruta_en_uso") or "—"
    if info.get("copia"):
        uso += "   ← copia temporal: el original no admite escritura"
    return (
        f"- Archivo configurado: `{info.get('ruta_configurada') or '—'}`\n"
        f"- Archivo en uso: `{uso}`\n"
        f"- ¿Admite escritura?: {'**no**' if info.get('solo_lectura') else 'sí'}\n"
        f"- Columnas reparadas al arrancar: {', '.join(info.get('columnas_agregadas') or []) or 'ninguna'}\n"
        f"- Escrituras rechazadas: {auth.fallos_escritura()}\n"
        f"- Último error de escritura: `{auth.ultimo_error_escritura() or info.get('error_escritura') or '—'}`")


def pantalla_error_base(error):
    """Muestra el motivo real de un fallo de la base de usuarios (sin traceback censurado)."""
    estilos_marca()
    cabecera_marca()
    st.error("🚨 **La app no puede usar la base de usuarios.**\n\n" + _descripcion_error_base(error))
    st.code(f"{type(error).__name__}: {error}", language="text")
    detalle = _diagnostico_base_markdown()
    if detalle:
        with st.expander("🔎 Diagnóstico de la base de usuarios", expanded=True):
            st.markdown(detalle)
            st.caption("Truco útil: en Streamlit Cloud, *Manage app* → *Logs* guarda el detalle completo, "
                       "pero el texto de arriba ya es la causa exacta.")


def avisos_base_de_usuarios():
    """Avisa, una vez por sesión, de los problemas de escritura o de esquema de la base."""
    info = auth.info_conexion()
    if not info:
        return
    clave = (info.get("ruta_en_uso"), info.get("copia"), info.get("solo_lectura"),
             tuple(info.get("columnas_agregadas") or ()), auth.fallos_escritura() > 0)
    if st.session_state.get("_aviso_base_visto") == clave:
        return
    st.session_state["_aviso_base_visto"] = clave
    agregadas = info.get("columnas_agregadas") or []
    if agregadas:
        st.info("🧩 Se actualizó el esquema de la base de usuarios (faltaban: "
                + ", ".join(f"`{columna}`" for columna in agregadas)
                + "). El acceso ya funciona con normalidad.")
    if info.get("copia"):
        st.warning("⚠️ **La base de usuarios del repositorio no admite escritura** "
                   f"(`{info.get('ruta_configurada')}`: {info.get('error_escritura') or 'sin permiso'}). "
                   f"Para no dejar a nadie fuera, la app está usando una copia temporal en "
                   f"`{info.get('ruta_en_uso')}`.\n\n"
                   "Los cambios de usuarios y contraseñas de esta sesión **se perderán cuando el "
                   "servidor se reinicie**. Pide a soporte que revise los permisos del despliegue "
                   "o que configure `[auth] db` (Secrets) con una ruta escribible.")
    elif info.get("solo_lectura"):
        st.error("🚨 **La base de usuarios no admite escrituras ahora mismo y no se pudo usar una "
                 f"copia**: {info.get('error_escritura') or 'sin permiso'}. Se puede entrar, pero "
                 "nada se guardará hasta que se resuelva.")
    if auth.fallos_escritura():
        st.warning(f"⚠️ {auth.fallos_escritura()} escrituras rechazadas en la base de usuarios. "
                   f"Último error: `{auth.ultimo_error_escritura() or '—'}`")


def pantalla_login(conn, secreto, ttl, aviso_semilla=None):
    """Formulario de acceso. Devuelve None (la sesión se fija y se recarga la app)."""
    estilos_marca()
    cabecera_marca()
    st.subheader("🔐 Acceso al sistema")
    st.caption("Sistema interno de NEMET. El acceso está restringido a cuentas autorizadas "
               "y todas las acciones quedan registradas en la bitácora.")
    st.info("🔒 **No hay registro abierto.** Las cuentas las crea un administrador desde el panel "
            "🛡️ *Administración de Usuarios*; si no tienes cuenta, pídesela a un administrador "
            "(te dará un usuario y una contraseña provisional).")

    if aviso_semilla and aviso_semilla[0] in ("creado", "actualizado"):
        st.success(f"🌱 {aviso_semilla[1]}")
        # Se publica la base en GitHub de inmediato: si el servidor se reinicia antes del
        # primer guardado, la cuenta de administrador no se pierde (disco efímero en la nube).
        respaldo = _respaldar_usuarios("alta del administrador inicial")
        if respaldo:
            getattr(st, respaldo[0], st.info)(respaldo[1])
    if aviso_semilla and aviso_semilla[0] == "sin_admins":
        st.error("🚨 El sistema no tiene ningún administrador activo y los Secrets no traen "
                 "credenciales de administrador inicial (`[auth] admin_usuario` y `admin_password`). "
                 "Configúralas en los Secrets de la app (o, en una instalación local, ejecuta "
                 "`python herramientas/crear_admin.py --usuario tu_usuario`) y recarga esta página. "
                 "Por seguridad, el primer administrador nunca se crea desde esta pantalla.")
        with st.expander("👤 Soy el administrador: ¿cómo entro?"):
            st.markdown(
                "**En Streamlit Community Cloud o servidor con Secrets**\n\n"
                "1. Abre los Secrets de la app (o el archivo `.streamlit/secrets.toml`) y agrega:\n"
                "   ```toml\n"
                "   [auth]\n"
                '   admin_usuario = "tu_usuario"\n'
                '   admin_password = "una-contraseña-larga-y-única"\n'
                "   ```\n"
                "2. Recarga la página: la app crea esa cuenta y te pide cambiar la contraseña al entrar.\n\n"
                "**En una instalación local (sin Secrets)**\n\n"
                "```bash\n"
                "python herramientas/crear_admin.py --usuario tu_usuario\n"
                "```\n\n"
                "Si ya tenías cuenta y olvidaste la contraseña, un administrador puede restablecerla "
                "desde el panel; o pon `admin_forzar = true` una sola vez en los Secrets para "
                "restablecer la del administrador inicial.")
        return None

    with st.form("form_acceso"):
        usuario = st.text_input("Usuario", key="acceso_usuario", autocomplete="username")
        password = st.text_input("Contraseña", type="password", key="acceso_password",
                                 autocomplete="current-password")
        entrar = st.form_submit_button("Entrar", type="primary", key="acceso_entrar")

    if entrar:
        try:
            datos, mensaje = auth.autenticar(conn, usuario, password, zona=ZONA_HORARIA)
        except (sqlite3.Error, OSError) as error:
            # Nunca más un cuadro rojo con el mensaje censurado: aquí se ve la causa real.
            pantalla_error_base(error)
            datos, mensaje = None, None
        if datos:
            st.session_state["_usuario"] = datos
            _fijar_token(datos, secreto, ttl)
            st.rerun()
        elif mensaje:
            st.error(f"❌ {mensaje}")

    st.caption("¿Olvidaste tu contraseña? Pide a un administrador que la restablezca desde el "
               "panel de administración; nadie puede recuperarla porque se guarda cifrada.")

    with st.expander("🔧 ¿Problemas para entrar?"):
        detalle = _diagnostico_base_markdown()
        if detalle:
            st.markdown(detalle)
            st.caption("Si algo falla, la app muestra el motivo exacto en pantalla antes de "
                       "cualquier cuadro de error genérico.")
        else:
            st.caption("La app no pudo leer el estado de la base de usuarios.")
    if not obtener_secret("auth", "session_secret"):
        st.caption("💡 Sugerencia de seguridad: define `[auth] session_secret` en los Secrets para "
                   "rotar la clave que firma las sesiones y `[auth] session_minutos` para ajustar "
                   "la expiración por inactividad.")
    return None


def pantalla_cambio_obligatorio(conn, usuario, secreto, ttl):
    """Bloquea la app hasta que el usuario cambie su contraseña provisional."""
    estilos_marca()
    cabecera_marca()
    st.subheader("🔑 Cambia tu contraseña")
    st.warning(f"Hola {usuario['nombre'] or usuario['usuario']}: tu contraseña es provisional. "
               "Por seguridad debes cambiarla antes de usar el sistema.")
    with st.form("form_password_obligatorio"):
        actual = st.text_input("Contraseña actual (la provisional)", type="password", key="cambio_actual")
        nueva = st.text_input("Contraseña nueva", type="password", key="cambio_nueva",
                              help=f"Mínimo {auth.LARGO_MINIMO_PASSWORD} caracteres, con letras y números.")
        repetir = st.text_input("Repite la contraseña nueva", type="password", key="cambio_repetir")
        guardar = st.form_submit_button("Guardar contraseña", type="primary", key="cambio_guardar")
    if guardar:
        if nueva != repetir:
            st.error("Las contraseñas nuevas no coinciden.")
        else:
            try:
                actualizado = auth.cambiar_password_propia(conn, usuario["id"], actual, nueva, zona=ZONA_HORARIA)
                _fijar_token(actualizado, secreto, ttl)
                st.session_state["_usuario"] = actualizado
                flash("✅ Contraseña actualizada. ¡Bienvenido al Sistema Maestro NEMET!")
                respaldo = _respaldar_usuarios("cambio de la contraseña provisional")
                if respaldo:
                    flash(respaldo[1], respaldo[0])
                st.rerun()
            except auth.ErrorAuth as error:
                st.error(str(error))
    if st.button("🔒 Cerrar sesión"):
        cerrar_sesion(conn, "Cerró sesión desde el cambio de contraseña obligatorio.")
        st.rerun()


def ejecutar_gate_acceso():
    """Punto único de entrada: sin sesión válida no se ejecuta ni un módulo de negocio."""
    conn = CONN
    avisos_base_de_usuarios()  # esquema reparado, base de solo lectura, escrituras rechazadas…
    aviso_semilla = sembrar_administrador_inicial(conn)
    secreto = auth.secreto_sesion(conn, obtener_secret("auth", "session_secret"))
    ttl = minutos_de_sesion()

    token = st.session_state.get("_token") or st.query_params.get(CLAVE_SESION_URL)
    usuario = auth.usuario_de_token(conn, secreto, token) if token else None
    if usuario is None:
        if token:
            _borrar_token()  # caducó, se firmó con otra clave o la cuenta se desactivó
        pantalla_login(conn, secreto, ttl, aviso_semilla)
        st.stop()

    # Ventana deslizante: la sesión se renueva mientras el usuario sigue trabajando.
    if time.time() - float(st.session_state.get("_token_emitido", 0)) > REFRESCO_TOKEN_SEG:
        _fijar_token(usuario, secreto, ttl)
    st.session_state["_usuario"] = usuario

    if usuario["debe_cambiar"]:
        pantalla_cambio_obligatorio(conn, usuario, secreto, ttl)
        st.stop()
    return usuario


def requiere_modulo(clave_modulo):
    """Corta la vista (y lo anota en la bitácora) si el rol no tiene permiso al módulo."""
    if auth.puede(SESION["rol"], clave_modulo):
        return True
    auth.registrar_evento(CONN, "permiso_denegado", actor=SESION["usuario"], actor_id=SESION["id"],
                          objetivo=clave_modulo,
                          detalle=f"El rol {auth.etiqueta_rol(SESION['rol'])} intentó abrir {clave_modulo}.",
                          zona=ZONA_HORARIA)
    st.error("🚫 Tu rol no tiene acceso a este módulo. Si lo necesitas, pide a un administrador "
             "que ajuste tus permisos.")
    st.stop()


def recargar_datos():
    """Sincroniza la caché de la sesión con el Excel respetando los permisos del rol."""
    st.session_state["inventario"] = cargar_inventario()
    if auth.puede(SESION["rol"], "clientes"):
        st.session_state["clientes"] = cargar_clientes()
    else:
        st.session_state["clientes"] = pd.DataFrame(columns=COLUMNAS_CLIENTES)


def bloque_usuario_sidebar(conn, secreto, ttl):
    """Identidad de la sesión, cambio de contraseña propio y cierre de sesión."""
    with st.sidebar.container(border=True):
        st.markdown(f"**{SESION['nombre'] or SESION['usuario']}**")
        st.caption(f"👤 `{SESION['usuario']}` · {auth.etiqueta_rol(SESION['rol'])}")
        st.caption(f"🕒 Último acceso: {fecha_legible(SESION['ultimo_acceso']) or '—'}")

    with st.sidebar.expander("🔑 Cambiar mi contraseña"):
        with st.form("form_mi_password"):
            actual = st.text_input("Contraseña actual", type="password", key="mi_password_actual")
            nueva = st.text_input("Contraseña nueva", type="password", key="mi_password_nueva",
                                  help=f"Mínimo {auth.LARGO_MINIMO_PASSWORD} caracteres, con letras y números.")
            repetir = st.text_input("Repite la nueva", type="password", key="mi_password_repetir")
            guardar = st.form_submit_button("Actualizar contraseña", key="mi_password_guardar")
        if guardar:
            if nueva != repetir:
                st.error("Las contraseñas nuevas no coinciden.")
            else:
                try:
                    actualizado = auth.cambiar_password_propia(conn, SESION["id"], actual, nueva, zona=ZONA_HORARIA)
                except auth.ErrorAuth as error:
                    st.error(str(error))
                else:
                    st.session_state["_usuario"] = actualizado
                    _fijar_token(actualizado, secreto, ttl)
                    flash("✅ Tu contraseña se actualizó correctamente.")
                    respaldo = _respaldar_usuarios("cambio de contraseña")
                    if respaldo:
                        flash(respaldo[1], respaldo[0])
                    st.rerun()

    if st.sidebar.button("🔒 Cerrar sesión"):
        cerrar_sesion(conn)
        st.rerun()


def _tras_cambio_de_usuarios(motivo):
    """Respalda la base de usuarios de inmediato (sin esperar el intervalo anti-spam) y recarga."""
    respaldo = _respaldar_usuarios(motivo)
    if respaldo:
        flash(respaldo[1], respaldo[0])
    st.rerun()


def _respaldar_usuarios(motivo):
    """Publica la base de usuarios en GitHub en el acto.

    En Streamlit Community Cloud el disco es efímero: si el servidor se reinicia antes
    del siguiente respaldo, las cuentas recién creadas (y las contraseñas recién
    cambiadas) se perderían. Devuelve (tipo_ui, mensaje) o None si `[git]` no está configurado.
    """
    return _respaldo_automatico(f"Respaldo automático: {motivo}",
                                rutas_extra=[RUTA_DB_USUARIOS], intervalo=False)


def panel_usuarios(conn):
    """Panel de administración: crear, editar, desactivar y eliminar administradores y usuarios."""
    requiere_modulo("usuarios_gestionar")  # doble candado: además del módulo, exige la acción de administrar
    st.subheader("🛡️ Administración de Usuarios y Permisos")
    st.caption("Solo los administradores activos ven este panel. Las contraseñas se guardan con hash "
               "scrypt (nunca en texto plano) y todas las acciones quedan en la bitácora.")

    metricas = auth.resumen(conn)
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Cuentas", metricas["total"])
    col2.metric("Activas", metricas["activos"])
    col3.metric("Administradores activos", metricas["admins_activos"])
    col4.metric("Por cambiar contraseña", metricas["por_cambiar"])
    col5.metric("Bloqueadas temporalmente", metricas["bloqueados"])
    if metricas["admins_activos"] <= 1:
        st.warning("⚠️ Solo hay un administrador activo. Crea otro administrador para no depender "
                   "de una sola cuenta (el sistema nunca se queda sin administradores activos).")

    credenciales = st.session_state.pop("_credenciales_recientes", None)
    if credenciales:
        st.warning(f"🔐 **Anota estas credenciales ahora**: no se vuelven a mostrar.\n\n"
                   f"Usuario: `{credenciales['usuario']}` — Contraseña temporal: `{credenciales['password']}`\n\n"
                   "Compártelas por un canal seguro: al iniciar sesión la app pedirá cambiarla.")

    usuarios = auth.listar_usuarios(conn)
    tab_cuentas, tab_crear, tab_actividad = st.tabs(["👥 Cuentas", "➕ Crear cuenta", "🧾 Actividad"])

    with tab_cuentas:
        st.dataframe(pd.DataFrame([{
            "Usuario": u["usuario"],
            "Nombre": u["nombre"],
            "Correo": u["correo"],
            "Rol": auth.etiqueta_rol(u["rol"]),
            "Estado": "🟢 Activa" if u["activo"] else "⚪ Desactivada",
            "Contraseña": "🔑 Provisional" if u["debe_cambiar"] else "Definida",
            "Último acceso": fecha_legible(u["ultimo_acceso"]) or "Nunca",
            "Bloqueada hasta": fecha_legible(u["bloqueado_hasta"]) or "—",
            "Creada": fecha_legible(u["creado_en"], con_hora=False),
        } for u in usuarios]), width="stretch", hide_index=True)

        etiquetas = {u["id"]: f"{u['usuario']} — {auth.etiqueta_rol(u['rol'])}"
                              f"{'' if u['activo'] else ' (desactivada)'}" for u in usuarios}
        id_objetivo = st.selectbox("Cuenta a administrar", list(etiquetas), format_func=etiquetas.get,
                                   key="cuenta_objetivo")
        objetivo = auth.obtener_usuario(conn, id_objetivo)
        es_uno_mismo = objetivo["id"] == SESION["id"]

        col_datos, col_acceso = st.columns(2)
        with col_datos:
            with st.form("form_datos_usuario"):
                st.markdown("**Datos de la cuenta**")
                nombre = st.text_input("Nombre completo", value=objetivo["nombre"], key="datos_nombre")
                correo = st.text_input("Correo", value=objetivo["correo"], key="datos_correo")
                guardar_datos = st.form_submit_button("💾 Guardar datos", key="datos_guardar")
            if guardar_datos:
                try:
                    auth.actualizar_datos(conn, SESION, objetivo["id"], nombre=nombre, correo=correo,
                                          zona=ZONA_HORARIA)
                except auth.ErrorAuth as error:
                    st.error(str(error))
                else:
                    flash(f"✅ Datos de `{objetivo['usuario']}` actualizados.")
                    _tras_cambio_de_usuarios("datos de usuario")

            with st.form("form_rol_usuario"):
                st.markdown("**Rol y permisos**")
                if es_uno_mismo:
                    st.caption("No puedes cambiar tu propio rol: pídelo a otro administrador.")
                rol_nuevo = st.selectbox("Rol", auth.ROLES if not es_uno_mismo else [objetivo["rol"]],
                                         index=auth.ROLES.index(objetivo["rol"]) if not es_uno_mismo else 0,
                                         format_func=auth.etiqueta_rol, disabled=es_uno_mismo, key="rol_nuevo")
                guardar_rol = st.form_submit_button("🔁 Aplicar rol", disabled=es_uno_mismo, key="rol_guardar")
            if guardar_rol:
                try:
                    auth.cambiar_rol(conn, SESION, objetivo["id"], rol_nuevo, zona=ZONA_HORARIA)
                except auth.ErrorAuth as error:
                    st.error(str(error))
                else:
                    flash(f"✅ `{objetivo['usuario']}` ahora es {auth.etiqueta_rol(rol_nuevo)}.")
                    _tras_cambio_de_usuarios("cambio de rol")

        with col_acceso:
            with st.form("form_password_usuario"):
                st.markdown("**Restablecer contraseña**")
                generar = st.checkbox("Generar una contraseña temporal", value=True, key="pass_generar")
                nueva = st.text_input("Contraseña nueva", type="password", disabled=generar, key="pass_nueva",
                                      help="Se pedirá cambiarla al iniciar sesión.")
                restablecer = st.form_submit_button("🔑 Restablecer", key="pass_restablecer")
            if restablecer:
                try:
                    _, temporal = auth.restablecer_password(conn, SESION, objetivo["id"],
                                                            password=None if generar else nueva,
                                                            zona=ZONA_HORARIA)
                except auth.ErrorAuth as error:
                    st.error(str(error))
                else:
                    if temporal:
                        st.session_state["_credenciales_recientes"] = {"usuario": objetivo["usuario"],
                                                                       "password": temporal}
                    flash(f"✅ Contraseña de `{objetivo['usuario']}` restablecida.")
                    _tras_cambio_de_usuarios("restablecimiento de contraseña")

            st.markdown("**Estado de la cuenta**")
            if objetivo["activo"]:
                st.caption("Una cuenta desactivada no puede iniciar sesión, pero conserva su historial.")
                if st.button("🚫 Desactivar cuenta", disabled=es_uno_mismo, key="cuenta_desactivar"):
                    try:
                        auth.establecer_activo(conn, SESION, objetivo["id"], False, zona=ZONA_HORARIA)
                    except auth.ErrorAuth as error:
                        st.error(str(error))
                    else:
                        flash(f"✅ `{objetivo['usuario']}` quedó desactivada.")
                        _tras_cambio_de_usuarios("desactivación de usuario")
            else:
                if st.button("♻️ Reactivar cuenta", type="primary", key="cuenta_reactivar"):
                    try:
                        auth.establecer_activo(conn, SESION, objetivo["id"], True, zona=ZONA_HORARIA)
                    except auth.ErrorAuth as error:
                        st.error(str(error))
                    else:
                        flash(f"✅ `{objetivo['usuario']}` quedó activa de nuevo.")
                        _tras_cambio_de_usuarios("reactivación de usuario")

            with st.expander("🗑️ Eliminar cuenta definitivamente"):
                st.caption("Se borra la cuenta y no se puede deshacer. La bitácora conserva sus acciones.")
                confirmacion = st.text_input(f"Escribe `{objetivo['usuario']}` para confirmar",
                                             key=f"confirmar_borrado_{objetivo['id']}")
                if st.button("Eliminar cuenta", key="cuenta_eliminar",
                             disabled=es_uno_mismo or confirmacion.strip() != objetivo["usuario"]):
                    try:
                        auth.eliminar_usuario(conn, SESION, objetivo["id"], zona=ZONA_HORARIA)
                    except auth.ErrorAuth as error:
                        st.error(str(error))
                    else:
                        flash(f"🗑️ La cuenta `{objetivo['usuario']}` fue eliminada.")
                        _tras_cambio_de_usuarios("eliminación de usuario")

    with tab_crear:
        with st.form("form_crear_usuario", clear_on_submit=True):
            col_a, col_b = st.columns(2)
            with col_a:
                usuario_nuevo = st.text_input("Usuario (para iniciar sesión)", key="nuevo_usuario",
                                              help="3-32 caracteres: letras, números, punto, guion o guion bajo.")
                nombre_nuevo = st.text_input("Nombre completo", key="nuevo_nombre")
            with col_b:
                correo_nuevo = st.text_input("Correo", key="nuevo_correo")
                rol_nuevo = st.selectbox("Rol", auth.ROLES, index=2, format_func=auth.etiqueta_rol,
                                         key="nuevo_rol")
            definir = st.checkbox("Definir yo la contraseña inicial (si no, se genera una temporal)",
                                  value=False, key="nuevo_definir")
            password_nueva = st.text_input("Contraseña inicial", type="password", disabled=not definir,
                                           key="nuevo_password",
                                           help=f"Mínimo {auth.LARGO_MINIMO_PASSWORD} caracteres, con letras y números.")
            debe_cambiar = st.checkbox("Pedir cambio de contraseña al primer inicio de sesión", value=True,
                                       key="nuevo_debe_cambiar")
            crear = st.form_submit_button("➕ Crear cuenta", type="primary", key="crear_cuenta")
        if crear:
            try:
                creado = auth.crear_usuario(conn, SESION, usuario_nuevo,
                                            password=password_nueva if definir else None,
                                            nombre=nombre_nuevo, correo=correo_nuevo, rol=rol_nuevo,
                                            debe_cambiar=debe_cambiar, zona=ZONA_HORARIA)
            except auth.ErrorAuth as error:
                st.error(str(error))
            else:
                st.session_state["_credenciales_recientes"] = {
                    "usuario": creado["usuario"],
                    "password": creado.get("password_temporal") or password_nueva,
                }
                flash(f"✅ Cuenta `{creado['usuario']}` creada como {auth.etiqueta_rol(creado['rol'])}.")
                _tras_cambio_de_usuarios("alta de usuario")

        st.markdown("**Permisos por rol**")
        st.dataframe(pd.DataFrame([{
            "Módulo": etiqueta,
            "Administrador": "✅" if auth.puede("admin", clave) else "—",
            "Editor": "✅" if auth.puede("editor", clave) else "—",
            "Usuario": "✅" if auth.puede("usuario", clave) else "—",
        } for etiqueta, clave in MODULOS_MENU]), width="stretch", hide_index=True)

    with tab_actividad:
        eventos = auth.listar_eventos(conn, limite=300)
        if eventos:
            st.dataframe(pd.DataFrame([{
                "Fecha": fecha_legible(e["fecha"]),
                "Actor": e["actor"] or "—",
                "Acción": e["accion"],
                "Objetivo": e["objetivo"] or "—",
                "Detalle": e["detalle"],
            } for e in eventos]), width="stretch", hide_index=True)
        else:
            st.info("Todavía no hay actividad registrada.")
        st.caption("La bitácora nunca guarda contraseñas: solo hashes (y las contraseñas provisorias "
                   "se muestran una única vez, en pantalla).")
        if st.button("🚪 Cerrar todas las sesiones (cambia la clave de firma)", key="cerrar_sesiones"):
            auth.rotar_secreto_sesion(conn, SESION, zona=ZONA_HORARIA)
            secreto = auth.secreto_sesion(conn, obtener_secret("auth", "session_secret"))
            _fijar_token(SESION, secreto, minutos_de_sesion())
            flash("✅ Sesiones revocadas: todos deberán iniciar sesión otra vez (esta sesión se renovó).")
            _tras_cambio_de_usuarios("revocación de sesiones")


# ==========================================
# INTERFAZ DE PROSPECCIÓN COMERCIAL
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)
def buscar_prospectos_osm(ciudad, radio, sectores, catalogo):
    """Caché de media hora: no repetir consultas idénticas a los servidores públicos.

    Devuelve (fichas, detalle); el detalle dice qué servidor respondió y si hubo que
    reducir el radio, para poder informarlo en pantalla sin adivinar.
    """
    return pros.buscar_osm_detallada(ciudad, radio, sectores, catalogo)


def alternativas_sin_osm():
    """Si OpenStreetMap no responde, ofrece salidas que NO dependen de la red.

    Nunca se inventan negocios ni contactos: reintentar (ahora contra varios servidores),
    importar un CSV propio o dar de alta el prospecto a mano.
    """
    if not st.session_state.get("pros_fallo_osm"):
        return
    with st.container(border=True):
        st.markdown("#### 🛟 OpenStreetMap no respondió: alternativas sin red")
        st.caption("Ninguna ficha se inventó ni se guardó. Estas rutas funcionan sin Internet:")
        st.markdown(
            "- **Reintentar**: la búsqueda ya prueba varios servidores públicos de Overpass, "
            "así que un segundo intento suele bastar.\n"
            "- **Importar un CSV** de negocios públicos (el bloque se abre solo al final).\n"
            "- **Agregar el prospecto manualmente** con los datos que ya tengas.")
        if st.button("🔁 Reintentar la búsqueda", key="pros_reintentar", type="primary",
                     width="stretch"):
            st.session_state.pop("pros_fallo_osm", None)
            st.session_state["pros_reintentar"] = True
            st.rerun()


def formulario_prospecto_manual(catalogo, expandido=False):
    """Alta directa en el Excel local; nunca geocodifica domicilios ni envía mensajes."""
    if st.session_state.pop("manual_limpiar_despues_de_guardar", False):
        # El permiso y el origen deben confirmarse otra vez para CADA nuevo negocio.
        # Limpiar antes de crear los widgets; hacerlo tras crearlos viola el estado de Streamlit.
        for campo in ("empresa", "giro", "ciudad", "zona", "direccion", "telefono", "whatsapp",
                      "correo", "sitio", "redes", "url_fuente", "latitud", "longitud", "origen",
                      "estado", "ultimo", "proximo", "notas", "permiso"):
            st.session_state.pop(f"manual_{campo}", None)
    with st.expander("➕ Agregar prospecto manualmente", expanded=expandido):
        st.caption("Registra solo datos **comerciales** publicados o que la empresa compartió con permiso. "
                   "No hace falta buscar en OpenStreetMap ni importar un archivo.")
        with st.form("form_alta_prospecto"):
            empresa = st.text_input("Empresa *", max_chars=160, key="manual_empresa")
            giro = st.selectbox("Tipo de negocio *", list(pros.SECTORES),
                                format_func=lambda s: pros.SECTORES[s]["nombre"], key="manual_giro")
            ciudad = st.text_input("Ciudad y estado *", value="Ciudad Obregón, Sonora",
                                   max_chars=120, key="manual_ciudad")
            zona = st.text_input("Zona / colonia", max_chars=120, key="manual_zona")
            direccion = st.text_input("Dirección comercial (si fue publicada)", max_chars=240,
                                      key="manual_direccion")
            telefono = st.text_input("Teléfono comercial", max_chars=70, key="manual_telefono")
            whatsapp = st.text_input("WhatsApp comercial (solo si lo publicaron expresamente)",
                                     max_chars=180, key="manual_whatsapp")
            correo = st.text_input("Correo comercial", max_chars=160, key="manual_correo")
            sitio = st.text_input("Sitio web", max_chars=300, key="manual_sitio")
            with st.expander("Datos adicionales y mapa (opcional)"):
                redes = st.text_input("Redes comerciales (URL)", max_chars=300, key="manual_redes")
                url_fuente = st.text_input("Enlace a la fuente pública (URL)", max_chars=300,
                                           key="manual_url_fuente")
                st.caption("Sin coordenadas, la dirección y ciudad sirven como búsqueda en Google Maps. "
                           "No introduzcas coordenadas de domicilios privados.")
                latitud = st.text_input("Latitud comercial (opcional)", key="manual_latitud")
                longitud = st.text_input("Longitud comercial (opcional)", key="manual_longitud")
            origen = st.text_input("Origen de los datos / autorización *", max_chars=240,
                                   placeholder="Ej.: catálogo público del negocio o contacto compartido con permiso",
                                   key="manual_origen")
            st.caption("La recomendación de producto se calcula automáticamente a partir del giro y "
                       "del Inventario. Puedes completar el perfil y su evidencia desde la ficha guardada.")
            estado = st.selectbox("Estado inicial", pros.ESTADOS_COMERCIALES, key="manual_estado")
            ultimo = st.date_input("Último contacto (opcional)", value=None, key="manual_ultimo")
            proximo = st.date_input("Próximo seguimiento (opcional)", value=None, key="manual_proximo")
            notas = st.text_area("Notas comerciales (máximo 500 caracteres)", max_chars=500,
                                 key="manual_notas")
            permiso = st.checkbox("Confirmo que son datos de contacto comerciales públicos o "
                                  "aportados con autorización; no son teléfonos privados.", key="manual_permiso")
            alta = st.form_submit_button("➕ Guardar prospecto localmente", type="primary", width="stretch",
                                          key="manual_guardar")
        if alta:
            try:
                ficha = pros.candidato_manual({
                    "Empresa": empresa, "Giro": giro, "Ciudad": ciudad, "Zona": zona,
                    "Dirección": direccion, "Teléfono": telefono, "WhatsApp": whatsapp,
                    "Correo": correo, "Sitio_web": sitio, "Redes": redes, "URL_fuente": url_fuente,
                    "Latitud": latitud, "Longitud": longitud, "Origen_datos": origen,
                    "Estado": estado, "Último_contacto": ultimo, "Próximo_seguimiento": proximo,
                    "Notas": notas, "Datos_comerciales_autorizados": permiso,
                }, catalogo, hoy=ahora_local().date())
                agregados, repetidos, clientes = guardar_prospectos([ficha])
                if not agregados:
                    st.warning("No se duplicó la ficha: " +
                               ("ya existe en Clientes." if clientes else "ya existe un prospecto con esa empresa, ciudad o contacto."))
                else:
                    flash(f"Prospecto manual guardado: {ficha['Empresa']} · {ficha['Segmento']} · "
                          f"potencial {ficha['Puntaje']}/100.")
                    respaldo = _respaldo_automatico("Respaldo automático: prospecto manual", intervalo=False)
                    if respaldo:
                        flash(respaldo[1], respaldo[0])
                    st.session_state["manual_limpiar_despues_de_guardar"] = True
                    st.rerun()
            except ValueError as error:
                st.error(str(error))
            except Exception as error:
                st.error(f"No se pudo guardar el prospecto manual: {error}")


def panel_prospeccion(inventario):
    # Streamlit ya adapta la barra lateral; aquí las columnas de los filtros y
    # formularios se apilan de forma legible en pantallas angostas. CSS fijo,
    # limitado a este módulo (nunca se interpola información de prospectos).
    st.markdown("""<style>
    @media (max-width: 768px) {
      .st-key-prospector-movil [data-testid="stHorizontalBlock"] {
        flex-direction: column !important; align-items: stretch !important;
      }
      .st-key-prospector-movil [data-testid="column"] {
        flex: 1 1 100% !important; width: 100% !important; min-width: 0 !important;
      }
      .st-key-prospector-movil [data-testid="stMarkdownContainer"] { overflow-wrap: anywhere; }
      .st-key-prospector-movil [data-testid="stDataFrame"] { max-width: 100%; }
    }
    </style>""", unsafe_allow_html=True)
    with st.container(key="prospector-movil"):
        _contenido_prospeccion(inventario)


def _contenido_prospeccion(inventario):
    st.subheader("🎯 NEMET PROSPECTOR")
    st.caption("Buscar → Detectar → Calificar → Contactar → Dar seguimiento → Vender")
    st.write("Busca negocios públicos por ciudad y giro, revisa el mapa y arma tu lista comercial. "
             "**Potencial no significa intención de compra:** comprueba los datos antes de contactar.")
    st.caption("Fuente: © OpenStreetMap contributors (ODbL). Se consulta solo al pulsar Buscar, sin extraer "
               "teléfonos privados ni enviar mensajes automáticamente. Un CSV propio también es opcional.")
    st.caption("🔒 Las notas y contactos SOLO se respaldan en GitHub cuando se confirma que el destino es PRIVADO. "
               "Sin respaldo, los datos del servidor de Streamlit Cloud pueden perderse al reiniciar: exporta el CSV.")
    with st.expander("¿Cómo se califica el potencial?"):
        st.write("Giro (50–66 puntos), productos publicados (hasta +12), distancia con coordenadas "
                 "(+5 hasta 10 km / +2 hasta 30 km), teléfono o correo (+9), WhatsApp explícito (+5) y "
                 "web/redes (+5). Tamaño (+3), actividad reciente (+4) y clientela (+2) solo cuentan "
                 "si aportas una fuente de verificación. Alta ≥80, Media ≥60, Exploratoria <60. "
                 "Un dato desconocido **no** se inventa ni penaliza. Las recomendaciones usan el inventario real.")
    catalogo = tuple(inventario["Descripcion"].dropna().astype(str).unique()) if "Descripcion" in inventario else ()

    with st.form("form_busqueda_prospectos"):
        c1, c2 = st.columns([2, 1])
        with c1:
            ciudad = st.text_input("Ciudad y estado (México)", "Ciudad Obregón, Sonora",
                                   max_chars=100, key="pros_ciudad")
        with c2:
            radio = st.slider("Radio desde el centro (km)", min_value=1, max_value=30,
                              value=30, key="pros_radio")
        sectores = st.multiselect(
            "Giros a buscar", list(pros.SECTORES), default=list(pros.SECTORES),
            format_func=lambda s: pros.SECTORES[s]["nombre"], key="pros_sectores")
        buscar = st.form_submit_button("🔎 Buscar negocios", key="pros_buscar")
    # El botón «Reintentar» de las alternativas vuelve a lanzar la misma búsqueda.
    reintentar = st.session_state.pop("pros_reintentar", False)
    if buscar or reintentar:
        st.session_state.pop("pros_resultados", None)  # nunca confundir fichas anteriores con un intento fallido
        st.session_state.pop("pros_fallo_osm", None)
        try:
            with st.spinner("Buscando negocios y contrastando con clientes existentes..."):
                encontrados, detalle = buscar_prospectos_osm(ciudad, radio, tuple(sectores), catalogo)
                nuevos, repetidos, clientes = nuevos_prospectos(encontrados)
            st.session_state["pros_resultados"] = nuevos
            st.session_state["pros_origen"] = f"OpenStreetMap · {ciudad.strip()} · {detalle['radio_usado']:g} km"
            st.session_state["pros_version"] = st.session_state.get("pros_version", 0) + 1
            st.info(f"Encontrados: {len(encontrados)} · Nuevos: {len(nuevos)} · "
                    f"Ya guardados: {repetidos} · Ya clientes: {clientes}.")
            st.caption(f"Servidor consultado: {detalle['servidor']} · fuente: {pros.FUENTE_OSM}")
            for aviso in detalle.get("avisos", []):
                st.warning(aviso)
            if not encontrados:
                st.warning("No hay negocios etiquetados para esos giros en esta zona de OpenStreetMap. "
                           "Prueba otro radio o importa un CSV de negocios públicos.")
            elif len(encontrados) >= pros.MAX_RESULTADOS:
                st.warning("Se alcanzó el límite de fichas de esta búsqueda. Reduce el radio o elige menos giros "
                           "para descubrir negocios que pudieron quedar fuera.")
        except (pros.ErrorBusqueda, ValueError) as error:
            st.session_state["pros_fallo_osm"] = str(error)
            st.error(str(error))
        except Exception as error:
            st.session_state["pros_fallo_osm"] = f"No se pudo realizar la búsqueda: {error}"
            st.error(st.session_state["pros_fallo_osm"])

    alternativas_sin_osm()
    formulario_prospecto_manual(catalogo, expandido=bool(st.session_state.get("pros_fallo_osm")))

    with st.expander("📂 Importar un CSV de negocios (alternativa si faltan fichas públicas)",
                     expanded=bool(st.session_state.get("pros_fallo_osm"))):
        st.caption("Solo datos de contacto **comercial** publicados o aportados con autorización. "
                   "Obligatorias: `Empresa`, `Giro` (o `Segmento`). Opcionales: ciudad, zona, dirección, "
                   "teléfono, **WhatsApp publicado**, correo, web, redes, coordenadas y productos del negocio. "
                   "Para puntuar tamaño, clientela o actividad escribe también `Fuente_perfil`.")
        plantilla = ("Empresa,Giro,Ciudad,Zona,Dirección,Teléfono,WhatsApp,Correo,Sitio_web,Redes,"
                    "URL_fuente,Latitud,Longitud,Productos_negocio,Tamaño,Tipo_clientela,"
                    "Última_actividad,Fuente_perfil\n")
        st.download_button("⬇️ Plantilla CSV", data=plantilla.encode("utf-8-sig"),
                           file_name="plantilla_prospectos.csv", mime="text/csv", key="pros_plantilla")
        archivo = st.file_uploader("Archivo CSV (UTF-8, máximo 500 KB / 300 filas)", type="csv", key="pros_archivo")
        if st.button("Clasificar archivo", disabled=archivo is None, key="pros_importar"):
            st.session_state.pop("pros_resultados", None)
            try:
                if archivo.size > 500_000:
                    raise ValueError("El CSV supera 500 KB.")
                contenido = archivo.getvalue()
                tabla = pd.read_csv(BytesIO(contenido), dtype=str, keep_default_na=False,
                                    encoding="utf-8-sig", nrows=301)
                tabla.columns = [col.strip() for col in tabla.columns]
                if "Empresa" not in tabla or not ({"Giro", "Segmento"} & set(tabla.columns)):
                    raise ValueError("Faltan las columnas Empresa y Giro (o Segmento). Usa la plantilla.")
                if len(tabla) > 300:
                    raise ValueError("Importa como máximo 300 negocios por archivo.")
                preparados = [pros.candidato_de_archivo(fila, catalogo, ciudad)
                              for fila in tabla.to_dict("records")]
                validos = [p for p in preparados if p]
                nuevos, repetidos, clientes = nuevos_prospectos(validos)
                st.session_state["pros_resultados"] = nuevos
                st.session_state["pros_origen"] = "CSV importado"
                st.session_state["pros_version"] = st.session_state.get("pros_version", 0) + 1
                st.info(f"Clasificados: {len(validos)} · Nuevos: {len(nuevos)} · "
                        f"Sin empresa/giro conocido: {len(preparados) - len(validos)} · "
                        f"Ya guardados: {repetidos} · Ya clientes: {clientes}.")
            except (ValueError, UnicodeError, pd.errors.ParserError) as error:
                st.error(f"No se pudo leer el CSV: {error}")
            except Exception as error:
                st.error(f"No se pudo clasificar el archivo: {error}")

    candidatos = st.session_state.get("pros_resultados", [])
    if candidatos:
        st.markdown(f"### Candidatos nuevos · {st.session_state.get('pros_origen', 'búsqueda')}")
        st.caption("Selecciona las fichas que deseas guardar. WhatsApp solo se muestra si el negocio "
                   "lo publicó expresamente. Sin contacto público, el estado inicial es ‘Por investigar’.")
        puntos_nuevos = pros.puntos_mapa(candidatos)
        if puntos_nuevos:
            with st.expander(f"🗺️ Ver {len(puntos_nuevos)} negocio(s) ubicados en el mapa"):
                st.map(pd.DataFrame(puntos_nuevos), latitude="lat", longitude="lon", color="color",
                       size=35, height=380)
        version = st.session_state["pros_version"]
        modo = st.radio("Vista de candidatos", ("Tarjetas (ideal en celular)", "Tabla detallada"),
                        key=f"pros_vista_{version}", horizontal=True)
        etiquetas = {i: f"{p['Empresa']} · {p['Ciudad']} · {p['Puntaje']}/100"
                     for i, p in enumerate(candidatos)}
        if modo == "Tarjetas (ideal en celular)":
            indice = st.selectbox("Revisar candidato", list(etiquetas), format_func=etiquetas.get,
                                  key=f"pros_revisar_{version}")
            elegido = candidatos[indice]
            with st.container(border=True):
                st.write(f"**{elegido['Empresa']}** · {elegido['Segmento']} · "
                         f"**Potencial {elegido['Puntaje']}/100** ({elegido['Prioridad']})")
                st.write(f"**Ciudad / zona:** {elegido['Ciudad']} · {elegido['Zona'] or 'Sin zona'} · "
                         f"**Dirección:** {elegido['Dirección'] or 'No publicada'}")
                st.write(f"**Teléfono comercial:** {elegido['Teléfono'] or 'No publicado'} · "
                         f"**Producto recomendado:** {elegido['Productos'] or 'Sin coincidencia en Inventario'}")
                st.caption(elegido["Motivo"])
                destino = pros.enlace_google_maps(elegido)
                if destino:
                    st.link_button("📍 Abrir ubicación en Google Maps", destino, width="stretch",
                                   help="Búsqueda externa: verifica el lugar antes de visitarlo.")
                fuente = pros.url_publica(elegido["URL_fuente"])
                if fuente:
                    st.link_button("Ver ficha de origen", fuente, width="stretch")
            todos = st.checkbox("Seleccionar todos los candidatos", key=f"pros_todos_{version}")
            elegidos_ids = st.multiselect("Elegir negocios para guardar (puedes buscar por nombre)",
                                          list(etiquetas), format_func=etiquetas.get,
                                          disabled=todos, key=f"pros_seleccion_{version}")
            elegidos = candidatos if todos else [candidatos[i] for i in elegidos_ids]
        else:
            columnas_vista = ("Empresa", "Segmento", "Prioridad", "Puntaje", "Motivo", "Productos",
                              "Ciudad", "Zona", "Distancia_km", "Dirección", "Teléfono", "WhatsApp", "Correo",
                              "Sitio_web", "Redes", "URL_fuente")
            vista = pd.DataFrame([{col: p[col] for col in columnas_vista} for p in candidatos])
            vista["Google_Maps"] = [pros.enlace_google_maps(p) for p in candidatos]
            vista.insert(0, "Guardar", True)
            seleccion = st.data_editor(
                vista, hide_index=True, width="stretch", num_rows="fixed",
                disabled=list(columnas_vista) + ["Google_Maps"],
                column_config={"Sitio_web": st.column_config.LinkColumn("Sitio web"),
                               "Redes": st.column_config.LinkColumn("Redes comerciales"),
                               "URL_fuente": st.column_config.LinkColumn("Ficha de origen"),
                               "Google_Maps": st.column_config.LinkColumn("Google Maps")},
                key=f"pros_editor_{version}")
            elegidos = [p for i, p in enumerate(candidatos) if bool(seleccion.iloc[i]["Guardar"])]
        if st.button(f"💾 Guardar {len(elegidos)} prospecto(s)", disabled=not elegidos, type="primary",
                     width="stretch", key="pros_guardar"):
            try:
                agregados, repetidos, clientes = guardar_prospectos(elegidos)
                st.session_state.pop("pros_resultados", None)
                flash(f"Se agregaron {agregados} prospectos. Omitidos: {repetidos} ya guardados, "
                      f"{clientes} ya clientes.")
                if agregados:
                    respaldo = _respaldo_automatico("Respaldo automático: prospección comercial", intervalo=False)
                    if respaldo:
                        flash(respaldo[1], respaldo[0])
                st.rerun()
            except Exception as error:
                st.error(f"No se pudo guardar la lista: {error}")

    st.markdown("### 📋 Lista para contactar")
    try:
        guardados = cargar_prospectos()
    except Exception as error:
        st.error(f"No se pudo leer la hoja Prospectos: {error}")
        return
    if guardados.empty:
        st.info("Aún no hay prospectos guardados. Busca negocios, agrégalos manualmente o importa un CSV.")
        return

    st.caption("Las fichas se guardan en la hoja local `Prospectos` del Excel; exporta una copia para "
               "resguardar tus datos, especialmente si el servidor se reinicia.")
    st.download_button("📥 Exportar todos los prospectos (CSV)", data=csv_prospectos(guardados),
                       file_name=f"prospectos_nemet_todos_{ahora_local():%Y-%m-%d}.csv", mime="text/csv",
                       key="pros_descargar_todos", width="stretch")
    pendientes = pros.seguimientos_pendientes(guardados.to_dict("records"), hoy=ahora_local().date())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Prospectos", len(guardados))
    c2.metric("Con contacto comercial", int((guardados["Teléfono"].ne("") |
                                              guardados["Correo"].ne("") | guardados["WhatsApp"].ne("")).sum()))
    c3.metric("Seguimientos para hoy o vencidos", len(pendientes))
    c4.metric("Clientes logrados", int(guardados["Estado"].eq("Cliente").sum()))
    if pendientes:
        st.warning(f"📅 Tienes {len(pendientes)} seguimiento(s) pendiente(s). La agenda se revisa "
                   "al entrar al módulo; no envía notificaciones automáticas.")
        with st.expander("Ver agenda pendiente"):
            st.dataframe(pd.DataFrame(pendientes)[["Empresa", "Ciudad", "Próximo_seguimiento", "Estado",
                                                   "Teléfono", "WhatsApp"]], hide_index=True, width="stretch")
    f1, f2, f3, f4 = st.columns([2, 1, 1, 2])
    with f1:
        texto_busqueda = st.text_input("Filtrar empresa o ciudad", key="pros_filtro_texto")
    with f2:
        prioridades = st.multiselect("Prioridad", ("Alta", "Media", "Exploratoria"), key="pros_filtro_prioridad")
    with f3:
        estados = st.multiselect("Estado", pros.ESTADOS, key="pros_filtro_estado")
    with f4:
        giros = st.multiselect("Giro", sorted(guardados["Segmento"].unique()), key="pros_filtro_giro")
    c_ciudad, c_zona = st.columns(2)
    with c_ciudad:
        ciudad_filtro = st.selectbox("Ciudad", ["Todas"] + sorted(guardados["Ciudad"].unique()),
                                    key="pros_filtro_ciudad")
    with c_zona:
        zonas_base = guardados if ciudad_filtro == "Todas" else guardados[guardados["Ciudad"] == ciudad_filtro]
        zonas_disponibles = sorted({zona or "Sin zona declarada" for zona in zonas_base["Zona"]})
        zona_filtro = st.selectbox("Zona / colonia", ["Todas"] + zonas_disponibles, key="pros_filtro_zona")
    solo_pendientes = st.checkbox("Mostrar solo seguimientos para hoy o vencidos", key="pros_solo_pendientes")
    filtrados = guardados.copy()
    if texto_busqueda:
        filtrados = filtrados[filtrados["Empresa"].str.contains(texto_busqueda, case=False, regex=False)
                              | filtrados["Ciudad"].str.contains(texto_busqueda, case=False, regex=False)]
    if prioridades:
        filtrados = filtrados[filtrados["Prioridad"].isin(prioridades)]
    if estados:
        filtrados = filtrados[filtrados["Estado"].isin(estados)]
    if giros:
        filtrados = filtrados[filtrados["Segmento"].isin(giros)]
    if ciudad_filtro != "Todas":
        filtrados = filtrados[filtrados["Ciudad"] == ciudad_filtro]
    if zona_filtro != "Todas":
        filtrados = filtrados[filtrados["Zona"] == ("" if zona_filtro == "Sin zona declarada" else zona_filtro)]
    if solo_pendientes:
        filtrados = filtrados[filtrados["Clave"].isin({p["Clave"] for p in pendientes})]
    filtrados = filtrados.assign(_orden=pd.to_numeric(filtrados["Puntaje"], errors="coerce").fillna(0))
    filtrados = filtrados.sort_values(["_orden", "Empresa"], ascending=[False, True]).drop(columns="_orden")
    if filtrados.empty:
        st.info("No hay prospectos que coincidan con esos filtros.")
        return
    st.markdown("#### 🗺️ Ciudad → zona → negocios")
    geo = pros.puntos_mapa(filtrados.to_dict("records"))
    if geo:
        st.map(pd.DataFrame(geo), latitude="lat", longitude="lon", color="color", size=35, height=400)
        st.caption(f"{len(geo)} de {len(filtrados)} prospectos tienen coordenadas comerciales públicas. "
                   "Naranja: alto · verde: medio · gris: exploratorio. El mapa no geocodifica domicilios.")
    else:
        st.info("Estas fichas no incluyen coordenadas verificables. Siguen disponibles en la lista; "
                "puedes consultar su dirección comercial y ficha de origen.")
    resumen_zonas = (filtrados.assign(Zona=filtrados["Zona"].replace("", "Sin zona declarada"))
                     .groupby(["Ciudad", "Zona"], as_index=False).size().rename(columns={"size": "Negocios"}))
    with st.expander("Ver negocios por ciudad y zona"):
        st.dataframe(resumen_zonas, hide_index=True, width="stretch")
    visibles = ("Empresa", "Segmento", "Prioridad", "Puntaje", "Productos", "Ciudad", "Zona",
                "Distancia_km", "Dirección", "Teléfono", "WhatsApp", "Correo", "Sitio_web", "Redes",
                "URL_fuente", "Fecha_alta", "Estado", "Próximo_seguimiento", "Notas")
    with st.expander("Ver tabla detallada (desliza horizontalmente en celular)"):
        tabla = filtrados[list(visibles)].copy()
        tabla["Google_Maps"] = [pros.enlace_google_maps(p) for p in filtrados.to_dict("records")]
        st.dataframe(tabla, hide_index=True, width="stretch",
                     column_config={"Sitio_web": st.column_config.LinkColumn("Sitio web"),
                                    "Redes": st.column_config.LinkColumn("Redes comerciales"),
                                    "URL_fuente": st.column_config.LinkColumn("Ficha de origen"),
                                    "Google_Maps": st.column_config.LinkColumn("Google Maps")})
    st.download_button("⬇️ Descargar lista filtrada (CSV)", data=csv_prospectos(filtrados[list(pros.COLUMNAS)]),
                       file_name=f"prospectos_nemet_{ahora_local():%Y-%m-%d}.csv", mime="text/csv",
                       key="pros_descargar", width="stretch")
    st.caption("La exportación incluye la fuente y la fecha de alta. © OpenStreetMap contributors para las fichas OSM; "
               "verifica los contactos antes de enviar comunicaciones y respeta las reglas locales de privacidad.")

    st.markdown("#### 🔎 Abrir ficha y dar seguimiento")
    fichas = {p["Clave"]: p for p in filtrados.to_dict("records")}
    clave = st.selectbox("Negocio", list(fichas), format_func=lambda k: f"{fichas[k]['Empresa']} · {fichas[k]['Ciudad']}",
                         key="pros_elegido")
    ficha = fichas[clave]
    with st.container(border=True):
        st.subheader(ficha["Empresa"])
        st.write(f"**Giro:** {ficha['Segmento']} · **Potencial:** {ficha['Prioridad']} "
                 f"({ficha['Puntaje']}/100) · **Estado:** {ficha['Estado']}")
        ubicacion = f"**Ubicación:** {ficha['Ciudad']} · {ficha['Zona'] or 'Zona no publicada'}"
        if ficha["Distancia_km"]:
            ubicacion += f" · {ficha['Distancia_km']} km del centro consultado"
        st.write(ubicacion)
        st.write(f"**Dirección comercial:** {ficha['Dirección'] or 'No publicada'} · "
                 f"**Teléfono:** {ficha['Teléfono'] or 'No publicado'} · "
                 f"**WhatsApp:** {ficha['WhatsApp'] or 'No publicado expresamente'}")
        mapa_google = pros.enlace_google_maps(ficha)
        if mapa_google:
            st.link_button("📍 Abrir en Google Maps", mapa_google, width="stretch",
                           help="Abre una búsqueda externa por dirección o coordenadas, no una ubicación verificada.")
        else:
            st.caption("Google Maps no disponible: falta dirección con ciudad o coordenadas comerciales válidas.")
        st.write(f"**Correo:** {ficha['Correo'] or 'No publicado'} · "
                 f"**Productos NEMET sugeridos:** {ficha['Productos'] or 'Sin coincidencia en inventario'}")
        st.write(f"**Productos/actividad del negocio:** {ficha['Productos_negocio'] or 'Sin dato público'}")
        st.caption(f"{ficha['Motivo']} · Fuente: {ficha['Fuente']} · "
                   f"Tamaño: {ficha['Tamaño'] or 'Sin dato'} · "
                   f"Clientela: {ficha['Tipo_clientela'] or 'Sin dato'} · "
                   f"Actividad: {ficha['Última_actividad'] or 'Sin dato verificado'}")
        if not ficha["Fuente_perfil"] and any(ficha[c] for c in ("Tamaño", "Tipo_clientela", "Última_actividad")):
            st.info("Tamaño, clientela o actividad importados SIN fuente de verificación: no afectan el puntaje.")
        enlaces = []
        for titulo, valor in (("Ver ficha de origen", ficha["URL_fuente"]),
                              ("Sitio web", ficha["Sitio_web"]), ("Redes comerciales", ficha["Redes"])):
            destino = pros.url_publica(valor)
            if destino:
                enlaces.append((titulo, destino))
        if enlaces:
            columnas_enlaces = st.columns(len(enlaces))
            for contenedor, (titulo, url) in zip(columnas_enlaces, enlaces):
                with contenedor:
                    st.link_button(titulo, url, width="stretch")
        if ficha["Estado"] == "No contactar":
            st.warning("Este negocio está marcado como ‘No contactar’. Respeta su decisión.")

    if ficha["Estado"] != "No contactar":
        with st.expander("💬 Preparar mensaje de WhatsApp (sin envío automático)"):
            firma = st.text_input("Tu nombre para el borrador", value=SESION.get("nombre") or "",
                                  max_chars=70, key="pros_firma_whatsapp")
            opciones_producto = [p.strip() for p in ficha["Productos"].split(";") if p.strip() in catalogo]
            if ficha["Productos"] and not opciones_producto:
                st.warning("La sugerencia guardada ya no está en Inventario. Usa el mensaje general "
                           "o sincroniza el catálogo antes de contactar.")
            producto_mensaje = st.selectbox("Producto a mencionar (opcional)",
                                            ["Mensaje general"] + opciones_producto,
                                            key=f"pros_prod_mensaje_{clave}")
            borrador = pros.preparar_mensaje(
                firma, ficha, producto_elegido=producto_mensaje if producto_mensaje != "Mensaje general" else "")
            st.code(borrador, language=None)
            enlace_wa = pros.enlace_whatsapp(ficha, borrador)
            if enlace_wa:
                st.link_button("💬 Abrir borrador en WhatsApp (revisar antes de enviar)", enlace_wa,
                               width="stretch")
            else:
                st.info("No hay WhatsApp comercial publicado y válido. Puedes copiar el borrador "
                        "para otro canal de contacto autorizado; no se supone que el teléfono tenga WhatsApp.")

    original = tuple(ficha[c] for c in pros.CAMPOS_SEGUIMIENTO)
    # Congelar la versión que vio ESTA sesión: rereleer Excel en un rerun no autoriza
    # sobrescribir las notas ni el próximo seguimiento de otra persona.
    clave_base = f"pros_base_{clave}"
    if clave_base not in st.session_state:
        st.session_state[clave_base] = original
    if st.session_state[clave_base] != original:
        st.warning("Este prospecto cambió en otra sesión. Recarga la ficha antes de guardar.")
    widgets = ("estado", "notas", "fecha", "proximo", "productos", "tamano", "clientela", "actividad", "fuente")
    if st.button("🔄 Recargar ficha", key=f"pros_recargar_{clave}"):
        st.session_state[clave_base] = original
        for widget in widgets:
            st.session_state.pop(f"pros_{widget}_{clave}", None)
        st.rerun()

    def fecha_de(campo):
        try:
            return date.fromisoformat(ficha[campo][:10]) if ficha[campo] else None
        except ValueError:
            return None

    with st.form(f"form_seguimiento_{clave}"):
        c_estado, c_ultimo, c_proximo = st.columns(3)
        with c_estado:
            nuevo_estado = st.selectbox("Estado comercial", pros.ESTADOS,
                                        index=pros.ESTADOS.index(ficha["Estado"]) if ficha["Estado"] in pros.ESTADOS else 0,
                                        key=f"pros_estado_{clave}")
        with c_ultimo:
            fecha_contacto = st.date_input("Último contacto", value=fecha_de("Último_contacto"),
                                           key=f"pros_fecha_{clave}")
        with c_proximo:
            proximo = st.date_input("Próximo seguimiento", value=fecha_de("Próximo_seguimiento"),
                                    key=f"pros_proximo_{clave}")
        notas = st.text_area("Notas de la conversación (máximo 500 caracteres)", value=ficha["Notas"],
                             max_chars=500, key=f"pros_notas_{clave}")
        with st.expander("Perfil comercial opcional (solo con evidencia)"):
            st.caption("Estos datos influyen en el puntaje solo si indicas la fuente pública o una nota de "
                       "verificación. La fecha de edición de OpenStreetMap NO demuestra actividad reciente.")
            productos_negocio = st.text_input("Productos/actividad declarados por el negocio",
                                               value=ficha["Productos_negocio"], max_chars=250,
                                               key=f"pros_productos_{clave}")
            c_tamano, c_clientela, c_actividad = st.columns(3)
            with c_tamano:
                tamano = st.selectbox("Tamaño comprobado", pros.TAMANOS,
                                      index=pros.TAMANOS.index(ficha["Tamaño"]) if ficha["Tamaño"] in pros.TAMANOS else 0,
                                      key=f"pros_tamano_{clave}")
            with c_clientela:
                clientela = st.selectbox("Tipo de clientela comprobado", pros.CLIENTELAS,
                                         index=pros.CLIENTELAS.index(ficha["Tipo_clientela"])
                                         if ficha["Tipo_clientela"] in pros.CLIENTELAS else 0,
                                         key=f"pros_clientela_{clave}")
            with c_actividad:
                actividad = st.date_input("Última actividad comercial comprobada",
                                          value=fecha_de("Última_actividad"), key=f"pros_actividad_{clave}")
            fuente_perfil = st.text_input("Fuente de la verificación (URL pública o nota)",
                                          value=ficha["Fuente_perfil"], max_chars=250,
                                          key=f"pros_fuente_{clave}")
        actualizar = st.form_submit_button("💾 Guardar seguimiento", key="pros_actualizar")
    if actualizar:
        try:
            cambios = {"Estado": nuevo_estado, "Notas": notas,
                       "Último_contacto": fecha_contacto.isoformat() if fecha_contacto else "",
                       "Próximo_seguimiento": proximo.isoformat() if proximo else "",
                       "Productos_negocio": productos_negocio,
                       "Tamaño": "" if tamano == "Sin dato" else tamano,
                       "Tipo_clientela": "" if clientela == "Sin dato" else clientela,
                       "Última_actividad": actividad.isoformat() if actividad else "",
                       "Fuente_perfil": fuente_perfil}
            cambio = guardar_seguimiento_prospecto(clave, st.session_state[clave_base], cambios)
            if cambio:
                st.session_state[clave_base] = tuple(pros.proteger_excel(cambios[c]) for c in pros.CAMPOS_SEGUIMIENTO)
            flash("Seguimiento actualizado." if cambio else "No hubo cambios en el seguimiento.",
                  "success" if cambio else "info")
            if cambio:
                respaldo = _respaldo_automatico("Respaldo automático: seguimiento de prospecto", intervalo=False)
                if respaldo:
                    flash(respaldo[1], respaldo[0])
            st.rerun()
        except (ConflictoGuardado, ValueError) as error:
            st.error(str(error))
        except Exception as error:
            st.error(f"No se pudo actualizar el seguimiento: {error}")


# ==========================================
# ACCESO (se ejecuta antes de tocar cualquier dato)
# ==========================================
# Cualquier fallo de la base de usuarios se explica en pantalla con su motivo real:
# Streamlit reemplaza el mensaje del traceback por «original error message is redacted».
try:
    CONN = conexion_usuarios(ruta_db_usuarios())
    SESION = ejecutar_gate_acceso()
except (sqlite3.Error, OSError) as error:
    pantalla_error_base(error)
    st.stop()

# ==========================================
# ESTADO INICIAL
# ==========================================
if "inventario" not in st.session_state or st.session_state["inventario"].empty:
    st.session_state["inventario"] = cargar_inventario()
if "clientes" not in st.session_state:
    # El directorio de clientes solo se carga con permiso al módulo (evita exponerlo a otros roles).
    if auth.puede(SESION["rol"], "clientes"):
        st.session_state["clientes"] = cargar_clientes()
    else:
        st.session_state["clientes"] = pd.DataFrame(columns=COLUMNAS_CLIENTES)
if "carrito_comercial" not in st.session_state:
    st.session_state["carrito_comercial"] = carrito_vacio()
if "carrito_area" not in st.session_state:
    st.session_state["carrito_area"] = carrito_vacio(por_area=True)
st.session_state.setdefault("version_editores", 0)

df_inv = st.session_state["inventario"]
df_clientes = st.session_state["clientes"]

# ==========================================
# MENÚ Y NAVEGACIÓN
# ==========================================
_logo_sidebar = logo_marca()
if _logo_sidebar is not None:
    st.sidebar.image(_logo_sidebar, width=160)
st.sidebar.title("📂 Menú Principal")
menu = st.sidebar.selectbox("Navegación", modulos_visibles(SESION["rol"]))
st.sidebar.divider()
if auth.puede(SESION["rol"], "respaldo"):
    if st.sidebar.button("☁️ Respaldar datos en GitHub"):
        with st.spinner("Respaldando Excel y base de usuarios..."):
            guardar_cambios_github("Respaldo manual de datos (Excel + usuarios)")
    st.sidebar.caption("Respaldo automático solo con Secrets `[git]` que apunten a un repo PRIVADO verificable. "
                       "Si es público o no se puede comprobar, se bloquea la subida de Excel y usuarios. "
                       "Este botón fuerza un respaldo manual sujeto a la misma verificación.")
    st.sidebar.divider()
bloque_usuario_sidebar(CONN, auth.secreto_sesion(CONN, obtener_secret("auth", "session_secret")), minutos_de_sesion())

mostrar_flash()
estilos_marca()

# El dashboard abre con la portada de marca (ya incluye logo y lema); si no se muestra,
# o en cualquier otra vista, entra la franja compacta de marca.
_portada_activa = menu == "📊 Dashboard & Resumen" and portada_marca()
if not _portada_activa:
    cabecera_marca()

if menu == "📊 Dashboard & Resumen":
    requiere_modulo("dashboard")
    st.title("🧪 Sistema Maestro NEMET")
    st.subheader("Panel General de Control y Logística")

    if st.button("🔄 Sincronizar Datos con Excel"):
        recargar_datos()
        st.session_state["version_editores"] += 1
        st.session_state.pop("inv_conflicto", None)
        st.session_state.pop("cli_conflicto", None)
        flash("¡Datos actualizados desde el archivo Excel!")
        st.rerun()

    stock = pd.to_numeric(df_inv.get("StockActual"), errors="coerce").fillna(0) if "StockActual" in df_inv.columns else None
    precio = pd.to_numeric(df_inv.get("PrecioPublicoIVA"), errors="coerce").fillna(0) if "PrecioPublicoIVA" in df_inv.columns else None
    valor_total = float((stock * precio).sum()) if stock is not None and precio is not None else 0.0
    por_reabastecer = int((stock <= pd.to_numeric(df_inv.get("StockMinimo"), errors="coerce").fillna(0)).sum()) \
        if stock is not None and "StockMinimo" in df_inv.columns else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total de SKUs Registrados", len(df_inv))
    if auth.puede(SESION["rol"], "clientes"):
        col2.metric("Clientes Registrados", len(df_clientes))
    else:
        col2.metric("Clientes Registrados", "🔒",
                    help="Tu rol no tiene acceso al directorio de clientes.")
    col3.metric("Valor Total Inventario ($)", f"${valor_total:,.2f} MXN")
    col4.metric("SKUs por Reabastecer", por_reabastecer)

    if "SKU" in df_inv.columns:
        duplicados = sorted(df_inv.loc[df_inv["SKU"].duplicated() & df_inv["SKU"].ne(""), "SKU"].unique())
        if duplicados:
            st.warning(f"⚠️ Hay SKUs repetidos en el inventario ({', '.join(duplicados)}). "
                       "Asigna códigos únicos para evitar confusiones en las cotizaciones.")

    st.markdown("### 🔍 Vista Rápida del Inventario Sincronizado")
    st.dataframe(df_inv, width="stretch")

elif menu == "📦 Control de Inventario y Edición":
    requiere_modulo("inventario")
    st.subheader("Gestión, Entradas, Salidas y Edición Directa")
    st.caption("Captura StockInicial, Entradas, Salidas, StockMinimo, PrecioCompra y PrecioPublicoIVA. "
               "Las columnas StockActual, AlertaStock, PrecioBaseSinIVA, IVA 16% y ValorInventario se calculan al guardar.")
    columnas_bloqueadas = [c for c in COLUMNAS_DERIVADAS_INV if c in df_inv.columns]
    df_editado = st.data_editor(
        df_inv, num_rows="dynamic", width="stretch", disabled=columnas_bloqueadas,
        key=f"editor_inv_{st.session_state['version_editores']}",
    )

    def _guardar_inv(forzar=False):
        guardar_inventario(df_editado, forzar=forzar)
        st.session_state["inventario"] = cargar_inventario()
        st.session_state["version_editores"] += 1
        st.session_state.pop("inv_conflicto", None)
        flash("¡Inventario actualizado y guardado exitosamente!")
        respaldo = _respaldo_automatico("Respaldo automático: inventario")
        if respaldo:
            flash(respaldo[1], respaldo[0])
        st.rerun()

    if st.button("💾 Guardar Cambios en Excel"):
        try:
            _guardar_inv()
        except ConflictoGuardado as e:
            st.session_state["inv_conflicto"] = True
            st.error(str(e))
        except Exception as e:
            st.error(f"Error al guardar: {e}")

    if st.session_state.get("inv_conflicto"):
        st.warning("⚠️ Conflicto de guardado pendiente: otra sesión modificó el archivo mientras editabas.")
        if st.button("💾 Sobrescribir con mis cambios", type="primary"):
            try:
                _guardar_inv(forzar=True)
            except Exception as e:
                st.error(f"Error al guardar: {e}")

elif menu == "👥 Gestión de Clientes":
    requiere_modulo("clientes")
    st.subheader("👥 Base de Datos y Directorio de Clientes")
    st.markdown("Agrega, edita o elimina la información de tus clientes directamente. Los cambios se guardarán en la pestaña `Clientes` de tu Excel.")

    df_cli_editado = st.data_editor(
        df_clientes, num_rows="dynamic", width="stretch",
        key=f"editor_clientes_{st.session_state['version_editores']}",
    )

    def _guardar_cli(forzar=False):
        guardar_clientes(df_cli_editado, forzar=forzar)
        st.session_state["clientes"] = cargar_clientes()
        st.session_state["version_editores"] += 1
        st.session_state.pop("cli_conflicto", None)
        flash("¡Base de datos de clientes guardada exitosamente en Excel!")
        respaldo = _respaldo_automatico("Respaldo automático: clientes")
        if respaldo:
            flash(respaldo[1], respaldo[0])
        st.rerun()

    if st.button("💾 Guardar Base de Datos de Clientes"):
        try:
            _guardar_cli()
        except ConflictoGuardado as e:
            st.session_state["cli_conflicto"] = True
            st.error(str(e))
        except Exception as e:
            st.error(f"Error al guardar clientes: {e}")

    if st.session_state.get("cli_conflicto"):
        st.warning("⚠️ Conflicto de guardado pendiente: otra sesión modificó el archivo mientras editabas.")
        if st.button("💾 Sobrescribir clientes con mis cambios", type="primary"):
            try:
                _guardar_cli(forzar=True)
            except Exception as e:
                st.error(f"Error al guardar clientes: {e}")

elif menu == "🎯 Prospección Comercial":
    requiere_modulo("prospeccion")
    panel_prospeccion(df_inv)

elif menu == "📏 Cotizador por Área y Milimétrico":
    requiere_modulo("cotizador_area")
    st.subheader("Cotizador por Área, Espesor y Proporción de Mezcla")

    with st.expander("📦 Consultar Inventario General"):
        st.dataframe(df_inv, width="stretch")

    if df_inv.empty:
        st.warning("No hay inventario cargado; revisa el archivo Excel.")
        st.stop()

    col_desc, col_sku, col_pres = columnas_inventario(df_inv)
    catalogo = cargar_catalogo_rendimientos()
    if not catalogo:
        st.warning(f"No se pudo leer la hoja `{HOJA_CATALOGO}`; se usará un rendimiento de {RENDIMIENTO_DEFAULT} kg/m²/mm para todos los productos.")

    # Solo se cotizan por área los productos con presentación en kg/L y con rendimiento en el catálogo
    kg_por_fila = df_inv[col_pres].apply(parsear_presentacion_kg)
    familias = []
    for descripcion in df_inv[col_desc].dropna().unique():
        if not str(descripcion).strip():
            continue
        if catalogo and buscar_en_catalogo(descripcion, catalogo) is None:
            continue
        if kg_por_fila[df_inv[col_desc] == descripcion].notna().any():
            familias.append(descripcion)
    if not familias:
        st.error(f"Ningún producto tiene presentación en kg/L y rendimiento definido en `{HOJA_CATALOGO}`.")
        st.stop()

    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        cliente_area, correo_area = seleccionar_cliente("area")
    with col_a2:
        ancho = st.number_input("Ancho (metros)", min_value=0.1, value=3.0, step=0.1)
        largo = st.number_input("Largo (metros)", min_value=0.1, value=4.0, step=0.1)
        area_total = ancho * largo
    with col_a3:
        espesor_mm = st.number_input("Espesor (mm)", min_value=0.1, value=1.0, step=0.5)
        prod_familia = st.selectbox("Seleccionar Línea de Producto", familias)

    info_producto = buscar_en_catalogo(prod_familia, catalogo) or {}
    rendimiento = info_producto.get("rendimiento", RENDIMIENTO_DEFAULT)
    kg_necesarios = area_total * espesor_mm * rendimiento

    st.info(f"📐 **Área Total:** {area_total:.2f} m² | **Rendimiento:** {rendimiento:g} kg/m² por mm | "
            f"**Material necesario:** **{kg_necesarios:.2f} kg**")
    if not info_producto:
        st.caption(f"Este producto no está en `{HOJA_CATALOGO}`; se usa el rendimiento por defecto ({RENDIMIENTO_DEFAULT}).")
    elif info_producto.get("prop_b", 0) > 0:
        prop_a, prop_b = info_producto["prop_a"], info_producto["prop_b"]
        kg_a = kg_necesarios * prop_a / (prop_a + prop_b)
        st.caption(f"🧪 Proporción de mezcla A:B = {prop_a:g}:{prop_b:g} → Parte A (resina): {kg_a:.2f} kg | "
                   f"Parte B (catalizador): {kg_necesarios - kg_a:.2f} kg")

    df_familia = df_inv[df_inv[col_desc] == prod_familia].copy()
    df_familia["Kg_Num"] = kg_por_fila[df_familia.index]
    df_familia = df_familia[df_familia["Kg_Num"].notna()].sort_values(by="Kg_Num", ascending=False)

    stock_por_sku = {}
    if "StockActual" in df_inv.columns:
        stock_norm = pd.to_numeric(df_inv["StockActual"], errors="coerce").fillna(0)
        stock_por_sku = {str(s).strip(): float(v) for s, v in zip(df_inv[col_sku], stock_norm)}

    resultados = []
    for _, fila in df_familia.iterrows():
        pres_kg = float(fila["Kg_Num"])
        precio_pub = float(valor_celda(fila, "PrecioPublicoIVA", 0.0))
        unidades = max(1, math.ceil(round(kg_necesarios / pres_kg, 6)))
        sku_fila = str(valor_celda(fila, col_sku))
        resultados.append({
            "SKU": sku_fila,
            "Presentación": str(fila[col_pres]),
            "Kg por unidad": pres_kg,
            "Precio Público": precio_pub,
            "Unidades": unidades,
            "Kg cubiertos": unidades * pres_kg,
            "Costo Total": unidades * precio_pub,
            "Stock": stock_por_sku.get(sku_fila.strip(), 0.0),
        })

    if resultados:
        optima = min(resultados, key=lambda x: (x["Costo Total"], x["Unidades"]))
        st.success(f"💡 **Recomendación Óptima:** Presentación de **{optima['Presentación']}** con **{optima['Unidades']} unidad(es)** "
                   f"({optima['Kg cubiertos']:.2f} kg) por **${optima['Costo Total']:,.2f} MXN**.")
        if optima["Unidades"] > optima["Stock"]:
            st.warning(f"⚠️ El stock actual de **{optima['SKU']}** es de {formato_cantidad(optima['Stock'])} unidad(es) "
                       f"y la recomendación pide {formato_cantidad(optima['Unidades'])}. "
                       "Considera reabastecer (o producir) antes de confirmar con el cliente.")
        with st.expander("Ver comparativa de todas las presentaciones"):
            st.dataframe(pd.DataFrame(resultados), width="stretch", hide_index=True)

        if st.button("➕ Agregar esta recomendación al Carrito"):
            agregar_al_carrito("carrito_area", {
                "SKU": optima["SKU"],
                "Descripcion": prod_familia,
                "Presentacion": optima["Presentación"],
                "Area_m2": round(area_total, 2),
                "Espesor_mm": espesor_mm,
                "Kg_Necesarios": round(kg_necesarios, 2),
                "Cantidad": optima["Unidades"],
                "Subtotal": optima["Costo Total"],
                "Detalle": (f"{prod_familia}\n{area_total:.2f} m² x {espesor_mm:g} mm ({kg_necesarios:.2f} kg)"
                            + (f"\n⚠️ Excede el stock actual: {formato_cantidad(optima['Stock'])} en almacén"
                               if optima["Unidades"] > optima["Stock"] else "")),
            })
            st.rerun()

    carrito_area = st.session_state["carrito_area"]
    if not carrito_area.empty:
        st.markdown("### 🛒 Carrito de Cotización por Área")
        st.dataframe(carrito_area.drop(columns=["Detalle"]), width="stretch", hide_index=True)

    bloque_acciones_cotizacion("carrito_area", carrito_area, cliente_area, correo_area,
                               "Cotización por Área y Sistemas Epóxicos")

elif menu == "📝 Cotizador Comercial Profesional":
    requiere_modulo("cotizador_comercial")
    st.subheader("Generador de Cotizaciones Comerciales")

    if df_inv.empty:
        st.warning("No hay inventario cargado; revisa el archivo Excel.")
        st.stop()

    col_desc, col_sku, col_pres = columnas_inventario(df_inv)
    cliente_comercial, correo_comercial = seleccionar_cliente("com")

    def etiqueta_producto(indice):
        fila = df_inv.loc[indice]
        return f"{fila[col_sku]} - {fila[col_desc]} ({fila[col_pres]}) - ${float(valor_celda(fila, 'PrecioPublicoIVA', 0)):,.2f}"

    # Se selecciona por posición (no por SKU) para que los SKUs repetidos no agreguen el producto equivocado
    indice_sel = st.selectbox("Seleccionar Producto", list(df_inv.index), format_func=etiqueta_producto)
    cant = st.number_input("Cantidad", min_value=1, value=1)

    if st.button("Agregar al Carrito Comercial"):
        fila = df_inv.loc[indice_sel]
        agregar_al_carrito("carrito_comercial", {
            "SKU": str(valor_celda(fila, col_sku)),
            "Descripcion": str(fila[col_desc]),
            "Presentacion": str(fila[col_pres]),
            "Cantidad": int(cant),
            "Subtotal": int(cant) * float(valor_celda(fila, "PrecioPublicoIVA", 0)),
        })
        st.rerun()

    carrito_comercial = st.session_state["carrito_comercial"]
    if not carrito_comercial.empty:
        st.markdown("### 🛒 Carrito Comercial")
        st.dataframe(carrito_comercial, width="stretch", hide_index=True)

    bloque_acciones_cotizacion("carrito_comercial", carrito_comercial, cliente_comercial, correo_comercial,
                               "Cotización Comercial")

elif menu == "📋 Historial de Cotizaciones (Folios)":
    requiere_modulo("historial")
    st.subheader("Historial y Auditoría de Cotizaciones")

    if auth.puede(SESION["rol"], "historial_borrar"):
        with st.expander("🗑️ Borrar Todo el Historial de Cotizaciones"):
            confirmar = st.checkbox("Confirmo que deseo borrar TODO el historial (esta acción no se puede deshacer)")
            if st.button("Borrar Historial", disabled=not confirmar, type="primary"):
                try:
                    escribir_hoja(pd.DataFrame(columns=COLUMNAS_HISTORIAL), HOJA_HISTORIAL)
                    for clave in ("carrito_area", "carrito_comercial"):
                        st.session_state.pop(f"{clave}_emitido", None)
                    auth.registrar_evento(CONN, "historial_borrado", actor=SESION["usuario"],
                                          actor_id=SESION["id"],
                                          detalle="Se borró todo el historial de cotizaciones.", zona=ZONA_HORARIA)
                    flash("¡Historial de cotizaciones limpiado exitosamente!")
                    respaldo = _respaldo_automatico("Respaldo automático: historial borrado")
                    if respaldo:
                        flash(respaldo[1], respaldo[0])
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al limpiar el historial: {e}")
    else:
        st.caption("🔒 Solo un administrador puede borrar el historial completo.")

    df_hist = cargar_historial()
    if df_hist.empty:
        st.info("Aún no hay cotizaciones registradas. El historial se crea automáticamente al emitir la primera cotización.")
    else:
        busqueda = st.text_input("🔍 Buscar por Folio o Cliente:")
        if busqueda:
            df_hist = df_hist[
                df_hist["Cliente"].astype(str).str.contains(busqueda, case=False, na=False, regex=False) |
                df_hist["Folio"].astype(str).str.contains(busqueda, case=False, na=False, regex=False)
            ]
        st.dataframe(df_hist, width="stretch", hide_index=True)
        st.metric("Total de Cotizaciones Emitidas", len(df_hist))

elif menu == "🛡️ Administración de Usuarios":
    requiere_modulo("admin_usuarios")
    panel_usuarios(CONN)

# Pie de marca (todas las vistas)
st.divider()
st.caption(f"© {ahora_local().year} NEMET · {LEMA_NEMET} · Materiales epóxicos y acabados arquitectónicos — Hermosillo, Sonora")
