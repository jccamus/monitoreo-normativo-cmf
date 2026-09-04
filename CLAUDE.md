# CLAUDE.md

Este archivo entrega orientación a Claude Code (claude.ai/code) para trabajar con el código de este repositorio.

## Qué es este proyecto

**Monitoreo Normativo CMF** — un scraper automatizado que corre a diario, detecta las nuevas resoluciones normativas que publica la Comisión para el Mercado Financiero (CMF) de Chile, extrae su estructura desde los PDF, guarda diferenciales en JSON por día y arma un dashboard HTML estático. Está pensado para periodistas que siguen los cambios regulatorios. El brief original está en `Propuesta - Cambios Normativos.txt` (en español, es el documento que manda).

Restricciones duras que están metidas en el diseño:
- **Sin LLM y sin base de datos.** El parsing es puro regex sobre el texto del PDF; el estado son archivos JSON versionados en el repo.
- **Sin costos.** Corre en GitHub Actions (plan gratuito) y publica en GitHub Pages.
- **Solo salida diferencial.** El JSON de cada día contiene únicamente las resoluciones que no se habían visto antes — esto lo maneja `data/state.json`.

## Dónde vive el conocimiento de este proyecto

**Casi todo patrón regex de `parser.py`, `store.py` y `dashboard.py` existe con la
forma exacta que tiene por un caso real que rompió, y ese caso está escrito en el
comentario que va justo encima.** No son comentarios decorativos: son el registro
de por qué la alternativa "obvia" no sirve.

Regla de trabajo: **antes de tocar un patrón, lee su comentario.** Si el cambio
que ibas a hacer lo contradice, el comentario gana hasta que puedas mostrar el
documento que lo desmiente. Y si arreglas algo, deja el caso escrito ahí mismo,
no acá.

Este archivo cubre lo que *no* cabe en un comentario: la forma del pipeline, los
contratos entre módulos, los modos de falla silenciosa y los flujos de operación.
Cuando dice «ver `_x()`», ahí está el detalle.

## Estructura del repositorio

La raíz del repositorio es la raíz del proyecto: `scraper/`, `data/`, `docs/` y
`.github/` cuelgan directo de acá, y los comandos se corren desde acá. Hasta el
30-07-2026 la aplicación vivía en un subdirectorio `proyecto/` que era su propio
repo git, con la documentación afuera y sin versionar — por eso este mismo
archivo llegó a describir columnas del dashboard que no existían en el código.
Si encuentras una ruta que empiece con `proyecto/`, está vieja.

- **`scraper/`** — el pipeline. Ver «Arquitectura» más abajo.
- **`data/`** — el estado: `state.json` (claves vistas y sello de la última
  consulta), `daily/YYYY-MM-DD.json` (diferenciales) y `revisiones.csv`
  (anotaciones manuales).
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
- **`otro-a-clasificar.csv`** (raíz) — agrupa las entradas que quedaron en
  `"Otro"` por su acción inicial, para decidir qué categoría nueva crear.
  **Es una foto y caduca**: su columna `categoria_sugerida` se calculó con las
  reglas de clasificación vigentes al escribirlo. **No hay script que lo genere**
  — salió de un `python -c` ad hoc y no quedó versionado, así que para
  regenerarlo hay que rehacer la consulta sobre `data/daily/` con las reglas de
  `store.inferir_tipos_acuerdo` del momento.
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
python scraper/reparse.py --degradadas       # suma las de parsed: False
python scraper/reparse.py --sin-vigencia --desde 2020-01-01  # suma las que quedaron sin vigencia
python scraper/reparse.py --recalcular --desde 2021-01-01   # re-parsea TODAS las del rango, no sólo las rotas
python scraper/reparse.py --recalcular --todas              # sin corte de año: las 607
python scraper/revisar.py                    # refresca data/revisiones.csv (revisión manual)
python scraper/revisar.py --estado           # sólo informa, no escribe
```

Python 3.11 en CI. El código usa sintaxis de tipos `list[dict]` / `str | None`, así que necesita ≥3.10.

Tests: no hay tests. Valida los cambios corriendo `main.py` y revisando el nuevo `data/daily/YYYY-MM-DD.json` y `docs/index.html`. Para iterar sobre el dashboard sin pegarle a la CMF, corre solo `python scraper/dashboard.py`: relee todo `data/daily/` y reescribe `docs/index.html` en segundos.

**Nota sobre la invocación:** `scraper/main.py` usa imports planos entre archivos
hermanos (`from fetch import …`). Córrelo como `python scraper/main.py` para que
Python agregue `scraper/` al `sys.path` automáticamente. NO lo "arregles"
convirtiéndolo en un paquete para correrlo como `python -m scraper.main` — los
imports se van a romper. Hubo un `scraper/__init__.py` vacío que hacía parecer
lo contrario; se eliminó el 07-08-2026 justamente porque inducía a ese error. No
lo vuelvas a crear.

## Arquitectura del pipeline

`scraper/main.py` orquesta un pipeline de 6 pasos. Las modificaciones normalmente tocan exactamente una etapa:

1. **`fetch.fetch_listado(from_date)`** — GET a la página de listado de la CMF,
   parseo de la tabla con BeautifulSoup y filtro de filas por `FRASES_CLAVE`
   (**37** frases; partió con las 5 del brief y creció para ampliar la red de
   captura — la categoría que ve la persona la decide el clasificador de después,
   no este filtro). Reintentos con backoff lineal (`30·intento`) y pausa cortés
   de 2–3 s entre requests.

   Dos comportamientos deliberados: **falla ruidosa** —agotados los reintentos
   sin una sola fila llama a `sys.exit(1)` para poner el build en rojo, en vez de
   devolver `[]`, que aguas abajo se leería como "sin novedades"— y **un HTTP 200
   sin tabla cuenta como intento fallido**, vía el parámetro `validar` de
   `_get_con_reintentos`.
2. **`diff.get_nuevas(resoluciones)`** — carga `data/state.json`, se queda con
   las no vistas y le estampa `_key` a cada una. `diff.commit_nuevas` escribe
   las claves de vuelta al final.

   `state.json` tiene dos llaves: `seen` (las claves `YYYY_NNNN`) y
   `ultima_consulta` (ISO con hora, lo que el dashboard rotula «Última
   actualización»). Lo escribe `diff.registrar_consulta()`, que `main.py` llama
   **apenas el fetch resulta y antes del corte por «sin novedades»**: un día sin
   resoluciones nuevas igual es un día en que se revisó. Consecuencia operativa:
   **`state.json` cambia en cada corrida**, o sea dos veces al día, así que los
   commits del workflow lo incluyen aunque no haya novedades.

   Al tocar `diff._save_state`, lee y reescribe el archivo completo. Emitir
   `{"seen": ...}` a secas borra `ultima_consulta`, y sólo los días con
   novedades — o sea, falla de forma intermitente.
3. **`fetch.fetch_pdf(url)`** — descarga el PDF de cada resolución nueva.
4. **`parser.parse_pdf(pdf_bytes, url)`** — texto con pdfplumber, respaldo
   PyMuPDF, y luego regex de dominio para poblar `ncg`, `resolucion`, `sesion`,
   `documento`, `modifica[]`, `ran_referencias`, `msi_referencias`,
   `archivos_afectados`, `vigencia`, el `tema` legible (bloque `REF:`),
   `resumen_acciones` (hasta 6 bullets) y, a falta de `resolucion`, una
   `fecha_documento` del encabezado.

   **Toda la extracción es regex**: los patrones `_*` viven al inicio de
   `parser.py`, cada uno con su comentario. Si agregas un patrón con una frase de
   varias palabras, pásala por **`_frase()`** — los PDF cortan en el salto de
   línea y un espacio literal no cruza. (La excepción es `_DOC_IDENTIDAD`, donde
   el salto hay que bloquearlo; su comentario explica por qué.) Usa
   **`_frase_flex()`** cuando además convenga tolerar la falta de tildes, que el
   extractor pierde según cómo esté incrustada la fuente.

   **Esto vale para cualquier comparación de texto, no sólo para los regex.** La
   lista de frases de vigencia inmediata se comparaba con `in` sobre el texto en
   minúsculas, sin pasar por `_frase()`, y por eso no reconocía casi ningún caso
   real: la sección de vigencia es corta y la frase casi siempre cae partida en
   dos renglones. Once entradas del histórico figuraban sin vigencia por un salto
   de línea. Si vas a buscar una frase en el texto de un PDF, hazlo con un patrón
   construido con `_frase()`; un `in` con un espacio literal es un falso negativo
   esperando ocurrir.
5. **`store.ensamblar_entrada(raw, parsed)`** — combina la fila del listado con
   los campos parseados. Prefiere la fecha exacta del PDF sobre el placeholder
   `YYYY-01-01`; guarda `resolucion` **sólo** si el PDF declara una de verdad; y
   guarda `modifica`, `vigencia`, `ran_referencias`, `msi_referencias`,
   `archivos_afectados`, `tema` y `resumen_acciones` **siempre**, no sólo cuando
   `parsed` es True. Luego `guardar_diferencial` escribe
   `data/daily/YYYY-MM-DD.json` de forma **idempotente**: si el archivo del día
   existe, fusiona por `clave` y las entradas nuevas pisan a las viejas, para que
   gane un re-parseo corregido. Un archivo ilegible se respalda como
   `*.json.corrupt-<timestamp>` en vez de descartarse en silencio.
6. **`dashboard.generar_html()`** — lee `data/daily/`, aplana y arma
   `docs/index.html` de una pasada. Es el módulo más grande con diferencia
   (~3.900 líneas, más de la mitad de la base de código) porque el HTML/CSS/JS va en
   línea en `_TEMPLATE` más funciones `_render_*`; no hay paso de build. Rinde
   **cuatro** pestañas:
   - **Agenda de tareas** — el **«Calendario de modificaciones»**, un riel
     horizontal de ±`MESES_AGENDA`
     meses alrededor de hoy (`_calendario_agenda`). La unidad no es el
     documento sino el **hito**: una fecha en que algo entra a regir
     (`_hitos_agenda`), así que un documento con dos plazos en meses distintos
     aparece dos veces, porque son dos obligaciones. Las vigencias inmediatas
     no traen fecha propia y se fechan con la publicación (`_fechas_vigencia`);
     sin esa equivalencia, lo que aplica de inmediato —lo más urgente— quedaba
     fuera de toda vista con eje temporal.

     Los meses vacíos consecutivos se apilan en un **mazo** (`_render_ag_mazo`)
     para que el ojo salte los tramos quietos, **salvo el mes en curso**, que
     siempre lleva tarjeta aunque esté vacío: es el ancla del eje. El riel se
     centra en hoy midiendo con `getBoundingClientRect` y no con `offsetLeft`,
     que cuenta desde el ancestro posicionado y no desde el contenedor con
     scroll — con `offsetLeft` abría en el primer mes.

     Arriba van tres paneles: cuerpo normativo (con conmutador entre tareas y
     cambios recibidos, y que **filtra el riel** al hacer clic), proyectos por
     mes, y **`_sin_fecha_agenda`: las obligaciones que el eje no puede
     mostrar** porque no tienen cuándo — cambios de archivo del MSI sin fecha,
     y lo que queda en `"ver texto"` después de que el parser intenta calcular
     los plazos relativos. Se declaran en vez de omitirse: un calendario que
     las esconde miente por omisión.

     Las fechas que el parser **calculó** desde un plazo declarado sí entran al
     riel, pero rotuladas «· calculada» (`_ROTULO_FUENTE`, `_calculo_de`), con
     la regla y la fecha base en el tooltip. Es el mismo criterio con que se
     distinguen «sección», «cláusula» y «confirmada»: la fecha vale, y de dónde
     salió también.

     Entre los paneles y el riel va **«Último cambio publicado»**
     (`_render_ag_ultimo`): la resolución más reciente, con enlace a su fila en
     el Listado. Reusa `_render_detalle` —el mismo detalle de la tabla— y el
     mismo orden que `_render_tabla`, así que «lo último» y «la primera fila de
     allá» no pueden divergir. Si cambias uno, cambia el otro.

     El buscador (`_indice_busqueda`) mira **todo el corpus, no la ventana**.
     Si mirara sólo los 13 meses del riel, «no hay cambios normativos en
     relación con el archivo consultado» sería falso para casi todo.
   - **Cambios relevantes** — `_agrupar_por_cuerpo`, recortado a los últimos
     5 años. La columna «Norma» sale de `_etiqueta_documento`.
   - **Revisión manual** — cambios de archivo sin fecha de vigencia. Es el único
     tab que rinde contenido aunque esté vacío: que no haya pendientes es
     información, y un panel en blanco se lee como si algo hubiera fallado.
   - **Listado completo** — stats, filtros, búsqueda, tabla con detalle
     expandible y línea de tiempo por NCG (`_agrupar_por_norma`, mínimo
     `_TIMELINE_MIN_EVENTOS` eventos, ordenada por cantidad de eventos).

     Ojo con las dos columnas de norma: **«Norma» es el documento**
     (`_etiqueta_documento`) y **«Norma(s) afectada(s)» son las que modifica**
     (`_normas_afectadas`, que descarta el número propio cuando el documento *es*
     una NCG). Y la línea de tiempo **sigue a la tabla por `data-clave`** en vez
     de reimplementar el filtrado.

### Dos reglas del front-end que se rompen solas si no las conoces

**El estado de un desplegable va en una clase, nunca en `style.display`.** Un
`display` en línea le gana a cualquier media query, y la vista de celular
convierte las tablas en tarjetas: una fila abierta tiene que poder ser `block`
allá y `table-row` acá. Por eso `toggleDetail`, `toggleDetalleCR` y
`aplicarFiltros` conmutan `.abierto` y el CSS decide por breakpoint. Si escribes
`elemento.style.display = 'table-row'`, el celular se rompe en silencio: se ve
bien hasta que alguien abre un detalle.

**Bajo 640px todas las tablas son tarjetas.** `#tabla-resoluciones`, `.cr-tabla`
y «Más allá de N meses» pasan a `display: block` y sus filas a `grid` con
`grid-template-areas`. Consecuencia: **cada `<td>` necesita clase**, incluidas
las que en escritorio no la necesitaban, porque una celda sin nombre no se puede
colocar en la grilla. Si agregas una columna, dale clase y ubícala en las áreas.

## Los modos de falla que no se ven

Los tres son silenciosos: no hay excepción, no hay log en rojo, y el resultado se
parece a un día tranquilo. Son el primer lugar donde mirar cuando algo "dejó de
funcionar".

### 1. La fecha placeholder — el más caro

La CMF lista cada norma con su fecha de publicación *original*, así que lo único
que `fetch` puede deducir es el año del nombre del PDF, y lo rellena como
`YYYY-01-01`. **La fecha real sale del encabezado del PDF**
(`parser._fecha_encabezado`), y `store.ensamblar_entrada` la prefiere.

Cuando esa extracción falla, la entrada queda fechada el 1 de enero: el cambio
aparece al comienzo del año en el dashboard y la actividad reciente se vuelve
invisible. **Se ve exactamente igual que "no hay novedades".** Si alguien reporta
que el dashboard dejó de actualizarse, descarta esto antes de sospechar del
scraping.

La regla del extractor: ante la duda devuelve `None` y cae al placeholder, que es
visiblemente sospechoso. Una fecha plausible pero errónea no lo es. El detalle
del anclaje está en el docstring de `_fecha_encabezado`.

Límite conocido: algunos PDF son sólo imagen (`ofc_1402_2026`, `ofc_1377_2025`).
No hay OCR en el proyecto; esas entradas se quedan con el placeholder.

### 2. La vigencia inventada

`vigencia` es el campo que alimenta la Agenda de tareas y el único que responde
la pregunta que le importa a quien sigue la regulación: *cuándo hay que tener
esto hecho*. **Se extrae de la sección de vigencia del PDF y de ninguna otra
parte.**

Ese "de ninguna otra parte" es la regla, no un detalle. Hasta julio de 2026, al
no reconocer la sección se barría el documento entero y ganaba la primera fecha
—que en un PDF de la CMF es siempre la del encabezado—: **las 126 entradas del
histórico con vigencia fechada repetían, sin una sola excepción, la fecha del
propio documento.** Sin sección reconocida el valor es `"no especificado"`.

Corolario para cualquier cambio acá: **no ensanches la ventana de búsqueda.**
Si un documento no declara vigencia donde corresponde, el resultado correcto es
el hueco. `vigencia.fuente` (`seccion` / `clausula_aplicacion` / `ninguna`)
existe para poder auditar de dónde salió cada dato.

### 3. `parsed: False` leído como falla del parser

No lo es: es un corte por época. Ver la sección propia más abajo.

## La vigencia: lo que hay que saber antes de tocarla

- **Un documento puede tener varios plazos**, y la CMF los escalona de tres
  formas distintas. `_parse_text` las prueba en orden y gana la primera que
  reconozca tramos:

  | forma | función | ejemplo |
  |---|---|---|
  | viñetas | `_parse_plazos` | circular 2370/2026 |
  | excepción con "salvo" | `_parse_excepciones` | circular 2373/2026 |
  | oraciones seguidas | `_parse_tramos_prosa` | circular 2356/2024, NCG 519/2024 |

  Cada plazo lleva su `texto` y su `inicio` propio; el `inicio` global se toma
  del **primero** (la regla general), no de un barrido de la sección entera.

  La tercera forma es la más común en circulares —"el número 1 rige a contar de
  esta fecha… los números 2 al 11 comenzarán a regir el 1 de diciembre"— y es la
  que **hay que tener presente al tocar la vigencia inmediata**: como
  `_parse_vigencia_global` evalúa la inmediatez antes que las fechas, un
  documento así queda descrito sólo como "inmediata" y **la fecha futura, que es
  la que obliga a hacer algo, desaparece de los datos**. Sin desdoblarlo, mejorar
  el reconocimiento de la inmediatez empeora el Calendario.

  `_parse_tramos_prosa` exige que cada oración lleve un **verbo de entrada en
  vigor** (`_VERBO_VIGENCIA`) y topea el resultado en `_MAX_TRAMOS_PROSA`. Las
  dos cosas son contención, no estilo: sin el verbo, la NCG 524/2024 aportaba 9
  tramos, 7 de ellos plazos de trámite ("tendrán hasta el 3 de febrero de 2025
  para presentar la solicitud") que habrían entrado al Calendario como
  obligaciones de vigencia.
- **El dashboard lee los plazos** (`_fechas_futuras`), así que un documento
  escalonado entra a la Agenda por su fecha futura aunque su `inicio` global diga
  `"inmediata"`.
- **Los oficios circulares no titulan sección de vigencia**: declaran cuándo rige
  en un párrafo de cierre. Para ellos está `_CLAUSULA_APLICACION`, que **sólo se
  usa a falta de sección** y exige que la fecha cuelgue de un verbo de aplicación
  dentro de la misma oración. Antes de agregarle otro verbo, verifica contra el
  texto que la fecha que vas a capturar es la de entrada en vigor y no una del
  ciclo de reporte.

  **El presente ("entra en vigor") está deliberadamente fuera de ese patrón, y no
  es un olvido.** Se probó y se midió: recuperaba 1 cláusula legítima y creaba 2
  falsas, porque un documento que *cita* la sección Vigencia de otra norma la
  transcribe en presente. La NCG 568/2026 sustituye el apartado Vigencia de otra
  norma y cita «La presente norma entra en vigor a partir del 1 de agosto de
  2025» — que habría fechado la 568 quince meses antes de su propia publicación.
  El detalle está en el comentario del patrón.
- **El encabezado de la sección tiene más formas de las que parece.**
  `_VIGENCIA_HEADING` acepta el enumerador y el sufijo "y aplicación" ("VI.
  Vigencia y aplicación"); `_TRANSITORIAS_HEADING` acepta singular y plural y la
  comilla de apertura, porque cuando una circular *inserta* la disposición en
  otro cuerpo normativo el encabezado queda dentro del texto citado. Los dos
  exigen que la línea sea sólo el título: sin eso, "durante la vigencia de la
  póliza" cortaría el cuerpo del documento ahí.
- **`_TRANSITORIAS_HEADING` es respaldo**, no alternativa: hay documentos con
  ambas secciones y manda la de vigencia.
- **Una norma puede fijarle la vigencia a otra.** `_vigencias_impuestas` mapea
  número de NCG → vigencia y `_parse_modificaciones` la usa **en vez de** la
  vigencia de la sección, para que la fecha quede atribuida a la norma que la
  recibe y no a la que la impone (caso canónico: NCG 564/2026 sobre la 550).

  El reverso de esa regla vive en `_clausula_aplicacion`: una fecha **dentro de
  comillas y con sujeto autorreferente** ("La presente norma rige a contar
  del…") es la vigencia de la norma que recibe el texto insertado, no la de
  quien lo inserta, así que se descarta. Ojo con el matiz: **no basta con estar
  citada.** Un documento también usa comillas para insertar una disposición
  transitoria *propia*, y ahí la fecha sí lo obliga a él — la circular
  2317/2022 no tiene otra. Lo que separa los dos casos es el sujeto del verbo,
  no las comillas.
- **Un plazo puede venir como regla en vez de fecha**, y se calcula. La circular
  2376/2026 dice "entrará en vigor en el plazo de un mes contado desde su
  publicación": no hay ninguna fecha escrita, pero el documento da la regla
  completa y la base es su propia fecha de publicación.
  `_resolver_plazo_relativo` la resuelve y deja el rastro en `vigencia.calculo`
  (`base`, `fecha_base`, `expresion`, `texto`), que es lo que el dashboard usa
  para rotular «· calculada» en vez de presentarla como fecha declarada.

  **Esto no contradice la regla de no inventar vigencias**, y la distinción hay
  que tenerla clara antes de tocar nada acá: la regla prohíbe *suponer* una
  fecha que el documento no da; calcular la que el documento *define* es otra
  cosa. La frontera está en `fecha_base`, que **sólo puede venir del PDF**
  (encabezado o resolución) y nunca del listado de la CMF, cuyo placeholder
  `YYYY-01-01` produciría una fecha con toda la apariencia de un dato y ningún
  respaldo.

  Dos condiciones que no se pueden relajar, ambas en el comentario del patrón:
  el plazo tiene que colgar de un **verbo de entrada en vigor** en la misma
  oración (si no, un "tendrán seis meses para adecuarse" —plazo de adecuación,
  no vigencia— pasaría por fecha), y los **días hábiles quedan fuera** porque
  contarlos exige el calendario de feriados, que este proyecto no tiene.

Límites conocidos: las excepciones en prosa fuera de viñeta no se capturan (la
NCG 562/2026 pierde su "1 de julio de 2027"), y un PDF con OCR de mala calidad
incrustado no se puede leer aunque declare su vigencia con toda claridad (la NCG
444/2020 entrega "regirán acontar\nde estafecha", con las palabras pegadas).

Y el descarte de la cita autorreferente deja un hueco a propósito: cuando el
documento **sólo** reemplaza el numeral Vigencia de otra norma sin nombrarla en
la misma frase —"Reemplácese el numeral II. Vigencia por el siguiente", con la
NCG afectada nombrada párrafos antes—, `_MOD_VIGENCIA` tampoco la reconoce y la
fecha se pierde para los dos lados (NCG 448/2020 sobre la 445). Se prefiere el
hueco a la fecha mal atribuida, que es la que había antes.

### Formatos de fecha que la CMF usa

`_FECHA_ALT` cubre las formas escritas y `_resolver_fecha` devuelve la fecha ISO
junto con su **precisión**; las dos últimas filas no traen fecha en el texto y
las resuelve `_resolver_plazo_relativo` / `_fecha_presente_anio` desde la fecha
de publicación:

| forma | ejemplo | precisión |
|---|---|---|
| día completo | `1° de julio de 2026` | `dia` |
| día completo sin preposición | `1° de julio 2023` | `dia` |
| día escrito en palabra | `el primero de julio de 2023` | `dia` |
| numérica | `13-07-2021` | `dia` |
| mes y año | `a partir del mes de diciembre de 2024` | `mes` |
| plazo contado | `en el plazo de un mes contado desde su publicación` | `dia` + `calculo` |
| mes ordinal siguiente | `el primer día del sexto mes siguiente a su emisión` | `dia` + `calculo` |
| año por referencia | `a contar del 12 de julio del presente año` | `dia` + `calculo` |

La preposición antes del año es opcional porque la CMF escribe las dos formas, y
exigirla mandaba a `"ver texto"` fechas completas y explícitas.

`primero` es el **único** día que la CMF llega a escribir con palabras —no hay
un solo "segundo de julio" en el corpus— y aparece porque el día 1 es donde cae
casi todo plazo. Sin él la fecha caía a la rama de mes suelto y se guardaba con
`precision: "mes"`, o sea el dashboard rotulaba "julio de 2023" un documento que
dice el día. No agregues los demás ordinales por simetría: cada alternativa que
no responde a un caso real es una forma más de calzar de casualidad.

La forma de mes es la habitual para las obligaciones de reporte. Se normaliza al
día 1 para poder ordenarla, y por eso `precision` viaja con el dato: el dashboard
rotula "diciembre de 2024" y no "2024-12-01", que afirmaría una exactitud que el
documento no da. **Si consumes `vigencia.inicio` en código nuevo, mira también
`precision` antes de presentarlo como fecha exacta, y `calculo` antes de
presentarlo como algo que el documento dice literalmente.**

## Archivos afectados y el aviso de revisión manual

`archivos_afectados` identifica los archivos normativos del MSI por su código
(`C11`, `R06`, `RDC01`) y **exige la palabra "archivo" delante**, porque el código
suelto se confunde con capítulos de la RAN, números de tabla y códigos de campo.
Cada archivo se fecha con la viñeta de vigencia que lo nombra
(`_vigencia_de_archivo`), no con la vigencia global.

**Un cambio de archivo sin fecha de vigencia se avisa, no se esconde.** El
dashboard rinde la sección "Cambios de archivo sin fecha de vigencia"
(`_requiere_revision`) con los documentos que modifican un archivo del MSI —lo que
genera una obligación de reporte— pero de los que no se pudo determinar desde
cuándo rige.

**Ojo con los dos contadores: no cuentan lo mismo, y se leen como si sí.** El tab
«Revisión manual» muestra `_requiere_revision` (sólo los que tocan un archivo del
MSI: la cola de trabajo). La celda «obligaciones sin fecha» de la Agenda muestra
`_sin_fecha_agenda`, que es ese conjunto **más** todo lo que el eje temporal no
puede ubicar por cualquier otra razón. El primero es un subconjunto del segundo,
así que verlos con números distintos es correcto y aun así parece una
contradicción — por eso la celda lleva el desglose en su tooltip y la fila del
panel nombra el tab. Si agregas un motivo nuevo a `_sin_fecha_agenda`, actualiza
ese rótulo o vuelves a abrir la misma confusión.

Esa lista es **en su mayoría** juicio y no deuda técnica: los casos que quedan
expresan la fecha entrelazada con el ciclo de reporte —"deberá aplicarse respecto
de la información referida al cierre del mes de agosto y, por lo tanto, enviarse
en septiembre de 2025"—, y cuál de las dos es la vigencia no lo decide un patrón.

Dicho eso, **no la tomes como cerrada sin mirarla.** Hasta septiembre de 2026 la
lista arrastraba documentos que sí eran mecánicos: vigencias inmediatas partidas
por un salto de línea, fechas escritas sin la preposición del año y plazos
declarados como regla en vez de fecha. Eran 21 de 22 entradas resolubles. Antes
de concluir que un caso pendiente requiere criterio humano, lee su sección de
vigencia entera: la pregunta es si el documento **declara** la fecha de alguna
forma, no si el parser la encontró.

Para agilizar la revisión, `_fechas_candidatas` adjunta en `vigencia.candidatas`
las fechas del cuerpo con su contexto. Se muestran rotuladas como candidatas y
**nunca** como la vigencia: presentarlas como dato firme sería repetir el bug que
originó todo esto.

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
de entrada, agrega las filas nuevas y recalcula el contexto. **El orden de
`revisiones.COLUMNAS` no es cosmético** — su comentario explica la trampa.

Qué acepta `vigencia`:

| valor | significado |
|---|---|
| `2025-11-01` | fecha exacta declarada en el documento |
| `2025-11` | el documento fija sólo el mes → `precision: "mes"` |
| `01-11-2025`, `01/11/2025` | lo que devuelve Excel al reformatear la celda |
| `inmediata` | el documento no declara vigencia y rige desde su publicación |

`inmediata` **y no la fecha del propio documento**: la fecha afirmaría que el
texto la declara, que es la confusión que originó los bugs de este proyecto.

Las otras columnas:

- `sin_fecha: si` es un resultado válido de revisión distinto de `inmediata`: el
  documento no declara vigencia **y tampoco corresponde asumir que rige desde su
  publicación**. Deja de figurar como pendiente sin inventarle una fecha.
- `archivos` permite una fecha distinta por archivo:
  `RDC40=2026-01-01;RDC02=2025-11-01`. Vacía, la vigencia aplica a todos.

**Las anotaciones viven fuera de `data/daily/` a propósito.** Editar la entrada
directamente no funciona: `reparse.py` hace `entrada.update(nueva)` y pisa todo
campo que el parser produce —incluida `vigencia`—, y `guardar_diferencial` fusiona
por `clave` reemplazando la entrada entera. Como capa aparte sobreviven a las dos
cosas.

El panel de «Revisión manual» lleva el procedimiento al lado de la lista
(`_render_como_anotar`). No es adorno: sin los comandos a la vista, la lista se
lee como un informe de solo lectura y el contador parece condenado a no bajar
nunca. Lo que hay que dejar dicho ahí es que **`dashboard.py` hace las dos cosas
en la misma pasada** —saca el documento de la lista y lo mete al Calendario—,
porque es lo que no se deduce mirando la página.

La capa se aplica **al renderizar** (`revisiones.aplicar` en `generar_html`), no al
guardar: los datos parseados quedan intactos y una anotación nueva sólo necesita
regenerar el HTML. Una vigencia anotada se marca con `fuente: "revision_manual"`
y se muestra como «· confirmada», nunca mezclada con lo extraído del PDF. Si el
parser después propone otra fecha, gana la anotación —quien anotó leyó el PDF—
pero queda `discrepa` y `generar_html` emite un warning, para poder retirar
anotaciones que ya no hacen falta.

## Qué significa `parsed: False`

Que del PDF no se pudo sacar **nada** identificable: ni `ncg`, ni `modifica[]`, ni
la identidad del documento.

**Es un corte por época, no una falla del parser.** Tras reprocesar las 607
entradas con `--recalcular --todas` (julio de 2026):

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
un paso de normalización difuso. Todo esto queda fuera de la ventana de 5 años del
dashboard, así que no afecta lo que se muestra.

**La identidad del documento no depende de `ncg`.** La resuelve
`parser._identidad_documento`, que llena `documento: {tipo, numero}` con `NCG`,
`Circular` u `Oficio Circular`. Hasta julio de 2026 `parsed: False` bastaba con no
encontrar `ncg` ni `modifica[]` —y **ninguna circular ni oficio circular tiene
número de NCG**—, así que 399 de 502 entradas degradadas eran documentos
perfectamente legibles que simplemente no eran NCG.

No confundir `documento.numero` con `ncg`: en un oficio circular que modifica la
NCG N°530, `documento` es el oficio y `ncg` es 530, la norma afectada.
`_etiqueta_documento` lo muestra y cae al nombre del PDF (`ncg_470_2022.pdf` →
«NCG N°470») cuando el parser no logra identificarlo, lo que cubre las normas
conjuntas con otro regulador.

## La descripción del listado no es el PDF: `modifica[]` con `fuente: "descripcion_cmf"`

Cuando el parser no encuentra ningún `modifica[]` en el PDF,
`store._modifica_desde_descripcion` lo deduce de la descripción del listado. Ese
respaldo acumuló tres bugs de la misma familia —tratar la descripción como si
fuera texto normativo: cualquier «N° número» pasaba por NCG, la acción se aplicaba
en bloque a todas las menciones, y nada distinguía modificar de citar—. Los dos
primeros están arreglados y documentados en `store.py`.

**Lo que importa acá es la consecuencia entre módulos: las entradas ya guardadas
conservan los tres defectos.** Por eso el dashboard ignora los `modifica[]` con
`fuente: "descripcion_cmf"` (en `_normas_afectadas` y en `_accion_sobre_norma`) y
vuelve a deducir desde la descripción, que viaja dentro de la entrada. El
histórico se repara **al renderizar y sin reparse**. Si algún día se corre
`reparse.py --recalcular --todas`, esos `modifica[]` quedan bien y los `continue`
pasan a ser inocuos, no incorrectos.

`_accion_sobre_norma` decide entre «Derogada por», «Modificada por» y «Referida
por» buscando el último verbo antes de la mención **dentro de su misma oración**.

## Arreglar el parser no arregla los datos ya guardados

`data/state.json` impide reprocesar una resolución ya vista, así que una mejora
del parser sólo afecta a lo que venga de aquí en adelante. Para propagarla al
histórico está `scraper/reparse.py`: recorre las entradas afectadas, vuelve a
bajar el PDF y reescribe la entrada dentro de su archivo `data/daily/`, sin tocar
`state.json`.

Por defecto toma las de fecha placeholder desde 2024; `--degradadas` suma las de
`parsed: False`, `--desde` mueve el corte y `--todas` lo elimina. Corre siempre
`--dry-run` primero, y regenera el dashboard después.

**`--recalcular` es el modo que hay que usar cuando el arreglo toca un campo que
las entradas ya tenían poblado.** Los otros filtros seleccionan entradas que *se
ven* rotas, y hay bugs que no se ven: una vigencia con la fecha equivocada tiene
el mismo aspecto que una correcta. Sin este flag, el reparse las salta y el
arreglo no llega nunca al histórico.

**`--sin-vigencia` es el atajo para el caso más frecuente**: el arreglo mejora la
extracción de la fecha de entrada en vigor. Selecciona las entradas que quedaron
en `"ver texto"` o `"no especificado"`, que ni el filtro de placeholder ni
`--degradadas` alcanzan. Es la diferencia entre bajar dos decenas de PDF y bajar
varios cientos con `--recalcular`. Si el arreglo además puede *cambiar* una
vigencia que ya tenía fecha, ahí sí hace falta `--recalcular`.

**Es lento a propósito**: respeta la pausa de 2–3 s entre descargas, así que unas
70 entradas toman ~15 minutos. No lo metas en el workflow diario.

## Contratos de datos de los que depende el código aguas abajo

- **Formato de la clave de estado:** `make_key(fecha, numero)` →
  `f"{year}_{numero.zfill(4)}"` → `"2026_0564"`. El dashboard, el motor de diff y
  el JSON guardado asumen todos esta forma. **Si la cambias, invalidas todo el
  historial.**
- **Convención del nombre de archivo del PDF:** el año de la *nueva* resolución se
  recupera del patrón `ncg_<num>_<year>.pdf` / `cir_<num>_<year>.pdf` en
  `_fecha_y_numero_desde_url`, porque la CMF lista cada norma con su fecha de
  publicación original. Si la CMF cambia esta forma de nombrar, las fechas del
  fetch se rompen.
- **`data/daily/`** acumula un archivo por día con las resoluciones nuevas
  (`YYYY-MM-DD.json`); en los días sin novedades no se escribe archivo. El
  directorio es **disperso por diseño** — no asumas densidad histórica (entre mayo
  y julio de 2026 hay 6 archivos, no 80).

### `tipo_acuerdo` y los filtros del dashboard

Es el contrato más enredado del proyecto, porque las categorías se producen en dos
lugares distintos.

**`store.TIPO_ACUERDO_MAP`** es un mapa de **patrones regex → categoría** sobre la
descripción del listado ya normalizada por `store.normalizar` (mayúsculas, sin
tildes, espacios colapsados). Hoy define **cinco**: `Consulta Pública`,
`Postergación de vigencia`, `Modificación NCG`, `Nueva Normativa` y `Circular`.
Más `"Otro"`, que es el centinela de «ninguna calzó». **Si agregas un patrón,
hazlo tolerante al artículo y al singular/plural, y escríbelo sin tildes.**

Dos consumidores no equivalentes:
- `store.inferir_tipo_acuerdo` devuelve **una** categoría (la primera que calza) y
  es la que se graba en el JSON.
- `store.inferir_tipos_acuerdo` devuelve **todas** las que calzan, y es la base de
  `dashboard._tipos_de_entrada`. Las categorías no son excluyentes: la circular
  2370/2026 emite una circular *y* modifica dos NCG.

**Hay una sexta categoría que no existe en `TIPO_ACUERDO_MAP`: «Derogación».** Se
genera sólo del lado del dashboard, en `_tipos_de_entrada`, a partir de
`_accion_sobre_norma` («Derogada por») y de `_es_derogacion` sobre la descripción.
Tiene botón en `TIPOS_FILTRO` y es la segunda categoría más poblada. Si buscas por
qué una entrada aparece bajo «Derogación» y no encuentras el patrón, es por esto.

Esa misma mecánica —`_tipos_de_entrada` suma lo que dice `_accion_sobre_norma`,
que es la función con la que la línea de tiempo rotula cada evento— existe para
que los dos mecanismos no diverjan. **Si agregas una categoría que tenga una
noción equivalente a nivel de norma afectada, engánchala igual.**

`generar_html` **recalcula `tipo_acuerdo` al renderizar** llamando a la misma
función de `store`, así que arreglar el calce no exige reparsear: la descripción
viaja dentro de la entrada.

`dashboard.TIPOS_FILTRO` define los botones y `_render_filtros` **omite los que
dan cero**: un botón sin filas detrás sólo puede vaciar la tabla. Es data-driven a
propósito, así que la categoría reaparece sola cuando llega el primer caso. Hoy
«Consulta Pública» está definida pero no se rinde: las 7 menciones del histórico
dicen «EXIME DEL TRÁMITE DE CONSULTA PÚBLICA», o sea documentos que se la
**saltaron**.

Reparto vigente sobre las 607 entradas (04-08-2026; suma más de 607 porque una
entrada puede llevar varias categorías):

| categoría | entradas |
|---|---|
| Otro | 364 |
| Derogación | 147 |
| Modificación NCG | 103 |
| Postergación de vigencia | 4 |
| Circular | 2 |
| Nueva Normativa | 1 |
| Consulta Pública | 0 |

La asimetría entre las 37 frases de captura de `fetch.FRASES_CLAVE` y las 5+1
categorías es la razón de que `"Otro"` sea el caso más común. **Es esperado, no un
bug** — pero es lo primero que hay que revisar cuando una resolución aparece bajo
"Otro". El insumo para decidir categorías nuevas es `otro-a-clasificar.csv`
(recuerda que caduca; ver «Estructura del repositorio»).

## Agendamiento

`.github/workflows/monitoreo.yml` corre `python scraper/main.py` **dos veces al
día** —11:00 y 15:00 UTC (≈ 8:00 AM y mediodía de Chile)— y luego hace commit de
cualquier cambio bajo `data/` y `docs/` de vuelta al repo.

La segunda corrida es una red de seguridad, no un duplicado: la CMF queda
inalcanzable de a ratos (05-08-2026 y 13-08-2026, las dos con `ConnectTimeout`)
y los reintentos de `fetch.py` cubren ~6 minutos, no media hora. Estirarlos más
choca con el peor caso del otro modo de falla —el 200 sin tabla, que sí lee
hasta 300 s por intento— y con el timeout del job. Como el pipeline es
diferencial e idempotente, si la primera corrida resultó la segunda sale por
«sin novedades». **Consecuencia: hay hasta dos commits `chore: monitoreo` por
día**, porque `ultima_consulta` cambia en cada consulta. GitHub Pages sirve `/docs`. El runner
tiene permiso `contents: write` y un timeout de 45 minutos, atado al peor caso
de `fetch.MAX_REINTENTOS` (ver su comentario: si bajas uno, baja el otro).

**El workflow commitea a `main`.** Un arreglo que viva en otra rama no llega a
producción hasta que se mergea. Y como el repo local no se actualiza solo, es fácil
diagnosticar sobre datos viejos: **haz `git fetch` antes de sacar conclusiones
sobre el estado del pipeline** — el `data/state.json` local puede estar decenas de
commits atrás y hacer parecer que faltan resoluciones que ya se capturaron.

Ojo con leer los commits como señal de salud: `docs/index.html` cambia **en cada
corrida** aunque no haya novedades, porque estampa `ultima_consulta` con su hora
—y la Agenda, la fecha de hoy—. Un commit `chore: monitoreo ...` **no** significa
que se hayan detectado resoluciones nuevas; para eso hay que mirar si apareció un
archivo nuevo en `data/daily/`. Y como hay dos corridas al día, **esperar un solo
commit diario también es equivocarse**: lo normal son dos.

El workflow además llama a `python scraper/dashboard.py` como paso aparte justo
después de `main.py`, aunque `main.py` ya invoca `generar_html()` al final. Es
redundante a propósito — garantiza que el dashboard se regenere incluso si
`main.py` salió temprano (por ejemplo por la ruta `Sin novedades` con
`sys.exit(0)`, que ya lo llama, o cualquier salida temprana futura). No lo elimines
sin auditar todas las rutas de salida de `main.py`.

## Trabajar con PDF en este entorno

Según el `CLAUDE.md` del curso padre, la herramienta Read no puede abrir PDF en
este entorno (no hay `pdftoppm`). Para inspeccionar el contenido de un PDF, corre
el parser contra un archivo descargado o pídele a la persona usuaria que pegue el
texto relevante en el chat. No intentes `Read` sobre rutas `.pdf`.

## Idioma

El código, los comentarios y los commits están en español (para calzar con el
dominio). La persona usuaria se comunica en español e inglés — respóndele en el
idioma que haya usado.
