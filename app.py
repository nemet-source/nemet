import math
import os
import re
import smtplib
import unicodedata
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import numpy as np
import pandas as pd
import streamlit as st
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

# Configuración de la página (debe ser el primer comando de Streamlit)
st.set_page_config(
    page_title="Sistema Maestro NEMET",
    page_icon="🧪",
    layout="wide"
)

# ==========================================
# CONSTANTES
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_FILE = os.path.join(BASE_DIR, "Sistema_Inventario_NEMET_Final.xlsx")

HOJA_INVENTARIO = "Inventario"
HOJA_CLIENTES = "Clientes"
HOJA_HISTORIAL = "Historial_Cotizaciones"
HOJA_CATALOGO = "Cat_Productos"

TITULO_INVENTARIO = "CONTROL DE INVENTARIO Y FACTURACIÓN - NEMET"
TASA_IVA = 0.16
RENDIMIENTO_DEFAULT = 1.2  # kg por m² por mm, si el producto no está en Cat_Productos

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


def flash(mensaje, tipo="success"):
    """Guarda un mensaje para mostrarlo después de un st.rerun()."""
    st.session_state["_flash"] = (tipo, mensaje)


def mostrar_flash():
    flash_msg = st.session_state.pop("_flash", None)
    if flash_msg:
        tipo, mensaje = flash_msg
        getattr(st, tipo, st.info)(mensaje)


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
def guardar_cambios_github(mensaje="Actualización automática de datos"):
    """Hace commit y push del Excel al repositorio. Requiere secrets [git] token y repo."""
    token = obtener_secret("git", "token")
    repo_name = obtener_secret("git", "repo")
    if not token or not repo_name:
        st.warning("Respaldo en GitHub no configurado: agrega `[git]` con `token` y `repo` en los Secrets de Streamlit.")
        return False
    try:
        import git  # Import perezoso: GitPython falla al importarse si no existe el binario git

        repo = git.Repo(BASE_DIR, search_parent_directories=True)
        with repo.config_writer() as git_config:
            git_config.set_value("user", "name", "Streamlit Bot")
            git_config.set_value("user", "email", "bot@streamlit.app")

        ruta_excel = os.path.relpath(EXCEL_FILE, repo.working_tree_dir)
        repo.index.add([ruta_excel])
        hay_cambios = bool(repo.index.diff("HEAD", paths=[ruta_excel]))
        if hay_cambios:
            repo.index.commit(mensaje)
        try:
            rama = repo.active_branch.name
        except TypeError:  # HEAD desprendido (detached)
            rama = "main"

        # El token solo se usa en esta llamada; no se guarda en .git/config.
        # Se empuja siempre para subir también commits previos que no se hayan podido enviar.
        repo.git.push(f"https://{token}@github.com/{repo_name}.git", f"HEAD:{rama}")
        if hay_cambios:
            st.success(f"¡Datos guardados y respaldados en GitHub (rama `{rama}`) correctamente!")
        else:
            st.info(f"El Excel no tenía cambios nuevos; el repositorio (rama `{rama}`) ya está al día.")
        return True
    except Exception as e:
        st.error(f"Error al sincronizar con GitHub: {str(e).replace(token, '***')}")
        return False


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
    """Reemplaza una hoja del Excel conservando las demás."""
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


def guardar_inventario(df_editado):
    df_final = quitar_filas_vacias(df_editado, ["SKU", "Descripcion"])
    df_final = recalcular_columnas_derivadas(df_final)
    if "SKU" in df_final.columns:
        df_final["SKU"] = df_final["SKU"].fillna("").astype(str).str.strip()
    escribir_hoja(df_final, HOJA_INVENTARIO, startrow=1, titulo=TITULO_INVENTARIO)


def guardar_clientes(df_editado):
    escribir_hoja(quitar_filas_vacias(df_editado, ["Nombre", "Empresa", "Correo"]), HOJA_CLIENTES)


# ==========================================
# FOLIOS, HISTORIAL, PDF Y CORREO
# ==========================================
def obtener_siguiente_folio():
    """Siguiente consecutivo del año en curso (COT-AAAA-NNN) según el mayor folio ya registrado."""
    anio_actual = datetime.now().year
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
    """Agrega la cotización al historial. Devuelve (ok, mensaje_error)."""
    try:
        df_hist = cargar_historial()
        nueva_fila = pd.DataFrame({
            "Folio": [folio],
            "Fecha": [datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            "Cliente": [cliente],
            "Detalle_Productos": [", ".join(describir_item(fila) for _, fila in items_carrito.iterrows())],
            "Total": [float(total_general)],
        })
        df_hist = nueva_fila if df_hist.empty else pd.concat([df_hist, nueva_fila], ignore_index=True)
        escribir_hoja(df_hist, HOJA_HISTORIAL)
        return True, ""
    except Exception as e:
        return False, str(e)


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
    for linea in (f"Folio: {folio}", f"Cliente: {cliente}", f"Fecha: {datetime.now():%Y-%m-%d}", titulo_detalle):
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
    ok, error = registrar_cotizacion_en_excel(folio, cliente, items, total)
    if ok:
        st.session_state[f"{clave}_emitido"] = {"firma": firma_carrito(items, cliente), "folio": folio}
        st.session_state[f"{clave}_msg"] = ("success", f"Cotización **{folio}** registrada en el historial.")
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

    col_b1, col_b2, col_b3 = st.columns(3)
    with col_b1:
        st.download_button("📄 Descargar PDF", data=pdf_bytes, file_name=f"{folio}.pdf", mime="application/pdf",
                           disabled=items.empty, key=f"{clave}_pdf",
                           on_click=registrar_si_es_necesario, args=(clave, folio, cliente, items, total))
    with col_b2:
        if st.button("📧 Enviar por Correo", disabled=items.empty, key=f"{clave}_mail"):
            try:
                enviar_cotizacion_por_correo(correo, cliente, folio, total, pdf_bytes)
                st.success(f"¡Correo enviado exitosamente a {correo}!")
                registrar_si_es_necesario(clave, folio, cliente, items, total)
                mensaje = st.session_state.pop(f"{clave}_msg", None)
                if mensaje:
                    getattr(st, mensaje[0])(mensaje[1])
            except Exception as e:
                st.error(f"Error al enviar correo: {e}")
    with col_b3:
        if st.button("🗑️ Limpiar Carrito", disabled=items.empty, key=f"{clave}_clear"):
            st.session_state[clave] = carrito_vacio(por_area=(clave == "carrito_area"))
            st.rerun()


# ==========================================
# ESTADO INICIAL
# ==========================================
if "inventario" not in st.session_state or st.session_state["inventario"].empty:
    st.session_state["inventario"] = cargar_inventario()
if "clientes" not in st.session_state:
    st.session_state["clientes"] = cargar_clientes()
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
st.sidebar.title("📂 Menú Principal")
menu = st.sidebar.selectbox("Navegación", [
    "📊 Dashboard & Resumen",
    "📦 Control de Inventario y Edición",
    "👥 Gestión de Clientes",
    "📏 Cotizador por Área y Milimétrico",
    "📝 Cotizador Comercial Profesional",
    "📋 Historial de Cotizaciones (Folios)"
])
st.sidebar.divider()
if st.sidebar.button("☁️ Respaldar Excel en GitHub"):
    guardar_cambios_github()
st.sidebar.caption("Sube el Excel al repositorio para no perder los datos al reiniciarse la app. "
                   "Requiere los Secrets `[git]` (token y repo).")

mostrar_flash()

if menu == "📊 Dashboard & Resumen":
    st.title("🧪 Sistema Maestro NEMET")
    st.subheader("Panel General de Control y Logística")

    if st.button("🔄 Sincronizar Datos con Excel"):
        st.session_state["inventario"] = cargar_inventario()
        st.session_state["clientes"] = cargar_clientes()
        st.session_state["version_editores"] += 1
        flash("¡Datos actualizados desde el archivo Excel!")
        st.rerun()

    stock = pd.to_numeric(df_inv.get("StockActual"), errors="coerce").fillna(0) if "StockActual" in df_inv.columns else None
    precio = pd.to_numeric(df_inv.get("PrecioPublicoIVA"), errors="coerce").fillna(0) if "PrecioPublicoIVA" in df_inv.columns else None
    valor_total = float((stock * precio).sum()) if stock is not None and precio is not None else 0.0
    por_reabastecer = int((stock <= pd.to_numeric(df_inv.get("StockMinimo"), errors="coerce").fillna(0)).sum()) \
        if stock is not None and "StockMinimo" in df_inv.columns else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total de SKUs Registrados", len(df_inv))
    col2.metric("Clientes Registrados", len(df_clientes))
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
    st.subheader("Gestión, Entradas, Salidas y Edición Directa")
    st.caption("Captura StockInicial, Entradas, Salidas, StockMinimo, PrecioCompra y PrecioPublicoIVA. "
               "Las columnas StockActual, AlertaStock, PrecioBaseSinIVA, IVA 16% y ValorInventario se calculan al guardar.")
    columnas_bloqueadas = [c for c in COLUMNAS_DERIVADAS_INV if c in df_inv.columns]
    df_editado = st.data_editor(
        df_inv, num_rows="dynamic", width="stretch", disabled=columnas_bloqueadas,
        key=f"editor_inv_{st.session_state['version_editores']}",
    )

    if st.button("💾 Guardar Cambios en Excel"):
        try:
            guardar_inventario(df_editado)
            st.session_state["inventario"] = cargar_inventario()
            st.session_state["version_editores"] += 1
            flash("¡Inventario actualizado y guardado exitosamente!")
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar: {e}")

elif menu == "👥 Gestión de Clientes":
    st.subheader("👥 Base de Datos y Directorio de Clientes")
    st.markdown("Agrega, edita o elimina la información de tus clientes directamente. Los cambios se guardarán en la pestaña `Clientes` de tu Excel.")

    df_cli_editado = st.data_editor(
        df_clientes, num_rows="dynamic", width="stretch",
        key=f"editor_clientes_{st.session_state['version_editores']}",
    )

    if st.button("💾 Guardar Base de Datos de Clientes"):
        try:
            guardar_clientes(df_cli_editado)
            st.session_state["clientes"] = cargar_clientes()
            st.session_state["version_editores"] += 1
            flash("¡Base de datos de clientes guardada exitosamente en Excel!")
            st.rerun()
        except Exception as e:
            st.error(f"Error al guardar clientes: {e}")

elif menu == "📏 Cotizador por Área y Milimétrico":
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

    resultados = []
    for _, fila in df_familia.iterrows():
        pres_kg = float(fila["Kg_Num"])
        precio_pub = float(valor_celda(fila, "PrecioPublicoIVA", 0.0))
        unidades = max(1, math.ceil(round(kg_necesarios / pres_kg, 6)))
        resultados.append({
            "SKU": str(valor_celda(fila, col_sku)),
            "Presentación": str(fila[col_pres]),
            "Kg por unidad": pres_kg,
            "Precio Público": precio_pub,
            "Unidades": unidades,
            "Kg cubiertos": unidades * pres_kg,
            "Costo Total": unidades * precio_pub,
        })

    if resultados:
        optima = min(resultados, key=lambda x: (x["Costo Total"], x["Unidades"]))
        st.success(f"💡 **Recomendación Óptima:** Presentación de **{optima['Presentación']}** con **{optima['Unidades']} unidad(es)** "
                   f"({optima['Kg cubiertos']:.2f} kg) por **${optima['Costo Total']:,.2f} MXN**.")
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
                "Detalle": f"{prod_familia}\n{area_total:.2f} m² x {espesor_mm:g} mm ({kg_necesarios:.2f} kg)",
            })
            st.rerun()

    carrito_area = st.session_state["carrito_area"]
    if not carrito_area.empty:
        st.markdown("### 🛒 Carrito de Cotización por Área")
        st.dataframe(carrito_area.drop(columns=["Detalle"]), width="stretch", hide_index=True)

    bloque_acciones_cotizacion("carrito_area", carrito_area, cliente_area, correo_area,
                               "Cotización por Área y Sistemas Epóxicos")

elif menu == "📝 Cotizador Comercial Profesional":
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
    st.subheader("Historial y Auditoría de Cotizaciones")

    with st.expander("🗑️ Borrar Todo el Historial de Cotizaciones"):
        confirmar = st.checkbox("Confirmo que deseo borrar TODO el historial (esta acción no se puede deshacer)")
        if st.button("Borrar Historial", disabled=not confirmar, type="primary"):
            try:
                escribir_hoja(pd.DataFrame(columns=COLUMNAS_HISTORIAL), HOJA_HISTORIAL)
                for clave in ("carrito_area", "carrito_comercial"):
                    st.session_state.pop(f"{clave}_emitido", None)
                flash("¡Historial de cotizaciones limpiado exitosamente!")
                st.rerun()
            except Exception as e:
                st.error(f"Error al limpiar el historial: {e}")

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
