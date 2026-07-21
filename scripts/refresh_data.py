"""Orquestador: lee data/raw_apartment_mx.parquet, agrega P&L por (mes, region)
para las dos vistas (ACC y Sintético), y escribe site/data/kpi_pnl.json.

Uso:
    make refresh
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import db_dtypes  # noqa: F401  registra tipos dbdate/dbtime del parquet
import pandas as pd

from scripts._pnl import (
    LABEL_OTROS,
    MIN_ROWS_PER_REGION,
    PNL_STRUCTURE,
    aggregate_all_regions,
    line_values_per_nid,
    prepare,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = REPO_ROOT / "data" / "raw_apartment_mx.parquet"
OUT_PATH = REPO_ROOT / "site" / "data" / "kpi_pnl.json"
OUT_FACTS_PATH = REPO_ROOT / "site" / "data" / "kpi_pnl_facts.json"


def _region_labels(df_prepared: pd.DataFrame) -> list[dict]:
    """Devuelve lista de regiones con conteo, ordenadas: real > Otros > Total."""
    counts = df_prepared["region_norm"].value_counts()
    real = [r for r in counts.index if r != LABEL_OTROS]
    real_sorted = sorted(real, key=lambda r: -int(counts[r]))
    ordered = real_sorted
    if LABEL_OTROS in counts.index:
        ordered.append(LABEL_OTROS)
    ordered.append("Total")
    return [
        {"key": r, "label": r, "filas": int(counts.get(r, 0)) if r != "Total" else int(counts.sum())}
        for r in ordered
    ]


def _long_to_nested(long_df: pd.DataFrame) -> dict:
    """{region → {mes → {key → valor}}} con floats redondeados a 2."""
    out: dict = {}
    for (region, mes), sub in long_df.groupby(["region", "mes"], sort=False):
        d = {row.key: round(float(row.valor), 2) for row in sub.itertuples()}
        out.setdefault(region, {})[mes] = d
    return out


def main() -> None:
    if not RAW_PATH.exists():
        raise SystemExit(f"No existe {RAW_PATH}. Corre `make raw` primero.")

    log.info("Leyendo %s ...", RAW_PATH)
    raw = pd.read_parquet(RAW_PATH)
    log.info("Raw: %d filas", len(raw))

    log.info("Preparando (mes + region_norm) ...")
    df = prepare(raw)
    log.info("Después de prepare: %d filas (excluidas %d por fecha nula)", len(df), len(raw) - len(df))

    regiones = _region_labels(df)
    log.info("Regiones finales: %s", [r["key"] for r in regiones])

    log.info("Agregando vista ACC ...")
    long_acc = aggregate_all_regions(df, "acc")
    log.info("Agregando vista Sintético ...")
    long_sint = aggregate_all_regions(df, "sintetico")

    meses = sorted(df["mes"].unique().tolist())

    payload = {
        "meta": {
            "generado_en": datetime.now().isoformat(timespec="seconds"),
            "tabla_fuente": "clients-domain-data-master.finance_wh_bi.finance_apartment_tracker_mx",
            "cohorte": "fecha_facturacion_venta (mes de escritura)",
            "currency": "MXN",
            "unidad": "unidades absolutas (el frontend divide por 1000 para mostrar en 000's)",
            "min_rows_per_region": MIN_ROWS_PER_REGION,
            "filas_raw": int(len(raw)),
            "filas_incluidas": int(len(df)),
            "filas_excluidas_por_fecha_nula": int(len(raw) - len(df)),
            "rango_fechas": {
                "min": pd.to_datetime(df["fecha_facturacion_venta"]).min().strftime("%Y-%m-%d"),
                "max": pd.to_datetime(df["fecha_facturacion_venta"]).max().strftime("%Y-%m-%d"),
            },
        },
        "estructura": PNL_STRUCTURE,
        "regiones": regiones,
        "meses": meses,
        "vistas": {
            "acc": _long_to_nested(long_acc),
            "sintetico": _long_to_nested(long_sint),
        },
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    log.info("Escrito → %s (%.1f KB)", OUT_PATH, OUT_PATH.stat().st_size / 1024)

    # ── kpi_pnl_facts.json: valores por-NID (para drill-down) ──
    log.info("Construyendo facts por-NID ...")
    line_keys = [r["key"] for r in PNL_STRUCTURE]

    facts_payload = {}
    for vista in ("acc", "sintetico"):
        per_nid = line_values_per_nid(df, vista)
        # arrays paralelos + matriz de valores (round a 2)
        nids = per_nid["nid"].astype(str).tolist()
        regs = per_nid["region"].astype(str).tolist()
        meses_ = per_nid["mes"].astype(str).tolist()
        matriz = []
        for k in line_keys:
            if k in per_nid.columns:
                # redondear a 2 decimales; convertir a floats python nativos
                col = per_nid[k].round(2).astype(float).tolist()
                matriz.append(col)
            else:
                matriz.append([0.0] * len(per_nid))
        facts_payload[vista] = {
            "columnas": line_keys,
            "nid": nids,
            "region": regs,
            "mes": meses_,
            # matriz [linea][nid_idx] → val
            "valores": matriz,
        }

    with open(OUT_FACTS_PATH, "w", encoding="utf-8") as f:
        json.dump(facts_payload, f, ensure_ascii=False, separators=(",", ":"))
    log.info("Escrito → %s (%.1f KB)", OUT_FACTS_PATH, OUT_FACTS_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
