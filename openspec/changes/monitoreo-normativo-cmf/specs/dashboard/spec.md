## ADDED Requirements

### Requirement: Página principal con título y descripción
El sistema SHALL mostrar en el encabezado del panel el título "Monitoreo Normativo CMF" y una línea descriptiva que explique el propósito de la información recopilada.

#### Scenario: Carga del panel
- **WHEN** el usuario accede a la URL de GitHub Pages
- **THEN** ve el título "Monitoreo Normativo CMF" y la descripción en la parte superior de la página

### Requirement: Tabla de resoluciones por tipo de norma
El sistema SHALL mostrar una tabla con todas las resoluciones registradas, con columnas: Fecha, N° Resolución, Tipo de Acuerdo, Norma(s) Afectada(s), y enlace al documento.

#### Scenario: Tabla con datos
- **WHEN** existen JSONs diferenciales con entradas
- **THEN** la tabla muestra todas las resoluciones ordenadas por fecha descendente

#### Scenario: Filtro por tipo de acuerdo
- **WHEN** el usuario selecciona un tipo de acuerdo (Consulta Pública, Nueva Norma, Modificación, Derogación)
- **THEN** la tabla muestra solo las resoluciones de ese tipo

### Requirement: Línea de tiempo de cambios por norma
El sistema SHALL mostrar una sección de línea de tiempo que agrupe los cambios históricos por norma afectada, permitiendo ver la secuencia de modificaciones de cada NCG.

#### Scenario: Norma con múltiples modificaciones
- **WHEN** una NCG ha sido modificada en múltiples resoluciones
- **THEN** aparece una entrada en la línea de tiempo con todas sus modificaciones ordenadas cronológicamente

### Requirement: Panel generado como HTML estático
El sistema SHALL generar el archivo `docs/index.html` durante el workflow de GitHub Actions, leyendo los datos desde los JSONs del repositorio.

#### Scenario: Generación automática
- **WHEN** el workflow de Actions termina exitosamente
- **THEN** `docs/index.html` se actualiza y GitHub Pages lo publica automáticamente

### Requirement: Franja de notificación de novedades
El sistema SHALL mostrar una franja destacada en la parte superior de la página cuando el diferencial del día en curso contiene entradas nuevas.

#### Scenario: Hay novedades hoy
- **WHEN** el JSON de la fecha actual (`data/daily/YYYY-MM-DD.json`) contiene al menos una entrada en `new_entries`
- **THEN** se muestra una franja de color destacado (ej. azul o amarillo) en la parte superior, con texto que indica la cantidad de nuevas resoluciones y la fecha

#### Scenario: Sin novedades hoy
- **WHEN** el diferencial del día está vacío o no existe aún
- **THEN** la franja no se muestra; el panel carga normalmente sin banner

#### Scenario: Franja con enlace a las novedades
- **WHEN** la franja está visible
- **THEN** incluye un enlace o botón que lleva directamente a las filas nuevas en la tabla

### Requirement: Compatibilidad sin backend
El panel SHALL funcionar completamente en el navegador del usuario, usando JavaScript para leer los archivos JSON desde URLs relativas del mismo repositorio en GitHub Pages.

#### Scenario: Lectura de datos desde el navegador
- **WHEN** el panel carga en el navegador
- **THEN** JavaScript hace fetch a los JSONs en `data/` y renderiza la tabla sin llamadas a un servidor externo
