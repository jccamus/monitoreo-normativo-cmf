## Why

El listado de la CMF dice, en columnas propias y con enlace al PDF de cada norma, qué normas modifica y deroga cada documento, y el proyecto hoy lo descarta: las normas afectadas salen sólo del texto del PDF (regex) o, a falta de eso, de la descripción. Medido sobre el listado del 16-09-2026, en 295 de las 388 entradas con alguna relación el listado nombra normas que el PDF no deja ver —la NCG 571/2026 deroga 55 normas según el listado y el parser encuentra 3— y, donde las dos fuentes conocen la misma norma, coinciden en si la deroga o la modifica en los 130 casos.

## What Changes

- `fetch` lee las columnas «Modifica a» y «Deroga a» de cada fila: tipo, número y año desde el `href` de cada enlace (`ncg_431_2019.pdf`), nunca desde el texto de la celda, y la fecha apareada por posición con la columna de fechas vecina.
- Cada entrada guarda esas relaciones en un campo nuevo, `relaciones_cmf`, **sin reemplazar ni modificar `modifica[]`**, que sigue siendo lo que dice el PDF.
- Un script nuevo completa `relaciones_cmf` en las 674 entradas ya guardadas con una sola consulta al listado, sin bajar PDF.
- El dashboard usa las relaciones del listado como **piso** de las normas afectadas —suma las que el PDF no ve, nunca quita las que sí ve— y como segunda fuente, después del PDF y antes de la descripción, para decidir «Derogada por» / «Modificada por».
- Una norma afectada que sólo conoce el listado queda identificable como tal en el dashboard.

## Capabilities

### New Capabilities
- `relaciones-listado`: extracción, almacenamiento, carga retroactiva y uso en el dashboard de las relaciones «modifica a» / «deroga a» que publica el listado de la CMF.

### Modified Capabilities
_(ninguna: `openspec/specs/` está vacío porque el cambio original no se archivó; lo nuevo se especifica como capacidad aparte)_

## Impact

- **Código**: `scraper/fetch.py` (lectura de columnas), `scraper/store.py` (`ensamblar_entrada`), `scraper/dashboard.py` (`_normas_afectadas_ids`, `_accion_sobre_norma`, detalle), `scraper/reparse.py` (no pisar el campo), script nuevo de carga retroactiva.
- **Datos**: campo nuevo en las entradas de `data/daily/`; las existentes se completan una vez. No cambia `make_key` ni `state.json`, así que la próxima corrida no baja ningún PDF extra.
- **Dashboard**: las categorías «Derogación» y «Modificación …» van a crecer y «Otro» a bajar; el reparto de CLAUDE.md se actualiza al aplicar.
- **Sin dependencias nuevas** y sin requests extra en la corrida diaria: el listado ya se descarga completo.
