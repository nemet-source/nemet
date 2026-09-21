import streamlit as st
import pandas as pd
from datetime import datetime
import openpyxl
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from fpdf import FPDF
import tempfile
import os

# Configuración de la página
st.set_page_config(
    page_title="Sistema Maestro NEMET",
    page_icon="🧪",
    layout="wide"
)

EXCEL_FILE = "Sistema_Inventario_NEMET_Final.xlsx"

# ==========================================
# FUNCIONES DE DATOS E HISTORIAL
# ==========================================
def obtener_siguiente_folio():
    """Genera un folio consecutivo basado en el historial existente en Excel."""
    try:
        df_hist = pd.read_excel(EXCEL_FILE, sheet_name="Historial_Cotizaciones")
        num = len(df_hist) + 1
    except Exception:
        num = 1
    anio_actual = datetime.now().year
    return f"COT-{anio_actual}-{num:03d}"

def registrar_cotizacion_en_excel(folio, cliente, items_carrito, total_general):
    """Registra la cotización generada en una hoja del archivo Excel de inventario."""
    try:
        try:
            df_hist = pd.read_excel(EXCEL_FILE, sheet_name="Historial_Cotizaciones")
        except Exception:
            df_hist = pd.DataFrame(columns=["Folio", "Fecha", "Cliente", "Detalle_Productos", "Total"])

        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        detalle_str = ", ".join([f"{row['Cantidad']}x {row['Descripcion']} ({row['Presentacion']})" for _, row in items_carrito.iterrows()])
        
        nueva_fila = pd.DataFrame({
            "Folio": [folio],
            "Fecha": [fecha_actual],
            "Cliente": [cliente],
            "Detalle_Productos": [detalle_str],
            "Total": [total_general]
        })
        
        df_hist = pd.concat([df_hist, nueva_fila], ignore_index=True)
        
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df_hist.to_excel(writer, sheet_name="Historial_Cotizaciones", index=False)
            
        return True
    except Exception as e:
        print(f"Error al guardar historial: {e}")
        return False

# Cargar inventario principal desde Excel
@st.cache_data(ttl=60)
def cargar_inventario():
    try:
        df = pd.read_excel(EXCEL_FILE, sheet_name="Inventario")
        return df
    except Exception:
        df = pd.read_excel(EXCEL_FILE)
        return df

df_inventario = cargar_inventario()

# ==========================================
# MENÚ DE NAVEGACIÓN
# ==========================================
st.sidebar.title("Menú Principal")
menu = st.sidebar.radio("Navegación", ["Cotizador por Área y Milimétrico", "📋 Historial de Cotizaciones"])

# ==========================================
# MÓDULO 1: COTIZADOR POR ÁREA Y INVENTARIO
# ==========================================
if menu == "Cotizador por Área y Milimétrico":
    st.title("🧪 Sistema Maestro NEMET - Cotizador Profesional")
    
    # Inicializar carrito en sesión
    if "carrito_area" not in st.session_state:
        st.session_state["carrito_area"] = pd.DataFrame(columns=["SKU", "Descripcion", "Presentacion", "Cantidad", "Subtotal"])

    # Mostrar inventario general cargado de Excel para consulta
    with st.expander("📦 Ver Inventario General Sincronizado"):
        st.dataframe(df_inventario, use_container_width=True)

    st.sidebar.markdown("---")
    st.sidebar.subheader("Parámetros de Área")
    ancho = st.sidebar.number_input("Ancho (metros)", min_value=0.1, value=3.0, step=0.1)
    largo = st.sidebar.number_input("Largo (metros)", min_value=0.1, value=4.0, step=0.1)
    area_total = ancho * largo
    st.sidebar.info(f"Área Calculada: **{area_total:.2f} m²**")

    # Lógica de recomendación de producto
    st.subheader("💡 Recomendación Óptima")
    kg_estimados = area_total * 1.5 
    presentacion_sugerida = "20 kg" if kg_estimados > 10 else "4 kg"
    precio_ejemplo = 7490.0 if presentacion_sugerida == "20 kg" else 1850.0

    st.success(f"Recomendación Óptima para {area_total:.1f} m²: **{presentacion_sugerida}** con 1 unidad por ${precio_ejemplo:,.2f} MXN.")

    if st.button("➕ Agregar esta recomendación al Carrito de Cotización"):
        nuevo_item = pd.DataFrame([{
            "SKU": "EPT-06",
            "Descripcion": "EPOXY PISOS (A y B) TRANSPARENTE",
            "Presentacion": presentacion_sugerida,
            "Cantidad": 1,
            "Subtotal": precio_ejemplo
        }])
        st.session_state["carrito_area"] = pd.concat([st.session_state["carrito_area"], nuevo_item], ignore_index=True)
        st.rerun()

    # Visualización del Carrito
    st.markdown("### 🛒 Carrito de Cotización por Área")
    if not st.session_state["carrito_area"].empty:
        st.dataframe(st.session_state["carrito_area"], use_container_width=True)

        subtotal_carrito = st.session_state["carrito_area"]["Subtotal"].sum()
        iva = subtotal_carrito * 0.16
        total_general = subtotal_carrito + iva

        st.markdown(f"**Subtotal:** ${subtotal_carrito:,.2f} MXN | **IVA (16%):** ${iva:,.2f} MXN | **Total:** **${total_general:,.2f} MXN**")

        col_c1, col_c2 = st.columns(2)
        with col_c1:
            nombre_cliente = st.text_input("Nombre del Cliente", "Cliente General")
        with col_c2:
            correo_cliente = st.text_input("Correo Electrónico del Cliente", "cliente@correo.com")

        folio_actual = obtener_siguiente_folio()
        st.caption(f"Folio asignado a la cotización: **{folio_actual}**")

        col1, col2, col3 = st.columns(3)

        # Generar PDF con FPDF2
        with col1:
            if st.button("📄 Descargar PDF"):
                try:
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("helvetica", "B", 16)
                    pdf.cell(0, 10, f"COTIZACION OFICIAL - {folio_actual}", 0, 1, "C")
                    pdf.set_font("helvetica", "", 12)
                    pdf.cell(0, 10, f"Cliente: {nombre_cliente}", 0, 1, "L")
                    pdf.cell(0, 10, f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1, "L")
                    pdf.ln(5)

                    pdf.set_font("helvetica", "B", 10)
                    pdf.cell(30, 8, "SKU", 1)
                    pdf.cell(80, 8, "Descripcion", 1)
                    pdf.cell(30, 8, "Cant.", 1, 0, "C")
                    pdf.cell(40, 8, "Subtotal", 1, 1, "R")

                    pdf.set_font("helvetica", "", 10)
                    for _, row in st.session_state["carrito_area"].iterrows():
                        pdf.cell(30, 8, str(row["SKU"]), 1)
                        pdf.cell(80, 8, str(row["Descripcion"]), 1)
                        pdf.cell(30, 8, str(row["Cantidad"]), 1, 0, "C")
                        pdf.cell(40, 8, f"${row['Subtotal']:,.2f}", 1, 1, "R")

                    pdf.ln(5)
                    pdf.cell(0, 8, f"Subtotal: ${subtotal_carrito:,.2f} MXN", 0, 1, "R")
                    pdf.cell(0, 8, f"IVA (16%): ${iva:,.2f} MXN", 0, 1, "R")
                    pdf.cell(0, 8, f"TOTAL: ${total_general:,.2f} MXN", 0, 1, "R")

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                        pdf.output(tmp_file.name)
                        tmp_path = tmp_file.name

                    with open(tmp_path, "rb") as f:
                        pdf_bytes = f.read()

                    os.unlink(tmp_path)
                    
                    registrar_cotizacion_en_excel(folio_actual, nombre_cliente, st.session_state["carrito_area"], total_general)

                    st.download_button(
                        label="📥 Descargar Archivo PDF",
                        data=pdf_bytes,
                        file_name=f"{folio_actual}.pdf",
                        mime="application/pdf"
                    )
                except Exception as e:
                    st.error(f"Error al generar PDF: {e}")

        # Envío de Correo con smtplib y Secrets
        with col2:
            if st.button("📧 Enviar por Correo al Cliente"):
                try:
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("helvetica", "B", 16)
                    pdf.cell(0, 10, f"COTIZACION OFICIAL - {folio_actual}", 0, 1, "C")
                    pdf.set_font("helvetica", "", 12)
                    pdf.cell(0, 10, f"Cliente: {nombre_cliente}", 0, 1, "L")
                    pdf.cell(0, 10, f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1, "L")
                    pdf.ln(5)

                    pdf.set_font("helvetica", "B", 10)
                    pdf.cell(30, 8, "SKU", 1)
                    pdf.cell(80, 8, "Descripcion", 1)
                    pdf.cell(30, 8, "Cant.", 1, 0, "C")
                    pdf.cell(40, 8, "Subtotal", 1, 1, "R")

                    pdf.set_font("helvetica", "", 10)
                    for _, row in st.session_state["carrito_area"].iterrows():
                        pdf.cell(30, 8, str(row["SKU"]), 1)
                        pdf.cell(80, 8, str(row["Descripcion"]), 1)
                        pdf.cell(30, 8, str(row["Cantidad"]), 1, 0, "C")
                        pdf.cell(40, 8, f"${row['Subtotal']:,.2f}", 1, 1, "R")

                    pdf.ln(5)
                    pdf.cell(0, 8, f"TOTAL: ${total_general:,.2f} MXN", 0, 1, "R")

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                        pdf.output(tmp_file.name)
                        tmp_path = tmp_file.name

                    remitente = st.secrets["email"]["remitente"]
                    password = st.secrets["email"]["password"]

                    msg = MIMEMultipart()
                    msg["From"] = remitente
                    msg["To"] = correo_cliente
                    msg["Subject"] = f"Cotización Oficial NEMET - {folio_actual}"

                    cuerpo = f"Hola {nombre_cliente},\n\nAdjunto encontrarás la cotización {folio_actual} solicitada.\n\nTotal: ${total_general:,.2f} MXN\n\nSaludos cordiales,\nEquipo NEMET"
                    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

                    with open(tmp_path, "rb") as f:
                        adjunto = MIMEApplication(f.read(), Name=f"{folio_actual}.pdf")
                    adjunto["Content-Disposition"] = f'attachment; filename="{folio_actual}.pdf"'
                    msg.attach(adjunto)

                    server = smtplib.SMTP("smtp.gmail.com", 587)
                    server.starttls()
                    server.login(remitente, password)
                    server.sendmail(remitente, correo_cliente, msg.as_string())
                    server.quit()

                    os.unlink(tmp_path)

                    registrar_cotizacion_en_excel(folio_actual, nombre_cliente, st.session_state["carrito_area"], total_general)

                    st.success("¡Correo enviado exitosamente al cliente!")
                except Exception as e:
                    st.error(f"Error al enviar correo: {e}")

        with col3:
            if st.button("🗑️ Limpiar Carrito de Área"):
                st.session_state["carrito_area"] = pd.DataFrame(columns=["SKU", "Descripcion", "Presentacion", "Cantidad", "Subtotal"])
                st.rerun()
    else:
        st.info("El carrito está vacío. Agrega una recomendación o producto para comenzar.")

# ==========================================
# MÓDULO 2: HISTORIAL DE COTIZACIONES
# ==========================================
elif menu == "📋 Historial de Cotizaciones":
    st.title("📋 Historial y Registro de Cotizaciones")
    st.markdown("Consulta todas las cotizaciones generadas previamente desde el sistema.")

    try:
        df_historial = pd.read_excel(EXCEL_FILE, sheet_name="Historial_Cotizaciones")
        if not df_historial.empty:
            busqueda = st.text_input("🔍 Buscar por Folio o Cliente:")
            if busqueda:
                df_filtrado = df_historial[
                    df_historial["Folio"].astype(str).str.contains(busqueda, case=False, na=False) |
                    df_historial["Cliente"].astype(str).str.contains(busqueda, case=False, na=False)
                ]
            else:
                df_filtrado = df_historial

            st.dataframe(df_filtrado, use_container_width=True)
            st.metric("Total de Cotizaciones Registradas", len(df_historial))
        else:
            st.info("Aún no hay cotizaciones registradas en el historial.")
    except Exception:
        st.info("Aún no se ha generado la hoja de historial en el archivo Excel. Se creará automáticamente en cuanto emitas tu primera cotización.")