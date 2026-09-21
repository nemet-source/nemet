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
# FUNCIONES DE CARGA Y GESTIÓN DE DATOS (BLINDADA)
# ==========================================
def cargar_inventario():
    """Carga de forma inteligente el inventario normalizando nombres de columnas."""
    try:
        if os.path.exists(EXCEL_FILE):
            try:
                df = pd.read_excel(EXCEL_FILE, sheet_name="Inventario")
            except Exception:
                df = pd.read_excel(EXCEL_FILE)
            
            # Limpiar espacios en nombres de columnas si los hay
            df.columns = df.columns.astype(str).str.strip()
            
            # Mapeo inteligente de columnas comunes por si varían en el Excel
            renombres = {}
            for col in df.columns:
                col_lower = col.lower()
                if 'sku' in col_lower:
                    renombres[col] = 'SKU'
                elif 'desc' in col_lower:
                    renombres[col] = 'Descripcion'
                elif 'presentacion' in col_lower or 'presentación' in col_lower:
                    renombres[col] = 'Presentacion'
                elif 'precio' in col_lower and ('publico' in col_lower or 'iva' in col_lower or 'venta' in col_lower):
                    renombres[col] = 'PrecioPublicoIVA'
                elif 'stock' in col_lower and 'actual' in col_lower:
                    renombres[col] = 'StockActual'
                elif 'stock' in col_lower and 'inicial' in col_lower:
                    renombres[col] = 'StockInicial'
                elif 'entrada' in col_lower:
                    renombres[col] = 'Entradas'
                elif 'salida' in col_lower:
                    renombres[col] = 'Salidas'
            
            df = df.rename(columns=renombres)
            
            # Asegurar columnas mínimas requeridas para que no falle nada
            if 'SKU' in df.columns:
                df['SKU'] = df['SKU'].astype(str)
            if 'PrecioPublicoIVA' not in df.columns:
                # Buscar cualquier columna numérica de precio si no se mapeó exacto
                for c in df.columns:
                    if 'precio' in c.lower():
                        df['PrecioPublicoIVA'] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)
                        break
                if 'PrecioPublicoIVA' not in df.columns:
                    df['PrecioPublicoIVA'] = 0.0

            return df
        else:
            st.error(f"No se encontró el archivo {EXCEL_FILE} en la carpeta.")
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Error al cargar el archivo Excel: {e}")
        return pd.DataFrame()

def obtener_siguiente_folio():
    try:
        df_hist = pd.read_excel(EXCEL_FILE, sheet_name="Historial_Cotizaciones")
        num = len(df_hist) + 1
    except Exception:
        num = 1
    anio_actual = datetime.now().year
    return f"COT-{anio_actual}-{num:03d}"

def registrar_cotizacion_en_excel(folio, cliente, items_carrito, total_general):
    try:
        try:
            df_hist = pd.read_excel(EXCEL_FILE, sheet_name="Historial_Cotizaciones")
        except Exception:
            df_hist = pd.DataFrame(columns=["Folio", "Fecha", "Cliente", "Detalle_Productos", "Total"])

        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        detalle_str = ", ".join([f"{row.get('Cantidad', 1)}x {row.get('Descripcion', '')} ({row.get('Presentacion', '')})" for _, row in items_carrito.iterrows()])
        
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

# Inicializar estados
if "inventario" not in st.session_state or st.session_state["inventario"].empty:
    st.session_state["inventario"] = cargar_inventario()

if "carrito" not in st.session_state:
    st.session_state["carrito"] = []

if "carrito_area" not in st.session_state:
    st.session_state["carrito_area"] = pd.DataFrame(columns=["SKU", "Descripcion", "Presentacion", "Cantidad", "Subtotal"])

df_inv = st.session_state["inventario"]

# ==========================================
# MENÚ Y NAVEGACIÓN
# ==========================================
st.sidebar.title("📂 Menú Principal")
menu = st.sidebar.selectbox("Navegación", [
    "📊 Dashboard & Resumen", 
    "📦 Control de Inventario y Edición", 
    "📏 Cotizador por Área y Milimétrico",
    "📝 Cotizador Comercial Profesional", 
    "📋 Historial de Cotizaciones (Folios)"
])

if menu == "📊 Dashboard & Resumen":
    st.title("🧪 Sistema Maestro NEMET")
    st.subheader("Panel General de Control y Logística")
    
    if st.button("🔄 Sincronizar Datos con Excel"):
        st.session_state["inventario"] = cargar_inventario()
        df_inv = st.session_state["inventario"]
        st.success("¡Datos actualizados desde el archivo Excel!")
        st.rerun()

    total_skus = len(df_inv)
    valor_total = (df_inv['StockActual'] * df_inv['PrecioPublicoIVA']).sum() if 'StockActual' in df_inv.columns and 'PrecioPublicoIVA' in df_inv.columns else 0.0

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total de SKUs Registrados", total_skus)
    with col2:
        st.metric("Valor Total Inventario ($)", f"${valor_total:,.2f} MXN")

    st.markdown("### 🔍 Vista Rápida del Inventario Sincronizado")
    st.dataframe(df_inv, use_container_width=True)

elif menu == "📦 Control de Inventario y Edición":
    st.subheader("Gestión, Entradas, Salidas y Edición Directa")
    df_editado = st.data_editor(df_inv, num_rows="dynamic", use_container_width=True, key="editor_inv")
    
    if st.button("💾 Guardar Cambios en Excel"):
        try:
            with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
                df_editado.to_excel(writer, sheet_name="Inventario", index=False)
            st.session_state["inventario"] = df_editado
            st.success("¡Inventario actualizado y guardado exitosamente!")
        except Exception as e:
            st.error(f"Error al guardar: {e}")

elif menu == "📏 Cotizador por Área y Milimétrico":
    st.subheader("Cotizador por Área, Espesor y Proporción de Mezcla")
    
    with st.expander("📦 Consultar Inventario General"):
        st.dataframe(df_inv, use_container_width=True)

    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        cliente_area = st.text_input("Nombre del Cliente", "Cliente General", key="cli_area")
        correo_area = st.text_input("Correo Electrónico", "cliente@correo.com", key="mail_area")
    with col_a2:
        ancho = st.number_input("Ancho (metros)", min_value=0.1, value=3.0, step=0.1)
        largo = st.number_input("Largo (metros)", min_value=0.1, value=4.0, step=0.1)
        area_total = ancho * largo
    with col_a3:
        espesor_mm = st.number_input("Espesor (mm)", min_value=0.1, value=1.0, step=0.5)

    st.info(f"📐 **Área Total Calculada:** **{area_total:.2f} m²**")

    # Asegurar columna Descripción válida
    col_desc = 'Descripcion' if 'Descripcion' in df_inv.columns else df_inv.columns[1]
    familias = df_inv[col_desc].unique() if col_desc in df_inv.columns else []
    prod_familia = st.selectbox("Seleccionar Línea de Producto", [f for f in familias if isinstance(f, str)])

    def extraer_kg(pres_str):
        import re
        nums = re.findall(r"[\d\.]+", str(pres_str))
        return float(nums[0]) if nums else 0.0

    df_familia = df_inv[df_inv[col_desc] == prod_familia].copy()
    col_pres = 'Presentacion' if 'Presentacion' in df_familia.columns else df_familia.columns[2]
    df_familia['Kg_Num'] = df_familia[col_pres].apply(extraer_kg)
    df_familia = df_familia[df_familia['Kg_Num'] > 0].sort_values(by='Kg_Num', ascending=False)

    kg_necesarios = area_total * espesor_mm * 1.2

    if not df_familia.empty:
        resultados = []
        for _, row in df_familia.iterrows():
            pres_kg = row['Kg_Num']
            precio_pub = row.get('PrecioPublicoIVA', 0.0)
            unidades = int((kg_necesarios // pres_kg) + (1 if kg_necesarios % pres_kg > 0 else 0))
            costo_total = unidades * precio_pub
            resultados.append({
                "SKU": row.get('SKU', ''),
                "Presentación": row[col_pres],
                "Precio Público": precio_pub,
                "Unidades": unidades,
                "Costo Total": costo_total
            })
        
        optima = min(resultados, key=lambda x: x['Costo Total'])
        st.success(f"💡 **Recomendación Óptima:** Presentación de **{optima['Presentación']}** con **{optima['Unidades']} unidad(es)** por **${optima['Costo Total']:,.2f} MXN**.")

        if st.button("➕ Agregar esta recomendación al Carrito"):
            nuevo_item = pd.DataFrame([{
                "SKU": optima['SKU'],
                "Descripcion": prod_familia,
                "Presentacion": optima['Presentación'],
                "Cantidad": optima['Unidades'],
                "Subtotal": optima['Costo Total']
            }])
            st.session_state["carrito_area"] = pd.concat([st.session_state["carrito_area"], nuevo_item], ignore_index=True)
            st.rerun()

    if not st.session_state["carrito_area"].empty:
        st.markdown("### 🛒 Carrito de Cotización por Área")
        st.dataframe(st.session_state["carrito_area"], use_container_width=True)

        subtotal_c = st.session_state["carrito_area"]["Subtotal"].sum()
        iva_c = subtotal_c * 0.16
        total_c = subtotal_c + iva_c

        st.markdown(f"**Subtotal:** ${subtotal_c:,.2f} MXN | **IVA (16%):** ${iva_c:,.2f} MXN | **Total:** **${total_c:,.2f} MXN**")

        folio_actual = obtener_siguiente_folio()
        st.caption(f"Folio consecutivo asignado: **{folio_actual}**")

        col_b1, col_b2, col_b3 = st.columns(3)

        with col_b1:
            if st.button("📄 Descargar PDF"):
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("helvetica", "B", 16)
                pdf.cell(0, 10, f"COTIZACION OFICIAL - {folio_actual}", 0, 1, "C")
                pdf.set_font("helvetica", "", 11)
                pdf.cell(0, 6, f"Cliente: {cliente_area}", 0, 1)
                pdf.cell(0, 6, f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1)
                pdf.ln(5)

                pdf.set_font("helvetica", "B", 10)
                pdf.cell(25, 8, "SKU", 1)
                pdf.cell(85, 8, "Descripcion", 1)
                pdf.cell(30, 8, "Cant.", 1, 0, "C")
                pdf.cell(40, 8, "Subtotal", 1, 1, "R")

                pdf.set_font("helvetica", "", 10)
                for _, row in st.session_state["carrito_area"].iterrows():
                    pdf.cell(25, 7, str(row["SKU"]), 1)
                    pdf.cell(85, 7, str(row["Descripcion"]), 1)
                    pdf.cell(30, 7, str(row["Cantidad"]), 1, 0, "C")
                    pdf.cell(40, 7, f"${row['Subtotal']:,.2f}", 1, 1, "R")

                pdf.ln(5)
                pdf.cell(0, 6, f"TOTAL: ${total_c:,.2f} MXN", 0, 1, "R")

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    pdf.output(tmp.name)
                    tmp_path = tmp.name

                with open(tmp_path, "rb") as f:
                    pdf_bytes = f.read()
                os.unlink(tmp_path)

                registrar_cotizacion_en_excel(folio_actual, cliente_area, st.session_state["carrito_area"], total_c)
                st.download_button("📥 Descargar Archivo PDF", pdf_bytes, file_name=f"{folio_actual}.pdf", mime="application/pdf")

        with col_b2:
            if st.button("📧 Enviar por Correo"):
                try:
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("helvetica", "B", 16)
                    pdf.cell(0, 10, f"COTIZACION OFICIAL - {folio_actual}", 0, 1, "C")
                    pdf.set_font("helvetica", "", 11)
                    pdf.cell(0, 6, f"Cliente: {cliente_area}", 0, 1)
                    pdf.ln(5)

                    pdf.set_font("helvetica", "B", 10)
                    pdf.cell(25, 8, "SKU", 1)
                    pdf.cell(85, 8, "Descripcion", 1)
                    pdf.cell(30, 8, "Cant.", 1, 0, "C")
                    pdf.cell(40, 8, "Subtotal", 1, 1, "R")

                    pdf.set_font("helvetica", "", 10)
                    for _, row in st.session_state["carrito_area"].iterrows():
                        pdf.cell(25, 7, str(row["SKU"]), 1)
                        pdf.cell(85, 7, str(row["Descripcion"]), 1)
                        pdf.cell(30, 7, str(row["Cantidad"]), 1, 0, "C")
                        pdf.cell(40, 7, f"${row['Subtotal']:,.2f}", 1, 1, "R")

                    pdf.ln(5)
                    pdf.cell(0, 6, f"TOTAL: ${total_c:,.2f} MXN", 0, 1, "R")

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        pdf.output(tmp.name)
                        tmp_path = tmp.name

                    remitente = st.secrets["email"]["remitente"]
                    password = st.secrets["email"]["password"]

                    msg = MIMEMultipart()
                    msg["From"] = remitente
                    msg["To"] = correo_area
                    msg["Subject"] = f"Cotización Oficial NEMET - {folio_actual}"
                    
                    cuerpo = f"Hola {cliente_area},\n\nAdjunto encontrarás la cotización {folio_actual}.\n\nTotal: ${total_c:,.2f} MXN\n\nSaludos cordiales,\nEquipo NEMET"
                    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

                    with open(tmp_path, "rb") as f:
                        adj = MIMEApplication(f.read(), Name=f"{folio_actual}.pdf")
                    adj["Content-Disposition"] = f'attachment; filename="{folio_actual}.pdf"'
                    msg.attach(adj)

                    server = smtplib.SMTP("smtp.gmail.com", 587)
                    server.starttls()
                    server.login(remitente, password)
                    server.sendmail(remitente, correo_area, msg.as_string())
                    server.quit()

                    os.unlink(tmp_path)
                    registrar_cotizacion_en_excel(folio_actual, cliente_area, st.session_state["carrito_area"], total_c)
                    st.success("¡Correo enviado exitosamente al cliente!")
                except Exception as e:
                    st.error(f"Error al enviar correo: {e}")

        with col_b3:
            if st.button("🗑️ Limpiar Carrito"):
                st.session_state["carrito_area"] = pd.DataFrame(columns=["SKU", "Descripcion", "Presentacion", "Cantidad", "Subtotal"])
                st.rerun()

elif menu == "📝 Cotizador Comercial Profesional":
    st.subheader("Generador de Cotizaciones Comerciales")
    cliente_comercial = st.text_input("Cliente / Empresa", "Cliente General", key="cli_com")
    
    col_desc = 'Descripcion' if 'Descripcion' in df_inv.columns else df_inv.columns[1]
    col_sku = 'SKU' if 'SKU' in df_inv.columns else df_inv.columns[0]
    col_pres = 'Presentacion' if 'Presentacion' in df_inv.columns else df_inv.columns[2]
    
    opciones_cot = [f"{row[col_sku]} - {row[col_desc]} ({row[col_pres]}) - ${row.get('PrecioPublicoIVA', 0):,.2f}" for _, row in df_inv.iterrows()]
    prod_sel = st.selectbox("Seleccionar Producto", opciones_cot)
    cant = st.number_input("Cantidad", min_value=1, value=1)
    
    if st.button("Agregar al Carrito Comercial"):
        sku_c = prod_sel.split(" - ")[0]
        fila = df_inv[df_inv[col_sku].astype(str) == sku_c].iloc[0]
        subtotal = cant * fila.get('PrecioPublicoIVA', 0)
        
        nuevo_item = {
            "SKU": sku_c,
            "Descripcion": fila[col_desc],
            "Presentacion": fila[col_pres],
            "Cantidad": cant,
            "Subtotal": subtotal
        }
        st.session_state["carrito"].append(nuevo_item)
        st.success("Producto agregado al carrito.")

    if st.session_state["carrito"]:
        df_car = pd.DataFrame(st.session_state["carrito"])
        st.dataframe(df_car, use_container_width=True)
        total_com = df_car['Subtotal'].sum()
        st.markdown(f"### **Total: ${total_com:,.2f} MXN**")
        
        if st.button("💾 Guardar y Registrar Folio"):
            folio_com = obtener_siguiente_folio()
            registrar_cotizacion_en_excel(folio_com, cliente_comercial, df_car, total_com)
            st.success(f"¡Cotización {folio_com} registrada con éxito en el historial de Excel!")

elif menu == "📋 Historial de Cotizaciones (Folios)":
    st.subheader("Historial y Auditoría de Cotizaciones")
    try:
        df_hist = pd.read_excel(EXCEL_FILE, sheet_name="Historial_Cotizaciones")
        if not df_hist.empty:
            busqueda = st.text_input("🔍 Buscar por Folio o Cliente:")
            if busqueda:
                df_hist = df_hist[
                    df_hist["Folio"].astype(str).str.contains(busqueda, case=False, na=False) |
                    df_hist["Cliente"].astype(str).str.contains(busqueda, case=False, na=False)
                ]
            st.dataframe(df_hist, use_container_width=True)
            st.metric("Total de Cotizaciones Emitidas", len(df_hist))
        else:
            st.info("Aún no hay cotizaciones registradas.")
    except Exception:
        st.info("Aún no se ha generado la hoja de historial en el Excel. Se creará automáticamente al emitir la primera cotización.")