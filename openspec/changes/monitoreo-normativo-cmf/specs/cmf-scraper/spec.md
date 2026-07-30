## ADDED Requirements

### Requirement: Extracción de resoluciones desde cmfchile.cl
El sistema SHALL realizar una solicitud HTTP GET a la URL de listado de la CMF y extraer todas las entradas de resoluciones disponibles en la página.

#### Scenario: Extracción exitosa
- **WHEN** el scraper accede a la URL de CMF y la página responde con HTTP 200
- **THEN** el sistema extrae todas las filas de resoluciones con: fecha, número de resolución, descripción y URL del documento

#### Scenario: Fallo de conexión
- **WHEN** el sitio CMF no responde o retorna error HTTP
- **THEN** el sistema registra el error en el log y termina con código de salida no-cero, sin modificar `state.json`

### Requirement: Filtrado por frases clave
El sistema SHALL filtrar las resoluciones extraídas, conservando solo aquellas cuya descripción contenga al menos una de las frases clave definidas.

#### Scenario: Resolución relevante detectada
- **WHEN** la descripción de una resolución contiene alguna de las frases: `APRUEBA CONSULTA PÚBLICA DE LA NORMA DE CARÁCTER GENERAL`, `POSPONER EL PLAZO LÍMITE DE LA CONSULTA PÚBLICA`, `MODIFICA LA NORMA DE CARÁCTER GENERAL`, `APRUEBA NUEVA NORMATIVA`, `EMITE CIRCULAR`
- **THEN** la resolución se incluye en el conjunto a procesar

#### Scenario: Resolución no relevante descartada
- **WHEN** la descripción no contiene ninguna de las frases clave
- **THEN** la resolución se omite sin registrar error

### Requirement: Respeto de rate limiting
El sistema SHALL introducir una pausa de entre 2 y 3 segundos entre cada solicitud HTTP al dominio cmfchile.cl.

#### Scenario: Múltiples requests en secuencia
- **WHEN** el scraper necesita descargar más de un documento
- **THEN** cada request subsiguiente espera al menos 2 segundos desde el anterior
