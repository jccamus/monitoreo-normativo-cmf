> **Nota de reconciliación (30-07-2026).** Este documento se escribió antes de
> implementar y varias decisiones cambiaron al chocar con los documentos
> reales. Las secciones marcadas con **⟳ En la implementación** registran en
> qué quedó cada una; el texto original se conserva porque el *rationale*
> sigue siendo válido aunque el detalle haya cambiado. La fuente de verdad
> sobre el comportamiento actual es `CLAUDE.md`, no este archivo.

## Context

La CMF publica resoluciones normativas en una URL fija con parámetros de búsqueda. La página lista acuerdos del Consejo que pueden crear, modificar o derogar Normas de Carácter General (NCG), aprobar Consultas Públicas, o emitir Circulares. Cada entrada enlaza a uno o más documentos PDF con el texto completo de la norma.

El sistema debe ejecutarse diariamente sin intervención humana, persistir su estado en el propio repositorio GitHub, y publicar automáticamente un panel de control en GitHub Pages.

No existe sistema previo — es un proyecto nuevo desde cero.

## Goals / Non-Goals

**Goals:**
- Detectar diariamente resoluciones nuevas en cmfchile.cl
- Parsear PDFs para extraer estructura normativa (NCG afectadas, acciones, vigencia)
- Generar JSON diferencial diario (solo novedades vs. estado anterior)
- Manejo especial para referencias a capítulos RAN y MSI
- Rastrear archivos normativos a crear/modificar/eliminar
- Panel de control en GitHub Pages con tabla y línea de tiempo
- Todo automatizado vía GitHub Actions, sin costo de infraestructura

**Non-Goals:**
- Almacenar el texto completo de las normas (solo referencias estructurales)
- Usar LLMs para interpretación (solo parsing determinista con Python)
- Cubrir otras fuentes normativas fuera de cmfchile.cl
- Autenticación o acceso restringido (todo público)
- Notificaciones push o alertas en tiempo real

## Decisions

### D1: Clave de unicidad = fecha + número de resolución
**Rationale**: El número de resolución es único dentro de un año; combinado con la fecha es globalmente único y estable. Alternativa considerada: solo número — descartado porque el sitio CMF puede mostrar resoluciones de años distintos con numeración similar.

### D2: JSON diferencial con `state.json` como referencia
**Rationale**: La URL CMF cambia poco; descargar y comparar todo cada día es ineficiente. `state.json` guarda el set completo de claves vistas; el diferencial diario contiene solo entradas nuevas. Alternativa considerada: base de datos SQLite — descartado para mantener todo en archivos planos sin dependencias de servidor.

```
data/
  state.json          → { "seen": ["2026_0564", ...] }
  daily/
    2026-05-06.json   → { "date": "...", "new_entries": [...] }
```

**⟳ En la implementación**: la clave quedó `YYYY_NNNN` —año más número
rellenado a 4 dígitos, `diff.make_key`— y no `fecha-completa_número`. El
día no aporta unicidad y la fecha del listado es la de publicación
*original* de la norma, muchas veces de hace décadas, así que incluirla
habría hecho la clave inestable. **Cambiar este formato invalida todo el
historial.**

### D3: PDF parsing con pdfplumber como librería principal
**Rationale**: pdfplumber maneja bien PDFs de texto digital (que es el caso CMF) y permite extracción por página con acceso al layout. PyMuPDF como fallback si pdfplumber falla en algún documento. Alternativa considerada: LLM — descartado por costo operacional.

Patrones regex identificados en documentos reales:
```python
NCG_NUM     = r"NORMA DE CARÁCTER GENERAL N°(\d+)"
RESOLUCION  = r"Resolución Exenta N°(\d+), de fecha (\d{1,2} de \w+ de \d{4})"
SESION      = r"Sesión Ordinaria N°(\d+) de (\d{1,2} de \w+ de \d{4})"
NORMA_MOD   = r"(MODIFICACIONES? )?NORMA DE CARÁCTER GENERAL N°(\d+)"
ACCION      = r"(Agréguese|Intercálase|Elimínese|Sustitúyase|Derógase|Modifíquese|Reemplácese)"
RAN_CAP     = r"Capítulo ([IVXLC\d][\w.-]*) (?:de la )?(?:Recopilación Actualizada de Normas|RAN)"
MSI_REF     = r"Manual de Sistemas de Información"
VIGENCIA    = r"a contar de[l]? (.+?)[\.\n]"
```

**⟳ En la implementación**: estos patrones son el punto de partida, no lo que
hay hoy en `parser.py`. Dos cambios de fondo:

- **Las frases de varias palabras se construyen con `_frase()`**, que las
  vuelve tolerantes al salto de línea. Los PDF cortan donde termina el
  renglón, así que el mismo documento escribe `NORMA DE CARÁCTER GENERAL
  N°550` y `NORMA DE CARÁCTER\nGENERAL N°550`. Con el espacio literal de
  arriba, la NCG 564/2026 no reconocía ni la norma que modifica ni su propio
  número.
- **`VIGENCIA` tal como está escrito arriba es exactamente el bug más caro
  del proyecto.** Aplicado al documento completo, «la primera fecha que
  encuentre» es siempre la del encabezado: las 126 entradas del histórico con
  vigencia fechada repetían, sin una sola excepción, la fecha del propio
  documento. La vigencia se extrae ahora **sólo dentro de su sección**, y sin
  sección reconocida el valor es `"no especificado"`. Un hueco visible es
  mejor que un dato inventado.

### D4: GitHub Actions con commit automático al repo
**Rationale**: El runner escribe los JSONs generados y hace `git commit + push` al final del workflow. Esto mantiene historial de cambios auditable y dispara automáticamente el rebuild de GitHub Pages. Se requiere el permiso `contents: write` en el workflow.

### D5: Panel de control como HTML estático en `/docs`
**Rationale**: GitHub Pages sirve `/docs` directamente. Un archivo `index.html` con JavaScript lee los JSONs desde el mismo repo (fetch relativo). Sin build system, sin framework — HTML + JS vanilla para minimizar complejidad de mantenimiento. La línea de tiempo se renderiza con una tabla HTML ordenada por fecha.

**⟳ En la implementación**: se mantuvo «sin build system y sin framework»,
pero **el HTML no lee JSONs desde el browser**. `dashboard.py` renderiza del
lado del servidor y escribe `docs/index.html` con los datos ya incrustados;
el JS que queda sólo maneja pestañas, filtros, búsqueda y despliegue de
detalle. Se evita así una dependencia de red en cada visita y cualquier
problema de CORS o de rutas relativas.

La línea de tiempo tampoco es «una tabla ordenada por fecha»: agrupa **por
NCG afectada** (`_agrupar_por_norma`), sólo incluye las normas tocadas 2 o
más veces —un evento suelto no es una línea de tiempo— y ordena **por
cantidad de eventos**, para que abra con las normas más movidas y no con la
NCG N°1. Dentro de cada norma sí van cronológicos. Cada evento indica quién
actuó y cómo: «2021-07-30 · Modificada por NCG N°458».

El panel terminó con **cuatro pestañas** (Agenda de tareas, Cambios
relevantes, Revisión manual, Listado completo) y no sólo la tabla y la línea
de tiempo previstas acá.

### D6: Frases clave como filtro en el scraper (no en el parser)
**Rationale**: Filtrar en la capa de scraping reduce el número de PDFs a descargar. Las frases son:
- `APRUEBA CONSULTA PÚBLICA DE LA NORMA DE CARÁCTER GENERAL`
- `POSPONER EL PLAZO LÍMITE DE LA CONSULTA PÚBLICA`
- `MODIFICA LA NORMA DE CARÁCTER GENERAL`
- `APRUEBA NUEVA NORMATIVA`
- `EMITE CIRCULAR`

**⟳ En la implementación**: `fetch.FRASES_CLAVE` creció de estas 5 a **37**
(ajustes técnicos, derogaciones, instrucciones, modificaciones de circulares
y oficios…) para ampliar la red de captura. La decisión de fondo se mantiene
—filtrar en la capa de scraping para no bajar PDFs de más—, pero cambió el
criterio: **el filtro captura ancho y el clasificador posterior decide la
categoría**, no al revés.

Ojo con la asimetría que esto genera: la clasificación (`store.TIPO_ACUERDO_MAP`)
tiene 6 categorías contra 37 frases de captura, así que la mayoría de las filas
no calza con ninguna y cae al centinela `"Otro"` (364 de 607). Es esperado, no
un bug. `otro-a-clasificar.csv` en la raíz es el insumo para decidir qué
categoría nueva crear.

## Risks / Trade-offs

| Riesgo | Mitigación |
|--------|------------|
| CMF cambia estructura HTML del sitio | Scraper falla con error explícito; alerta en Actions log |
| PDF con layout inesperado no parseable | Guardar entrada con `parsed: false` y URL para revisión manual |
| GitHub Actions falla silenciosamente | Notificación de fallo nativa de GitHub Actions vía email |
| Numeración de resoluciones se reinicia anualmente | La clave incluye año extraído de la fecha |
| PDFs escaneados (no texto digital) | Detectar y marcar como `requires_ocr: true`; fuera de scope v1 |
| Rate limiting de cmfchile.cl | Sleep entre requests (2-3 segundos); el scraper corre una vez al día |

**⟳ En la implementación**, dos filas quedaron distinto:

- **No existe `requires_ocr`.** Los PDF ilegibles usan `parsed: false`, el
  mismo marcador de la fila anterior. Y resultó ser **un corte por época**
  más que un caso raro: la CMF digitalizó su archivo alrededor de 2020, y de
  las 607 entradas hay 426 degradadas, casi todas anteriores a esa fecha.
  Quedan fuera de la ventana de 5 años del panel, así que no afectan lo que
  se muestra. Hay dos variantes y ninguna alcanzable sin OCR de verdad:
  imagen pura, y OCR de mala calidad incrustado —que es peor, porque parece
  texto y ningún regex de dominio lo puede leer—.
- **«Scraper falla con error explícito» se cumplió, pero hubo que ampliarlo.**
  No basta con detectar el error de red: la CMF devuelve cada tanto un
  **HTTP 200 cuyo cuerpo no trae la tabla**, y eso abortaba la corrida de
  inmediato (2 de 30 runs programados se perdieron así). `_get_con_reintentos`
  recibe ahora un validador y el parseo mismo cuenta como criterio de éxito,
  así que una respuesta inservible entra al backoff como cualquier otra falla.

## Migration Plan

1. Crear repositorio GitHub público
2. Activar GitHub Pages desde `/docs`
3. Añadir secret `GITHUB_TOKEN` (ya disponible por defecto en Actions)
4. Primer run manual para poblar `state.json` con el historial existente
5. Activar cron en el workflow para ejecución diaria

## Open Questions

_(todas resueltas)_

**Decisiones tomadas:**
- **Cron**: `0 11 * * *` (UTC) = 8:00 AM hora Chile en verano
- **Bootstrap**: el primer run manual debe retroceder hasta enero 2024 (`--from 2024-01-01`)
- **Notificaciones**: franja destacada en la parte superior del panel de control (no email ni Slack); aparece cuando el último diferencial diario contiene entradas nuevas

**⟳ En la implementación**: la franja no se construyó. Lo que hay es el
**resaltado de las filas nuevas** en el Listado completo: `_render_tabla`
recibe las entradas del diferencial más reciente y les pone la clase
`.nueva`. La tarea 7.6 quedó marcada como hecha sin que la franja existiera
—ver la nota en `tasks.md`—.

Al usarlo apareció una razón para no reponerla tal cual: `docs/index.html`
cambia todos los días aunque no haya novedades, porque el Cuadro de mando
estampa la fecha de hoy, y `data/daily/` es disperso por diseño (entre mayo y
julio de 2026 hubo 6 archivos, no 80). Una franja que diga «hay novedades»
tiene que colgar de la existencia de un archivo diario nuevo, no de que el
build haya corrido.
