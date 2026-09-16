"""Motor de agregación P&L por (mes_venta, region).

Espeja la estructura de las hojas `P&L MM MEX ACC` y `P&L MM MEX SINTETICO`
del Excel de referencia:

    /Users/kamimunozacosta/Downloads/P&L Apartment Análisis.xlsx

- Vista ACC       → usa columnas *_accounting
- Vista Sintético → usa *_ue con fallback a *_accounting fila por fila
                    (fila = un NID, no un mes). Además:
                    · Remodeling se detalla en Mejoras/Pinturas/Reparaciones (valor_obra_pipefy_*)
                    · Alistamiento se toma de *_accounting (no tiene _ue)
                    · Incluye Kit Post Remo

Todos los valores en MXN.

Regla especial · Remodeling en vista Sintético
─────────────────────────────────────────────────
La fila **Remodeling** en la vista Sintético (y sus 5 sublíneas: Mejoras,
Pinturas, Reparaciones, Alistamiento, Kit Post Remo) se agrupa por
`end_remo` (mes en que se cerró la remodelación) en vez de por
`fecha_facturacion_venta`. Esto refleja el costo "unit económico" de la
cohorte que se remodeló en el mes, no de la que se facturó en el mes.

NIDs sin `end_remo` → fallback a `fecha_facturacion_venta` (con warning).

Consecuencia: la Remodeling Sintético NO es reconciliable línea por línea
contra la Remodeling ACC (que sigue agrupada por mes de facturación).
Managerial (ACC) queda intacta.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

# Sublíneas de Remo que forman la vista Sintético (se re-agrupan por end_remo).
# Alistamiento se comparte con la vista ACC pero en Sintético también se agrupa
# por end_remo — el motor recomputa la fila `rem_alistamiento` según la vista.
REMO_SINTETICO_KEYS = (
    "rem_mejoras",
    "rem_pinturas",
    "rem_reparaciones",
    "rem_alistamiento",
    "rem_kit_post",
    "remodeling",
)

# Sublíneas de Transaction Costs · sellers (TC compra) que forman la vista
# Sintético. Se re-agrupan por `date_of_purchase_real_deed_financial`
# (mes de escritura de compra Habi) en vez de `fecha_facturacion_venta`
# para reflejar el costo "unit económico" de la cohorte que Habi escrituró
# de compra en el mes.
# NIDs sin escritura de compra pero facturados → fallback a mes de facturación.
# NIDs sin escritura y sin facturación → excluidos.
TC_SELLERS_SINT_KEYS = (
    "txs_poder",
    "txs_notariales",
    "txs_clg",
    "txs_cancelacion",
    "txs_certificaciones",
    "txs_otros",
    "tramites_sellers",
)

# Sublíneas de Transaction Costs · buyers (TC venta) que forman la vista
# Sintético. Se re-agrupan por `date_of_sell_real_deed_financial`
# (mes de escritura de venta Habi) en vez de `fecha_facturacion_venta`
# para reflejar el costo "unit económico" de la cohorte que Habi escrituró
# de venta en el mes.
# NIDs sin escritura de venta pero facturados → fallback a mes de facturación.
# NIDs sin escritura y sin facturación → excluidos.
TC_BUYERS_SINT_KEYS = (
    "txb_apertura",
    "txb_avaluo",
    "txb_isr",
    "txb_inscripcion",
    "txb_notariales",
    "txb_otros",
    "tramites_buyers",
)

# Sublíneas de Commercial · buyers (comisiones que se devengan cuando se
# compromete la venta al comprador final). Se re-agrupan por `date_psa_buyers`
# (promesa de venta) sobre el UNIVERSO COMPLETO del tracker en vista Sintético.
# NIDs sin promesa venta pero facturados → fallback a mes de facturación.
# NIDs sin promesa venta y sin facturación → excluidos.
COMMERCIAL_SINT_BUYERS_KEYS = (
    "com_ext_buyers",
    "com_int_buyers",
)

# Sublíneas de Commercial · sellers (comisiones que se devengan cuando se
# compromete la compra al owner). Se re-agrupan por
# `date_of_purchase_promise_financial` (promesa de compra) sobre el UNIVERSO
# COMPLETO del tracker en vista Sintético.
# NIDs sin promesa compra pero facturados → fallback a mes de facturación.
# NIDs sin promesa compra y sin facturación → excluidos.
COMMERCIAL_SINT_SELLERS_KEYS = (
    "com_ext_sellers",
    "com_int_sellers",
)

# Rollups de Commercial que se recomputan a partir de las 4 subcuentas
# post re-agrupación. `external_commissions` = ext_buyers + ext_sellers,
# `internal_commissions` = int_buyers + int_sellers, `commercial` = suma total.
COMMERCIAL_SINT_ROLLUP_KEYS = (
    "external_commissions",
    "internal_commissions",
    "commercial",
)

# Umbral de filas totales para colapsar en 'Otros'
MIN_ROWS_PER_REGION = 50
# Los NIDs con region NULL se asignan a EDO MEX (decisión operativa de Kamila,
# 2026-07-21: la mayoría son EDO MEX sin etiquetar).
DEFAULT_REGION_FOR_NULLS = "EDO MEX"
LABEL_OTROS = "Otros"
# Regiones que SIEMPRE se muestran individualmente, sin importar si están debajo
# del umbral MIN_ROWS_PER_REGION. Decisión operativa de Kamila (2026-07-27).
WHITELIST_REGIONS = {"GUANAJUATO"}
# Fusión CDMX → EDO MEX. Decisión operativa de Kamila (2026-08-20): la mayoría
# del OpEx local se contabiliza en CDMX pero aplica también a EDO MEX; ambas
# regiones se tratan como una sola bajo el rótulo EDO MEX en todo el pipeline
# (tracker, payroll, rent). Marketing ya mapea "Valle de México" → EDO MEX en
# el query de origen.
REGION_ALIASES = {"CDMX": "EDO MEX"}


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _num(series: pd.Series) -> pd.Series:
    """Convierte a float y trata NaN como 0 para sumas."""
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _coalesce_ue_acc(df: pd.DataFrame, ue_col: str, acc_col: str) -> pd.Series:
    """Vista Sintético: usa _ue si no es NaN, si no _accounting. Fila por fila."""
    ue = pd.to_numeric(df[ue_col], errors="coerce")
    acc = pd.to_numeric(df[acc_col], errors="coerce")
    return ue.where(ue.notna(), acc).fillna(0.0)


def _apply_region_aliases(region: pd.Series) -> pd.Series:
    """Fusión de regiones (p.ej. CDMX → EDO MEX). Se aplica antes de contar."""
    return region.replace(REGION_ALIASES)


def _normalize_region(region: pd.Series, counts: pd.Series) -> pd.Series:
    """NaN → EDO MEX (default). Regiones con <MIN_ROWS_PER_REGION → 'Otros',
    salvo las que estén en WHITELIST_REGIONS (se muestran siempre individuales).
    """
    below = [r for r in counts[counts < MIN_ROWS_PER_REGION].index.tolist()
             if r not in WHITELIST_REGIONS]
    out = region.where(region.notna(), DEFAULT_REGION_FOR_NULLS)
    out = out.where(~out.isin(below), LABEL_OTROS)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# preparación
# ─────────────────────────────────────────────────────────────────────────────

def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Añade columnas de agrupación al universo COMPLETO del apartment_tracker.

    - `mes` (YYYY-MM string) derivado de `fecha_facturacion_venta`. `NaT` para
      NIDs no facturados.
    - `facturado` (bool) — True si `fecha_facturacion_venta` no es NULL. Todas
      las líneas Managerial (ACC) y las no-Remo del Sintético (ingresos, TC,
      Holding, Financing, Commercial) filtran por `facturado==True`. La línea
      Remo Sintético opera sobre el universo completo (facturados y no).
    - `region_norm` con REGION_ALIASES (CDMX→EDO MEX), fallback EDO MEX para
      region=NULL, y colapso a 'Otros' bajo MIN_ROWS_PER_REGION (aplicado sobre
      el universo COMPLETO para que Remo Sint respete el mismo esquema regional
      que las demás líneas).
    - `mes_end_remo` (YYYY-MM string) derivado de `end_remo`. NIDs sin end_remo
      Y sin fecha_facturacion_venta caen a NaT (se excluyen del Remo Sint —
      no hay mes al cual asignar el costo). NIDs sin end_remo pero facturados
      caen a `mes` (fallback existente).

    Cambio 2026-09-15 vs versión previa: `prepare()` ya NO filtra NIDs sin
    facturación. El filtro se aplica ahora al nivel de `aggregate()`/vista,
    para que la vista Sintético Remo pueda operar sobre el universo completo.
    """
    out = df.copy()
    fecha = pd.to_datetime(out["fecha_facturacion_venta"], errors="coerce")
    out["facturado"] = fecha.notna()
    out["mes"] = fecha.dt.to_period("M").astype(str)  # 'NaT' string para no facturados
    n_no_fact = int((~out["facturado"]).sum())
    if n_no_fact > 0:
        log.info(
            "prepare(): %d NIDs sin fecha_facturacion_venta se mantienen (universo Remo Sintético). "
            "Managerial (ACC) y no-Remo del Sintético filtran a %d facturados.",
            n_no_fact, int(out["facturado"].sum()),
        )

    region_aliased = _apply_region_aliases(out["region"])
    counts_by_region = region_aliased.value_counts(dropna=False)
    out["region_norm"] = _normalize_region(region_aliased, counts_by_region)

    if "end_remo" in out.columns:
        end_remo_dt = pd.to_datetime(out["end_remo"], errors="coerce")
        end_remo_str = end_remo_dt.dt.to_period("M").astype(str)
        # Fallback: NIDs sin end_remo pero facturados → mes de facturación.
        # NIDs sin end_remo Y sin facturación → NaT (no cuentan en Remo Sint).
        fallback_mask = end_remo_dt.isna() & out["facturado"]
        n_fallback = int(fallback_mask.sum())
        n_sin_ambos = int((end_remo_dt.isna() & ~out["facturado"]).sum())
        if n_fallback > 0:
            log.warning(
                "Remo Sintético: %d NIDs facturados sin end_remo → fallback a fecha_facturacion_venta.",
                n_fallback,
            )
        if n_sin_ambos > 0:
            log.info(
                "Remo Sintético: %d NIDs sin end_remo y sin facturación → excluidos (sin mes al cual asignar).",
                n_sin_ambos,
            )
        out["mes_end_remo"] = end_remo_str.where(~fallback_mask, out["mes"])
    else:
        log.warning("end_remo no está en el raw — Remo Sintético caerá 100%% al fallback (fecha_facturacion_venta).")
        out["mes_end_remo"] = out["mes"]

    # Mes de escritura de compra Habi — bucket para TC Sellers en vista Sintético.
    # Fallback: NIDs sin deed_compra pero facturados → mes de facturación.
    # NIDs sin deed_compra Y sin facturación → NaT (se excluyen del bucket).
    if "date_of_purchase_real_deed_financial" in out.columns:
        deed_c_dt = pd.to_datetime(out["date_of_purchase_real_deed_financial"], errors="coerce")
        deed_c_str = deed_c_dt.dt.to_period("M").astype(str)
        fallback_c_mask = deed_c_dt.isna() & out["facturado"]
        n_fb_c = int(fallback_c_mask.sum())
        n_sin_c = int((deed_c_dt.isna() & ~out["facturado"]).sum())
        if n_fb_c > 0:
            log.warning(
                "TC Sellers Sintético: %d NIDs facturados sin date_of_purchase_real_deed_financial → fallback a fecha_facturacion_venta.",
                n_fb_c,
            )
        if n_sin_c > 0:
            log.info(
                "TC Sellers Sintético: %d NIDs sin escritura de compra y sin facturación → excluidos.",
                n_sin_c,
            )
        out["mes_deed_compra"] = deed_c_str.where(~fallback_c_mask, out["mes"])
    else:
        log.warning("date_of_purchase_real_deed_financial no está en el raw — TC Sellers Sint caerá 100%% al fallback.")
        out["mes_deed_compra"] = out["mes"]

    # Mes de escritura de venta Habi — bucket para TC Buyers en vista Sintético.
    # Fallback: NIDs sin deed_venta pero facturados → mes de facturación.
    # NIDs sin deed_venta Y sin facturación → NaT (se excluyen del bucket).
    if "date_of_sell_real_deed_financial" in out.columns:
        deed_v_dt = pd.to_datetime(out["date_of_sell_real_deed_financial"], errors="coerce")
        deed_v_str = deed_v_dt.dt.to_period("M").astype(str)
        fallback_v_mask = deed_v_dt.isna() & out["facturado"]
        n_fb_v = int(fallback_v_mask.sum())
        n_sin_v = int((deed_v_dt.isna() & ~out["facturado"]).sum())
        if n_fb_v > 0:
            log.warning(
                "TC Buyers Sintético: %d NIDs facturados sin date_of_sell_real_deed_financial → fallback a fecha_facturacion_venta.",
                n_fb_v,
            )
        if n_sin_v > 0:
            log.info(
                "TC Buyers Sintético: %d NIDs sin escritura de venta y sin facturación → excluidos.",
                n_sin_v,
            )
        out["mes_deed_venta"] = deed_v_str.where(~fallback_v_mask, out["mes"])
    else:
        log.warning("date_of_sell_real_deed_financial no está en el raw — TC Buyers Sint caerá 100%% al fallback.")
        out["mes_deed_venta"] = out["mes"]

    # Mes de promesa de venta al comprador — bucket para Commercial Buyers
    # en vista Sintético. Fallback: NIDs sin promesa venta pero facturados →
    # mes de facturación. NIDs sin promesa Y sin facturación → NaT (excluidos).
    if "date_psa_buyers" in out.columns:
        prom_b_dt = pd.to_datetime(out["date_psa_buyers"], errors="coerce")
        prom_b_str = prom_b_dt.dt.to_period("M").astype(str)
        fallback_pb_mask = prom_b_dt.isna() & out["facturado"]
        n_fb_pb = int(fallback_pb_mask.sum())
        n_sin_pb = int((prom_b_dt.isna() & ~out["facturado"]).sum())
        if n_fb_pb > 0:
            log.warning(
                "Commercial Buyers Sintético: %d NIDs facturados sin date_psa_buyers → fallback a fecha_facturacion_venta.",
                n_fb_pb,
            )
        if n_sin_pb > 0:
            log.info(
                "Commercial Buyers Sintético: %d NIDs sin promesa venta y sin facturación → excluidos.",
                n_sin_pb,
            )
        out["mes_promesa_buyers"] = prom_b_str.where(~fallback_pb_mask, out["mes"])
    else:
        log.warning("date_psa_buyers no está en el raw — Commercial Buyers Sint caerá 100%% al fallback.")
        out["mes_promesa_buyers"] = out["mes"]

    # Mes de promesa de compra al owner — bucket para Commercial Sellers en
    # vista Sintético. Fallback igual que promesa venta.
    if "date_of_purchase_promise_financial" in out.columns:
        prom_s_dt = pd.to_datetime(out["date_of_purchase_promise_financial"], errors="coerce")
        prom_s_str = prom_s_dt.dt.to_period("M").astype(str)
        fallback_ps_mask = prom_s_dt.isna() & out["facturado"]
        n_fb_ps = int(fallback_ps_mask.sum())
        n_sin_ps = int((prom_s_dt.isna() & ~out["facturado"]).sum())
        if n_fb_ps > 0:
            log.warning(
                "Commercial Sellers Sintético: %d NIDs facturados sin date_of_purchase_promise_financial → fallback a fecha_facturacion_venta.",
                n_fb_ps,
            )
        if n_sin_ps > 0:
            log.info(
                "Commercial Sellers Sintético: %d NIDs sin promesa compra y sin facturación → excluidos.",
                n_sin_ps,
            )
        out["mes_promesa_sellers"] = prom_s_str.where(~fallback_ps_mask, out["mes"])
    else:
        log.warning("date_of_purchase_promise_financial no está en el raw — Commercial Sellers Sint caerá 100%% al fallback.")
        out["mes_promesa_sellers"] = out["mes"]

    return out


# ─────────────────────────────────────────────────────────────────────────────
# líneas del P&L
# ─────────────────────────────────────────────────────────────────────────────

# Estructura declarativa. Cada línea define:
#   key            : id corto usado en el JSON
#   label          : nombre visible en el frontend
#   parent         : grupo padre (para jerarquía)
#   type           : 'kpi' | 'subcuenta' | 'grupo' | 'rubro' | 'total'
#   sign           : 'income' (positivo esperado), 'cost' (negativo), 'net'
#
# Los cálculos numéricos van más abajo en `_calc_lines_for_group`.

PNL_STRUCTURE = [
    # ── ingresos ──
    # Orden waterfall: (+) GMV sin HC100 + (+) Fee HC100 = (=) GMV Precio Venta Habi.
    # El GMV Habi (con fee incluido) es la base de todos los ratios/unit costs
    # y el input al Gross Profit.
    {"key": "invoiced_sales", "label": "# Invoiced Sales", "parent": None, "type": "kpi", "sign": "count"},
    {"key": "gmv_sin_hc100", "label": "(+) GMV Selling Price (sin HC100)", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "fee_hc100", "label": "(+) Fee HC100", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "fee_exclusividad", "label": "(+) Fee Exclusividad Venta", "parent": None, "type": "kpi", "sign": "income",
     "note": "Ingreso por exclusividad venta (cuenta 41010118, subsidiaria Tu Habi). Fuente: auxiliar_contable_mx, joineado por NID. Suma al GMV Habi para el cálculo del Gross Profit."},
    {"key": "gmv_habi", "label": "(=) GMV Precio de Venta Habi", "parent": None, "type": "total", "sign": "income"},
    {"key": "purchase_price", "label": "(-) GMV Purchase Price", "parent": None, "type": "kpi", "sign": "cost"},
    {"key": "gross_profit", "label": "(=) Gross Profit", "parent": None, "type": "total", "sign": "net"},
    {"key": "iva", "label": "(-) IVA", "parent": None, "type": "kpi", "sign": "cost"},
    {"key": "gp_sin_iva", "label": "(=) Gross Profit sin IVA", "parent": None, "type": "total", "sign": "net"},

    # ── remodeling ──
    {"key": "rem_mejoras", "label": "Mejoras", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "sintetico"},
    {"key": "rem_pinturas", "label": "Pinturas", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "sintetico"},
    {"key": "rem_reparaciones", "label": "Reparaciones", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "sintetico"},
    {"key": "rem_remodeling_acc", "label": "Remodeling Accounting", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "acc"},
    {"key": "rem_alistamiento", "label": "Alistamiento", "parent": "remodeling", "type": "subcuenta", "sign": "cost"},
    {"key": "rem_kit_post", "label": "Kit Post Remo", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "sintetico"},
    {"key": "remodeling", "label": "Remodeling Costs", "parent": None, "type": "rubro", "sign": "cost",
     "note": "Vista Sintético: se agrupa por mes en que terminó la remodelación (end_remo), no por mes de facturación. El drill muestra los NIDs remodelados ese mes. Vista Managerial (ACC): se agrupa por mes de facturación."},

    # ── transaction costs · sellers ──
    {"key": "txs_poder", "label": "Poder", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_notariales", "label": "Gastos Notariales", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_clg", "label": "Clg", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_cancelacion", "label": "Cancelación de hipoteca", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_certificaciones", "label": "Certificaciones", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_otros", "label": "Otros gastos en la venta", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "tramites_sellers", "label": "Trámites Sellers", "parent": "transaction_costs", "type": "grupo", "sign": "cost"},

    # ── transaction costs · buyers ──
    {"key": "txb_apertura", "label": "Apertura de expediente", "parent": "tramites_buyers", "type": "subcuenta", "sign": "cost"},
    {"key": "txb_avaluo", "label": "Avalúos", "parent": "tramites_buyers", "type": "subcuenta", "sign": "cost"},
    {"key": "txb_isr", "label": "ISR", "parent": "tramites_buyers", "type": "subcuenta", "sign": "cost"},
    {"key": "txb_inscripcion", "label": "Inscripción de crédito", "parent": "tramites_buyers", "type": "subcuenta", "sign": "cost"},
    {"key": "txb_notariales", "label": "Gastos Notariales", "parent": "tramites_buyers", "type": "subcuenta", "sign": "cost"},
    {"key": "txb_otros", "label": "Otros gastos en la compra", "parent": "tramites_buyers", "type": "subcuenta", "sign": "cost"},
    {"key": "tramites_buyers", "label": "Trámites Buyers", "parent": "transaction_costs", "type": "grupo", "sign": "cost"},

    {"key": "transaction_costs", "label": "Transaction Costs", "parent": None, "type": "rubro", "sign": "cost"},

    # ── holding costs ──
    {"key": "hol_admin", "label": "Property Management Fees", "parent": "holding", "type": "subcuenta", "sign": "cost"},
    {"key": "hol_limpieza", "label": "Cleaning Fee", "parent": "holding", "type": "subcuenta", "sign": "cost"},
    {"key": "hol_utilities", "label": "Utilities", "parent": "holding", "type": "subcuenta", "sign": "cost"},
    {"key": "hol_predial", "label": "Estate Tax", "parent": "holding", "type": "subcuenta", "sign": "cost"},
    {"key": "holding", "label": "Holding Costs", "parent": None, "type": "rubro", "sign": "cost"},

    # ── seguridad y recuperación ──
    {"key": "seg_alarmas", "label": "Costo Alarmas", "parent": "seguridad", "type": "subcuenta", "sign": "cost"},
    {"key": "seguridad", "label": "(-) Costo Seguridad y recuperación", "parent": None, "type": "rubro", "sign": "cost"},

    # ── commercial · external ──
    {"key": "com_ext_buyers", "label": "Comisiones externas buyers", "parent": "external_commissions", "type": "subcuenta", "sign": "cost"},
    {"key": "com_ext_sellers", "label": "Comisiones externas sellers", "parent": "external_commissions", "type": "subcuenta", "sign": "cost"},
    {"key": "external_commissions", "label": "External Commissions", "parent": "commercial", "type": "grupo", "sign": "cost"},

    # ── commercial · internal ──
    {"key": "com_int_buyers", "label": "Internal buyers infra", "parent": "internal_commissions", "type": "subcuenta", "sign": "cost"},
    {"key": "com_int_sellers", "label": "Internal sellers", "parent": "internal_commissions", "type": "subcuenta", "sign": "cost"},
    {"key": "internal_commissions", "label": "Internal Commissions", "parent": "commercial", "type": "grupo", "sign": "cost"},

    {"key": "commercial", "label": "Commercial Costs", "parent": None, "type": "rubro", "sign": "cost"},

    # ── totales ──
    {"key": "direct_costs", "label": "(-) Direct Costs", "parent": None, "type": "rubro", "sign": "cost"},
    {"key": "unlevered_profit", "label": "(=) Unlevered Profit", "parent": None, "type": "total", "sign": "net"},
    {"key": "financing_costs", "label": "(-) Financing Costs", "parent": None, "type": "kpi", "sign": "cost"},
    {"key": "contribution_margin", "label": "(=) Contribution Margin", "parent": None, "type": "total", "sign": "net"},
]


# ─────────────────────────────────────────────────────────────────────────────
# estructura del waterfall CONSOLIDADO (tab MM + Inmo)
# ─────────────────────────────────────────────────────────────────────────────
# Suma MM y Inmo por región×mes y aplica Local OpEx UNA sola vez al final
# (payroll/rent/marketing sirven a ambas líneas, no solo a MM).
# Los rubros MM/Inmo son filas separadas para lectura del waterfall
# (Contribution MM + Contribution Inmo = Contribution Total).

PNL_STRUCTURE_CONSOLIDATED = [
    # ── conteos ──
    {"key": "cons_props_mm", "label": "# Properties MM", "parent": "cons_props_total", "type": "kpi", "sign": "count"},
    {"key": "cons_props_inmo", "label": "# Properties Inmo", "parent": "cons_props_total", "type": "kpi", "sign": "count"},
    {"key": "cons_props_total", "label": "# Properties Total", "parent": None, "type": "total", "sign": "count"},

    # ── revenue (GMV) ──
    {"key": "cons_gmv_mm", "label": "GMV MM", "parent": "cons_gmv_total", "type": "kpi", "sign": "income"},
    {"key": "cons_gmv_inmo", "label": "GMV Inmo (Inmo 100 + Tradicional)", "parent": "cons_gmv_total", "type": "kpi", "sign": "income"},
    {"key": "cons_gmv_total", "label": "(=) GMV Consolidado", "parent": None, "type": "total", "sign": "income"},

    # ── contribution por línea ──
    {"key": "cons_cm_mm", "label": "Contribution Margin MM", "parent": "cons_cm_total", "type": "kpi", "sign": "net"},
    {"key": "cons_cm_inmo", "label": "Contribution Margin Inmo", "parent": "cons_cm_total", "type": "kpi", "sign": "net"},
    {"key": "cons_cm_total", "label": "(=) Contribution Margin Total", "parent": None, "type": "total", "sign": "net"},

    # ── local OpEx (mismo bloque que antes, ahora aquí en el consolidado) ──
    {"key": "payroll_local", "label": "Payroll local", "parent": "local_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "headcount_local", "label": "Headcount local", "parent": "payroll_local", "type": "informativo", "sign": "count", "extern": True,
     "note": "HC atribuible a la ciudad al cierre del mes (snapshot Aline/Lis). Informativo — no afecta local_opex."},
    {"key": "rent_atribuible", "label": "Rent (atribuible por ciudad)", "parent": "rent", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "rent_wework_nl_jal", "label": "Rent NL + JAL (WeWork · no separable)", "parent": "rent", "type": "subcuenta", "sign": "cost", "extern": True, "only_total": True,
     "note": "WeWork agrupa las oficinas de Monterrey (NL) y Guadalajara (JAL) bajo un solo c_tercero en OPEX. El grano de la fuente (proveedor × mes × país) no permite separar el gasto entre las dos ciudades — se muestra combinado solo en el consolidado."},
    {"key": "rent_nacional", "label": "Rent Nacional / no atribuible", "parent": "rent", "type": "subcuenta", "sign": "cost", "extern": True, "only_total": True,
     "note": "Proveedores de servicios sin ciudad atribuible (telecoms, papelería, terceros nacionales). Vendors principales: AT&T Comunicaciones Digitales, México Red de Telecomunicaciones, A de A México, Manuel Gutierrez González, Du Papier, Daniel Sebastián Ávila Arroyo. Representa ~12% del Rent MX YTD según el mapeo de Danibot."},
    {"key": "rent", "label": "Rent", "parent": "local_opex", "type": "grupo", "sign": "cost", "extern": True},
    {"key": "marketing_city", "label": "Marketing (ciudad)", "parent": "local_opex", "type": "subcuenta", "sign": "cost", "extern": True,
     "note": "Marketing digital atribuido por área metropolitana (Facebook, Google, etc.). Sirve a MM y a Inmo — por eso se resta solo en el consolidado."},
    {"key": "corp_opex_sales_ops", "label": "Sales & Ops", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_tech", "label": "Tech", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_prof_fees", "label": "Professional Fees", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_courier", "label": "Courier & Transportation", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True,
     "note": "FACTURIFY SA DE CV (que factura Uber Business consolidado MX) se prorratea manualmente por ciudad: JALISCO 30% · NUEVO LEON 32.7% · EDO MEX 25.6% · GUANAJUATO 4.2% · QUERETARO 7.5%. Share basado en gastos de julio 2026. TEMPORAL — se reemplazará por share real cuando se consiga el histórico de viajes por ciudad."},
    {"key": "corp_opex_travel", "label": "Travel Expenses", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_empl_rel", "label": "Employee Relations", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_other", "label": "Other - Local Expenses", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_nacional", "label": "OpEx Corp Nacional / no atribuible", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True, "only_total": True,
     "note": "Gasto corporativo etiquetado como GLOBAL MEX o MÉXICO en bet_data_p2 (sin ciudad atribuible). Se muestra solo en el Total, no en columnas de ciudad."},
    {"key": "corp_opex", "label": "OpEx Corporativo", "parent": "local_opex", "type": "grupo", "sign": "cost", "extern": True,
     "note": "OpEx corporativo (Sales & Ops, Tech, Prof Fees, Courier, Travel, Employee Relations, Other Local) de bet_data_p2. Excluye Payroll, Marketing y Rent que ya están en Local OpEx. Excluye Merbos (entidad distinta)."},
    {"key": "local_opex", "label": "(-) Local OpEx", "parent": None, "type": "rubro", "sign": "cost", "extern": True,
     "note": "Payroll + Rent + Marketing + Corp OpEx city-level. Sirve a MM y a Inmo simultáneamente, por eso se aplica UNA sola vez sobre la Contribution Total (no sobre MM o Inmo por separado)."},
    {"key": "net_city_contribution", "label": "(=) Net City Contribution", "parent": None, "type": "total", "sign": "net", "extern": True},
]


# ─────────────────────────────────────────────────────────────────────────────
# cálculo por vista
# ─────────────────────────────────────────────────────────────────────────────

def _line_values(df: pd.DataFrame, vista: str) -> dict[str, pd.Series]:
    """Devuelve dict {key → serie indexada por df.index} con el valor por-fila
    de cada línea (antes de agrupar por mes/region).

    `vista` ∈ {'acc', 'sintetico'}.
    """
    is_sint = vista == "sintetico"

    def pick(ue_col: str | None, acc_col: str) -> pd.Series:
        """Sintético: coalesce(_ue, _accounting). ACC: solo _accounting."""
        if is_sint and ue_col and ue_col in df.columns:
            return _coalesce_ue_acc(df, ue_col, acc_col)
        return _num(df[acc_col])

    lines: dict[str, pd.Series] = {}

    # ── ingresos ──
    #  Nota: hc100_financial en el tracker es un flag string ('Si'/'No'), no el monto.
    #  El monto del fee = sell_price - sell_price_MM_sin_HC100
    lines["invoiced_sales"] = pd.Series(1, index=df.index, dtype=float)  # count
    lines["gmv_sin_hc100"] = _num(df["sell_price_MM_sin_HC100_financial"])
    sell_price = _num(df["sell_price_financial"])
    lines["fee_hc100"] = sell_price - lines["gmv_sin_hc100"]
    # Fee Exclusividad viene del auxiliar_contable (cuenta 41010118), joineado por
    # NID en refresh_data.py. Se suma al GMV Habi como un ingreso adicional que
    # cobra Habi por darle exclusividad al vendedor.
    lines["fee_exclusividad"] = _num(df["fee_income"]) if "fee_income" in df.columns else pd.Series(0.0, index=df.index)
    lines["gmv_habi"] = sell_price + lines["fee_exclusividad"]
    lines["purchase_price"] = -_num(df["buy_price_financial"])
    # Gross Profit se calcula sobre GMV Habi (con Fee HC100 + Fee Exclusividad incluidos).
    # Los units cost del dashboard también se miden sobre gmv_habi.
    lines["gross_profit"] = lines["gmv_habi"] + lines["purchase_price"]
    lines["iva"] = -_num(df["IVA"])
    lines["gp_sin_iva"] = lines["gross_profit"] + lines["iva"]

    # ── remodeling ──
    #  ACC: Remodeling Accounting + Alistamiento (sin kit)
    #  Sint: Mejoras + Pinturas + Reparaciones + Alistamiento + Kit Post Remo
    lines["rem_mejoras"] = -_num(df["valor_obra_pipefy_mejoras"])
    lines["rem_pinturas"] = -_num(df["valor_obra_pipefy_pintura"])
    lines["rem_reparaciones"] = -_num(df["valor_obra_pipefy_reparaciones"])
    lines["rem_remodeling_acc"] = -_num(df["remodeling_accounting"])
    lines["rem_alistamiento"] = -_num(df["alistamiento_accounting"])
    lines["rem_kit_post"] = -_num(df["valor_kit_post_remo"])
    if is_sint:
        lines["remodeling"] = (
            lines["rem_mejoras"] + lines["rem_pinturas"] + lines["rem_reparaciones"]
            + lines["rem_alistamiento"] + lines["rem_kit_post"]
        )
    else:
        lines["remodeling"] = lines["rem_remodeling_acc"] + lines["rem_alistamiento"]

    # ── transaction · sellers ──
    lines["txs_poder"] = -pick("tramites_sellers_poder_ue", "tramites_sellers_poder_accounting")
    lines["txs_notariales"] = -pick("tramites_sellers_costos_notariales_ue", "tramites_sellers_costos_notariales_accounting")
    lines["txs_clg"] = -pick("tramites_sellers_clg_ue", "tramites_sellers_clg_accounting")
    lines["txs_cancelacion"] = -pick("tramites_sellers_cancelacion_hipoteca_ue", "tramites_sellers_cancelacion_hipoteca_accounting")
    lines["txs_certificaciones"] = -pick("tramites_sellers_certificaciones_ue", "tramites_sellers_certificaciones_accounting")
    lines["txs_otros"] = -_num(df["tramites_sellers_otros_gastos_accounting"])
    lines["tramites_sellers"] = (
        lines["txs_poder"] + lines["txs_notariales"] + lines["txs_clg"]
        + lines["txs_cancelacion"] + lines["txs_certificaciones"] + lines["txs_otros"]
    )

    # ── transaction · buyers ──
    lines["txb_apertura"] = -pick("tramites_buyers_apertura_expediente_ue", "tramites_buyers_apertura_expediente_accounting")
    lines["txb_avaluo"] = -pick("tramites_buyers_avaluo_ue", "tramites_buyers_avaluo_accounting")
    lines["txb_isr"] = -pick("tramites_buyers_isr_ue", "tramites_buyers_isr_accounting")
    lines["txb_inscripcion"] = -pick("tramites_buyers_inscripcion_credito_ue", "tramites_buyers_inscripcion_credito_accounting")
    # notariales buyers: sólo hay accounting
    lines["txb_notariales"] = -_num(df["tramites_buyers_costos_notariales_accounting"])
    lines["txb_otros"] = -_num(df["tramites_buyers_otros_gastos_accounting"])
    lines["tramites_buyers"] = (
        lines["txb_apertura"] + lines["txb_avaluo"] + lines["txb_isr"]
        + lines["txb_inscripcion"] + lines["txb_notariales"] + lines["txb_otros"]
    )

    lines["transaction_costs"] = lines["tramites_sellers"] + lines["tramites_buyers"]

    # Nota: el Excel de referencia incluye una línea "Transaction Costs HC100"
    # como suma independiente en Direct Costs, pero las 4 columnas que usa ya
    # están dentro de Trámites Buyers → double-counting. Se omite.

    # ── holding ──
    lines["hol_admin"] = -pick("holding_administracion_ue", "holding_administracion_accounting")
    lines["hol_limpieza"] = -pick("holding_limpieza_ue", "holding_limpieza_ACCOUNTING")
    lines["hol_utilities"] = -pick("holding_servicios_publicos_ue", "holding_servicios_publicos_accounting")
    lines["hol_predial"] = -pick("holding_predial_ue", "holding_predial_accounting")
    lines["holding"] = (
        lines["hol_admin"] + lines["hol_limpieza"] + lines["hol_utilities"] + lines["hol_predial"]
    )

    # ── seguridad ──
    #  ACC usa alarmas_accounting; SINTETICO usa total_alarmas_model
    if is_sint:
        lines["seg_alarmas"] = -_num(df["total_alarmas_model"])
    else:
        lines["seg_alarmas"] = -_num(df["alarmas_accounting"])
    lines["seguridad"] = lines["seg_alarmas"]

    # ── commercial · external ──
    lines["com_ext_buyers"] = -pick("comisiones_buyers_externa_ue", "comisiones_buyers_externa_accounting")
    lines["com_ext_sellers"] = -_num(df["comisiones_sellers_externa_accounting"])
    lines["external_commissions"] = lines["com_ext_buyers"] + lines["com_ext_sellers"]

    # ── commercial · internal ──
    #  ACC: usa comisiones_buyers_interna (columna base) y comisiones_sellers_interna
    #  Sint: usa las variantes _ue
    if is_sint:
        lines["com_int_buyers"] = -_num(df["comisiones_buyers_interna_ue"])
        lines["com_int_sellers"] = -_num(df["comisiones_sellers_interna_ue"])
    else:
        lines["com_int_buyers"] = -_num(df["comisiones_buyers_interna"])
        lines["com_int_sellers"] = -_num(df["comisiones_sellers_interna"])
    lines["internal_commissions"] = lines["com_int_buyers"] + lines["com_int_sellers"]

    lines["commercial"] = lines["external_commissions"] + lines["internal_commissions"]

    # ── totales ──
    lines["direct_costs"] = (
        lines["remodeling"] + lines["transaction_costs"] + lines["holding"]
        + lines["seguridad"] + lines["commercial"]
    )
    lines["unlevered_profit"] = lines["gp_sin_iva"] + lines["direct_costs"]
    lines["financing_costs"] = -_num(df["financing_costs_"])
    lines["contribution_margin"] = lines["unlevered_profit"] + lines["financing_costs"]

    return lines


def line_values_per_nid(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Devuelve un DataFrame por-NID con columnas [nid, region, mes, mes_end_remo, <key1>, <key2>, ...].

    Cada columna key es el valor de esa línea del P&L para ese NID en esa vista.
    Se usa para el drill-down desde el frontend.

    - Vista ACC: filtra al universo FACTURADO (mismo que aggregate ACC).
    - Vista Sintético: incluye el UNIVERSO COMPLETO (facturados y no). El
      frontend drillea por `mes` para líneas no-Remo (solo van a matchear
      facturados) y por `mes_end_remo` para las 6 keys de Remo (matchean
      facturados y no facturados, según el mes en que cerró la remo).

    Nota: `mes` = mes de facturación (fecha_facturacion_venta), 'NaT' string
    para no facturados. `mes_end_remo` = mes de cierre de remodelación (con
    fallback a mes de facturación si NULL y NID facturado; 'NaT' string si
    ni end_remo ni facturación existen).
    """
    if vista == "acc":
        df_use = df_prepared.loc[df_prepared["facturado"]].copy()
    else:  # sintetico
        df_use = df_prepared
    lines = _line_values(df_use, vista)
    wide = pd.DataFrame(lines)
    wide.insert(0, "mes_promesa_sellers", df_use["mes_promesa_sellers"].values)
    wide.insert(0, "mes_promesa_buyers", df_use["mes_promesa_buyers"].values)
    wide.insert(0, "mes_deed_venta", df_use["mes_deed_venta"].values)
    wide.insert(0, "mes_deed_compra", df_use["mes_deed_compra"].values)
    wide.insert(0, "mes_end_remo", df_use["mes_end_remo"].values)
    wide.insert(0, "mes", df_use["mes"].values)
    wide.insert(0, "region", df_use["region_norm"].values)
    wide.insert(0, "nid", df_use["nid"].values)
    return wide


def _remo_sint_by_end_remo(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Re-agrega las 6 keys de Remo Sintético por (region, mes_end_remo) sobre
    el UNIVERSO COMPLETO del tracker (facturados y no facturados).

    Devuelve long DF con columnas [region, mes, key, valor] donde `mes` es el
    mes de `end_remo` (o fallback fecha_facturacion_venta si NULL y NID facturado).
    NIDs sin end_remo y sin facturación quedan con mes_end_remo=NaT → excluidos
    del groupby.

    También produce la fila Total (todas las regiones sumadas) para cada mes.
    """
    lines = _line_values(df_prepared, "sintetico")
    remo_cols = list(REMO_SINTETICO_KEYS)
    wide = pd.DataFrame({k: lines[k] for k in remo_cols})
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes_end_remo"].values
    # Excluir filas con mes NaT (NIDs sin end_remo y sin facturación).
    wide = wide.loc[wide["mes"].notna() & (wide["mes"] != "NaT")].copy()

    by_region = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    total = wide.drop(columns=["region"]).groupby("mes", as_index=False).sum(numeric_only=True)
    total["region"] = "Total"

    out = pd.concat([by_region, total], ignore_index=True)
    return out.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")


def _remo_sint_nid_count_by_end_remo(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Cuenta NIDs con `end_remo` en cada (region, mes_end_remo) sobre el
    UNIVERSO COMPLETO del tracker (facturados y no facturados).

    Devuelve long DF con key='remodeling_nid_count'. Se emite tanto por región
    como para 'Total'. Se usa para el tooltip informativo "# NIDs remodelados
    en el mes: N" en el drill/hover de la fila Remo Sintético.

    Excluye filas con mes_end_remo=NaT (NIDs sin end_remo Y sin facturación
    — no hay mes al cual asignar el conteo).
    """
    df = df_prepared[["region_norm", "mes_end_remo"]].copy()
    df.columns = ["region", "mes"]
    df = df.loc[df["mes"].notna() & (df["mes"] != "NaT")].copy()
    by_region = df.groupby(["region", "mes"]).size().reset_index(name="valor")
    by_region["key"] = "remodeling_nid_count"

    total = df.groupby("mes").size().reset_index(name="valor")
    total["region"] = "Total"
    total["key"] = "remodeling_nid_count"

    return pd.concat([by_region[["region", "mes", "key", "valor"]],
                      total[["region", "mes", "key", "valor"]]], ignore_index=True)


def _tc_sellers_sint_by_deed_compra(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Re-agrega las 7 keys de TC Sellers Sintético por (region, mes_deed_compra)
    sobre el UNIVERSO COMPLETO del tracker (facturados y no facturados).

    Devuelve long DF con columnas [region, mes, key, valor] donde `mes` es el
    mes de escritura de compra Habi (con fallback fecha_facturacion_venta si
    NULL y NID facturado). NIDs sin escritura de compra y sin facturación
    quedan con mes_deed_compra=NaT → excluidos del groupby.

    También emite la fila Total (todas las regiones sumadas) por mes.
    """
    lines = _line_values(df_prepared, "sintetico")
    tcs_cols = list(TC_SELLERS_SINT_KEYS)
    wide = pd.DataFrame({k: lines[k] for k in tcs_cols})
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes_deed_compra"].values
    wide = wide.loc[wide["mes"].notna() & (wide["mes"] != "NaT")].copy()

    by_region = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    total = wide.drop(columns=["region"]).groupby("mes", as_index=False).sum(numeric_only=True)
    total["region"] = "Total"

    out = pd.concat([by_region, total], ignore_index=True)
    return out.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")


def _tc_sellers_sint_nid_count_by_deed_compra(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Cuenta NIDs con `date_of_purchase_real_deed_financial` en cada
    (region, mes_deed_compra) sobre el UNIVERSO COMPLETO del tracker.

    Se usa para el tooltip informativo "# NIDs escriturados compra este mes: N"
    en el drill/hover de la fila TC Sellers Sintético.

    Excluye filas con mes_deed_compra=NaT (NIDs sin escritura y sin facturación).
    """
    df = df_prepared[["region_norm", "mes_deed_compra"]].copy()
    df.columns = ["region", "mes"]
    df = df.loc[df["mes"].notna() & (df["mes"] != "NaT")].copy()
    by_region = df.groupby(["region", "mes"]).size().reset_index(name="valor")
    by_region["key"] = "tc_sellers_nid_count"

    total = df.groupby("mes").size().reset_index(name="valor")
    total["region"] = "Total"
    total["key"] = "tc_sellers_nid_count"

    return pd.concat([by_region[["region", "mes", "key", "valor"]],
                      total[["region", "mes", "key", "valor"]]], ignore_index=True)


def _tc_buyers_sint_by_deed_venta(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Re-agrega las 7 keys de TC Buyers Sintético por (region, mes_deed_venta)
    sobre el UNIVERSO COMPLETO del tracker (facturados y no facturados).

    Devuelve long DF con columnas [region, mes, key, valor] donde `mes` es el
    mes de escritura de venta Habi (con fallback fecha_facturacion_venta si
    NULL y NID facturado). NIDs sin escritura de venta y sin facturación
    quedan con mes_deed_venta=NaT → excluidos del groupby.

    También emite la fila Total (todas las regiones sumadas) por mes.
    """
    lines = _line_values(df_prepared, "sintetico")
    tcb_cols = list(TC_BUYERS_SINT_KEYS)
    wide = pd.DataFrame({k: lines[k] for k in tcb_cols})
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes_deed_venta"].values
    wide = wide.loc[wide["mes"].notna() & (wide["mes"] != "NaT")].copy()

    by_region = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    total = wide.drop(columns=["region"]).groupby("mes", as_index=False).sum(numeric_only=True)
    total["region"] = "Total"

    out = pd.concat([by_region, total], ignore_index=True)
    return out.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")


def _tc_buyers_sint_nid_count_by_deed_venta(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Cuenta NIDs con `date_of_sell_real_deed_financial` en cada
    (region, mes_deed_venta) sobre el UNIVERSO COMPLETO del tracker.

    Se usa para el tooltip informativo "# NIDs escriturados venta este mes: N"
    en el drill/hover de la fila TC Buyers Sintético.

    Excluye filas con mes_deed_venta=NaT (NIDs sin escritura y sin facturación).
    """
    df = df_prepared[["region_norm", "mes_deed_venta"]].copy()
    df.columns = ["region", "mes"]
    df = df.loc[df["mes"].notna() & (df["mes"] != "NaT")].copy()
    by_region = df.groupby(["region", "mes"]).size().reset_index(name="valor")
    by_region["key"] = "tc_buyers_nid_count"

    total = df.groupby("mes").size().reset_index(name="valor")
    total["region"] = "Total"
    total["key"] = "tc_buyers_nid_count"

    return pd.concat([by_region[["region", "mes", "key", "valor"]],
                      total[["region", "mes", "key", "valor"]]], ignore_index=True)


def _commercial_sint_buyers_by_promise(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Re-agrega las 2 keys de Commercial Buyers (com_ext_buyers, com_int_buyers)
    por (region, mes_promesa_buyers) sobre el UNIVERSO COMPLETO del tracker.

    Devuelve long DF [region, mes, key, valor] donde `mes` es el mes de la
    promesa de venta al comprador (con fallback fecha_facturacion_venta si
    NULL y NID facturado). NIDs sin promesa y sin facturación quedan con
    mes_promesa_buyers=NaT → excluidos del groupby.

    Emite también la fila Total (todas las regiones) por mes.
    """
    lines = _line_values(df_prepared, "sintetico")
    cols = list(COMMERCIAL_SINT_BUYERS_KEYS)
    wide = pd.DataFrame({k: lines[k] for k in cols})
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes_promesa_buyers"].values
    wide = wide.loc[wide["mes"].notna() & (wide["mes"] != "NaT")].copy()

    by_region = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    total = wide.drop(columns=["region"]).groupby("mes", as_index=False).sum(numeric_only=True)
    total["region"] = "Total"

    out = pd.concat([by_region, total], ignore_index=True)
    return out.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")


def _commercial_sint_sellers_by_promise(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Re-agrega las 2 keys de Commercial Sellers (com_ext_sellers,
    com_int_sellers) por (region, mes_promesa_sellers) sobre el UNIVERSO
    COMPLETO del tracker.

    Devuelve long DF [region, mes, key, valor] donde `mes` es el mes de la
    promesa de compra al owner (con fallback fecha_facturacion_venta si NULL
    y NID facturado). NIDs sin promesa y sin facturación quedan con
    mes_promesa_sellers=NaT → excluidos del groupby.

    Emite también la fila Total (todas las regiones) por mes.
    """
    lines = _line_values(df_prepared, "sintetico")
    cols = list(COMMERCIAL_SINT_SELLERS_KEYS)
    wide = pd.DataFrame({k: lines[k] for k in cols})
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes_promesa_sellers"].values
    wide = wide.loc[wide["mes"].notna() & (wide["mes"] != "NaT")].copy()

    by_region = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    total = wide.drop(columns=["region"]).groupby("mes", as_index=False).sum(numeric_only=True)
    total["region"] = "Total"

    out = pd.concat([by_region, total], ignore_index=True)
    return out.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")


def _commercial_sint_rollups_from_subs(all_long: pd.DataFrame) -> pd.DataFrame:
    """Recomputa external_commissions, internal_commissions y commercial a
    partir de las 4 subcuentas ya re-agrupadas en el long (Sintético).

      external_commissions = com_ext_buyers + com_ext_sellers
      internal_commissions = com_int_buyers + com_int_sellers
      commercial           = external_commissions + internal_commissions

    Devuelve long DF con las 3 nuevas filas por (region, mes). El caller es
    responsable de borrar los rollups viejos antes de concatenar.
    """
    subs = ("com_ext_buyers", "com_ext_sellers", "com_int_buyers", "com_int_sellers")
    wide = all_long.loc[all_long["key"].isin(subs)].pivot_table(
        index=["region", "mes"], columns="key", values="valor",
        aggfunc="sum", fill_value=0.0,
    )
    ext_b = wide.get("com_ext_buyers", 0.0)
    ext_s = wide.get("com_ext_sellers", 0.0)
    int_b = wide.get("com_int_buyers", 0.0)
    int_s = wide.get("com_int_sellers", 0.0)
    ext = ext_b + ext_s
    intr = int_b + int_s
    com = ext + intr

    rollups = pd.DataFrame({
        "external_commissions": ext,
        "internal_commissions": intr,
        "commercial": com,
    }).reset_index().melt(
        id_vars=["region", "mes"], var_name="key", value_name="valor",
    )
    return rollups


def _commercial_sint_nid_count_buyers(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Cuenta NIDs con `date_psa_buyers` en cada (region, mes_promesa_buyers).

    Se usa para el tooltip "# NIDs con promesa venta este mes: N" en el
    drill/hover de las filas Commercial Buyers Sintético.
    """
    df = df_prepared[["region_norm", "mes_promesa_buyers"]].copy()
    df.columns = ["region", "mes"]
    df = df.loc[df["mes"].notna() & (df["mes"] != "NaT")].copy()
    by_region = df.groupby(["region", "mes"]).size().reset_index(name="valor")
    by_region["key"] = "commercial_buyers_nid_count"

    total = df.groupby("mes").size().reset_index(name="valor")
    total["region"] = "Total"
    total["key"] = "commercial_buyers_nid_count"

    return pd.concat([by_region[["region", "mes", "key", "valor"]],
                      total[["region", "mes", "key", "valor"]]], ignore_index=True)


def _commercial_sint_nid_count_sellers(df_prepared: pd.DataFrame) -> pd.DataFrame:
    """Cuenta NIDs con `date_of_purchase_promise_financial` en cada
    (region, mes_promesa_sellers).

    Se usa para el tooltip "# NIDs con promesa compra este mes: N" en el
    drill/hover de las filas Commercial Sellers Sintético.
    """
    df = df_prepared[["region_norm", "mes_promesa_sellers"]].copy()
    df.columns = ["region", "mes"]
    df = df.loc[df["mes"].notna() & (df["mes"] != "NaT")].copy()
    by_region = df.groupby(["region", "mes"]).size().reset_index(name="valor")
    by_region["key"] = "commercial_sellers_nid_count"

    total = df.groupby("mes").size().reset_index(name="valor")
    total["region"] = "Total"
    total["key"] = "commercial_sellers_nid_count"

    return pd.concat([by_region[["region", "mes", "key", "valor"]],
                      total[["region", "mes", "key", "valor"]]], ignore_index=True)


def aggregate(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Devuelve DataFrame long: columnas [region, mes, key, valor].

    Filtra al universo FACTURADO (NIDs con fecha_facturacion_venta not null).
    Se aplica tanto a ACC como a la Sintético — para Sintético, las 6 keys
    de Remo se sobreescriben después con _remo_sint_by_end_remo() que sí opera
    sobre el universo completo.
    """
    df_fact = df_prepared.loc[df_prepared["facturado"]].copy()
    lines = _line_values(df_fact, vista)
    # empaquetar en un DF ancho de una vez
    wide = pd.DataFrame(lines)
    wide["region"] = df_fact["region_norm"].values
    wide["mes"] = df_fact["mes"].values
    grouped = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    long = grouped.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return long


def aggregate_all_regions(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Igual a aggregate pero también añade fila 'Total' (todas las regiones).

    En vista Sintético, las 6 keys de Remo (Mejoras/Pinturas/Reparaciones/
    Alistamiento/Kit Post + Remodeling total) se re-agrupan por `end_remo`
    sobre el UNIVERSO COMPLETO del tracker (facturados y no facturados).
    Managerial (ACC) y las líneas no-Remo del Sintético siguen filtradas al
    universo facturado.

    Además se emite `remodeling_nid_count` (informativo, para tooltip) sobre
    el mismo universo completo.
    """
    by_region = aggregate(df_prepared, vista)
    # Total: mismo filtro que aggregate() — universo facturado.
    df_fact = df_prepared.loc[df_prepared["facturado"]].copy()
    lines = _line_values(df_fact, vista)
    wide = pd.DataFrame(lines)
    wide["mes"] = df_fact["mes"].values
    total = wide.groupby("mes", as_index=False).sum(numeric_only=True)
    total["region"] = "Total"
    total_long = total.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    all_long = pd.concat([by_region, total_long], ignore_index=True)

    if vista == "sintetico":
        # Sobrescribir Remo Sintético con re-agregación por end_remo SOBRE UNIVERSO
        # COMPLETO (incluye NIDs sin fecha_facturacion_venta).
        remo_long = _remo_sint_by_end_remo(df_prepared)
        # 1) borrar las filas viejas (agrupadas por fact_venta, universo facturado)
        #    de las 6 keys de Remo
        mask_stale = all_long["key"].isin(REMO_SINTETICO_KEYS)
        all_long = all_long.loc[~mask_stale].copy()
        # 2) pegar las nuevas filas (por end_remo, universo completo)
        all_long = pd.concat([all_long, remo_long], ignore_index=True)

        # Sobrescribir TC Sellers Sintético (7 keys: 6 subcuentas + total) con
        # re-agregación por date_of_purchase_real_deed_financial SOBRE UNIVERSO
        # COMPLETO (incluye NIDs escriturados de compra aún no facturados de venta).
        tcs_long = _tc_sellers_sint_by_deed_compra(df_prepared)
        mask_stale_tcs = all_long["key"].isin(TC_SELLERS_SINT_KEYS)
        all_long = all_long.loc[~mask_stale_tcs].copy()
        all_long = pd.concat([all_long, tcs_long], ignore_index=True)

        # Sobrescribir TC Buyers Sintético (7 keys: 6 subcuentas + total) con
        # re-agregación por date_of_sell_real_deed_financial SOBRE UNIVERSO
        # COMPLETO (incluye NIDs escriturados de venta aún no facturados).
        tcb_long = _tc_buyers_sint_by_deed_venta(df_prepared)
        mask_stale_tcb = all_long["key"].isin(TC_BUYERS_SINT_KEYS)
        all_long = all_long.loc[~mask_stale_tcb].copy()
        all_long = pd.concat([all_long, tcb_long], ignore_index=True)

        # Sobrescribir Commercial Sintético — 4 subcuentas + 3 rollups.
        # Buyers (com_ext_buyers + com_int_buyers) por `date_psa_buyers` y
        # sellers (com_ext_sellers + com_int_sellers) por
        # `date_of_purchase_promise_financial`, ambos sobre UNIVERSO COMPLETO.
        # Los rollups external_commissions / internal_commissions / commercial
        # se recomputan de las subcuentas ya re-agrupadas.
        com_b_long = _commercial_sint_buyers_by_promise(df_prepared)
        com_s_long = _commercial_sint_sellers_by_promise(df_prepared)
        mask_stale_com_subs = all_long["key"].isin(
            list(COMMERCIAL_SINT_BUYERS_KEYS) + list(COMMERCIAL_SINT_SELLERS_KEYS)
        )
        all_long = all_long.loc[~mask_stale_com_subs].copy()
        all_long = pd.concat([all_long, com_b_long, com_s_long], ignore_index=True)
        # Recomputar los 3 rollups con las subcuentas re-agrupadas.
        com_rollups = _commercial_sint_rollups_from_subs(all_long)
        mask_stale_com_rollups = all_long["key"].isin(COMMERCIAL_SINT_ROLLUP_KEYS)
        all_long = all_long.loc[~mask_stale_com_rollups].copy()
        all_long = pd.concat([all_long, com_rollups], ignore_index=True)

        # 3) recalcular transaction_costs (afectado por nuevo tramites_sellers
        #    y nuevo tramites_buyers) y direct_costs / unlevered_profit /
        #    contribution_margin para reflejar Remo, TC Sellers, TC Buyers Y
        #    Commercial en universo ampliado en los totales. Sin este recompute
        #    los totales quedarían inconsistentes con las filas de detalle.
        #    ⚠️ Consecuencia esperada: CM Sint puede tener costos TC compra/venta
        #    o comisiones sin GMV proporcional (NIDs escriturados/prometidos aún
        #    no facturados).
        all_long = _recompute_sint_totals(all_long)
        # 4) añadir count de NIDs remodelados por (region, mes_end_remo)
        nid_count = _remo_sint_nid_count_by_end_remo(df_prepared)
        all_long = pd.concat([all_long, nid_count], ignore_index=True)
        # 5) añadir count de NIDs escriturados compra por (region, mes_deed_compra)
        nid_count_tcs = _tc_sellers_sint_nid_count_by_deed_compra(df_prepared)
        all_long = pd.concat([all_long, nid_count_tcs], ignore_index=True)
        # 6) añadir count de NIDs escriturados venta por (region, mes_deed_venta)
        nid_count_tcb = _tc_buyers_sint_nid_count_by_deed_venta(df_prepared)
        all_long = pd.concat([all_long, nid_count_tcb], ignore_index=True)
        # 7) counts NIDs con promesa compra/venta para tooltips Commercial
        nid_count_cb = _commercial_sint_nid_count_buyers(df_prepared)
        nid_count_cs = _commercial_sint_nid_count_sellers(df_prepared)
        all_long = pd.concat([all_long, nid_count_cb, nid_count_cs], ignore_index=True)

    return all_long


def _recompute_sint_totals(all_long: pd.DataFrame) -> pd.DataFrame:
    """Recomputa transaction_costs, direct_costs, unlevered_profit y
    contribution_margin en el long de Sintético después de sobreescribir
    Remo, TC Sellers, TC Buyers y/o Commercial con universo completo.

    Asume que el long ya trae los rubros ya re-agrupados por su driver
    operacional (remodeling por end_remo, tramites_sellers por deed_compra,
    tramites_buyers por deed_venta, commercial por promesa buyers/sellers).
    Este helper solo suma esos rubros a los totales.

    Fórmulas (mismo signo que _line_values):
      transaction_costs   = tramites_sellers + tramites_buyers
      direct_costs        = remodeling + transaction_costs + holding
                          + seguridad + commercial
      unlevered_profit    = gp_sin_iva + direct_costs
      contribution_margin = unlevered_profit + financing_costs
    """
    RECALC_KEYS = ("transaction_costs", "direct_costs", "unlevered_profit", "contribution_margin")
    # Pivot long → wide para acceso rápido por (region, mes, key)
    wide = all_long.pivot_table(
        index=["region", "mes"], columns="key", values="valor",
        aggfunc="sum", fill_value=0.0,
    )

    # Nueva Remo (universo ampliado por end_remo) y nuevo tramites_sellers
    # (universo ampliado por deed_compra) por (region, mes). Faltantes → 0.
    rem_new = wide.get("remodeling", 0.0)
    tcs_new = wide.get("tramites_sellers", 0.0)
    tcb = wide.get("tramites_buyers", 0.0)
    tc_new = tcs_new + tcb
    hol = wide.get("holding", 0.0)
    seg = wide.get("seguridad", 0.0)
    com = wide.get("commercial", 0.0)
    gp = wide.get("gp_sin_iva", 0.0)
    fin = wide.get("financing_costs", 0.0)

    direct_costs_new = rem_new + tc_new + hol + seg + com
    unlevered_new = gp + direct_costs_new
    cm_new = unlevered_new + fin

    new_totals = pd.DataFrame({
        "transaction_costs": tc_new,
        "direct_costs": direct_costs_new,
        "unlevered_profit": unlevered_new,
        "contribution_margin": cm_new,
    }).reset_index().melt(
        id_vars=["region", "mes"], var_name="key", value_name="valor",
    )

    mask_stale = all_long["key"].isin(RECALC_KEYS)
    out = all_long.loc[~mask_stale].copy()
    return pd.concat([out, new_totals], ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# inyección de Local OpEx (fuentes externas: payroll/rent/marketing)
# ─────────────────────────────────────────────────────────────────────────────

# Componentes que suman al `rent` (grupo). `rent_atribuible` es requerido; los otros
# dos son `only_total` (WeWork mezcla NL+JAL, Nacional son servicios sin ciudad) y
# se tratan como 0 cuando no están presentes.
_RENT_ONLY_TOTAL = ("rent_wework_nl_jal", "rent_nacional")

# Sub-métricas del OpEx corporativo (bet_data_p2) — se suman al total `corp_opex`
# por ciudad. `corp_opex_nacional` es only_total (GLOBAL MEX + MÉXICO).
_CORP_OPEX_SUBS = (
    "corp_opex_sales_ops",
    "corp_opex_tech",
    "corp_opex_prof_fees",
    "corp_opex_courier",
    "corp_opex_travel",
    "corp_opex_empl_rel",
    "corp_opex_other",
)
_CORP_OPEX_ONLY_TOTAL = ("corp_opex_nacional",)


def build_consolidated_long(
    mm_long: pd.DataFrame,
    inmo_long: pd.DataFrame | None,
    opex_long: pd.DataFrame | None,
) -> pd.DataFrame:
    """Construye el waterfall consolidado MM + Inmo + Local OpEx.

    - `mm_long` es la salida de `aggregate_all_regions(df_mm, vista)` (incluye 'Total').
    - `inmo_long` tiene columnas [region, mes, key, valor] con las keys nativas de Inmo:
      contribution_margin, gmv_inmo100, gmv_trad, properties_total.
      Puede ser None si no se cargó Inmo.
    - `opex_long` tiene columnas [region, mes, key, valor] con las sublíneas externas:
      payroll_local, rent_atribuible, rent_wework_nl_jal, rent_nacional, marketing_city.

    Genera claves consolidadas:
      cons_props_mm, cons_props_inmo, cons_props_total,
      cons_gmv_mm,   cons_gmv_inmo,   cons_gmv_total,
      cons_cm_mm,    cons_cm_inmo,    cons_cm_total,
      (payroll_local, rent_*, marketing_city, rent, local_opex, net_city_contribution).

    Reglas:
    - Si Inmo no está para (region, mes) → cons_props_inmo=0, cons_gmv_inmo=0, cons_cm_inmo=0
      (Inmo no operó ahí ese mes).
    - `rent` = rent_atribuible + WeWork(0 si falta) + Nacional(0 si falta).
    - `local_opex` = payroll + rent + marketing (marketing=0 si no está). Requiere
      payroll_local y rent_atribuible; si falta alguno (post-cobertura), no se emite.
    - `net_city_contribution` = cons_cm_total + local_opex (emite null si local_opex falta).
    """
    # 1) MM: extraer invoiced_sales, gmv_habi, contribution_margin por (region, mes)
    mm_by_cell: dict[tuple[str, str], dict[str, float]] = {}
    mm_keys_of_interest = {"invoiced_sales", "gmv_habi", "contribution_margin"}
    for row in mm_long.itertuples():
        if row.key in mm_keys_of_interest:
            mm_by_cell.setdefault((row.region, row.mes), {})[row.key] = float(row.valor)

    # 2) Inmo: extraer properties_total, gmv_inmo100+gmv_trad, contribution_margin
    inmo_by_cell: dict[tuple[str, str], dict[str, float]] = {}
    if inmo_long is not None and len(inmo_long) > 0:
        for row in inmo_long.itertuples():
            inmo_by_cell.setdefault((row.region, row.mes), {})[row.key] = float(row.valor)

    # 3) OpEx: (region, mes) → {payroll_local, rent_atribuible, ...}
    opex_by_cell: dict[tuple[str, str], dict[str, float]] = {}
    if opex_long is not None and len(opex_long) > 0:
        for row in opex_long.itertuples():
            opex_by_cell.setdefault((row.region, row.mes), {})[row.key] = float(row.valor)

    # 4) Emitir filas consolidadas para todas las (region, mes) donde exista MM ó Inmo.
    all_cells = set(mm_by_cell.keys()) | set(inmo_by_cell.keys())
    new_rows: list[dict] = []
    for (region, mes) in all_cells:
        mm = mm_by_cell.get((region, mes), {})
        inmo = inmo_by_cell.get((region, mes), {})

        props_mm = mm.get("invoiced_sales", 0.0)
        props_inmo = inmo.get("properties_total", 0.0)
        gmv_mm = mm.get("gmv_habi", 0.0)
        gmv_inmo = inmo.get("gmv_inmo100", 0.0) + inmo.get("gmv_trad", 0.0)
        cm_mm = mm.get("contribution_margin", 0.0)
        cm_inmo = inmo.get("contribution_margin", 0.0)

        new_rows.extend([
            {"region": region, "mes": mes, "key": "cons_props_mm", "valor": props_mm},
            {"region": region, "mes": mes, "key": "cons_props_inmo", "valor": props_inmo},
            {"region": region, "mes": mes, "key": "cons_props_total", "valor": props_mm + props_inmo},
            {"region": region, "mes": mes, "key": "cons_gmv_mm", "valor": gmv_mm},
            {"region": region, "mes": mes, "key": "cons_gmv_inmo", "valor": gmv_inmo},
            {"region": region, "mes": mes, "key": "cons_gmv_total", "valor": gmv_mm + gmv_inmo},
            {"region": region, "mes": mes, "key": "cons_cm_mm", "valor": cm_mm},
            {"region": region, "mes": mes, "key": "cons_cm_inmo", "valor": cm_inmo},
            {"region": region, "mes": mes, "key": "cons_cm_total", "valor": cm_mm + cm_inmo},
        ])

        # Local OpEx: solo si hay data en la fuente para (region, mes)
        cells = opex_by_cell.get((region, mes), {})
        for k, v in cells.items():
            new_rows.append({"region": region, "mes": mes, "key": k, "valor": v})

        # rent (grupo): requiere rent_atribuible
        if "rent_atribuible" in cells:
            rent_val = cells["rent_atribuible"] + sum(cells.get(k, 0.0) for k in _RENT_ONLY_TOTAL)
            new_rows.append({"region": region, "mes": mes, "key": "rent", "valor": rent_val})

        # corp_opex (grupo) = suma de sub-métricas + nacional (si aplica)
        has_any_corp = any(k in cells for k in _CORP_OPEX_SUBS + _CORP_OPEX_ONLY_TOTAL)
        corp_val = 0.0
        if has_any_corp:
            corp_val = (
                sum(cells.get(k, 0.0) for k in _CORP_OPEX_SUBS)
                + sum(cells.get(k, 0.0) for k in _CORP_OPEX_ONLY_TOTAL)
            )
            new_rows.append({"region": region, "mes": mes, "key": "corp_opex", "valor": corp_val})

        # local_opex + net_city_contribution: requieren payroll + rent_atribuible.
        # Marketing y Corp OpEx = 0 si no están.
        if "payroll_local" in cells and "rent_atribuible" in cells:
            local_opex_val = (
                cells["payroll_local"]
                + cells["rent_atribuible"]
                + sum(cells.get(k, 0.0) for k in _RENT_ONLY_TOTAL)
                + cells.get("marketing_city", 0.0)
                + corp_val
            )
            new_rows.append({"region": region, "mes": mes, "key": "local_opex", "valor": local_opex_val})
            new_rows.append({
                "region": region, "mes": mes,
                "key": "net_city_contribution",
                "valor": (cm_mm + cm_inmo) + local_opex_val,
            })

    return pd.DataFrame(new_rows) if new_rows else pd.DataFrame(columns=["region", "mes", "key", "valor"])
