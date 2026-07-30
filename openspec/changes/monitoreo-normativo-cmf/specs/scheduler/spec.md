## ADDED Requirements

### Requirement: Ejecución diaria automática vía GitHub Actions
El sistema SHALL ejecutarse automáticamente cada día mediante un workflow de GitHub Actions con schedule cron.

#### Scenario: Ejecución programada
- **WHEN** llega la hora definida en el cron (11:00 UTC, equivalente a 8:00 AM hora Chile en verano)
- **THEN** GitHub Actions lanza el workflow `monitoreo.yml` en un runner Ubuntu

### Requirement: Commit automático de resultados
El sistema SHALL hacer commit y push de los archivos generados (`state.json`, `data/daily/YYYY-MM-DD.json`, `docs/index.html`) al finalizar cada ejecución exitosa.

#### Scenario: Ejecución con novedades
- **WHEN** el workflow termina y existen archivos modificados
- **THEN** se hace `git commit` con mensaje `"chore: monitoreo YYYY-MM-DD"` y `git push` al branch principal

#### Scenario: Ejecución sin novedades
- **WHEN** el diferencial está vacío y no hay archivos modificados
- **THEN** el workflow termina sin hacer commit

### Requirement: Permiso de escritura al repositorio
El workflow SHALL usar el token `GITHUB_TOKEN` con permiso `contents: write` para poder hacer push al repositorio.

#### Scenario: Configuración de permisos
- **WHEN** el workflow se ejecuta
- **THEN** el step de configuración git usa el token de Actions con las credenciales del bot de GitHub

### Requirement: Ejecución manual disponible
El workflow SHALL permitir ejecución manual mediante `workflow_dispatch` para pruebas y backfill.

#### Scenario: Ejecución manual
- **WHEN** el usuario hace click en "Run workflow" en la interfaz de GitHub Actions
- **THEN** el workflow se ejecuta inmediatamente con los mismos pasos que la ejecución programada
