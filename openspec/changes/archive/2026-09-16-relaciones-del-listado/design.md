## Context

Cada fila del listado de la CMF tiene 18 celdas `<td>`. Las cuatro relaciones ocupan dos celdas cada una —una con los enlaces y otra con las fechas—, bajo un encabezado de dos renglones con `colspan`:

| celdas | columna |
|---|---|
| 9 / 10 | Modifica a (número / fecha) |
| 11 / 12 | Modificada por |
| 13 / 14 | Deroga a |
| 15 / 16 | Derogada por |

Lo que se midió sobre el listado completo (4.608 filas, 16-09-2026):

- **La celda de enlaces intercala enlaces vacíos** hacia la SBIF (`LeyNorma?indice=…`) o hacia `__.pdf`. Contando sólo los enlaces con texto, el número de ítems y el de fechas cuadra en las 2.622 celdas con contenido, sin excepción. (El «2 normas y 4 fechas» de la NCG 484 que anotaba el pendiente salía de contar como normas sólo los enlaces a PDF: son 4 ítems con 4 fechas, dos de ellos capítulos de la RAN.)
- **El tipo sale del `href`** en 3.862 de 3.871 ítems de las cuatro columnas (en las dos que se guardan, 1.940 de 1.947). Los 9 restantes: capítulos de la RAN (`cap. 2-2`, enlazados a una página del portal), oficios ordinarios (`ofo_5280_2010.pdf`) y un `ofc_6.016_1999.pdf` con punto de miles. En ningún caso el texto del enlace contradice al `href`.
- **Frente al PDF**: en 295 de 388 entradas guardadas el listado aporta normas que el parser no ve; en 10 el parser ve normas que el listado no trae (la NCG 565/2026 deroga la NCG 534 y el listado la omite); donde ambos conocen la norma, la acción coincide en 130 de 130.

El caso que conviene tener a mano: la NCG 562/2026 deroga la NCG 18 y las Circulares 632 y 695 en una sección «VI. DEROGACIÓN» que va *después* de la de vigencia. El parser corta el cuerpo en la vigencia y no la ve; el listado sí la trae. Se sospechó primero un error del listado y era un hueco del parser.

**⟳ Después de archivar (16-09-2026)**: el hueco de la 562 se corrigió en el parser. De las 10 entradas en que el parser veía normas que el listado no trae, 4 eran menciones dentro de un texto citado (NCG 479, 520 y 524, circular 2360) y hoy el parser las descarta (`parser._en_cita_local`); otras 2 eran series ex-SBIF: la Circular N°1 de Empresas Emisoras en la 537, que hoy se descarta, y la Circular N°12 de 2010 en la 534, que no: la 534 no escribe «de Auditores Externos» y la serie sólo se deduce del contexto.

## Goals / Non-Goals

**Goals:**
- Guardar «Modifica a» y «Deroga a» por entrada, con la fuente separada de `modifica[]`.
- Completar el histórico sin bajar PDF.
- Que el dashboard muestre las normas afectadas que sólo conoce el listado y use su acción.

**Non-Goals:**
- **«Modificada por» y «Derogada por» no se guardan.** Ver la decisión 2.
- No se corrige el parser por lo que aporte el listado (el hueco de la 562 queda anotado aparte).
- No se modelan las relaciones con capítulos de la RAN ni con oficios ordinarios más allá de guardarlas: el dashboard sólo indexa NCG, Circular y Oficio Circular.

## Decisions

**1. Campo propio `relaciones_cmf`, no entradas en `modifica[]` con otra `fuente`.**
`modifica[]` ya carga la historia de `fuente: "descripcion_cmf"`, que obligó a que el dashboard la ignore y la re-deduzca. Mezclar una tercera fuente en la misma lista repite ese problema, y `reparse.py` reescribe `modifica[]` completo con lo del PDF. Forma:

```json
"relaciones_cmf": {
  "modifica_a": [{"tipo": "NCG", "numero": 431, "anio": 2019, "fecha": "2019-02-12", "url": "…/ncg_431_2019.pdf"}],
  "deroga_a":   [{"tipo": null, "numero": null, "anio": null, "fecha": "2022-08-05", "referencia": "cap. 2-2", "url": "…"}]
}
```

`tipo` usa los mismos valores que `tipo_norma` (`NCG`, `Circular`, `Oficio Circular`) para que el par `(tipo, numero)` sea la misma identidad que ya usa el dashboard. Lo que no calza con `ncg|cir|ofc` se guarda con `tipo: null` y el texto del enlace en `referencia`: no se descarta información, pero tampoco se le inventa identidad.

*Alternativa descartada*: `modifica[]` con `fuente: "listado_cmf"`. Más corta, pero ata dos fuentes con ciclos de vida distintos a la misma lista.

**2. Sólo las relaciones salientes.**
«Modifica a» y «Deroga a» describen al documento y no cambian: se fijan cuando se publica. «Modificada por» y «Derogada por» describen lo que *otros* documentos le hicieron después, y crecen con el tiempo. Como cada entrada se guarda una vez (diff por clave), una foto de esas columnas quedaría desactualizada con la primera norma posterior, y se leería como «nadie la ha modificado». Lo entrante ya se obtiene invirtiendo las relaciones salientes de las demás entradas, que es lo que hace la línea de tiempo.

*Alternativa descartada*: guardar las cuatro con fecha de consulta y refrescarlas en cada corrida. Reescribiría decenas de archivos de `data/daily/` dos veces al día y ensuciaría cada commit del workflow.

**3. Identidad desde el `href`, fecha por posición, y ante descuadre nada.**
El texto de la celda trae el número pelado (`275`), sin tipo ni año; el `href` trae los tres. La fecha se aparea por índice entre los enlaces con texto y las fechas de la celda vecina. Si las cantidades no coinciden, esa relación de esa fila no emite nada y se registra un warning: un apareo corrido asigna fechas a la norma equivocada sin que se note. Hoy son 0 casos.

La posición de las celdas se valida contra el encabezado (renglón 1: «Modifica a», «Deroga a» con `colspan=2`) en vez de fijar índices a ciegas: si la CMF reordena la tabla, el campo queda vacío y hay warning, igual que en `_fecha_y_numero_desde_columnas`.

**4. En el dashboard: el listado es piso, y segunda fuente para la acción.**
- `_normas_afectadas_ids` suma las `(tipo, numero)` de `relaciones_cmf` a lo que ya calcula. Nunca resta.
- `_accion_sobre_norma` consulta, en orden: `modifica[]` del PDF → `relaciones_cmf` (`deroga_a` → «Derogada por», `modifica_a` → «Modificada por») → descripción. El PDF va primero porque es el texto normativo; el listado antes de la descripción porque es estructurado y la descripción se deduce con regex.
- Una norma que sólo aporta el listado se rotula como tal en el detalle, con el mismo criterio que «· calculada» o «· confirmada»: el dato vale y de dónde salió también.

Como `_tipos_de_entrada` se engancha a `_accion_sobre_norma`, las categorías se corrigen solas, sin tocar `TIPO_ACUERDO_MAP`.

**5. Carga retroactiva con un script propio, idempotente.**
`scraper/relaciones.py` baja el listado una vez, indexa las filas por URL (sin el parámetro `&t=` de los enlaces `ver_sgd.php`, que cambia en cada consulta) y escribe `relaciones_cmf` en cada entrada de `data/daily/`. Reescribir el campo con el mismo valor no cambia el archivo, así que se puede correr cuantas veces haga falta.

*Alternativa descartada*: un modo de `reparse.py`. `reparse` existe para re-bajar PDF; mezclarle un modo sin PDF confunde su contrato («es lento a propósito»).

**6. `reparse.py` no pisa el campo.**
`reparse` arma un `raw` sintético desde la entrada y hace `entrada.update(nueva)`. `ensamblar_entrada` sólo emite `relaciones_cmf` cuando el `raw` lo trae, y `reparse` le pasa el de la entrada guardada; así el campo sobrevive a un reparse aunque no venga del listado.

## Risks / Trade-offs

- [El listado se equivoca y el dashboard muestra una norma afectada que no lo es] → rótulo de origen en el detalle; el PDF manda sobre la acción cuando nombra la norma; `relaciones_cmf` queda separado y auditable.
- [La CMF cambia el HTML de la tabla] → validación contra el encabezado; campo vacío más warning en vez de datos corridos.
- [Las categorías cambian de golpe (muchas entradas «Otro» ganan categoría)] → medir el reparto antes y después al aplicar y dejarlo en CLAUDE.md, como en los cambios anteriores.
- [Series ex-SBIF en el listado] → la identidad sale del `href` (`cir_…` del sitio CMF), no del texto; si el listado enlaza un PDF CMF, es una circular CMF. No se esperan series ex-SBIF acá, pero se verifica al aplicar.
  **⟳ Verificación parcial (16-09-2026)**: ninguna circular del listado pasa del N°2.400, cuando la numeración CMF va en la 2.378 y la ex-SBIF llega a la 3.530. Es un indicio, no una prueba: una serie ex-SBIF de número bajo no se distinguiría así.

## Migration Plan

1. Mergear el código (fetch, store, dashboard, reparse, script).
2. Correr `python scraper/relaciones.py` una vez y regenerar el dashboard; commitear `data/daily/` y `docs/index.html`.
3. Las corridas siguientes guardan el campo en cada entrada nueva.

Rollback: revertir el commit de código. El campo extra en los JSON no lo lee nada más y puede quedarse.

## Open Questions

- ¿Conviene un aviso cuando el PDF y el listado discrepan en la acción sobre la misma norma? Hoy son 0 casos; se puede agregar como warning en `generar_html`, igual que `discrepa` en las revisiones manuales.
