"""Trae el raw de finance_apartment_tracker_mx (mismo query del Excel `APT MEX MM`)
y lo guarda como parquet en data/raw_apartment_mx.parquet.

Añade la columna `region` (ciudad) al SELECT — es la única diferencia funcional
respecto al Excel.

Uso:
    make raw
"""

from __future__ import annotations

import logging
from pathlib import Path

from scripts._bq import BILLING_PROJECT, TABLE_APT_MX, run_query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "data" / "raw_apartment_mx.parquet"

QUERY = f"""
select
    nid,
    region,
    fecha_facturacion_venta,
    date_of_purchase_promise_financial,
    date_of_purchase_real_deed_financial,
    date_psa_buyers,
    date_of_sell_real_deed_financial,
    prestamo_compraventa,
    sell_price_financial,
    hc100_financial,
    sell_price_MM_sin_HC100_financial,
    buy_price_financial,
    valor_obra_pipefy_mejoras,
    valor_obra_pipefy_pintura,
    valor_obra_pipefy_reparaciones,
    valor_obra_pipefy_total,
    alistamiento_accounting,
    remodeling_accounting,
    remo_total_accounting,
    tramites_sellers_poder_ue,
    tramites_sellers_costos_notariales_ue,
    tramites_sellers_clg_ue,
    tramites_sellers_cancelacion_hipoteca_ue,
    tramites_sellers_certificaciones_ue,
    tramites_sellers_otros_gastos_accounting,
    tramites_sellers_total_ue,
    tramites_buyers_apertura_expediente_ue,
    tramites_buyers_avaluo_ue,
    tramites_buyers_isr_ue,
    tramites_buyers_inscripcion_credito_ue,
    tramites_buyers_certificaciones_ue,
    tramites_buyers_otros_gastos_accounting,
    tramites_buyers_total_ue,
    holding_administracion_ue,
    holding_limpieza_ue,
    holding_predial_ue,
    holding_servicios_publicos_ue,
    holding_total_ue,
    alarmas_accounting,
    comisiones_sellers_externa_accounting,
    comisiones_buyers_externa_accounting,
    comisiones_sellers_interna_ue,
    comisiones_buyers_interna,
    comisiones_buyers_interna_ue,
    financing_costs as financing_costs_,
    financial_entity_type,
    if(prestamo_compraventa = "compra-venta", 0,
       greatest(((sell_price_MM_sin_HC100_financial - buy_price_financial) / 1.16 * 0.16), 0)
    ) as IVA,
    greatest(coalesce(comisiones_buyers_interna_infra, 0),
             coalesce(comisiones_buyers_interna_ue, 0)) as COM_BUYERS,
    extract(month from fecha_facturacion_venta) as MES,
    extract(year from fecha_facturacion_venta) as ANO,
    marketing_model,
    economic_holding_period_days,
    financing,
    tramites_buyers_costos_notariales_accounting,
    valor_kit_post_remo,
    valor_seguridad,
    total_alarmas_model,
    comisiones_buyers_externa_ue,
    tramites_sellers_poder_accounting,
    tramites_sellers_costos_notariales_accounting,
    tramites_sellers_clg_accounting,
    tramites_sellers_cancelacion_hipoteca_accounting,
    tramites_sellers_certificaciones_accounting,
    tramites_buyers_apertura_expediente_accounting,
    tramites_buyers_avaluo_accounting,
    tramites_buyers_isr_accounting,
    tramites_buyers_inscripcion_credito_accounting,
    holding_limpieza_ACCOUNTING,
    holding_administracion_accounting,
    holding_servicios_publicos_accounting,
    holding_predial_accounting,
    comisiones_sellers_interna,
    first_upload_to_web,
    case
        when fecha_aportacion is null then null
        else
            purchase_price * (
                case
                    when financing in ('IDB') then 0.85
                    when financing in ('BBVA') and fecha_aportacion <  '2025-07-01' then 0.80
                    when financing in ('BBVA') and fecha_aportacion >= '2025-07-01' then 0.88
                    else 0.80
                end
            )
    end as valor_financiado,
    end_remo,
    valor_a_pagar_alistamiento
from `{TABLE_APT_MX}`
"""


def main() -> None:
    log.info("Trayendo raw de %s (billing=%s) ...", TABLE_APT_MX, BILLING_PROJECT)
    df = run_query(QUERY, label="apartment_mx_raw")
    log.info("Total filas: %d", len(df))
    log.info("Rango fecha_facturacion_venta: %s → %s",
             df["fecha_facturacion_venta"].min(), df["fecha_facturacion_venta"].max())
    log.info("Regiones únicas: %d", df["region"].nunique(dropna=True))
    log.info("Filas con region NULL: %d", df["region"].isna().sum())

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PATH, index=False)
    log.info("Escrito → %s (%.1f MB)", OUT_PATH, OUT_PATH.stat().st_size / 1024**2)


if __name__ == "__main__":
    main()
