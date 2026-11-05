# Cotizador General de Catálogo — formato del CSV del proveedor

**Fecha:** 05 de noviembre de 2026
**Archivos de origen:** `Cotizador_Catalogo_Epoxy_Art_Completo.csv` (681 B) y
`Cotizador_Catalogo_Epoxy_Art_Completo.xlsx` (el «.xml» del que hablaba el cliente:
**es un `.xlsx` renombrado**, OOXML real — ni Excel 2003 XML (SpreadsheetML) ni XML genérico).

---

## 1. Los dos archivos y cuál manda

| | CSV (681 B) | XLSX (11.6 KB) |
|---|---|---|
| Qué trae | El **presupuesto de muestra con precios capturados**: 4 ítems + `TOTAL GENERAL 4.00 / $1,800.00` | La misma plantilla **sin precios** + **Catálogo de Precios** (70 renglones: `Costo Proveedor`, `Precio Público Sugerido`, escalas Min/Max) |

* **Para el formato y el presupuesto** (ítems, unidades, precios, TOTAL): **manda el CSV** — es
  el único que trae precios capturados.
* **Para costos, escalas y precios unitarios**: **manda el XLSX** (Catálogo de Precios).
* **Rendimiento y dosis**: ninguno de los dos los trae → siguen mandando los de `Cat_Productos`
  (el archivo que sí tiene precios, costos, rendimiento y dosis).

**Defectos del XLSX que NO se aplicaron:**

1. Renglón «Kit Borlas Pulimento» duplicado y corrupto (`2 / 373.11 / 2`): era el fragmento del
   precio **$2,373.11** de **EPOXY SPIKES PREMIUM**, que por eso aparecía faltante en el
   catálogo. Restaurado con $2,373.11 / $2,498.00.
2. Columna «Línea» desplazada (Epo Deep marcado Herramienta, Primer marcado Complemento…):
   manda la línea de `Inventario` (Resina / Complemento / Herramienta / Acabado).
3. Dos costos que difieren de la lista vieja (`Precios Nuevos Resina.csv` decía 661.13 y
   276.27): quedan los del XLSX/Inventario (EP 05 $666.13, EM 02 $279.27).

## 2. Fichas actualizadas (88 SKU conservados)

| Ficha | Qué pasó |
|---|---|
| `Inventario` | Ya estaba al día con los dos archivos: **88 SKU**, precios y costos cuadran. Sin cambios. |
| `Escalas_Precios_KG` | Ya estaba al día: **39 escalas** = lista del proveedor, con márgenes calculados. Sin cambios. |
| `Cat_Productos` | **8 precios viejos actualizados** con lo que trajeron los archivos: PASTA 60/100/250/500 g y 1 kg → **80/120/280/540/860**; POLIURETANO 5/10/20 L → **2,250/4,400/8,600** (coherentes con las escalas $450/$440/$430 por L). Rendimientos, proporciones y dosis intactos. |
| `Catalogo_Presupuestos` *(nueva)* | Los 70 renglones del catálogo del proveedor, limpios, con margen bruto calculado y línea correcta. Es la fuente del Cotizador General. |

## 3. El formato del presupuesto

Es exactamente el del CSV del proveedor (cp1252, saltos CRLF, comillas solo cuando hay comas):

```
Ítem / Producto | Unidad / Presentación | Cantidad Requerida | Precio Unitario ($) | Subtotal ($ MXN)
```

…con 15 renglones de artículo (en la muestra: 4 capturados + 11 huecos con `$0.00`) y cierre
**`TOTAL GENERAL`** (cantidad total y importe total). La muestra del proveedor:

| Ítem | Unidad | Cant. | P. Unitario | Subtotal |
|---|---|---:|---:|---:|
| Epoxy pisos 5K | 0 - 5.999 KG | 1.00 | $480.00 | $480.00 |
| Epoxy Primer 9KG | 0 - 9,999 KG | 1.00 | $400.00 | $400.00 |
| Jalador Epóxico Premium | PZA | 1.00 | $425.00 | $425.00 |
| Rodillo de Picos | PZA | 1.00 | $495.00 | $495.00 |
| **TOTAL GENERAL** | | **4.00** | | **$1,800.00** |

* **Cotizador**: módulo *Cotizador Comercial Profesional* → fuente **«🧾 Catálogo de presupuestos»**.
* **PDF** y **exportación CSV** (botón «📥 Descargar CSV») salen en este formato; el CSV es
  **byte a byte** el del proveedor (lo prueban `tests_cotizador_general.py`).
* La aritmética vive en `cotizador_general.py` (sin Streamlit).

## 4. Decisión: los márgenes flacos se quedan **como vienen**

Pregunta del cliente: ¿poner piso de margen de 15 % a los flacos (EPRT 01 0.50 %, rodillos
2.6–4.2 %, jalador premium 4.45 %, cuarzo +60 kg 2.2 %) o dejarlos como vienen en la lista del
proveedor? **Respuesta: como vienen.**

* Los flacos están **dentro de la muestra** (Epoxy Primer 9KG 0.5 %, Jalador Premium 4.45 % y
  Rodillo de Picos 4.23 % son 3 de los 4 ítems): un piso de 15 % los reprecia y los 4 ítems
  dejarían de dar **$1,800.00** (darían $1,983.69), rompiendo la meta del formato.
* Con precios tal cual, la meta se cumple al centavo y los márgenes reales quedan **visibles**
  en `Escalas_Precios_KG` (`Margen_Porcentaje_Sobre_Venta`) y en `Catalogo_Presupuestos`
  (`Margen Bruto (%)`) para decidir el día que se quiera reprecias.

## 5. Pruebas

```
python tests_cotizador_general.py  →  13/13 OK
```

* La muestra cotizada desde el catálogo (por UI, con AppTest) da **$1,800.00** y su CSV coincide
  **byte a byte** con `Cotizador_Catalogo_Epoxy_Art_Completo.csv`.
* Las fichas quedan verificadas: 88 SKU, 39 escalas, 8 precios nuevos y el catálogo completo
  con Epoxy Spikes Premium restaurado.
* El PDF lleva el formato del CSV y no filtra costo ni margen.
