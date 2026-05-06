import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"

TIPO_ACUERDO_MAP = {
    "APRUEBA CONSULTA PÚBLICA": "Consulta Pública",
    "POSPONER EL PLAZO LÍMITE DE LA CONSULTA PÚBLICA": "Prórroga Consulta Pública",
    "MODIFICA LA NORMA DE CARÁCTER GENERAL": "Modificación NCG",
    "APRUEBA NUEVA NORMATIVA": "Nueva Normativa",
    "EMITE CIRCULAR": "Circular",
}


def _inferir_tipo_acuerdo(descripcion: str) -> str:
    desc_upper = descripcion.upper()
    for frase, tipo in TIPO_ACUERDO_MAP.items():
        if frase in desc_upper:
            return tipo
    return "Otro"


def ensamblar_entrada(raw: dict, parsed: dict) -> dict:
    """Combina los datos del listado HTML con el parsing del PDF."""
    entrada = {
        "clave": raw.get("_key", ""),
        "fecha": raw.get("fecha"),
        "resolucion": parsed.get("resolucion") or {
            "tipo": "Exenta",
            "numero": raw.get("numero"),
            "fecha": raw.get("fecha"),
        },
        "sesion": parsed.get("sesion"),
        "ncg": parsed.get("ncg"),
        "tipo_acuerdo": _inferir_tipo_acuerdo(raw.get("descripcion", "")),
        "descripcion_cmf": raw.get("descripcion", ""),
        "url_documento": raw.get("url_documento"),
        "parsed": parsed.get("parsed", False),
    }

    if parsed.get("parsed"):
        entrada["modifica"] = parsed.get("modifica", [])
        entrada["vigencia"] = parsed.get("vigencia", {})
        entrada["ran_referencias"] = parsed.get("ran_referencias", [])
        entrada["msi_referencias"] = parsed.get("msi_referencias", [])
        entrada["archivos_afectados"] = parsed.get("archivos_afectados", [])

    return entrada


def guardar_diferencial(nuevas: list[dict], fecha: str | None = None) -> Path:
    """Escribe el JSON diferencial del día en data/daily/YYYY-MM-DD.json."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    hoy = fecha or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DAILY_DIR / f"{hoy}.json"

    payload = {
        "date": hoy,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "new_entries": nuevas,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info("Diferencial guardado: %s (%d entradas)", path, len(nuevas))
    return path
