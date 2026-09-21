import streamlit as st
import pandas as pd
import datetime
import os
from fpdf import FPDF

st.set_page_config(page_title="Sistema Maestro NEMET", page_icon="🧪", layout="wide")

EXCEL_FILE = "Sistema_Inventario_NEMET_Final.xlsx"

# Función de carga inteligente y sincronizada con el Excel actual
def cargar_datos():
    try:
        if os.path.exists(EXCEL_FILE):
            df = pd.read_excel(EXCEL_FILE, sheet_name="Inventario", header=1)
            df = df.loc[:, ~df.columns.str.contains("^Unnamed")]
            
            if 'Presentacion' in df.columns:
                df['Presentacion'] = df['Presentacion'].astype(str)
            if 'SKU' in df.columns:
                df['SKU'] = df['SKU'].astype(str)
                
            return df
        else:
            st.error(f"No se encontró el archivo {EXCEL_FILE} en la carpeta.")
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Error al cargar o sincronizar el archivo Excel: {e}")
        return pd.DataFrame()

# Sincronizamos o cargamos los datos al iniciar o refrescar la sesión
if "inventario" not in st.session_state or st.session_state["inventario"].empty:
    st.session_state["inventario"] = cargar_datos()

if "cotizaciones_historial" not in st.session_state:
    st.session_state["cotizaciones_historial"] = []

if "carrito" not in st.session_state:
    st.session_state["carrito"] = []

# Carrito específico para el cotizador por área
if "carrito_area" not in st.session_state:
    st.session_state["carrito_area"] = []

df_inv = st.session_state["inventario"]

st.markdown("""
    <style>
    .main-header { font-size: 2.2rem; color: #1E3A8A; font-weight: bold; }
    .sub-header { font-size: 1.3rem; color: #3B82F6; font-weight: 600; }
    .stButton>button {
        background-color: #1E3A8A;
        color: white;
        font-weight: bold;
        border-radius: 8px;
        padding: 8px 16px;
    }
    .stButton>button:hover {
        background-color: #1D4ED8;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-header">🧪 SISTEMA MAESTRO NEMET - INVENTARIO Y COTIZADOR</p>', unsafe_allow_html=True)
st.markdown("---")

menu = st.sidebar.selectbox("📂 Menú Principal", [
    "📊 Dashboard & Resumen", 
    "📦 Control de Inventario", 
    "📏 Cotizador por Área y Milimétrico",
    "📝 Cotizador Profesional", 
    "📈 Historial de Cotizaciones"
])

if menu == "📊 Dashboard & Resumen":
    st.markdown('<p class="sub-header">Panel General de Control</p>', unsafe_allow_html=True)
    
    if st.button("🔄 Sincronizar con Archivo Excel Actual"):
        st.session_state["inventario"] = cargar_datos()
        df_inv = st.session_state["inventario"]
        st.rerun()

    total_skus = len(df_inv)
    stock_bajo = len(df_inv[df_inv['StockActual'] <= df_inv['StockMinimo']]) if 'StockActual' in df_inv.columns and 'StockMinimo' in df_inv.columns else 0
    
    if 'ValorInventario' in df_inv.columns:
        valor_total = df_inv['ValorInventario'].sum()
    elif 'StockActual' in df_inv.columns and 'PrecioPublicoIVA' in df_inv.columns:
        valor_total = (df_inv['StockActual'] * df_inv['PrecioPublicoIVA']).sum()
    else:
        valor_total = 0.0

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total de SKUs Registrados", total_skus)
    with col2:
        st.metric("Alertas de Stock Bajo", stock_bajo)
    with col3:
        st.metric("Valor Total Inventario ($)", f"${valor_total:,.2f}")

    st.markdown("### ⚠️ Alertas de Reabastecimiento")
    if 'StockActual' in df_inv.columns and 'StockMinimo' in df_inv.columns:
        alertas_df = df_inv[df_inv['StockActual'] <= df_inv['StockMinimo']]
        if not alertas_df.empty:
            cols_alerta = [c for c in ['SKU', 'Descripcion', 'Presentacion', 'StockActual', 'StockMinimo', 'AlertaStock'] if c in df_inv.columns]
            st.dataframe(alertas_df[cols_alerta], use_container_width=True)
        else:
            st.success("¡Excelente! No hay productos con stock por debajo del mínimo.")

    st.markdown("### 🔍 Vista Rápida del Inventario Actual (Sincronizado)")
    st.dataframe(df_inv, use_container_width=True)

elif menu == "📦 Control de Inventario":
    st.markdown('<p class="sub-header">Gestión de Entradas, Salidas y Actualización de Stock</p>', unsafe_allow_html=True)
    
    tab1, tab2, tab3 = st.tabs(["➕ Registrar Movimiento", "✏️ Modificar / Nuevo Producto", "💾 Sincronizar y Guardar en Excel"])
    
    with tab1:
        st.subheader("Registrar Entrada o Salida de Mercancía")
        opciones_sku = [
            f"{row['SKU']} - {row['Descripcion']} ({row['Presentacion']})"
            for _, row in df_inv.iterrows()
        ]
        sku_sel = st.selectbox("Seleccione el SKU", opciones_sku)
        sku_codigo = sku_sel.split(" - ")[0]
        
        tipo_mov = st.radio("Tipo de Movimiento", ["Entrada (Compra / Producción)", "Salida (Venta / Merma)"])
        cantidad = st.number_input("Cantidad", min_value=1, value=1, step=1)
        
        if st.button("Aplicar Movimiento"):
            idx = df_inv[df_inv['SKU'].astype(str) == sku_codigo].index[0]
            if "Entrada" in tipo_mov:
                df_inv.loc[idx, 'Entradas'] += cantidad
                st.success(f"Se sumaron {cantidad} unidades al SKU {sku_codigo}.")
            else:
                df_inv.loc[idx, 'Salidas'] += cantidad
                st.success(f"Se registraron {cantidad} salidas del SKU {sku_codigo}.")
            
            df_inv.loc[idx, 'StockActual'] = df_inv.loc[idx, 'StockInicial'] + df_inv.loc[idx, 'Entradas'] - df_inv.loc[idx, 'Salidas']
            df_inv.loc[idx, 'AlertaStock'] = "⚠️ REABASTECER" if df_inv.loc[idx, 'StockActual'] <= df_inv.loc[idx, 'StockMinimo'] else "✅ ÓPTIMO"
            if 'PrecioPublicoIVA' in df_inv.columns:
                df_inv['ValorInventario'] = df_inv['StockActual'] * df_inv['PrecioPublicoIVA']
            
            st.session_state["inventario"] = df_inv
            df_inv.to_excel(EXCEL_FILE, sheet_name='Inventario', index=False, startrow=1)
            st.success("¡Movimiento aplicado y guardado automáticamente en el archivo Excel!")
            st.rerun()

    with tab2:
        st.subheader("Agregar Nuevo Producto al Inventario")
        with st.form("nuevo_producto"):
            n_sku = st.text_input("SKU")
            n_desc = st.text_input("Descripción")
            n_pres = st.text_input("Presentación (ej. 1 kg, 5 L)")
            n_min = st.number_input("Stock Mínimo", min_value=0, value=5)
            n_ini = st.number_input("Stock Inicial", min_value=0, value=0)
            n_compra = st.number_input("Precio Compra ($)", min_value=0.0, value=0.0)
            n_base = st.number_input("Precio Base Sin IVA ($)", min_value=0.0, value=0.0)
            
            submit_prod = st.form_submit_button("Agregar Producto")
            if submit_prod and n_sku:
                iva = n_base * 0.16
                publico = n_base + iva
                nuevo_reg = {
                    'SKU': n_sku,
                    'Descripcion': n_desc,
                    'Presentacion': n_pres,
                    'StockMinimo': n_min,
                    'StockInicial': n_ini,
                    'Entradas': 0,
                    'Salidas': 0,
                    'StockActual': n_ini,
                    'AlertaStock': "⚠️ REABASTECER" if n_ini <= n_min else "✅ ÓPTIMO",
                    'PrecioCompra': n_compra,
                    'PrecioBaseSinIVA': n_base,
                    'IVA 16%': iva,
                    'PrecioPublicoIVA': publico,
                    'ValorInventario': n_ini * publico
                }
                df_inv = pd.concat([df_inv, pd.DataFrame([nuevo_reg])], ignore_index=True)
                st.session_state["inventario"] = df_inv
                df_inv.to_excel(EXCEL_FILE, sheet_name='Inventario', index=False, startrow=1)
                st.success(f"Producto {n_sku} agregado y sincronizado con Excel exitosamente.")
                st.rerun()

    with tab3:
        st.subheader("Sincronización Manual con Excel")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            if st.button("📥 Cargar desde Excel al Sistema"):
                st.session_state["inventario"] = cargar_datos()
                st.success("¡Datos recargados exitosamente desde el archivo Excel!")
                st.rerun()
        with col_s2:
            if st.button("📤 Guardar Sistema hacia Excel"):
                df_inv.to_excel(EXCEL_FILE, sheet_name='Inventario', index=False, startrow=1)
                st.success("¡Inventario del sistema guardado en el archivo Excel!")

elif menu == "📏 Cotizador por Área y Milimétrico":
    st.markdown('<p class="sub-header">Cotizador Milimétrico, Rendimiento por Área y Proporción de Mezcla</p>', unsafe_allow_html=True)
    st.write("Calcula los metros cuadrados, el espesor, obtén el desglose inteligente por presentaciones y **agrega múltiples productos (o sistemas completos)** a la cotización formal.")
    
    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        cliente_area = st.text_input("Nombre del Cliente", "Monica Merelles", key="cliente_area_input")
    with col_a2:
        area_m2 = st.number_input("Metros Cuadrados (m²)", min_value=1.0, value=16.0, step=0.5)
    with col_a3:
        espesor_mm = st.number_input("Espesor Requerido (mm)", min_value=0.1, value=1.0, step=0.5)

    familias_disponibles = df_inv['Descripcion'].unique()
    prod_familia = st.selectbox("Seleccionar Línea de Producto", [f for f in familias_disponibles if isinstance(f, str)])

    proporciones_default = {
        "EPO-DEEP": (100.0, 42.0),
        "EPO-FAST": (100.0, 84.0),
        "EPOXY PISOS": (100.0, 50.0),
        "EPOXY PRIMER": (100.0, 35.0),
        "EPO-DEEP ULTRA": (100.0, 33.0),
        "EPO-PAINT": (2.0, 1.0),
        "POLIURETANO TRANSPARENTE": (100.0, 50.0),
        "POLIURETANO (AYB) TRANSPARENTE": (100.0, 50.0)
    }
    
    default_a, default_b = proporciones_default.get(prod_familia, (100.0, 50.0))

    st.markdown(f"### ⚙️ Configuración de Proporción de Mezcla para: {prod_familia}")
    
    col_cfg1, col_cfg2 = st.columns(2)
    with col_cfg1:
        partes_resina = st.number_input("Partes de Resina (Parte A)", min_value=0.1, value=default_a, step=1.0, key=f"resina_{prod_familia}")
    with col_cfg2:
        partes_catalizador = st.number_input("Partes de Catalizador / Endurecedor (Parte B)", min_value=0.1, value=default_b, step=1.0, key=f"cat_{prod_familia}")

    df_familia = df_inv[df_inv['Descripcion'] == prod_familia].copy()

    def extraer_kg(pres_str):
        try:
            import re
            nums = re.findall(r"[\d\.]+", str(pres_str))
            if nums:
                return float(nums[0])
        except:
            pass
        return 0.0

    df_familia['Kg_Num'] = df_familia['Presentacion'].apply(extraer_kg)
    df_familia = df_familia[df_familia['Kg_Num'] > 0].sort_values(by='Kg_Num', ascending=False)

    rendimiento_teorico = 1.2
    kg_necesarios = area_m2 * espesor_mm * rendimiento_teorico

    st.markdown("---")
    st.info(f"📐 **Área a Cubrir:** {area_m2} m² | **Espesor:** {espesor_mm} mm | **Total Material Necesario (con rendimiento):** **{kg_necesarios:.2f} kg**")

    if not df_familia.empty:
        st.markdown("### 📦 Desglose y Optimización de Presentaciones")
        
        resultados_desglose = []
        
        for _, row in df_familia.iterrows():
            pres_kg = row['Kg_Num']
            precio_pub = row['PrecioPublicoIVA']
            costo_kg = precio_pub / pres_kg if pres_kg > 0 else 0
            
            unidades_ind = int((kg_necesarios // pres_kg) + (1 if kg_necesarios % pres_kg > 0 else 0))
            kg_aportados_ind = unidades_ind * pres_kg
            costo_total_ind = unidades_ind * precio_pub
            
            resultados_desglose.append({
                "SKU": row['SKU'],
                "Presentación": row['Presentacion'],
                "Precio Público (IVA Inc.)": f"${precio_pub:,.2f}",
                "Costo por kg": f"${costo_kg:,.2f}",
                "Unidades Necesarias": unidades_ind,
                "Kg Totales Aportados": f"{kg_aportados_ind:.2f} kg",
                "Costo Total ($)": f"${costo_total_ind:,.2f}",
                "_costo_num": costo_total_ind,
                "_precio_pub": precio_pub
            })
        
        df_desglose = pd.DataFrame(resultados_desglose)
        optima = min(resultados_desglose, key=lambda x: x['_costo_num'])
        
        st.dataframe(df_desglose.drop(columns=['_costo_num', '_precio_pub']), use_container_width=True)
        
        st.success(f"💡 **Recomendación Óptima:** La presentación más eficiente en costo para cubrir los {kg_necesarios:.2f} kg es **{optima['Presentación']}** con **{optima['Unidades Necesarias']} unidades** (Total aportado: {optima['Kg Totales Aportados']} por **{optima['Costo Total ($)']}**).")

        suma_partes = partes_resina + partes_catalizador
        p_resina = (kg_necesarios * partes_resina) / suma_partes
        p_catalizador = (kg_necesarios * partes_catalizador) / suma_partes

        st.markdown(f"### 🧪 Proporción de Mezcla Recomendada para el Proyecto ({prod_familia})")
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.metric("Parte A (Resina Epóxica)", f"{p_resina:.2f} kg")
        with col_m2:
            st.metric("Parte B (Catalizador / Endurecedor)", f"{p_catalizador:.2f} kg")

        st.markdown("---")
        
        col_add1, col_add2 = st.columns(2)
        with col_add1:
            if st.button("➕ Agregar esta recomendación al Carrito de Cotización"):
                item_area = {
                    "SKU": optima['SKU'],
                    "Descripcion": prod_familia,  # Nombre limpio para la celda de la tabla
                    "Presentacion": optima['Presentación'],
                    "Cantidad": optima['Unidades Necesarias'],
                    "PrecioUnitario": optima['_precio_pub'],
                    "Subtotal": optima['_costo_num'],
                    "Area": area_m2,
                    "Espesor": espesor_mm,
                    "MezclaInfo": f"Parte A: {p_resina:.2f}kg | Parte B: {p_catalizador:.2f}kg"
                }
                st.session_state["carrito_area"].append(item_area)
                st.success(f"¡Se agregaron {optima['Unidades Necesarias']} unidad(es) de {prod_familia} al carrito de cotización por área!")
        with col_add2:
            if st.button("🗑️ Limpiar Carrito de Área"):
                st.session_state["carrito_area"] = []
                st.rerun()

        # Mostrar carrito actual de área si tiene elementos
        if st.session_state["carrito_area"]:
            st.markdown("### 🛒 Carrito de Cotización por Área (Múltiples Productos)")
            df_c_area = pd.DataFrame(st.session_state["carrito_area"])
            st.dataframe(df_c_area[['SKU', 'Descripcion', 'Presentacion', 'Cantidad', 'Subtotal']], use_container_width=True)
            
            total_carrito_area = df_c_area['Subtotal'].sum()
            sub_c_area = total_carrito_area / 1.16
            iva_c_area = total_carrito_area - sub_c_area
            
            st.markdown(f"**Subtotal:** ${sub_c_area:,.2f} MXN | **IVA (16%):** ${iva_c_area:,.2f} MXN")
            st.markdown(f"### **Total General del Paquete: ${total_carrito_area:,.2f} MXN**")

            # Generar PDF del carrito completo de área
            class PDFArea(FPDF):
                def header(self):
                    self.set_font('helvetica', 'B', 15)
                    self.set_text_color(30, 58, 138)
                    self.cell(0, 10, 'SISTEMA MAESTRO NEMET', 0, 1, 'L')
                    self.set_font('helvetica', '', 10)
                    self.set_text_color(100, 100, 100)
                    self.cell(0, 5, 'Productos Quimicos y Especialidades Epoxicas', 0, 1, 'L')
                    self.ln(5)

                def footer(self):
                    self.set_y(-15)
                    self.set_font('helvetica', 'I', 8)
                    self.set_text_color(150, 150, 150)
                    self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

            pdf_a = PDFArea()
            pdf_a.add_page()
            pdf_a.set_font('helvetica', '', 11)
            
            pdf_a.cell(0, 6, f"Cliente: {cliente_area}", 0, 1)
            pdf_a.cell(0, 6, f"Fecha: {datetime.date.today()}", 0, 1)
            pdf_a.cell(0, 6, f"Cotizacion por Area y Sistemas Epoxicos", 0, 1)
            
            # Mostrar especificaciones generales del proyecto limpiamente en el PDF
            if st.session_state["carrito_area"]:
                p_item = st.session_state["carrito_area"][0]
                pdf_a.cell(0, 6, f"Parametros: Area: {p_item.get('Area', 16.0)} m² | Espesor: {p_item.get('Espesor', 1.0)} mm", 0, 1)
            
            pdf_a.ln(5)

            # Tabla de ítems con anchos óptimos y texto limpio
            pdf_a.set_fill_color(30, 58, 138)
            pdf_a.set_text_color(255, 255, 255)
            pdf_a.set_font('helvetica', 'B', 9)
            pdf_a.cell(25, 8, "SKU", 1, 0, 'C', True)
            pdf_a.cell(75, 8, "Descripcion / Sistema", 1, 0, 'L', True)
            pdf_a.cell(30, 8, "Presentacion", 1, 0, 'C', True)
            pdf_a.cell(20, 8, "Unidades", 1, 0, 'C', True)
            pdf_a.cell(40, 8, "Subtotal", 1, 1, 'R', True)

            pdf_a.set_font('helvetica', '', 9)
            pdf_a.set_text_color(50, 50, 50)
            for itm in st.session_state["carrito_area"]:
                pdf_a.cell(25, 7, str(itm['SKU']), 1, 0, 'C')
                pdf_a.cell(75, 7, str(itm['Descripcion']), 1, 0, 'L')
                pdf_a.cell(30, 7, str(itm['Presentacion']), 1, 0, 'C')
                pdf_a.cell(20, 7, str(itm['Cantidad']), 1, 0, 'C')
                pdf_a.cell(40, 7, f"${itm['Subtotal']:,.2f}", 1, 1, 'R')

            pdf_a.ln(5)
            pdf_a.set_font('helvetica', 'B', 10)
            pdf_a.cell(0, 6, f"Subtotal: ${sub_c_area:,.2f} MXN", 0, 1, 'R')
            pdf_a.cell(0, 6, f"IVA (16%): ${iva_c_area:,.2f} MXN", 0, 1, 'R')
            pdf_a.cell(0, 6, f"Total General: ${total_carrito_area:,.2f} MXN", 0, 1, 'R')

            pdf_bytes_multiarea = bytes(pdf_a.output())
            
            st.download_button(
                label="📄 Descargar Cotización Completa en PDF",
                data=pdf_bytes_multiarea,
                file_name=f"Cotizacion_Paquete_Area_{cliente_area.replace(' ', '_')}.pdf",
                mime="application/pdf",
                key="btn_download_multiarea_pdf"
            )

elif menu == "📝 Cotizador Profesional":
    st.markdown('<p class="sub-header">Generador de Cotizaciones Comerciales</p>', unsafe_allow_html=True)
    
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        cliente_nombre = st.text_input("Nombre del Cliente / Empresa", "Cliente General")
        cot_fecha = st.date_input("Fecha de Cotización", datetime.date.today())
    with col_c2:
        validez = st.selectbox("Validez de la Cotización", ["15 días", "30 días", "60 días"])
        condiciones = st.text_input("Condiciones de Pago", "Contado / Transferencia bancaria")

    st.markdown("### Seleccionar Productos para la Cotización")

    opciones_cotizador = [
        f"{row['SKU']} - {row['Descripcion']} ({row['Presentacion']}) - ${row['PrecioPublicoIVA']:,.2f}"
        for _, row in df_inv.iterrows()
    ]

    prod_select = st.selectbox("Producto", opciones_cotizador)
    cant_select = st.number_input("Cantidad a Cotizar", min_value=1, value=1, key="cant_cot")
    
    if st.button("Agregar a la Cotización"):
        sku_c = prod_select.split(" - ")[0]
        fila_prod = df_inv[df_inv['SKU'].astype(str) == sku_c].iloc[0]
        
        precio_unit = fila_prod['PrecioPublicoIVA']
        item = {
            "SKU": sku_c,
            "Descripcion": fila_prod['Descripcion'],
            "Presentacion": fila_prod['Presentacion'],
            "Cantidad": cant_select,
            "PrecioUnitario": precio_unit,
            "Subtotal": cant_select * precio_unit
        }
        st.session_state["carrito"].append(item)
        st.success("Producto agregado al carrito de cotización.")

    if st.session_state["carrito"]:
        st.markdown("### 🛒 Resumen de Ítems Cotizados")
        df_carrito = pd.DataFrame(st.session_state["carrito"])
        st.dataframe(df_carrito, use_container_width=True)
        
        total_cotizacion = df_carrito['Subtotal'].sum()
        subtotal_sin_iva = total_cotizacion / 1.16
        iva_total = total_cotizacion - subtotal_sin_iva
        
        st.markdown(f"**Subtotal (Sin IVA):** ${subtotal_sin_iva:,.2f} MXN")
        st.markdown(f"**IVA (16%):** ${iva_total:,.2f} MXN")
        st.markdown(f"### **Total a Pagar (Con IVA): ${total_cotizacion:,.2f} MXN**")

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("💾 Guardar Cotización en Historial"):
                nueva_cot = {
                    "Fecha": str(cot_fecha),
                    "Cliente": cliente_nombre,
                    "Validez": validez,
                    "Condiciones": condiciones,
                    "Total": total_cotizacion,
                    "Items": st.session_state["carrito"].copy()
                }
                st.session_state["cotizaciones_historial"].append(nueva_cot)
                st.success("Cotización guardada exitosamente en el historial.")
        with col_btn2:
            if st.button("🗑️ Limpiar Carrito"):
                st.session_state["carrito"] = []
                st.rerun()

elif menu == "📈 Historial de Cotizaciones":
    st.markdown('<p class="sub-header">Historial de Cotizaciones Generadas</p>', unsafe_allow_html=True)
    
    if st.session_state["cotizaciones_historial"]:
        if st.button("🗑️ Borrar Todo el Historial"):
            st.session_state["cotizaciones_historial"] = []
            st.rerun()
            
        st.markdown("---")
        
        for i, cot in enumerate(st.session_state["cotizaciones_historial"]):
            with st.expander(f"Cotización #{i+1} - Cliente: {cot['Cliente']} ({cot['Fecha']}) - Total: ${cot['Total']:,.2f}"):
                st.write(f"**Fecha:** {cot['Fecha']}")
                st.write(f"**Cliente:** {cot['Cliente']}")
                st.write(f"**Validez:** {cot.get('Validez', '30 días')} | **Condiciones:** {cot.get('Condiciones', 'Contado')}")
                
                df_items = pd.DataFrame(cot['Items'])
                st.dataframe(df_items, use_container_width=True)
                
                total_c = cot['Total']
                sub_c = total_c / 1.16
                iva_c = total_c - sub_c
                
                st.markdown(f"**Subtotal:** ${sub_c:,.2f} MXN | **IVA (16%):** ${iva_c:,.2f} MXN")
                st.markdown(f"### **Total General:** ${total_c:,.2f} MXN")
                
                class PDFHist(FPDF):
                    def header(self):
                        self.set_font('helvetica', 'B', 15)
                        self.set_text_color(30, 58, 138)
                        self.cell(0, 10, 'SISTEMA MAESTRO NEMET', 0, 1, 'L')
                        self.set_font('helvetica', '', 10)
                        self.set_text_color(100, 100, 100)
                        self.cell(0, 5, 'Productos Quimicos y Especialidades', 0, 1, 'L')
                        self.ln(5)

                    def footer(self):
                        self.set_y(-15)
                        self.set_font('helvetica', 'I', 8)
                        self.set_text_color(150, 150, 150)
                        self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

                pdf_h = PDFHist()
                pdf_h.add_page()
                pdf_h.set_font('helvetica', '', 11)
                
                pdf_h.cell(0, 6, f"Cotizacion: #{i+1}", 0, 1)
                pdf_h.cell(0, 6, f"Cliente: {cot['Cliente']}", 0, 1)
                pdf_h.cell(0, 6, f"Fecha: {cot['Fecha']}", 0, 1)
                pdf_h.cell(0, 6, f"Validez: {cot.get('Validez', '30 días')} | Condiciones: {cot.get('Condiciones', 'Contado')}", 0, 1)
                pdf_h.ln(5)

                pdf_h.set_fill_color(30, 58, 138)
                pdf_h.set_text_color(255, 255, 255)
                pdf_h.set_font('helvetica', 'B', 9)
                pdf_h.cell(25, 8, "SKU", 1, 0, 'C', True)
                pdf_h.cell(75, 8, "Descripcion", 1, 0, 'L', True)
                pdf_h.cell(30, 8, "Presentacion", 1, 0, 'C', True)
                pdf_h.cell(20, 8, "Cant.", 1, 0, 'C', True)
                pdf_h.cell(40, 8, "Subtotal", 1, 1, 'R', True)

                pdf_h.set_font('helvetica', '', 9)
                pdf_h.set_text_color(50, 50, 50)
                for item in cot['Items']:
                    pdf_h.cell(25, 7, str(item['SKU']), 1, 0, 'C')
                    pdf_h.cell(75, 7, str(item['Descripcion']), 1, 0, 'L')
                    pdf_h.cell(30, 7, str(item['Presentacion']), 1, 0, 'C')
                    pdf_h.cell(20, 7, str(item['Cantidad']), 1, 0, 'C')
                    pdf_h.cell(40, 7, f"${item['Subtotal']:,.2f}", 1, 1, 'R')

                pdf_h.ln(5)
                pdf_h.set_font('helvetica', 'B', 10)
                pdf_h.cell(0, 6, f"Subtotal: ${sub_c:,.2f} MXN", 0, 1, 'R')
                pdf_h.cell(0, 6, f"IVA (16%): ${iva_c:,.2f} MXN", 0, 1, 'R')
                pdf_h.cell(0, 6, f"Total a Pagar: ${total_c:,.2f} MXN", 0, 1, 'R')

                pdf_bytes_hist = bytes(pdf_h.output())
                
                st.download_button(
                    label=f"📄 Descargar Cotización #{i+1} en PDF",
                    data=pdf_bytes_hist,
                    file_name=f"Cotizacion_NEMET_{i+1}_{cot['Cliente'].replace(' ', '_')}.pdf",
                    mime="application/pdf",
                    key=f"btn_pdf_hist_{i}"
                )
                
                st.markdown("---")
                if st.button(f"🗑️ Eliminar Cotización #{i+1}", key=f"del_{i}"):
                    st.session_state["cotizaciones_historial"].pop(i)
                    st.rerun()
    else:
        st.info("No hay cotizaciones guardadas en esta sesión.")