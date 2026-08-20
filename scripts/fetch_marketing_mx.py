"""Trae el raw de marketing spend MX por área metropolitana y lo guarda como
parquet en data/raw_marketing_mx.parquet.

Fuente: `sellers-main-prod.bi_mx.resumen_inversiones_regiones_mexico`.
El mapeo `area_metropolitana → region` es el que Kamila definió — NO idéntico al
de payroll (Valle de México va a EDO MEX aquí, mientras que el staff área metro
en payroll queda en CDMX).

Valores en USD. La conversión a MXN (FX 18.5) se aplica en refresh_data.py.

Uso:
    make raw_mkt
"""

from __future__ import annotations

import logging
from pathlib import Path

from scripts._bq import BILLING_PROJECT, run_query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "data" / "raw_marketing_mx.parquet"

QUERY = """
SELECT
    ir.mes as ir_mes_inversion,
    CASE
        WHEN ir.area_metropolitana = 'Valle de México' THEN 'EDO MEX'
        WHEN ir.area_metropolitana = 'Zona metropolitana Guadalajara' THEN 'JALISCO'
        WHEN ir.area_metropolitana = 'Zona metropolitana Monterrey' THEN 'NUEVO LEON'
        WHEN ir.area_metropolitana = 'Zona metropolitana Hidalgo' THEN 'HIDALGO'
        WHEN ir.area_metropolitana = 'Zona metropolitana Queretaro' THEN 'QUERETARO'
        WHEN ir.area_metropolitana = 'Zona metropolitana Guanajuato' THEN 'GUANAJUATO'
        ELSE 'Otros'
    END as ir_area_metropolitana,
    SUM(ir.spend) as ir_spend
FROM `sellers-main-prod.bi_mx.resumen_inversiones_regiones_mexico` as ir
GROUP BY 1, 2
ORDER BY 1 DESC, 2 ASC
"""


def main() -> None:
    log.info("Trayendo marketing MX de sellers-main-prod.bi_mx.resumen_inversiones_regiones_mexico (billing=%s) ...",
             BILLING_PROJECT)
    df = run_query(QUERY, label="marketing_mx_raw")
    log.info("Total filas: %d", len(df))
    if not df.empty:
        log.info("Rango mes: %s → %s", df["ir_mes_inversion"].min(), df["ir_mes_inversion"].max())
        log.info("Regiones únicas: %s", sorted(df["ir_area_metropolitana"].unique().tolist()))
        log.info("Spend total USD: %.1f", df["ir_spend"].sum())

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PATH, index=False)
    log.info("Escrito → %s (%.1f KB)", OUT_PATH, OUT_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
