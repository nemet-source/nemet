# Corrección: los pigmentos se cotizaban como resinas (COT-2026-003)

**Alcance:** `cotizador_calculo.py` (nuevo), `app.py`, la hoja `Cat_Productos` del Excel y
las pruebas `tests_cotizador.py` / `tests_cotizador_app.py`.

## Qué pasaba

La hoja `Cat_Productos` traía, en las filas de **EPOXY PASTA**, **EPOXY TINTA**,
**PIGMENTOS FOTOLUMINISCENTES** y **EPOXY METAL PERLADO**, un `Rendimiento = 1`
(kg/m²·mm). El cotizador aplicaba la fórmula de las resinas —`m² × mm × rendimiento`—,
así que 16 m² de pasta pedían **16 kg** (100 veces los 160 g de la receta: 10 g/m²) y la
tinta **16 L**. Los productos base sí estaban bien: EPOXY PRIMER `0.3333` kg/m²·mm
(247 g de A + 86 g de B por m²) y EPOXY PISOS `1.2` kg/m²·mm (800 g + 400 g).

### Reconstrucción de COT-2026-003 (16 m², 1 mm)

| Renglón | Cantidad | Precio | Total |
|---|---|---|---|
| EPOXY PRIMER (kilo exacto, escala 0–9.999 kg) | 5.3328 kg | $400.00/kg | $2,133.12 |
| EPOXY PISOS (cubeta) | 1 × 20 kg | $8,000.00 | $8,000.00 |
| **EPOXY PASTA** ← error | 16 × 1 kg | $840.00 | $13,440.00 |
| **EPOXY TINTA** ← error | 16 × 1 L | $550.00 | $8,800.00 |
| **Total (IVA incl.)** | | | **$32,373.12** |
| Subtotal sin IVA que imprimía el PDF | | | **$27,907.86** |

El subtotal del documento ($27,907.86 = $32,373.12 ÷ 1.16) coincide centavo a centavo, así
que esos cuatro renglones explican la cotización completa: **$22,240 de los $32,373 eran
pigmento inflado**.

## Cómo queda el mismo trabajo

| Renglón | Cantidad correcta | Cómo se calcula | Total |
|---|---|---|---|
| EPOXY PRIMER (kilo exacto) | 5.3328 kg | 16 m² × 1 mm × 0.3333 | $2,133.12 |
| EPOXY PISOS (cubeta) | 1 × 20 kg cubre los 19.2 kg | 16 m² × 1 mm × 1.2 | $8,000.00 |
| EPOXY PASTA | 2 × 100 g (160 g) | 16 m² × 10 g/m² = 160 g | $224.00 |
| EPOXY TINTA | 1 × 1 L (960 g cubiertos) | 16 m² × 60 g/m² = 960 g | $550.00 |
| **Total (IVA incl.)** | | | **$10,907.12** |
| Subtotal sin IVA | | | $9,402.69 |
| IVA (16 %) | | | $1,504.43 |

**Ahorro para el cliente: $21,466.00 (66.3 %).** La pasta también puede venderse en 250 g
($270) o 1 kg ($840) si se prefiere una sola pieza; el cotizador recomienda la más
económica y deja ver todas las presentaciones con su margen.

## Qué cambió en el sistema (para que no vuelva a pasar)

1. **Datos:** `Cat_Productos` ganó la columna `Dosis_g_m2` (g/m²) más `Fuente_Dosis`, y se
   vació el `Rendimiento` heredado de las 19 filas de pigmentos. Valores de partida:
   pasta `10`, metalizados `8` y fotoluminiscentes `10` g/m² (manual/criterio NEMET) y
   tinta `60` g/m² (≈1 L por 16 m²; editable).
2. **Lógica única:** `cotizador_calculo.py` decide la fórmula por familia —resina por
   espesor o pigmento por dosificación—, calcula `unidades_para_cubrir` y elige la
   presentación más económica. La app lo importa; no hay una segunda aritmética.
3. **Reglas de seguridad:**
   * Un nombre de familia pigmentada **nunca** se calcula con `Rendimiento`, aunque la hoja
     lo traiga (así una hoja vieja tampoco infla).
   * Sin `Dosis_g_m2` se usa `DOSIS_PIGMENTO_DEFAULT_G_M2 = 10 g/m²` y se avisa en pantalla.
   * El cotizador muestra la dosificación con su fuente y **permite ajustarla** antes de
     agregar al carrito (y al cambiar de pigmento se reinicia a la del catálogo).
   * El espesor se deshabilita para pigmentos; el renglón del PDF queda como `[16.00 m²]`
     sin «× 1 mm», y los pigmentos no ofrecen «kilo exacto» a granel.
   * Las familias sin rendimiento ni dosificación se listan en un desplegable: ya no
     desaparecen en silencio del cotizador por área.

## Cómo ajustar una dosificación

* **Definitiva:** edita la celda `Dosis_g_m2` de la familia en `Cat_Productos` (una vez por
  fila, la app toma la primera y la usa para todas las presentaciones) y anota el origen en
  `Fuente_Dosis`.
* **Puntual, sin tocar el Excel:** cambia el número en «Dosificación del pigmento (g/m²)»
  dentro del cotizador; el detalle interno del carrito guarda la dosis aplicada.

## Pruebas

```bash
python tests_cotizador.py      # 20/20  aritmética y datos (sin Streamlit)
python tests_cotizador_app.py  #  7/7   la interfaz real: pasta 160 g / 2×100 g / $224,
                               #        tinta 1 L / $550, PISOS 20 kg, PRIMER 5.33 kg
```

`tests_cotizador.py` incluye el caso «catálogo viejo» (rendimiento 1 y sin dosis) para
garantizar que 16 m² de pasta vuelven a dar 160 g, nunca 16 kg. Las suites anteriores siguen
igual: `tests_bateria.py` 29/29, `tests_autenticacion.py` 55/55 y `tests_flujo_app.py`
16/18 (los dos fallos son de Overpass y ya existían sin esta corrección).
