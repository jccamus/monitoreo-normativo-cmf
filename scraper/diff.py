import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).parent.parent / "data" / "state.json"


def _load_state() -> set[str]:
    if not STATE_PATH.exists():
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _save_state(set())
        return set()
    with open(STATE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("seen", []))


def _save_state(seen: set[str]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"seen": sorted(seen)}, f, ensure_ascii=False, indent=2)


def make_key(fecha: str, numero: str | None) -> str:
    """Genera clave YYYY_NNNN desde fecha ISO y número de resolución."""
    year = fecha[:4] if fecha else "0000"
    num = str(numero).zfill(4) if numero else "0000"
    return f"{year}_{num}"


def get_nuevas(resoluciones: list[dict]) -> list[dict]:
    """Retorna solo las resoluciones no vistas previamente."""
    seen = _load_state()
    nuevas = []
    for r in resoluciones:
        key = make_key(r.get("fecha", ""), r.get("numero"))
        if key not in seen:
            r["_key"] = key
            nuevas.append(r)
    logger.info("%d resoluciones nuevas de %d totales", len(nuevas), len(resoluciones))
    return nuevas


def commit_nuevas(nuevas: list[dict]) -> None:
    """Agrega las claves de las nuevas resoluciones a state.json."""
    seen = _load_state()
    for r in nuevas:
        seen.add(r["_key"])
    _save_state(seen)
    logger.info("state.json actualizado con %d nuevas claves", len(nuevas))
