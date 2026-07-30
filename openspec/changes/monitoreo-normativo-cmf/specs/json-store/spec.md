## ADDED Requirements

### Requirement: Estructura del JSON diferencial diario
El sistema SHALL generar un archivo `data/daily/YYYY-MM-DD.json` por cada ejecución, con la siguiente estructura mínima.

#### Scenario: JSON con entradas nuevas
- **WHEN** el diferencial detecta resoluciones nuevas
- **THEN** el archivo generado contiene: `date` (ISO 8601), `generated_at` (timestamp UTC), `new_entries` (array de objetos resolución)

#### Scenario: JSON vacío
- **WHEN** no hay resoluciones nuevas
- **THEN** el archivo contiene `date`, `generated_at` y `new_entries: []`

### Requirement: Estructura del objeto resolución en el JSON
Cada entrada en `new_entries` SHALL contener los campos: `clave`, `fecha`, `resolucion`, `sesion`, `tipo_acuerdo`, `descripcion_cmf`, `url_documento`, `parsed`, y condicionalmente `modifica`, `vigencia`, `ran_referencias`, `msi_referencias`, `archivos_afectados`.

#### Scenario: Resolución completamente parseada
- **WHEN** el PDF fue procesado exitosamente
- **THEN** `parsed: true` y todos los campos estructurales están presentes

#### Scenario: Resolución no parseada
- **WHEN** el PDF no pudo ser procesado
- **THEN** `parsed: false` y solo están presentes los campos extraídos del HTML del listado

### Requirement: Persistencia de state.json
El sistema SHALL mantener `data/state.json` con el array `seen` de todas las claves procesadas históricamente.

#### Scenario: Primera ejecución
- **WHEN** `state.json` no existe
- **THEN** el sistema lo crea con `seen: []` antes de procesar

#### Scenario: Ejecución subsiguiente
- **WHEN** `state.json` existe con claves previas
- **THEN** el sistema lo lee, hace el diff, y añade las nuevas claves al array `seen`
