> **Nota de reconciliación (30-07-2026).** El *why* y el alcance de esta
> propuesta se cumplieron. Dos detalles quedaron distinto en la
> implementación y se corrigen abajo: la clave del diferencial es
> **año+número**, no fecha+número, y el panel terminó con cuatro pestañas y
> no sólo tabla más línea de tiempo. El detalle de qué cambió y por qué está
> en `design.md` y en `tasks.md`; el comportamiento actual, en `CLAUDE.md`.

## Why

El seguimiento manual de cambios normativos en el sitio de la CMF es lento y propenso a omisiones. Este sistema automatiza la detección diaria de nuevas resoluciones, las estructura en JSON y las presenta en un panel público — permitiendo a periodistas y analistas reaccionar oportunamente a cambios regulatorios en el mercado financiero chileno.

## What Changes

- **Nuevo**: Scraper Python que extrae resoluciones del sitio cmfchile.cl filtrando por frases clave
- **Nuevo**: Parser de PDFs (pdfplumber/PyMuPDF) que extrae estructura normativa sin LLM
- **Nuevo**: Motor de diferencial que compara el estado actual vs. el último JSON guardado
- **Nuevo**: Generación de archivo `daily/YYYY-MM-DD.json` con solo los cambios del día
- **Nuevo**: Manejo especial para referencias a RAN (capítulos) y MSI
- **Nuevo**: Seguimiento de archivos normativos a crear, modificar o eliminar
- **Nuevo**: GitHub Actions workflow con ejecución diaria automática (cron)
- **Nuevo**: Panel de control en GitHub Pages (HTML estático) con tabla por tipo de norma y línea de tiempo

## Capabilities

### New Capabilities

- `cmf-scraper`: Extracción y filtrado de resoluciones desde cmfchile.cl por frases clave
- `pdf-parser`: Parsing estructural de PDFs normativos CMF con Python (sin LLM)
- `diff-engine`: Comparación diferencial entre estado actual y último snapshot, clave **año+número** (`YYYY_NNNN`)
- `json-store`: Almacenamiento de JSONs diferenciales y estado persistente en el repositorio
- `ran-msi-handler`: Detección y extracción especial de referencias a capítulos RAN y MSI
- `file-tracker`: Seguimiento de archivos normativos afectados (crear/modificar/eliminar)
- `dashboard`: Panel de control estático en GitHub Pages, renderizado del lado del servidor por `dashboard.py`, con cuatro pestañas (Agenda de tareas, Cambios relevantes, Revisión manual, Listado completo con tabla y línea de tiempo)
- `scheduler`: GitHub Actions workflow con cron diario, commit automático y trigger de Pages

### Modified Capabilities

_(ninguna — proyecto nuevo)_

## Impact

- **Nuevo repositorio GitHub** (público): contiene código, datos y panel de control
- **Dependencias Python**: `requests`, `beautifulsoup4`, `pdfplumber` o `PyMuPDF`, `python-dateutil`
- **GitHub Actions**: runner Ubuntu, permisos de escritura al repo para commit automático
- **GitHub Pages**: activado en `/docs`, sin costo por ser repo público
- **Sin base de datos**: todo el estado vive en archivos JSON dentro del repo
- **Sin costos de API**: no se usa ningún LLM externo
