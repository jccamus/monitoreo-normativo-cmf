## ADDED Requirements

### Requirement: Extracción de metadatos del encabezado
El sistema SHALL extraer del texto PDF los metadatos principales del documento normativo: número de NCG, fecha, número y fecha de sesión, y número y fecha de resolución exenta (cuando exista).

#### Scenario: Documento con resolución exenta
- **WHEN** el PDF contiene texto con patrón `Resolución Exenta N°(\d+), de fecha (\d{1,2} de \w+ de \d{4})`
- **THEN** se extraen número y fecha de la resolución exenta

#### Scenario: Documento sin resolución exenta
- **WHEN** el PDF no menciona resolución exenta (solo sesión del Consejo)
- **THEN** el campo `resolucion` en el JSON resultante es `null`

### Requirement: Extracción de modificaciones por sección
El sistema SHALL identificar las normas afectadas y las acciones de modificación dentro del cuerpo del documento.

#### Scenario: Documento con secciones romanas múltiples
- **WHEN** el PDF contiene secciones `I.`, `II.`, etc. cada una con `MODIFICACIONES NORMA DE CARÁCTER GENERAL N°(\d+)`
- **THEN** se genera un item por cada sección con: norma afectada, lista de acciones (Agréguese, Intercálase, etc.) y ubicaciones (sección, letra, inciso)

#### Scenario: Documento con modificación directa sin secciones
- **WHEN** el PDF contiene una única acción de modificación sin numeración romana
- **THEN** se genera un único item de modificación con la norma afectada y la acción detectada

### Requirement: Extracción de vigencia
El sistema SHALL extraer la información de vigencia de cada sección, incluyendo fechas de transición cuando existan.

#### Scenario: Vigencia inmediata
- **WHEN** el texto de vigencia contiene `a partir de esta fecha` o `rige a contar de esta fecha`
- **THEN** `vigencia.inicio` se registra como `"inmediata"`

#### Scenario: Vigencia con fecha específica
- **WHEN** el texto contiene una fecha explícita de vigencia (ej. `a más tardar el 30 de abril de 2026`)
- **THEN** `vigencia.plazo` se registra con la fecha en formato ISO 8601

### Requirement: Manejo de fallo de parsing
El sistema SHALL detectar cuando no puede extraer la estructura mínima de un PDF y marcarlo para revisión manual.

#### Scenario: PDF no parseable
- **WHEN** el parser no puede extraer el número de NCG ni las acciones de modificación
- **THEN** la entrada se guarda con `parsed: false` y el campo `url` para revisión manual, sin lanzar excepción
