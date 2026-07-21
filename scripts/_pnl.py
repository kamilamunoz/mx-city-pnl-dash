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
"""

from __future__ import annotations

import pandas as pd

# Umbral de filas totales para colapsar en 'Otros'
MIN_ROWS_PER_REGION = 50
# Los NIDs con region NULL se asignan a EDO MEX (decisión operativa de Kamila,
# 2026-07-21: la mayoría son EDO MEX sin etiquetar).
DEFAULT_REGION_FOR_NULLS = "EDO MEX"
LABEL_OTROS = "Otros"


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


def _normalize_region(region: pd.Series, counts: pd.Series) -> pd.Series:
    """NaN → EDO MEX (default). Regiones con <MIN_ROWS_PER_REGION → 'Otros'."""
    below = counts[counts < MIN_ROWS_PER_REGION].index.tolist()
    out = region.where(region.notna(), DEFAULT_REGION_FOR_NULLS)
    out = out.where(~out.isin(below), LABEL_OTROS)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# preparación
# ─────────────────────────────────────────────────────────────────────────────

def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Añade columna `mes` (YYYY-MM string) y `region_norm` (con 'Sin región' y 'Otros').

    Excluye filas con `fecha_facturacion_venta` nula (NIDs sin facturar todavía).
    """
    out = df.copy()
    fecha = pd.to_datetime(out["fecha_facturacion_venta"])
    out = out.loc[fecha.notna()].copy()
    out["mes"] = pd.to_datetime(out["fecha_facturacion_venta"]).dt.to_period("M").astype(str)
    counts_by_region = out["region"].value_counts(dropna=False)
    out["region_norm"] = _normalize_region(out["region"], counts_by_region)
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
    {"key": "invoiced_sales", "label": "# Invoiced Sales", "parent": None, "type": "kpi", "sign": "count"},
    {"key": "gmv_habi", "label": "(+) GMV Precio de Venta Habi", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "fee_hc100", "label": "(+) Fee HC100", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "gmv_sin_hc100", "label": "GMV Selling Price (sin HC100)", "parent": None, "type": "kpi", "sign": "income"},
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
    {"key": "remodeling", "label": "Remodeling Costs", "parent": None, "type": "rubro", "sign": "cost"},

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

    # ── transaction costs HC100 (pendiente: lógica del cálculo con Kamila) ──
    {"key": "transaction_costs_hc100", "label": "Transaction Costs HC100", "parent": None, "type": "rubro", "sign": "cost", "pendiente": True},

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
    lines["gmv_habi"] = _num(df["sell_price_financial"])
    lines["gmv_sin_hc100"] = _num(df["sell_price_MM_sin_HC100_financial"])
    lines["fee_hc100"] = lines["gmv_habi"] - lines["gmv_sin_hc100"]
    lines["purchase_price"] = -_num(df["buy_price_financial"])
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

    # Transaction Costs HC100: línea del Excel que no se deriva de las columnas
    # de la query. Placeholder = 0 hasta que confirmemos con Kamila la fórmula.
    lines["transaction_costs_hc100"] = pd.Series(0.0, index=df.index)

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
        lines["remodeling"] + lines["transaction_costs"] + lines["transaction_costs_hc100"]
        + lines["holding"] + lines["seguridad"] + lines["commercial"]
    )
    lines["unlevered_profit"] = lines["gp_sin_iva"] + lines["direct_costs"]
    lines["financing_costs"] = -_num(df["financing_costs_"])
    lines["contribution_margin"] = lines["unlevered_profit"] + lines["financing_costs"]

    return lines


def line_values_per_nid(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Devuelve un DataFrame por-NID con columnas [nid, region, mes, <key1>, <key2>, ...].

    Cada columna key es el valor de esa línea del P&L para ese NID en esa vista.
    Se usa para el drill-down desde el frontend.
    """
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide.insert(0, "mes", df_prepared["mes"].values)
    wide.insert(0, "region", df_prepared["region_norm"].values)
    wide.insert(0, "nid", df_prepared["nid"].values)
    return wide


def aggregate(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Devuelve DataFrame long: columnas [region, mes, key, valor]."""
    lines = _line_values(df_prepared, vista)
    # empaquetar en un DF ancho de una vez
    wide = pd.DataFrame(lines)
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes"].values
    grouped = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    long = grouped.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return long


def aggregate_all_regions(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Igual a aggregate pero también añade fila 'Total' (todas las regiones)."""
    by_region = aggregate(df_prepared, vista)
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide["mes"] = df_prepared["mes"].values
    total = wide.groupby("mes", as_index=False).sum(numeric_only=True)
    total["region"] = "Total"
    total_long = total.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return pd.concat([by_region, total_long], ignore_index=True)
