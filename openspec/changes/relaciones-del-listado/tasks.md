## 1. Extracción en fetch

- [ ] 1.1 Validar el encabezado de la tabla en `_parse_listado` y ubicar las celdas de «Modifica a» y «Deroga a»; si no están donde se esperan, warning y relaciones vacías para todas las filas
- [ ] 1.2 Función que lee una celda de relación: enlaces con texto, identidad desde el `href` (`ncg|cir|ofc`, número con punto de miles), `referencia` para los que no calzan, fechas de la celda contigua apareadas por posición
- [ ] 1.3 Descuadre entre ítems y fechas: no emitir esa relación y registrar warning con la URL
- [ ] 1.4 Agregar `relaciones_cmf` a la fila que devuelve `_extraer_celda`, documentando en el comentario los casos medidos (enlaces vacíos a la SBIF, NCG 484, `ofc_6.016_1999.pdf`)
- [ ] 1.5 Verificar sobre `listado.html` guardado: 0 descuadres, 3.862 ítems con tipo, NCG 565 con 11 derogadas y NCG 571 con 55

## 2. Almacenamiento

- [ ] 2.1 `store.ensamblar_entrada` copia `relaciones_cmf` del `raw` sólo cuando viene, sin tocar `modifica[]`
- [ ] 2.2 `reparse.py` pasa el `relaciones_cmf` de la entrada guardada en el `raw` sintético; verificar con un reparse acotado que el campo sobrevive

## 3. Carga retroactiva

- [ ] 3.1 `scraper/relaciones.py`: una consulta al listado, índice por URL sin `&t=`, escritura idempotente de `relaciones_cmf` en `data/daily/`, informe de entradas no encontradas, modo `--dry-run`
- [ ] 3.2 Correr en seco, revisar el informe y luego correr de verdad; confirmar que una segunda corrida no cambia archivos

## 4. Dashboard

- [ ] 4.1 `_normas_afectadas_ids` suma las `(tipo, numero)` de `relaciones_cmf` con tipo conocido
- [ ] 4.2 `_accion_sobre_norma`: PDF → `relaciones_cmf` → descripción
- [ ] 4.3 Rotular en el detalle las normas afectadas que sólo aporta el listado; darle clase a cualquier celda nueva y ubicarla en la grilla de celular
- [ ] 4.4 Medir el reparto de categorías antes y después y revisar a mano una muestra de entradas que salen de «Otro»

## 5. Cierre

- [ ] 5.1 Actualizar CLAUDE.md: pipeline (fetch y store), contratos de datos (`relaciones_cmf`), reparto de categorías y por qué no se guardan las relaciones entrantes
- [ ] 5.2 Regenerar `docs/index.html`, commitear código y datos por separado, y pushear
- [x] 5.3 Anotar en `pendientes-malla.md` el hueco de la NCG 562/2026 (sección de derogación después de la de vigencia, «Derogase» sin tilde)
