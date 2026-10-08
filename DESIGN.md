# DESIGN.md

Dirección: **Desclasificado**. Fotocopia fría, tinta y tachados. El sitio es un archivo de expedientes; cada decisión visual sale de esa metáfora o no entra.

## Diales

| Dial | Valor (1-10) | Qué significa aquí |
|---|---|---|
| ENERGY | 6 | Titulares enormes y sellos inclinados, pero fondo plano y sin degradados. |
| RHYTHM | 5 | Filas de índice regulares; los saltos de escala se reservan para H1 y caso destacado. |
| MOTION | 3 | Solo dos movimientos con función: el tachado que se levanta y la barra de escaneo en curso. Todo se apaga con `prefers-reduced-motion`. |

## Tipografía

- **Archivo (condensada, 800-900)**: titulares y cifras. Parece rótulo de carpeta.
- **Literata**: texto de lectura de los relatos. Serif pensada para párrafos largos.
- **JetBrains Mono, mayúsculas con tracking 0.06-0.18em**: solo metadatos de expediente (EXP-0001, fuente, fecha, etiquetas, botones). Motivo: imita el mecanografiado de una ficha. El cuerpo del texto nunca va en mono ni en mayúsculas.

## Color

- Claro (por defecto): papel `#e8ebe8`, tinta `#0d1015`.
- Oscuro: sistema o elección manual, tinta como fondo.
- Un rojo (`--accent`, `#b82511` en claro) para sello, conspiración y botón Escanear. Horror azul, paranormal verde: el color codifica categoría, no decora.
- Marcatextos amarillo `--hl` solo para lo que se "desclasifica".
- Todo texto cumple 4.5:1 sobre todas las superficies donde aparece, incluido hover (lo comprueba `tests/test_ui.py`).

## Layout

- Contenedor de 1240 px, índice en filas con carril izquierdo (id y categoría).
- Radio de 2 px en todo; la única forma redonda es el botón de tema.
- Zonas táctiles de 44 px como mínimo en todo control.

## Motivos permitidos

- Sello inclinado, tachado que se revela al pasar el cursor, enfocar o en pantallas sin hover.
- Sombra dura (`--shadow-hard`) solo en hover.

## Motivos descartados

- Flechas decorativas en bandas y CTA: la banda entera ya es un enlace.
- Etiquetas pequeñas sobre el H1 que repitan lo que dicen el H1, la entradilla o el pie.
- Indicadores de estado animados sin estado: el punto de Escanear solo parpadea mientras escanea.
- Rayas largas en textos visibles.
