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
   (`Unnamed`, `Unnamed`, `PrecioBaseSinIVA` duplicada), así que el libro queda con 14 columnas
   limpias.
2. **Se guardó la plantilla subida** en `respaldos/Sistema_Inventario_2026-10-31_sin_SKU.xlsx`
   para que nada se pierda: si esa plantilla traía capturas nuevas, se pueden copiar a mano.
3. **La app ya no permite inventario sin SKU** (`sku_nemet.py`):
   * al cargar la hoja, cualquier `SKU` vacío (`""`, `None`, `NaN`, `NONE`, `-`) recibe un código;
   * el módulo de inventario avisa cuántos generó y ofrece **🔖 Guardar SKUs generados** para
     fijarlos en el Excel;
   * al guardar el inventario se aplica la misma regla, de modo que un renglón nuevo o un código
     borrado por accidente nunca dejan el archivo sin SKU;
   * los SKUs ya capturados no se reescriben ni se reenumeran.
4. **Pruebas**: `tests_sku.py` (18 casos, sin Streamlit) y `tests_sku_app.py` (4 casos, AppTest
   sobre una copia temporal con la hoja vacía, reproduce este incidente de punta a punta).

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
python tests_sku.py        # 18/18 reglas de generación y datos reales del Excel
python tests_sku_app.py    #  4/4 la app rellena, avisa y guarda los SKUs faltantes
python tests_cotizador.py  # 20/20 el cotizador vuelve a encontrar los productos del inventario
```

## 6. Si vuelve a pasar

* Al subir un Excel nuevo, revisa que la hoja `Inventario` traiga la columna `SKU` con códigos y
  que los nombres de `Descripcion` existan en `Cat_Productos`.
* Si llega sin códigos, la app los genera sola: entra a **📦 Control de Inventario y Edición** y
  pulsa **🔖 Guardar SKUs generados**.
* El dashboard avisa cuando hay SKUs repetidos; conviene resolverlos antes de cotizar.
