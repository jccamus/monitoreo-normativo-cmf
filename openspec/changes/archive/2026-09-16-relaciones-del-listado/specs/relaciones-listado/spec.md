## ADDED Requirements

### Requirement: Lectura de las relaciones salientes del listado
El sistema SHALL extraer, de cada fila del listado de la CMF, las relaciones «Modifica a» y «Deroga a», tomando el tipo, el número y el año de cada norma relacionada desde el `href` de su enlace y la fecha desde la celda de fechas contigua, apareadas por posición entre los enlaces que tienen texto.

#### Scenario: Relación con enlaces a PDF de la CMF
- **WHEN** la celda «Deroga a» de la NCG 565/2026 contiene enlaces con texto a `ncg_275_2010.pdf` y `cir_75_1981.pdf`, intercalados con enlaces vacíos a la SBIF
- **THEN** el sistema produce los ítems `{tipo: "NCG", numero: 275, anio: 2010}` y `{tipo: "Circular", numero: 75, anio: 1981}`, cada uno con la fecha que ocupa su misma posición, e ignora los enlaces vacíos

#### Scenario: Número con separador de miles en el href
- **WHEN** el `href` de un ítem es `ofc_6.016_1999.pdf`
- **THEN** el ítem queda con `tipo: "Oficio Circular"` y `numero: 6016`

#### Scenario: Ítem sin patrón de norma reconocible
- **WHEN** el enlace de un ítem no calza con `ncg_`, `cir_` ni `ofc_` (por ejemplo, un capítulo de la RAN o un oficio ordinario `ofo_`)
- **THEN** el ítem se guarda con `tipo`, `numero` y `anio` nulos, su texto en `referencia` y su fecha, sin asignarle identidad deducida del texto

#### Scenario: Cantidad de ítems y de fechas no cuadra
- **WHEN** una celda de relación tiene un número de enlaces con texto distinto del número de fechas de su celda contigua
- **THEN** el sistema no emite ningún ítem para esa relación de esa fila y registra un warning con la URL del documento

#### Scenario: La tabla cambió de estructura
- **WHEN** el encabezado del listado no tiene «Modifica a» y «Deroga a» en las posiciones esperadas
- **THEN** el sistema no emite relaciones para ninguna fila, registra un warning y el resto del pipeline sigue funcionando

### Requirement: Almacenamiento separado de modifica[]
El sistema SHALL guardar las relaciones del listado en el campo `relaciones_cmf` de cada entrada, con las listas `modifica_a` y `deroga_a`, sin agregar, quitar ni alterar entradas de `modifica[]`.

#### Scenario: Entrada nueva con relaciones
- **WHEN** la corrida diaria captura un documento cuya fila trae relaciones
- **THEN** la entrada guardada en `data/daily/` contiene `relaciones_cmf` con esas relaciones y `modifica[]` contiene sólo lo que produjo el parser y el respaldo de la descripción

#### Scenario: Reparse de una entrada existente
- **WHEN** `reparse.py` reprocesa una entrada que ya tiene `relaciones_cmf`
- **THEN** la entrada conserva `relaciones_cmf` sin cambios

### Requirement: Sólo relaciones salientes
El sistema SHALL NOT guardar las columnas «Modificada por» ni «Derogada por» en las entradas, porque describen cambios posteriores al documento y quedarían desactualizadas en un almacenamiento diferencial.

#### Scenario: Fila con relaciones entrantes
- **WHEN** la fila de la NCG 562/2026 trae «Derogada por: NCG 571»
- **THEN** la entrada de la 562 no guarda esa relación, y la derogación se conoce por el `deroga_a` de la entrada de la 571

### Requirement: Carga retroactiva sin PDF
El sistema SHALL proveer un comando que complete `relaciones_cmf` en todas las entradas existentes de `data/daily/` a partir de una sola consulta al listado, sin descargar documentos, identificando cada entrada por su URL sin el parámetro variable `&t=`.

#### Scenario: Primera carga
- **WHEN** se ejecuta el comando sobre un histórico sin `relaciones_cmf`
- **THEN** cada entrada cuya URL aparece en el listado queda con el campo completo, y se informa cuántas entradas no se encontraron en el listado

#### Scenario: Ejecución repetida
- **WHEN** se ejecuta el comando dos veces seguidas sin cambios en el listado
- **THEN** la segunda ejecución no modifica ningún archivo

### Requirement: Uso de las relaciones en el dashboard
El dashboard SHALL sumar las normas de `relaciones_cmf` a las normas afectadas de cada entrada sin quitar ninguna de las que ya obtiene del PDF o de la descripción, y SHALL decidir la acción sobre cada norma consultando primero `modifica[]` del PDF, luego `relaciones_cmf` y por último la descripción.

#### Scenario: Norma que sólo conoce el listado
- **WHEN** el listado indica que la NCG 571/2026 deroga la NCG 26 y el PDF parseado no la menciona
- **THEN** la NCG 26 aparece entre las normas afectadas de la 571 con la acción «Derogada por», rotulada como proveniente del listado de la CMF

#### Scenario: Norma que conocen ambas fuentes
- **WHEN** el PDF y el listado nombran la misma norma
- **THEN** la acción que se muestra es la que deduce el PDF y la norma no se rotula como proveniente sólo del listado

#### Scenario: Norma que sólo conoce el PDF
- **WHEN** el PDF de la NCG 565/2026 deroga la NCG 534 y el listado no la incluye
- **THEN** la NCG 534 sigue apareciendo como «Derogada por»

#### Scenario: Categorías de filtro
- **WHEN** una entrada clasificada «Otro» tiene en `relaciones_cmf` una derogación de una NCG
- **THEN** la entrada aparece bajo el filtro «Derogación»
