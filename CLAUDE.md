# CLAUDE.md

Este archivo entrega orientación a Claude Code (claude.ai/code) para trabajar con el código de este repositorio.

## Qué es este proyecto

**Monitoreo Normativo CMF** — un scraper automatizado que corre a diario, detecta las nuevas resoluciones normativas que publica la Comisión para el Mercado Financiero (CMF) de Chile, extrae su estructura desde los PDF, guarda diferenciales en JSON por día y arma un dashboard HTML estático. Está pensado para periodistas que siguen los cambios regulatorios. El brief original está en `Propuesta - Cambios Normativos.txt` (en español, es el documento que manda).

Restricciones duras que están metidas en el diseño:
- **Sin LLM y sin base de datos.** El parsing es puro regex sobre el texto del PDF; el estado son archivos JSON versionados en el repo.
- **Sin costos.** Corre en GitHub Actions (plan gratuito) y publica en GitHub Pages.
- **Solo salida diferencial.** El JSON de cada día contiene únicamente las resoluciones que no se habían visto antes — esto lo maneja `data/state.json`.

## Estructura del repositorio

La raíz del repositorio es la raíz del proyecto: `scraper/`, `data/`, `docs/` y
`.github/` cuelgan directo de acá, y los comandos se corren desde acá. Hasta el
30-07-2026 la aplicación vivía en un subdirectorio `proyecto/` que era su propio
repo git, con la documentación afuera y sin versionar — por eso este mismo
archivo llegó a describir columnas del dashboard que no existían en el código.
Si encuentras una ruta que empiece con `proyecto/`, está vieja.

- **`scraper/`** — el pipeline. Ver «Arquitectura» más abajo.
- **`data/`** — el estado: `state.json` (claves vistas), `daily/YYYY-MM-DD.json`
  (diferenciales) y `revisiones.csv` (anotaciones manuales).
- **`docs/`** — lo que sirve GitHub Pages. `index.html` es generado; no lo edites
  a mano, se reescribe en cada corrida.
- **`openspec/`** — la propuesta, el diseño y las tareas del cambio original
  (`openspec/changes/monitoreo-normativo-cmf/`). Están reconciliados con la
  implementación y llevan notas ⟳ donde el resultado terminó siendo distinto de
  lo planificado. **Como registro de diseño valen; como descripción del sistema
  manda este archivo.**
- **`Propuesta - Cambios Normativos.txt`** — el brief original, en español. Es el
  documento que manda sobre qué tiene que hacer el producto.
- **`auditoria/`** — exportes hechos a mano, fuera del pipeline; nada de código
  los lee. El útil es `descartadas.csv` (~5.200 filas del listado CMF que
  `FRASES_CLAVE` rechazó): es el insumo para decidir qué frase agregar al filtro.
  `nuevas-a-capturar.csv` son candidatas; `agf/seguros/valores/otros-cambios-relevantes.csv`
  son cortes por cuerpo normativo de lo ya capturado.
- **`otro-a-clasificar.csv`** (raíz) — el único generado por script: agrupa las
  entradas que quedaron en `"Otro"` por su acción inicial, para decidir qué
  categoría nueva crear. **Es una foto y caduca** — se calcula con las reglas de
  clasificación vigentes al escribirlo, así que regenéralo antes de usarlo.
- **`.claude/`** — el flujo OpenSpec (skills y comandos). `.agent/` y `.gemini/`
  son las mismas skills duplicadas para otros asistentes: quedan en disco pero
  están en `.gitignore`, igual que `.claude/settings.local.json`, que es
  configuración de la máquina.

## Comandos habituales (correr desde la raíz del repo)

```powershell
pip install -r requirements.txt              # instalación por única vez
python scraper/main.py                       # corrida diaria: fetch + diff + parse + store + dashboard
python scraper/main.py --from 2024-01-01     # carga histórica desde un año dado
python scraper/dashboard.py                  # regenera solo docs/index.html (sin fetch)
python scraper/reparse.py --dry-run          # qué entradas ya guardadas corregiría un fix del parser
python scraper/reparse.py                    # re-parsea las entradas con fecha placeholder (2024+)
python scraper/reparse.py --recalcular --desde 2021-01-01   # re-parsea TODAS las del rango, no sólo las rotas
python scraper/revisar.py                    # refresca data/revisiones.csv (revisión manual)
```

Python 3.11 en CI. El código usa sintaxis de tipos `list[dict]` / `str | None`, así que necesita ≥3.10.

Tests: no hay tests. Valida los cambios corriendo `main.py` y revisando el nuevo `data/daily/YYYY-MM-DD.json` y `docs/index.html`. Para iterar sobre el dashboard sin pegarle a la CMF, corre solo `python scraper/dashboard.py`: relee todo `data/daily/` y reescribe `docs/index.html` en segundos.

**Nota sobre la invocación:** `scraper/main.py` usa imports planos entre archivos hermanos (`from fetch import …`). Córrelo como `python scraper/main.py` para que Python agregue `scraper/` al `sys.path` automáticamente. NO lo "arregles" convirtiéndolo en un paquete para correrlo como `python -m scraper.main` — los imports se van a romper.

## Arquitectura del pipeline

`scraper/main.py` orquesta un pipeline de 6 pasos. Las modificaciones normalmente tocan exactamente una etapa:

1. **`fetch.fetch_listado(from_date)`** — hace GET a la página de listado de la CMF, parsea la tabla de resultados con BeautifulSoup y filtra las filas con `FRASES_CLAVE`. Partió con las cinco frases que define el brief, pero desde entonces creció a **37** (modificaciones, ajustes técnicos, derogaciones, instrucciones…) para ampliar la red de captura — el clasificador que viene después, y no este filtro, es el que decide la categoría que ve la persona. Tiene reintentos con backoff (lineal, `30·intento`) y una pausa cortés de 2–3 s entre requests. **Falla en forma ruidosa, no silenciosa:** agotados los reintentos sin una sola fila, llama a `sys.exit(1)` para poner el build en rojo, en vez de devolver `[]` y que aguas abajo se lea por error como "sin novedades".

Lo que cuenta como intento fallido incluye **un HTTP 200 cuyo cuerpo no trae la tabla**, no sólo los errores de red. `_get_con_reintentos` recibe un `validar` opcional y `fetch_listado` le pasa el parseo mismo, así que una respuesta inservible entra al backoff como cualquier otra falla. Esto importa porque la CMF devuelve ese 200 raro cada tanto —el 28-07-2026 respondió en 0,84 s cuando lo normal son 5,4 MB— y antes abortaba la corrida de inmediato: 2 de los últimos 30 runs programados se perdieron así. El día perdido igual se recuperaba solo, porque el diff compara contra el listado completo y no incrementalmente por fecha, pero ese día no se regeneraba el dashboard.
2. **`diff.get_nuevas(resoluciones)`** — carga `data/state.json` (un set de claves `YYYY_NNNN`), se queda solo con las resoluciones no vistas y le estampa `_key` a cada una. Después de procesar con éxito, `diff.commit_nuevas` escribe las claves de vuelta. **El formato de la clave es `make_key(fecha, numero)` → `"2026_0564"` — si lo cambias, invalidas todo el historial.**
3. **`fetch.fetch_pdf(url)`** — descarga el PDF de cada resolución nueva.
4. **`parser.parse_pdf(pdf_bytes, url)`** — extrae el texto con pdfplumber, cae a PyMuPDF como respaldo y luego corre regex de dominio para poblar `ncg`, `resolucion`, `sesion`, `modifica[]`, `ran_referencias`, `msi_referencias`, `archivos_afectados`, `vigencia`, más el `tema` legible por humanos (el bloque de encabezado `REF:`) y `resumen_acciones` (hasta 6 bullets accionables del cuerpo) y, cuando no encuentra `resolucion`, una `fecha_documento` tomada de la primera fecha del encabezado. **Toda la extracción es a base de regex — mira los patrones `_*` al inicio de `parser.py`.** Las frases literales de varias palabras se construyen con el helper `_frase()`, que las vuelve tolerantes al salto de línea: los PDF cortan donde termina el renglón, así que el mismo documento escribe `NORMA DE CARÁCTER GENERAL N°550` y `NORMA DE CARÁCTER\nGENERAL N°550`. Con un espacio literal, la NCG 564/2026 —cuyo único contenido es modificar la NCG 550— no reconocía ni la norma modificada ni su propio número. Si agregas un patrón con una frase de varias palabras, pásala por `_frase()`. Cuando un PDF tiene secciones con números romanos (I, II, III…), `_parse_modificaciones` corta el cuerpo por sección y le atribuye una `vigencia` a cada una; los documentos sin secciones reciben vigencia global. El encabezado de la sección de vigencia (`_VIGENCIA_HEADING`) delimita el cuerpo. Un resultado se degrada a `parsed: False` si no encuentra ni un `ncg` ni ningún `modifica[]`.
5. **`store.ensamblar_entrada(raw, parsed)`** — combina la fila del listado con los campos parseados del PDF. Importante: prefiere la fecha exacta del PDF (`resolucion.fecha`) por sobre el placeholder derivado de la URL (`YYYY-01-01`). **`resolucion` sólo se guarda cuando el PDF declara una de verdad** (`Resolución Exenta N°X de fecha Y`): antes, cuando no había ninguna, se rellenaba con el número del nombre del archivo —que es el número del propio documento— y quedaba una "Resolución Exenta N°2.370" inexistente en 600 de 607 entradas, que el dashboard mostraba bajo el encabezado "N° Resolución". La identidad del documento vive en `documento`, no acá. Cuando el parser no encuentra ningún `modifica[]`, cae a `_modifica_desde_descripcion`, que extrae los números de NCG directamente desde la descripción del listado (marcándolos con `fuente: "descripcion_cmf"`). `modifica`, `vigencia`, `ran_referencias`, `msi_referencias`, `archivos_afectados`, `tema` y `resumen_acciones` se guardan **siempre**; antes estaban detrás de un `if parsed:` que los tiraba a la basura justo en las entradas más pobres. Luego `guardar_diferencial` escribe `data/daily/YYYY-MM-DD.json`. Esa escritura es **idempotente**: si el archivo del día ya existe (carga histórica + corrida agendada el mismo día), fusiona por `clave`, dejando que las entradas nuevas pisen a las viejas, para que gane un re-parseo corregido. Un archivo que no logra parsearse se renombra a `*.json.corrupt-<timestamp>` (el sufijo que no termina en `.json` evita que el glob `*.json` del dashboard se atore con él) en vez de descartarse en silencio.
6. **`dashboard.generar_html()`** — lee cada archivo de `data/daily/`, aplana las entradas y arma `docs/index.html` de una sola pasada. Es con diferencia el módulo más grande (~1.300 líneas, la mitad de la base de código) porque el HTML/CSS/JS va todo en línea en `_TEMPLATE` más funciones `_render_*`; no hay paso de build. Rinde **cuatro** pestañas:
   - **Agenda de tareas** — `_clasificar_tareas` reparte en **tres** columnas: ≤30 / 31–60 / 61+ días. Cuenta la vigencia propia, la de cualquier `modifica[]` y la de cualquier `vigencia.plazos[]`. Debajo va `_render_retrospectiva`, agrupada por mes, con lo que **ya debió aplicarse**: ahí caen las vigencias inmediatas, que no traen fecha propia y se fechan con la publicación vía `_fechas_vigencia`. Sin esa equivalencia, todo lo que aplica de inmediato —lo más urgente— quedaba fuera de cualquier vista con eje temporal.
   - **Cambios relevantes** — `_agrupar_por_cuerpo` reparte por cuerpo normativo (ver más abajo), recortado a los últimos 5 años. La columna «Norma» sale de `_etiqueta_documento`.
   - **Revisión manual** — los cambios de archivo sin fecha de vigencia (ver más abajo). Es el único tab que rinde contenido aunque esté vacío: que no haya pendientes es información, y un panel en blanco se lee como si algo hubiera fallado.
   - **Listado completo** — stats, botones de filtro, búsqueda libre, tabla con detalle expandible y línea de tiempo por NCG (`_agrupar_por_norma`). Ojo con las dos columnas de norma: «Norma» es el documento (`_etiqueta_documento`) y «Norma(s) afectada(s)» son las que modifica (`_normas_afectadas`). Esta última descarta el número propio cuando el documento *es* una NCG — `entrada["ncg"]` guarda el número del propio documento en ese caso, y la NCG 568 aparecía modificándose a sí misma.

     La línea de tiempo **sigue a la tabla por `data-clave`** en vez de reimplementar el filtrado: vive en su propia `<section>` y antes el filtro no la tocaba, así que al elegir «Circular» —que deja 2 filas de 607— la tabla quedaba casi vacía y abajo seguían apareciendo las 244 normas completas. Sólo entran las normas afectadas 2 o más veces (`_TIMELINE_MIN_EVENTOS`): un evento suelto no es una línea de tiempo y ya está en la tabla con todo su detalle. Van ordenadas por cantidad de eventos, no por número — por número abría en la NCG N°1, que es justo donde no hay nada que ver.

   El resaltado de filas "nuevas" toma las entradas del archivo diario más reciente (`diferenciales[0]`, que `_cargar_diferenciales` deja primero por orden descendente de nombre). Durante un tiempo `_render` llamaba a `_render_tabla(entradas, [])` con una lista vacía fija y el resaltado estaba inerte: el cálculo estaba escrito y correcto, sólo que nunca se le pasaba nada.

## La fecha de una entrada: el punto frágil del pipeline

La CMF lista cada norma con su fecha de publicación *original*, así que la única
fecha que `fetch` puede deducir es el año del nombre del PDF, que se rellena
como el placeholder `YYYY-01-01`. **La fecha real sale del encabezado del PDF**,
vía `parser._fecha_encabezado`, y `store.ensamblar_entrada` la prefiere sobre el
placeholder.

Cuando esa extracción falla, la entrada queda fechada el 1 de enero. No se cae
nada, no hay error en los logs: simplemente el cambio aparece al comienzo del
año en el dashboard y la actividad reciente se vuelve invisible. **Es el modo de
falla más caro de este proyecto porque se ve igual que "no hay novedades".** Si
alguien reporta que el dashboard "dejó de actualizarse", esto es lo primero que
hay que descartar — antes de sospechar del scraping.

`_fecha_encabezado` se ancla en la línea de guiones bajos que cierra el bloque
`REF:` y busca la fecha sólo en los 300 caracteres siguientes:

```
REF: Modifica la Circular N°2.364 para bancos.
_________________________________
15 de junio de 2026          ← la fecha del documento
CIRCULAR N°2.371
```

Dos decisiones deliberadas, cada una por un bug real:

- **Anclar en el separador, no en los primeros N caracteres.** El bloque `REF:`
  mide de una línea a más de diez. Con la ventana fija de 500 que había antes,
  `cir_2373_2026.pdf` (14 de julio de 2026) perdía su fecha por 17 caracteres.
- **No ampliar la ventana de respaldo.** Sólo se usa cuando no hay separador, y
  son 600 caracteres. Ampliarla deja entrar fechas del cuerpo: en
  `ofc_1394_2025.pdf` capturaba "15 de mayo de 2024" —una referencia a otra
  resolución— y fechaba el documento un año antes. Ante la duda, devolver `None`
  y caer al placeholder es mejor: es visiblemente sospechoso, una fecha
  plausible pero errónea no.

`_FECHA_SPAN` acepta `de` y `del` ("24 de noviembre **del** 2025"), porque la
CMF alterna entre ambas.

Límite conocido: algunos PDF son sólo imagen y no dan texto (`ofc_1402_2026`,
`ofc_1377_2025`). No hay OCR en el proyecto; esas entradas se quedan con el
placeholder.

## La vigencia: buscarla sólo dentro de su sección

`vigencia` es el campo que alimenta el Cuadro de mando y el único que responde
la pregunta que le interesa a alguien que sigue la regulación: *cuándo hay que
tener esto hecho*. Se extrae de la sección de vigencia del PDF y **de ninguna
otra parte**.

Ese "de ninguna otra parte" es la regla, no un detalle. Hasta julio de 2026,
cuando no se reconocía la sección, `_parse_vigencia_global` recibía el documento
completo y se quedaba con la primera fecha que encontrara — que en un PDF de la
CMF es siempre la del encabezado. El resultado era una fecha de entrada en vigor
con pinta de correcta y sistemáticamente falsa: **las 126 entradas del histórico
con vigencia fechada repetían, sin una sola excepción, la fecha del propio
documento.** Sin sección reconocida el valor es ahora `"no especificado"`. Mismo
criterio que `_fecha_encabezado`: un hueco visible es mejor que un dato
inventado.

`_VIGENCIA_HEADING` sigue exigiendo que la palabra ocupe la línea entera —eso es
lo que evita truncar el cuerpo al toparse con "vigencia" dentro de un párrafo,
como en `"Reemplácese el texto de la sección Vigencia de la NCG N°550…"`— pero
admite el enumerador que la precede y no exige mayúsculas. El patrón anterior
(`\n\s*VIGENCIA\s*\n`) aceptaba sólo la palabra desnuda: sobre 46 documentos de
2025–2026 reconocía 11 secciones y se le escapaban 17, entre ellas todas las
`II. VIGENCIA` / `IV. Vigencia` / `m. Vigencia`, que son la forma habitual.

**Un documento puede tener varios plazos**, y se expresan de dos maneras:

- **En viñetas** — `_parse_plazos` las desdobla en `vigencia.plazos[]`.
- **En prosa, como excepción a la regla general** — `_parse_excepciones`. La
  circular 2373/2026 dice "entrarán en vigencia a contar del 1 de enero de 2028,
  **con excepción de** los ajustes estipulados en el Capítulo 1-13 de la RAN,
  los cuales tienen vigencia inmediata": dos grupos con fechas distintas en una
  sola oración. Sin desdoblarla, el grupo que rige de inmediato —el accionable
  hoy— no existía en los datos.

Cuando hay plazos, el `inicio` global se toma del **primero** (la regla general)
y no de un barrido de la sección entera: `_parse_vigencia_global` evalúa las
frases de inmediatez antes que las fechas, así que la excepción "…tienen
vigencia inmediata" se imponía sobre el 1 de enero de 2028 de la regla.

Cada plazo lleva su `texto` y su `inicio` propio. La circular 2370/2026 es el caso canónico: unos
capítulos de la RAN rigen de inmediato y otros desde el 1 de julio de 2026. Con
un único campo `inicio` esa segunda fecha desaparecía. El dashboard lee los
plazos en `_fechas_futuras`, así que un documento escalonado entra al Cuadro de
mando por su fecha futura aunque su `inicio` global diga `"inmediata"`.

Detalles que existen por un bug real:

- **`_FECHA_SPAN` acepta el ordinal (`1°`, `1º`).** La CMF fija casi todos los
  plazos el día 1 y lo escribe así; sin esto, la fecha más importante del
  documento era justo la que no se reconocía.
- **La última viñeta se corta con `_FIN_ULTIMO_PLAZO`.** No tiene una viñeta
  siguiente que la acote, así que se tragaba el párrafo posterior.
- **El bloque de firma se recorta** (`_FIRMA`): la sección se extiende hasta el
  final del texto y el nombre de quien firma quedaba pegado al último plazo.

**Los oficios circulares no titulan una sección de vigencia**: declaran cuándo
rige el cambio en un párrafo de cierre. Para ellos hay un respaldo,
`_CLAUSULA_APLICACION`, que **sólo se usa cuando no existe la sección** y exige
que la fecha cuelgue de un verbo de aplicación ("se exigirán a contar de…",
"deben ser reportadas hasta…") y esté en la misma oración. Ese anclaje es lo que
lo distingue del bug que reemplazó: no toma "la primera fecha del documento",
toma una fecha que el documento declara como fecha de aplicación. Ante la duda
no devuelve nada.

`vigencia.fuente` registra de dónde salió el dato — `seccion`,
`clausula_aplicacion` o `ninguna`— para poder auditarlo y para distinguir lo
extraído de lo que de verdad falta.

Límites conocidos: un plazo relativo ("120 días después de su emisión", "a
contar de diciembre de 2025", sin día) queda en `"ver texto"`, no en fecha; y las
excepciones redactadas en prosa y no en viñeta no se capturan (la NCG 562/2026
dice "excepto el numeral II, que entra en vigor el 1 de julio de 2027" y esa
segunda fecha se pierde).

`_TRANSITORIAS_HEADING` reconoce "Disposiciones transitorias", que es donde
algunos documentos ponen la fecha en vez de titular "Vigencia". Se usa **sólo
como respaldo** cuando no hay sección de vigencia: hay documentos con las dos
—la NCG 562/2026 tiene `IV. DISPOSICIONES TRANSITORIAS` y `V. VIGENCIA`— y ahí
manda la de vigencia.

## Una norma que fija la vigencia de otra

La NCG 564/2026 no tiene contenido propio: lo único que hace es reemplazar la
sección Vigencia de la NCG 550. Su propia vigencia es "rige a contar de esta
fecha", pero el dato que importa —que la 550 empieza a regir el 1 de marzo de
2027— vive dentro del texto citado.

`_vigencias_impuestas` mapea número de NCG → vigencia y `_parse_modificaciones`
la usa **en vez de** la vigencia de la sección, para que la fecha quede
atribuida a la norma que la recibe y no a la que la impone. `_acotar_cita`
recorta el texto citado contando la profundidad de comillas: la cita de la 564
contiene `“CMF Supervisa”` anidado y cortar en la primera comilla de cierre
dejaba fuera el plazo del 1 de abril de 2027.

## Archivos afectados y el aviso de revisión manual

`archivos_afectados` identifica los archivos normativos del MSI por su código
(`C11`, `R06`, `E24`, `RDC01`: 1-3 letras y 2-3 dígitos) y **exige la palabra
"archivo" delante**, porque el código suelto se confunde con capítulos de la RAN
(`8-4`, `21-30`), números de tabla y códigos de campo. El patrón anterior
buscaba prosa genérica y capturaba fragmentos de oración: de 154 entradas sólo 9
tenían archivos, con "nombres" como *"un fondo"* o *"copia del poder en virtud
del cual actúa"*. Ninguno era un archivo.

Cada archivo se fecha con la viñeta de vigencia que lo nombra
(`_vigencia_de_archivo`), no con la vigencia global: la circular 2370/2026 fija
el 1 de julio de 2026 sólo para R06 y R07, y deja el resto en aplicación
inmediata.

`_ARCHIVO_NO_CAMBIO` descarta las referencias cruzadas. El oficio 1375/2025 dice
"estas operaciones se reportan exclusivamente en este archivo, **no deben
incluirse** en los archivos D32, D33 y D35": nombra tres archivos para decir
dónde *no* hay que informar. Sin el filtro entraban como archivos modificados.

**Un cambio de archivo sin fecha de vigencia se avisa, no se esconde.** El
dashboard rinde una sección "Cambios de archivo sin fecha de vigencia"
(`_requiere_revision` en `dashboard.py`) con los documentos que modifican un
archivo del MSI —lo que genera una obligación de reporte— pero de los que no se
pudo determinar desde cuándo rige.

Esa lista **no es deuda técnica pendiente de regex**. Los casos que quedan
expresan la fecha entrelazada con el ciclo de reporte: "deberá aplicarse
respecto de la información referida al cierre del mes de agosto y, por lo tanto,
enviarse en septiembre de 2025". Cuál de las dos es la vigencia —el período de
referencia o el mes de envío— es un juicio, no un patrón. Antes de agregar otro
verbo a `_CLAUSULA_APLICACION`, verifica contra el texto que la fecha que vas a
capturar es efectivamente la de entrada en vigor.

Para que la revisión sea rápida, `_fechas_candidatas` adjunta en
`vigencia.candidatas` las fechas del cuerpo con su contexto, descartando las del
encabezado y las que cuelgan de una referencia a otra norma. Se muestran
rotuladas como candidatas y **nunca** como la vigencia: presentarlas como dato
firme sería repetir el bug que originó todo esto.

## Anotar a mano lo que el parser no puede resolver

`data/revisiones.csv` es la hoja de trabajo de la revisión manual. Se edita en
Excel (separador `;`, UTF-8 con BOM, que es lo que Excel en español espera).

```powershell
python scraper/revisar.py            # crea o refresca la planilla
python scraper/revisar.py --estado   # sólo informa, no escribe
python scraper/dashboard.py          # aplica lo anotado
```

Dos clases de columnas: la persona llena la **entrada** (`vigencia`, `sin_fecha`,
`archivos`, `nota`, `revisado`) y `revisar.py` escribe el **contexto** (`norma`,
`fecha_documento`, `archivos_detectados`, `fechas_candidatas`, `pdf`) para poder
decidir sin abrir el PDF. Refrescar nunca pisa lo escrito: conserva las columnas
de entrada, agrega las filas nuevas y recalcula el contexto.

**El orden importa y no es cosmético: las de entrada van pegadas a `clave`,
antes del contexto.** Al revés la planilla es una trampa — en los documentos sin
fechas candidatas esa celda queda vacía y es la primera en blanco de la fila, así
que se escribe ahí en vez de en `vigencia`. Pasó con los 7 documentos sin pistas
en el primer uso real y el dato se perdió sin aviso. De ahí también el
guardarraíl de `cargar()`: las citas que genera `revisar.py` siempre traen
puntos suspensivos, así que un valor sin ellos en `fechas_candidatas` se escribió
a mano y se avisa por log.

Qué acepta `vigencia`:

| valor | significado |
|---|---|
| `2025-11-01` | fecha exacta declarada en el documento |
| `2025-11` | el documento fija sólo el mes → `precision: "mes"` |
| `01-11-2025`, `01/11/2025` | lo que devuelve Excel al reformatear la celda |
| `inmediata` | el documento no declara vigencia y rige desde su publicación |

`inmediata` **y no la fecha del propio documento**: la fecha afirmaría que el
texto la declara, y es exactamente la confusión que originó los bugs de este
proyecto. Aguas abajo se fecha con la publicación, igual que las que detecta el
parser.

Las otras columnas:

- `sin_fecha: si` es un resultado válido de revisión distinto de `inmediata`: el
  documento no declara vigencia **y tampoco corresponde asumir que rige desde su
  publicación**. Deja de figurar como pendiente sin inventarle una fecha.
- `archivos` permite una fecha distinta por archivo:
  `RDC40=2026-01-01;RDC02=2025-11-01`. Vacía, la vigencia aplica a todos.

**Las anotaciones viven fuera de `data/daily/` a propósito.** Editar la entrada
directamente no funciona: `reparse.py` hace `entrada.update(nueva)` y pisa todo
campo que el parser produce —incluida `vigencia`—, y `guardar_diferencial`
fusiona por `clave` reemplazando la entrada entera. Como capa aparte sobreviven
a las dos cosas.

La capa se aplica **al renderizar** (`revisiones.aplicar` en `generar_html`), no
al guardar: los datos parseados quedan intactos y una anotación nueva sólo
necesita regenerar el HTML, sin reparsear.

Una vigencia anotada se marca con `fuente: "revision_manual"` y el dashboard la
muestra como «· confirmada», nunca mezclada con lo extraído del PDF. Si el
parser después aprende a leer ese documento y propone otra fecha, gana la
anotación —quien anotó leyó el PDF— pero queda `discrepa` y `generar_html`
emite un warning, para poder retirar anotaciones que ya no hacen falta.

## Formatos de fecha que la CMF usa

`_FECHA_ALT` cubre las tres formas, y `_resolver_fecha` devuelve la fecha ISO
junto con su **precisión**:

| forma | ejemplo | precisión |
|---|---|---|
| día completo | `1° de julio de 2026` | `dia` |
| numérica | `13-07-2021` | `dia` |
| mes y año | `a partir del mes de diciembre de 2024` | `mes` |

La forma de mes es la habitual para las obligaciones de reporte. Se normaliza al
día 1 para poder ordenarla, y por eso `precision: "mes"` viaja con el dato: el
dashboard la rotula "diciembre de 2024" en vez de "2024-12-01", que afirmaría
una exactitud que el documento no da. Si consumes `vigencia.inicio` en código
nuevo, mira también `precision` antes de presentarlo como fecha exacta.

## Qué significa `parsed: False`

Significa que del PDF no se pudo sacar **nada** identificable: ni `ncg`, ni
`modifica[]`, ni la identidad del documento.

**Es un corte por época, no una falla del parser.** Tras reprocesar las 607
entradas con `--recalcular --todas` (julio de 2026), así queda el corpus:

| período | legibles | degradadas |
|---|---|---|
| hasta 1999 | 1 | 64 |
| 2000–2009 | 1 | 210 |
| 2010–2019 | 2 | 148 |
| 2020 en adelante | 177 | 4 |

La CMF digitalizó su archivo alrededor de 2020. Lo anterior son escaneos, en dos
variantes y ninguna alcanzable sin OCR de verdad:

- **Imagen pura**, sin capa de texto (`ncg_427_2018`, `cir_2243_2019`).
- **Con OCR de mala calidad incrustado**, que es peor porque parece texto: el
  `ofc_141_2001_01` entrega `OFICIOCIRCULAR`, `No 00141.`, la fecha como
  `-0%8~.2~~1` y saltos de línea cada dos palabras. Hay caracteres, pero ningún
  regex de dominio los puede leer.

No hay OCR en el proyecto y agregarlo tampoco arreglaría la segunda variante sin
un paso de normalización difuso. Todo esto queda fuera de la ventana de 5 años
del dashboard, así que no afecta lo que se muestra.

Hasta julio de 2026 significaba otra cosa y era una trampa: bastaba con no
encontrar `ncg` ni `modifica[]`, y **ninguna circular ni oficio circular tiene
número de NCG**. 399 de las 502 entradas degradadas eran documentos
perfectamente legibles que simplemente no eran NCG. El dashboard les mostraba
"⚠ PDF no procesado", que era falso, y `store` descartaba su vigencia.

La identidad ahora la resuelve `parser._identidad_documento`, que llena
`documento: {tipo, numero}` con `NCG`, `Circular` u `Oficio Circular`. El
detalle que lo hace funcionar: **exige que la línea contenga sólo el tipo y el
número**. Las menciones a otras normas dentro del bloque `REF:` llevan texto
detrás, así que un match laxo devuelve la norma equivocada — la circular
2369/2026 abre con `REF: MODIFICA CIRCULAR N°1459, QUE …` y su propio número,
`CIRCULAR N°2369`, va solo en su línea más abajo. El número admite separador de
miles (`N° 2.373`).

No confundir `documento.numero` con `ncg`: en un oficio circular que modifica la
NCG N°530, `documento` es el oficio y `ncg` es 530, la norma afectada.

Los separadores dentro de `_DOC_IDENTIDAD` son `[ \t]` y **no** `\s`, justamente
porque `\s` incluye el salto de línea y eso anula la exigencia de línea
completa: la NCG 470/2022 abre con `DEROGA\nNORMA DE CARACTER GENERAL\nN°342.`
y se identificaba como la 342, la norma que deroga. Es la excepción a la regla
de `_frase()` — aquí el salto de línea hay que bloquearlo, no tolerarlo.

`documento` se muestra en el dashboard vía `_etiqueta_documento`, que cae al
nombre del archivo PDF (`ncg_470_2022.pdf` → «NCG N°470») cuando el parser no
logra identificarlo. Eso cubre las normas conjuntas con otro regulador, que
escriben su identidad en línea con el nombre del organismo
(`COMISIÓN PARA EL MERCADO FINANCIERO NCG N° 542`) y no en una línea propia.

## La descripción del listado no es el PDF: `modifica[]` con `fuente: "descripcion_cmf"`

Cuando el parser no encuentra ningún `modifica[]` en el PDF,
`store._modifica_desde_descripcion` lo deduce de la descripción del listado.
Ese respaldo acumuló tres bugs de la misma familia —tratar la descripción como
si fuera texto normativo— y conviene tenerlos presentes antes de tocarlo:

1. **Cualquier «N° número» se tomaba por una NCG.** El patrón era
   `N[°o]\s*(\d+)`, y la descripción nombra circulares, oficios, leyes y
   decretos con esa misma forma: «MODIFICA CIRCULAR N°2022» producía una «NCG
   N°2022», «LEY N° 18490» una «NCG N°18490», el D.L. N°3.500 una «NCG N°3500».
   La línea de tiempo mostraba **244 normas donde hay 92**, con números que no
   pueden existir: la NCG más alta ronda la 570. Ahora el número exige venir
   precedido de la designación de NCG.
2. **La acción se aplicaba en bloque.** Era `"Derógase" if "DEROGA" in
   desc_upper else "Modifícase"`, o sea que un solo DEROGA en cualquier parte
   marcaba *todos* los números como derogados. El 2009_0264 modifica la NCG
   N°152 y deroga el Oficio Circular N°502; la 152 aparecía derogada. Ahora
   manda el verbo más cercano por la izquierda de *cada* mención.
3. **Nada distinguía modificar de citar.** El Oficio Circular N°502 imparte
   instrucciones «SEGÚN NORMA DE CARÁCTER GENERAL N°152»: la invoca, no la
   toca.

**Las entradas ya guardadas conservan los tres defectos**, así que el dashboard
ignora los `modifica[]` con `fuente: "descripcion_cmf"` (en `_normas_afectadas`
y en `_accion_sobre_norma`) y vuelve a deducir desde la descripción, que viaja
dentro de la entrada. Por eso el histórico se repara **al renderizar y sin
reparse**. Si algún día se corre `reparse.py --recalcular --todas`, esos
`modifica[]` quedan bien y los `continue` pasan a ser inocuos, no incorrectos.

`_accion_sobre_norma` decide entre «Derogada por», «Modificada por» y
«Referida por» buscando el último verbo antes de la mención **dentro de su
misma oración**. Los dos detalles que lo hacen funcionar existen por un caso
real: el corte de oración ignora los puntos de «D.L.» y «N°3.500» (si no, la
mención de la NCG N°318 quedaba en una «oración» que empezaba en `L. N° 3500
DE 1980, Y A LA`, sin el verbo que la gobierna), y los verbos excluyen las
nominalizaciones que nombran la materia en vez de un cambio —`INCORPORA(?!CION)`,
porque «la INCORPORACIÓN de bienes raíces» hacía pasar por modificación una
simple cita—.

## Arreglar el parser no arregla los datos ya guardados

`data/state.json` impide reprocesar una resolución ya vista, así que una mejora
del parser sólo afecta a lo que venga de aquí en adelante. Para propagarla al
histórico está `scraper/reparse.py`: recorre las entradas afectadas, vuelve a
bajar el PDF y reescribe la entrada dentro de su archivo `data/daily/`, sin
tocar `state.json`.

Por defecto toma las de fecha placeholder desde 2024; `--degradadas` suma las de
`parsed: False` y `--desde` mueve el corte. Corre siempre `--dry-run` primero, y
regenera el dashboard después.

**`--recalcular` es el modo que hay que usar cuando el arreglo toca un campo que
las entradas ya tenían poblado.** Los otros filtros seleccionan entradas que *se
ven* rotas, y hay bugs que no se ven: una vigencia con la fecha equivocada tiene
el mismo aspecto que una correcta. Sin este flag, el reparse las salta y el
arreglo no llega nunca al histórico.

**Es lento a propósito**: respeta la pausa de 2–3 s entre descargas, así que unas
70 entradas toman ~15 minutos. No lo metas en el workflow diario.

## Contratos de datos de los que depende el código aguas abajo

- **Formato de la clave de estado:** `f"{year}_{numero.zfill(4)}"`. El dashboard, el motor de diff y el JSON guardado asumen todos esta forma.
- **Convención del nombre de archivo del PDF:** la CMF lista cada NCG con la fecha de publicación *original* (muchas veces de hace décadas). El año de la *nueva* resolución se recupera del patrón del nombre de archivo del PDF `ncg_<num>_<year>.pdf` / `cir_<num>_<year>.pdf` en `_fecha_y_numero_desde_url`. Si la CMF cambia esta forma de nombrar, las fechas obtenidas al momento del fetch se van a romper.
- **`tipo_acuerdo`** se infiere en `store.TIPO_ACUERDO_MAP`, que es un mapa de **patrones regex → categoría** sobre la descripción del listado ya normalizada por `store.normalizar` (mayúsculas, sin tildes, espacios colapsados). Antes eran frases literales buscadas con `in` sobre el texto crudo, y por eso el artículo y las tildes decidían la categoría: la clave decía `"MODIFICA LA NORMA DE CARÁCTER GENERAL"` y las descripciones que escriben `"MODIFICA NORMA DE CARACTER GENERAL N°507"` caían al centinela `"Otro"`. Eran 40 entradas mal clasificadas; el filtro «Modificación NCG» mostraba 37 donde hay 77. **Si agregas un patrón, hazlo tolerante al artículo y al singular/plural, y escríbelo sin tildes.**

  Dos consumidores distintos y no equivalentes: `store.inferir_tipo_acuerdo` devuelve **una** categoría (la primera que calza) y es la que se graba en el JSON; `store.inferir_tipos_acuerdo` devuelve **todas** las que calzan y es la que usa el dashboard vía `_tipos_de_entrada`. Las categorías no son excluyentes: la circular 2370/2026 emite una circular *y* modifica las NCG 303 y 451, y con una sola etiqueta desaparecía del otro filtro. `_stats` cuenta por la lista completa para que las píldoras del Resumen no contradigan a los botones de al lado.

  **`_tipos_de_entrada` no se queda con el patrón: suma lo que dice `_accion_sobre_norma`**, que es la misma función con la que la línea de tiempo rotula cada evento. Sin eso hay dos mecanismos midiendo lo mismo por caminos distintos y divergen — la NCG N°209 mostraba «1 de 7 eventos» junto a «5 la modifican», porque cuatro de esos cinco dicen `APRUEBA MODIFICACIONES A LA NORMA DE CARÁCTER GENERAL N°209` y el patrón de categoría no los reconocía. Ampliar el patrón arreglaba esos cuatro y dejaba otros catorce; derivar ambas cosas del mismo análisis los vuelve coherentes **por construcción**. Si agregas una categoría nueva que tenga una noción equivalente a nivel de norma afectada, engánchala igual. `"Otro"` se descarta en cuanto otra categoría calza: es el centinela de «ninguna calzó» y si se queda infla su conteo.

  Como `tipo_acuerdo` está grabado en cada JSON de `data/daily/`, arreglar el calce sólo alcanzaría a lo que entre de aquí en adelante. Por eso `generar_html` lo **recalcula al renderizar** llamando a la misma función de `store` — no hace falta reparsear, porque la descripción viaja dentro de la entrada.

  `dashboard.TIPOS_FILTRO` define los botones y `_render_filtros` **omite los que dan cero**: un botón sin filas detrás sólo puede vaciar la tabla. Es data-driven a propósito, así que la categoría reaparece sola cuando llega el primer caso. Hoy «Consulta Pública» está definida pero no se rinde: las 7 menciones del histórico dicen «EXIME DEL TRÁMITE DE CONSULTA PÚBLICA», o sea documentos que se la **saltaron**. La categoría «Prórroga Consulta Pública» se eliminó del todo (postergaba el plazo de una consulta y no hay ni un caso en 607); no confundirla con **«Postergación de vigencia»**, que son 4 casos reales y sí importan, porque mover la entrada en vigor de una norma es mover una fecha de cumplimiento.

  Sigue habiendo asimetría con `fetch.FRASES_CLAVE` (37 frases de captura contra 6 categorías), así que `"Otro"` continúa siendo el caso más común (364 de 607 al 30-07-2026, tras el arreglo del calce) — es esperado, no un bug, pero es lo primero que hay que revisar cuando una resolución aparece bajo "Otro". El insumo para decidir nuevas categorías es `otro-a-clasificar.csv` en la raíz; **regenéralo antes de usarlo**, porque su columna `categoria_sugerida` se calcula con las reglas vigentes al momento de escribirlo.
- **`data/daily/`** acumula un archivo por día con las resoluciones nuevas (nombre `YYYY-MM-DD.json`); en los días sin novedades no se escribe archivo. El directorio es disperso por diseño — no asumas densidad histórica.

## Agendamiento

`.github/workflows/monitoreo.yml` corre `python scraper/main.py` a diario a las 11:00 UTC (≈ 8:00 AM de Chile en verano) y luego hace commit de cualquier cambio bajo `data/` y `docs/` de vuelta al repo. GitHub Pages sirve `/docs`. El runner tiene permiso `contents: write` y un timeout de 30 minutos.

**El workflow commitea a `main`.** Un arreglo que viva en otra rama no llega a
producción hasta que se mergea. Y como el repo local no se actualiza solo, es
fácil diagnosticar sobre datos viejos: **haz `git fetch` antes de sacar
conclusiones sobre el estado del pipeline** — el `data/state.json` local puede
estar decenas de commits atrás y hacer parecer que faltan resoluciones que en
realidad ya se capturaron.

Ojo con leer los commits diarios como señal de salud: `docs/index.html` cambia
todos los días aunque no haya novedades, porque el Cuadro de mando estampa la
fecha de hoy. Un commit `chore: monitoreo ...` diario **no** significa que se
hayan detectado resoluciones nuevas; para eso hay que mirar si apareció un
archivo nuevo en `data/daily/`, que es disperso por diseño (entre mayo y julio
de 2026 hubo 6 archivos, no 80).

El workflow además llama a `python scraper/dashboard.py` como paso aparte justo después de `main.py`, aunque `main.py` ya invoca `generar_html()` al final. Esto es redundante a propósito — garantiza que el dashboard se regenere incluso si `main.py` salió temprano (por ejemplo por la ruta `Sin novedades` con `sys.exit(0)`, que ya lo llama, o cualquier salida temprana futura). No lo elimines sin auditar todas las rutas de salida de `main.py`.

## Trabajar con PDF en este entorno

Según el `CLAUDE.md` del curso padre, la herramienta Read no puede abrir PDF en este entorno (no hay `pdftoppm`). Para inspeccionar el contenido de un PDF, corre el parser contra un archivo descargado o pídele a la persona usuaria que pegue el texto relevante en el chat. No intentes `Read` sobre rutas `.pdf`.

## Idioma

El código, los comentarios y los commits están en español (para calzar con el dominio). La persona usuaria se comunica en español e inglés — respóndele en el idioma que haya usado.
