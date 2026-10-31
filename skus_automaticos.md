# SKUs automáticos y recuperación del inventario (31-oct-2026)

## 1. Qué se reportó

En la app desplegada, la columna `SKU` del inventario aparecía con **`NONE`** en todas las
filas: el inventario seguía listando los productos, pero ningún renglón tenía código, así que
las cotizaciones y el PDF salían sin SKU.

## 2. Qué había pasado

El último cambio del repositorio (`3f45cd3 · Add files via upload`, 31-oct-2026 21:04 UTC)
reemplazó `Sistema_Inventario_NEMET_Final.xlsx` por una **plantilla vieja** del libro. La
comparación con la versión buena anterior (`fdecb4e5 · 07-oct-2026`, «Cotizador: los pigmentos
se dosifican en g/m² y no por espesor») muestra que la plantilla llegó incompleta:

| Aspecto | Versión buena (07-oct) | Plantilla subida (31-oct) |
|---|---|---|
| Renglones de producto | 88 | 70 (+18 filas vacías al final) |
| Columna `SKU` | 88 códigos (`EDT 01`, `EPT 04`, `POLIU 02`…) | **vacía en las 88 filas** |
| Costo de compra | columna `PrecioCompra` con el costo con IVA del proveedor | **no existe** (se pierde el margen real) |
| Precios | precio de venta por presentación (con IVA) | escalas del proveedor por kg/L |
| Nombres de producto | los de `Cat_Productos` (`EPOXY PISOS (A Y B) TRANSPARENTE`…) | reescritos (`Epoxy pisos 5K`…) |
| Columnas | `SKU, Descripcion, Presentacion, …, ValorInventario` | `SKU, Producto, Unidad de venta, …, Margen bruto (%), Línea` |

Consecuencias que no se ven a simple vista:

* **Se perdieron 18 SKUs**: `EPOXY-MICA` y `EPOXY-MICA (combinado)` (4), `EPOXY-GRANO` y su
  combinación (4), `PIGMENTOS FOTOLUMINISCENTES` (3), `EPO-PAINT`, `ARENA SILICA`, `MEZCLADOR`,
  `COPA DIAMANTE`, `BÁSCULA DIGITAL` y `JALADOR NIVELADOR`.
* **El cotizador por área quedó inservible**: relaciona inventario y `Cat_Productos` por nombre
  exacto, y con los nombres nuevos **ningún** producto coincidía (respondía «Producto no
  encontrado en inventario»).
* **La columna `Línea` venía corrida** dos filas: `Cáscara de Naranja 8K` aparecía como
  *Acabado*, `+20K` y todo `Estuco Veneciano` como *Herramienta*.
* Los precios por kg de la plantilla no son los precios de venta por presentación que usa la
  app para cotizar, y sin `PrecioCompra` el margen de cada SKU se pierde.

## 3. Qué se hizo

1. **Se restauró la hoja `Inventario`** desde la versión buena del 07-oct: 88 SKUs, `PrecioCompra`
   y nombres compatibles con `Cat_Productos`. Se limpiaron tres columnas basura que arrastraba
   (`Unnamed`, `Unnamed`, `PrecioBaseSinIVA` duplicada), así que el libro queda con 15 columnas
   limpias (14 del catálogo + `Línea`).
2. **Se guardó la plantilla subida** en `respaldos/Sistema_Inventario_2026-10-31_sin_SKU.xlsx`
   y, en vez de copiarla a mano, **se migró con un programa** (`herramientas/migrar_plantilla.py`,
   lógica en `migracion_plantilla.py`): ver la sección 7.
3. **La app ya no permite inventario sin SKU** (`sku_nemet.py`):
   * al cargar la hoja, cualquier `SKU` vacío (`""`, `None`, `NaN`, `NONE`, `-`) recibe un código;
   * el módulo de inventario avisa cuántos generó y ofrece **🔖 Guardar SKUs generados** para
     fijarlos en el Excel;
   * al guardar el inventario se aplica la misma regla, de modo que un renglón nuevo o un código
     borrado por accidente nunca dejan el archivo sin SKU;
   * los SKUs ya capturados no se reescriben ni se reenumeran.
4. **Pruebas**: `tests_sku.py` (21 casos, sin Streamlit), `tests_sku_app.py` (7 casos, AppTest:
   la hoja vacía y el libro migrado) y `tests_migracion.py` (24 casos con los precios, costos,
   `Línea` y el catálogo completo).

## 4. Cómo se arman los códigos

Formato `PREFIJO NN`, igual que el catálogo histórico:

| Regla | Ejemplo |
|---|---|
| El prefijo es el histórico de la familia | `EPOXY PISOS (A Y B) TRANSPARENTE` → `EPT` |
| Un nombre reescrito hereda el prefijo de su familia | `Epoxy pisos 5K` → `EPT 01` |
| El consecutivo sigue el tamaño de la presentación, con unidades reales | `20 ml` → `ET 01` … `1 L` → `ET 06` |
| Una familia nueva recibe el acrónimo de sus palabras | `SELLADOR POLIASPÁRTICO 5 L` → `SP 01` |
| Dos familias nunca comparten prefijo | si `EP` es de pasta, pisos usa `EPO`/`EPT` |
| Filas de relleno sin descripción | se quedan sin SKU |

La correspondencia familia → prefijo vive en `ALIAS_FAMILIAS` (`sku_nemet.py`). Si algún día se
renombra una familia completa, basta agregar su nombre normalizado a esa tabla para que los
códigos nuevos sigan la nomenclatura correcta.

## 5. Cómo verificarlo

```bash
python tests_sku.py        # 21/21 reglas de generación, datos reales y plantilla migrada
python tests_sku_app.py    #  7/7 la app rellena, avisa y guarda los SKUs faltantes
python tests_migracion.py  # 24/24 nombres, precios por presentación, costos, línea y catálogo
python tests_cotizador.py  # 20/20 el cotizador vuelve a encontrar los productos del inventario
```

## 6. Si vuelve a pasar

* Al subir un Excel nuevo, revisa que la hoja `Inventario` traiga la columna `SKU` con códigos y
  que los nombres de `Descripcion` existan en `Cat_Productos`.
* Si llega sin códigos, la app los genera sola: entra a **📦 Control de Inventario y Edición** y
  pulsa **🔖 Guardar SKUs generados**.
* El dashboard avisa cuando hay SKUs repetidos; conviene resolverlos antes de cotizar.

## 7. La migración de la plantilla (misma fecha)

La plantilla no solo venía sin SKU: tampoco estaba en el formato del catálogo. Se convirtió con
`migracion_plantilla.py` (probado en `tests_migracion.py`) y se aplica al libro con

```bash
python herramientas/migrar_plantilla.py --revisar   # reporte, no escribe nada
python herramientas/migrar_plantilla.py             # actualiza el Excel
```

### 7.1 Qué se convirtió

| Plantilla | Estructura de la app |
|---|---|
| `Producto` reescrito (`Epoxy pisos 5K`) | `Descripcion` = nombre de `Cat_Productos` (`EPOXY PISOS (A Y B) TRANSPARENTE`) |
| `Unidad de venta` = tramo (`0 - 5.999 KG`) | `Presentacion` real (`1.5 kg`, `60 g`, `1 Pz`) |
| `Costo proveedor` y `Precio sugerido` **por kg/L** | `PrecioCompra` y `PrecioPublicoIVA` **por presentación**, con IVA |
| `Línea` corrida dos filas | `Línea` recalculada por familia |

Las escalas se convierten multiplicando el tramo que le toca a cada presentación:

| SKU | Presentación | Tramo del proveedor | Precio con IVA | Costo |
|---|---|---|---|---|
| `EPT 01` | 1.5 kg | 0 – 5.999 kg · $480/kg | **$720** | $542.13 |
| `EPT 06` | 20 kg | + 20 kg · $400/kg | **$8 000** | $6 029.00 |
| `EDT 02` | 4.26 kg | 0 – 4.299 kg · $560/kg | **$2 386** | $1 967.61 |
| `EP 02` | 100 g | ya era precio por bolsa | **$120** | $99.25 |
| `ESV 03` | 19 L | 19 L · $440/L | **$8 360** | $6 859.00 |
| `GW 02` | 19 L | 19 L · $225/L | **$4 275** | $3 473.77 |

Estuco y Grano Wall estaban capturados con el precio **por litro** como si fuera el del bote
completo ($440 el de 19 L), así que su margen y su cotización salían diecinueve veces abajo; la
migración los multiplica por el tamaño del bote. El poliuretano pasa de $2 090 a $2 250 en 5 L y
estrena costo (antes `PrecioCompra = 0`, es decir margen del 100 %); las pastas suben $8–$20 por
bolsa y también estrenan costo.

Resultado: **71 de las 88 fichas** traen precio y costo del proveedor, con márgenes entre 0.5 %
(EPOXY PRIMER de 0–9.999 kg, que ya venía así en la lista de precios) y 25 %.

### 7.2 La columna `Línea`

La plantilla la traía corrida dos filas: **37 de los 71 renglones** con precio declaraban una
línea distinta a la de su familia (Cáscara de Naranja como *Complemento* y *Herramienta*, Epo
Deep/Fast como *Herramienta*, tinta y pasta vacías). La migración la recalcula por familia:

| Línea | Familias |
|---|---|
| Resina | EPOXY PISOS, EPOXY PRIMER, EPO-DEEP, EPO-DEEP ULTRA, EPO-FAST, POLIURETANO |
| Acabado | CÁSCARA DE NARANJA, ESTUCO VENECIANO, GRANO WALL |
| Complemento | pastas, tintas, metalizados, fotoluminiscentes, micas, hojuelas, granos, cuarzo, veins, arena sílica, EPO-PAINT |
| Herramienta | rodillos, felpas, jaladores, cuña, mezclador, spikes, borlas, copa diamante, báscula, lijadora, bubles |

### 7.3 Huecos del catálogo que se cerraron

`Cat_Productos` no tenía 13 de las familias del inventario, así que el cotizador les ponía el
rendimiento por defecto (1.2 kg/m²·mm) sin avisar. Ahora tienen su consumo, con la fuente
anotada en `Fuente_Dosis` (las que no vienen del manual dicen «Criterio NEMET … ajustable»):

| Familia | Consumo |
|---|---|
| CÁSCARA DE NARANJA | 100 g/m² |
| EPOXY-HOJUELA (1 color) y (combinación) | 150 g/m² |
| EPOXY-GRANO y (combinación) | 2 500 g/m² |
| EPOXY-MICA y (combinado) | 8 g/m² (manual) |
| EPOXY VEINS | 20 g/m² |
| EPOXY CUARZO | 2 500 g/m² |
| ARENA SILICA | 2 000 g/m² |
| ESTUCO VENECIANO | 1.0 kg/m²·mm |
| GRANO WALL | 1.5 kg/m²·mm |
| EPO-PAINT (A y B) | 0.2 kg/m²·mm |

`Escalas_Precios_KG` ganó los tramos que solo vivían en la plantilla: poliuretano (4),
estuco veneciano (3) y grano wall (2). El cotizador ahora marca como dosificación por área todo
producto que traiga `Dosis_g_m2`, aunque su nombre no sea de los pigmentos de siempre.

### 7.4 Lo que sigue pendiente de capturar

* `EPA 01` (EPO-PAINT) y `AS 01` (ARENA SILICA) **no tienen presentación ni precio** en ninguna
  de las dos fuentes, así que la app no puede cotizarlos: faltan datos del proveedor.
* 17 fichas siguen sin `PrecioCompra` (fotoluminiscentes, grano, mica, jalador nivelador,
  mezclador, copa diamante y báscula): la plantilla no trae su costo. Mientras tanto su margen
  aparece como «desconocido» en el aviso de validación.
* Los consumos marcados «Criterio NEMET» son valores de arranque; ajústalos en `Cat_Productos`
  cuando el manual NEMET los especifique.
