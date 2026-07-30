## ADDED Requirements

### Requirement: Detección de archivos normativos afectados
El sistema SHALL identificar en el texto del PDF menciones explícitas a archivos que deben ser creados, modificados o eliminados como consecuencia de la norma.

#### Scenario: Archivo a crear mencionado
- **WHEN** el texto contiene instrucciones que implican la creación de un archivo o formulario nuevo (ej. "deberá presentar el formulario X", "se crea el archivo Y")
- **THEN** se registra en `archivos_afectados` con `accion: "crear"` y el nombre/descripción del archivo

#### Scenario: Archivo a modificar mencionado
- **WHEN** el texto modifica un formulario, archivo o anexo existente
- **THEN** se registra con `accion: "modificar"` y referencia al archivo afectado

#### Scenario: Archivo a eliminar mencionado
- **WHEN** el texto deroga o elimina un formulario o archivo existente
- **THEN** se registra con `accion: "eliminar"` y referencia al archivo afectado

#### Scenario: Sin archivos mencionados
- **WHEN** el PDF no menciona archivos, formularios ni anexos específicos
- **THEN** `archivos_afectados` es un array vacío

### Requirement: Captura de fecha de vigencia del archivo
El sistema SHALL registrar la fecha desde la cual el archivo afectado entra en vigencia, cuando esté disponible en el texto.

#### Scenario: Fecha de vigencia explícita
- **WHEN** el texto asocia el archivo a una fecha de vigencia
- **THEN** `archivos_afectados[].vigencia` contiene la fecha en formato ISO 8601

#### Scenario: Fecha de vigencia no especificada
- **WHEN** no hay fecha asociada al archivo
- **THEN** `archivos_afectados[].vigencia` es `null`
