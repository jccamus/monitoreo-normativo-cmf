> **Nota de reconciliación (30-07-2026).** Esta lista se escribió antes de
> implementar. Se revisó contra el código y los datos reales: las tareas cuyo
> resultado terminó siendo distinto de lo planificado llevan una línea `⟳`
> debajo, y las que **dejaron de tener sentido** aparecen tachadas con `[~]`
> y la razón. Dos (7.6 y 7.7) se implementaron de verdad y **se perdieron
> después en una reescritura**, sin que nadie desmarcara la tarea.
> Para el comportamiento actual del sistema manda `CLAUDE.md`, no este archivo.

## 1. Estructura del repositorio

- [ ] 1.1 Crear repositorio GitHub público `monitoreo-normativo-cmf`
- [x] 1.2 Crear estructura de carpetas: `scraper/`, `data/daily/`, `docs/`, `.github/workflows/`
- [x] 1.3 Crear `data/state.json` inicial con `{ "seen": [] }`
- [x] 1.4 Crear `requirements.txt` con dependencias: `requests`, `beautifulsoup4`, `pdfplumber`, `python-dateutil`
- [x] 1.5 Crear `.gitignore` excluyendo archivos temporales y PDFs descargados

## 2. Scraper CMF

- [x] 2.1 Implementar `scraper/fetch.py`: GET a la URL CMF y parseo HTML con BeautifulSoup
- [x] 2.2 Extraer filas de resoluciones: fecha, número, descripción, URL del documento
- [x] 2.3 Implementar filtro por frases clave (las 5 definidas en el diseño)
- [x] 2.4 Agregar pausa de 2-3 segundos entre requests
- [x] 2.5 Manejar errores HTTP con logging y exit code no-cero

## 3. Motor diferencial

- [x] 3.1 Implementar `scraper/diff.py`: carga `state.json` y genera clave `YYYY_NNNN`
- [x] 3.2 Comparar resoluciones extraídas contra claves en `state.json`
- [x] 3.3 Retornar lista de resoluciones nuevas (o vacío si no hay novedades)
- [x] 3.4 Actualizar `state.json` con las nuevas claves al finalizar

## 4. Parser de PDFs

- [x] 4.1 Implementar `scraper/parser.py`: descarga PDF desde URL y extrae texto con pdfplumber
- [x] 4.2 Extraer metadatos del encabezado: NCG número, fecha, sesión, resolución exenta
- [x] 4.3 Detectar y parsear secciones romanas con normas afectadas y acciones de modificación
- [x] 4.4 Extraer vigencia por sección (inmediata, fecha específica, cláusula de transición)
- [x] 4.5 Implementar fallback a PyMuPDF cuando pdfplumber falla
- [x] 4.6 Marcar entradas no parseables con `parsed: false` sin lanzar excepción

## 5. Handlers especiales

- [x] 5.1 Implementar detección de referencias RAN con regex `Capítulo ([IVXLC\d][\w.-]*) (?:de la )?(?:Recopilación|RAN)`
- [x] 5.2 Implementar detección de referencias MSI con contexto de 200 caracteres
- [x] 5.3 Implementar detección de archivos afectados (crear/modificar/eliminar) con sus fechas de vigencia

## 6. Generación de JSON

- [x] 6.1 Implementar `scraper/store.py`: ensambla el objeto resolución con todos los campos
- [x] 6.2 Generar `data/daily/YYYY-MM-DD.json` con estructura definida en specs
- [x] 6.3 Manejar el caso de diferencial vacío (archivo generado con `new_entries: []`)

## 7. Panel de control (dashboard)

- [x] 7.1 Implementar `scraper/dashboard.py`: lee todos los JSONs en `data/daily/` y genera `docs/index.html`
- [x] 7.2 Tabla HTML con columnas: Fecha, N° Resolución, Tipo de Acuerdo, Norma(s) Afectada(s), Enlace
  - ⟳ Las columnas quedaron **Fecha · Norma · Tipo de acuerdo · Norma(s) afectada(s) · Vigencia · PDF**. «N° Resolución» se eliminó: el PDF declara una resolución exenta sólo en 7 de 607 casos, y el resto se rellenaba con el número del propio documento, mostrando una «Resolución Exenta N°2.370» que no existe. La identidad del documento vive ahora en la columna «Norma». Se agregó «Vigencia», que es el dato que responde la pregunta de fondo.
- [x] 7.3 Filtro por tipo de acuerdo (dropdown o botones)
- [x] 7.4 Sección de línea de tiempo agrupada por NCG afectada, ordenada cronológicamente
  - ⟳ Dentro de cada norma sí va cronológica, pero **las normas se ordenan por cantidad de eventos**, no por número: por número abría en la NCG N°1 con un evento suelto, que es justo donde no hay línea que ver. Sólo entran las normas afectadas 2 o más veces.
- [x] 7.5 Título "Monitoreo Normativo CMF" y línea descriptiva en el encabezado
  - ⟳ El título va en estilo de oración («Monitoreo normativo CMF») por la guía de marca CMF, y el encabezado lleva el logo oficial en su versión blanca sobre banda navy.
- [ ] 7.6 Implementar franja de notificación: leer JSON del día actual, mostrar banner si `new_entries` no está vacío
  - ⚠ **Se implementó y después se perdió.** Estuvo viva en el commit `6609585` («feat: dashboard con filtros, banner novedades y agrupacion por NCG») y desapareció en la reescritura del dashboard, sin que nadie lo notara ni actualizara esta tarea. La marca `[x]` era correcta cuando se puso. Hoy no existe ninguna franja en `docs/index.html`; lo que cubre parte de su función es el resaltado de filas nuevas, que sí quedó (`_render_tabla` recibe las entradas del diferencial más reciente y les aplica la clase `.nueva`).
  - El código original se conserva en la rama `archivo/2026-05-07-pre-v2-main`. Antes de reponerlo, leer la nota de `design.md`: la condición no puede ser «el build corrió hoy» —el HTML cambia a diario aunque no haya novedades— sino «apareció un archivo nuevo en `data/daily/`».
- [ ] 7.7 Franja con texto de cantidad de novedades, fecha, y enlace/scroll a las filas nuevas en la tabla
  - ⚠ Igual que 7.6: se perdió con la franja. El resaltado de filas cubre sólo la parte de «cuáles son las nuevas».
- [x] 7.8 Diseño responsive sin frameworks externos (CSS inline o `<style>` embebido)
  - ⟳ Se mantiene sin framework, pero el CSS ya no es ad-hoc: implementa los tokens del **CMF Design System** (color, tipografía, espaciado, componentes), embebidos en `_TEMPLATE`.

- [x] 7.9 *(no planificada)* Pestañas «Agenda de tareas», «Cambios relevantes» y «Revisión manual», además del listado. El brief pedía una tabla; al usarlo quedó claro que la pregunta que importa es *cuándo hay que tener esto hecho*, y eso necesita un eje temporal propio.

## 8. GitHub Actions workflow

- [x] 8.1 Crear `.github/workflows/monitoreo.yml` con trigger `schedule: cron: '0 11 * * *'` y `workflow_dispatch`
- [x] 8.2 Configurar el job con `permissions: contents: write`
- [x] 8.3 Steps: checkout → setup Python → install deps → run scraper → run dashboard generator
- [x] 8.4 Step de commit: `git config`, `git add`, `git diff --quiet || git commit && git push`
- [x] 8.5 Verificar que el workflow no hace commit cuando no hay cambios

## 9. Activación de GitHub Pages

- [ ] 9.1 Activar GitHub Pages en el repositorio apuntando a `/docs`
- [ ] 9.2 Verificar que `docs/index.html` se sirve correctamente en la URL de Pages
- [~] 9.3 ~~Confirmar que el fetch de JSONs desde el browser funciona sin errores CORS~~ — **no aplica**. Presuponía el diseño D5 original, donde `index.html` leía los JSONs desde el browser. `dashboard.py` renderiza del lado del servidor y escribe el HTML con los datos ya incrustados, así que la página no hace ninguna petición de datos: no hay fetch que confirmar ni CORS que pueda fallar.

## 10. Bootstrap histórico

- [x] 10.1 Implementar flag `--from YYYY-MM-DD` en el scraper para bootstrap con rango de fechas
- [x] 10.2 Ejecutar bootstrap manual con `--from 2024-01-01` para poblar `state.json` e historial de JSONs
  - Hecho, y con más alcance del previsto: `data/daily/2026-05-07.json` trae 564 entradas y el histórico llega hasta 1990. `state.json` tiene 607 claves.
- [x] 10.3 Verificar que el bootstrap no descarga PDFs ya procesados en runs subsiguientes
  - Verificado por los datos: tras el bootstrap, los archivos diarios siguientes traen 1–3 entradas cada uno, no 564. El diff contra `state.json` funciona.

## 11. Pruebas finales

- [ ] 11.1 Probar el parser con los dos documentos de ejemplo (NCG 561 y NCG 559)
- [ ] 11.2 Ejecutar el workflow de Actions manualmente y verificar el JSON diferencial
- [ ] 11.3 Verificar que el panel muestra la franja cuando hay novedades y la oculta cuando no
- [ ] 11.4 Confirmar que una segunda ejecución no genera duplicados en `state.json`
- [ ] 11.5 Verificar que el panel carga correctamente en el browser desde GitHub Pages
