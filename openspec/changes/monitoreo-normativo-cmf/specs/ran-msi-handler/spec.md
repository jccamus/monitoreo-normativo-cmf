## ADDED Requirements

### Requirement: Detección de referencias a capítulos RAN
El sistema SHALL detectar en el texto del PDF toda mención a capítulos de la Recopilación Actualizada de Normas (RAN) de bancos y extraer el identificador del capítulo.

#### Scenario: Referencia explícita a capítulo RAN
- **WHEN** el texto contiene patrones como `Capítulo X-Y de la Recopilación Actualizada de Normas` o `Capítulo [código] del RAN`
- **THEN** se extrae el identificador del capítulo (ej. `III.B.4`, `X-Y`) y se incluye en `ran_referencias`

#### Scenario: Sin referencias RAN
- **WHEN** el texto no menciona la Recopilación Actualizada de Normas
- **THEN** `ran_referencias` es un array vacío

### Requirement: Detección de referencias al MSI
El sistema SHALL detectar toda mención al Manual de Sistemas de Información (MSI) en el texto del PDF.

#### Scenario: Referencia al MSI detectada
- **WHEN** el texto contiene `Manual de Sistemas de Información`
- **THEN** se registra en `msi_referencias` con el contexto textual inmediato (máximo 200 caracteres alrededor de la mención)

#### Scenario: Sin referencias MSI
- **WHEN** el texto no menciona el Manual de Sistemas de Información
- **THEN** `msi_referencias` es un array vacío

### Requirement: Inclusión en el JSON diferencial
El sistema SHALL incluir los campos `ran_referencias` y `msi_referencias` en cada objeto resolución del JSON, incluso cuando estén vacíos.

#### Scenario: Objeto resolución con referencias RAN y MSI
- **WHEN** el PDF menciona capítulos RAN y el MSI
- **THEN** ambos campos aparecen en el JSON con sus valores extraídos
