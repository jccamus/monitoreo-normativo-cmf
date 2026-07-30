## ADDED Requirements

### Requirement: Clave de unicidad por resolución
El sistema SHALL identificar cada resolución con una clave compuesta de año (extraído de la fecha) y número de resolución, con formato `YYYY_NNNN`.

#### Scenario: Generación de clave
- **WHEN** una resolución tiene fecha `2026-04-10` y número `3920`
- **THEN** su clave es `2026_3920`

### Requirement: Detección de entradas nuevas
El sistema SHALL comparar el conjunto de resoluciones extraídas del sitio CMF contra las claves registradas en `state.json`, identificando solo las no vistas previamente.

#### Scenario: Nuevas resoluciones presentes
- **WHEN** el scraper extrae resoluciones y alguna tiene una clave no registrada en `state.json`
- **THEN** esa resolución se incluye en el diferencial diario

#### Scenario: Sin novedades
- **WHEN** todas las resoluciones extraídas ya están en `state.json`
- **THEN** el diferencial diario se genera vacío (`new_entries: []`) y no se procesan PDFs

### Requirement: Actualización de estado post-ejecución
El sistema SHALL agregar las claves de las nuevas resoluciones procesadas exitosamente a `state.json` al finalizar cada ejecución.

#### Scenario: Actualización exitosa
- **WHEN** el diferencial diario se ha generado sin errores críticos
- **THEN** `state.json` se actualiza con las nuevas claves antes del commit
