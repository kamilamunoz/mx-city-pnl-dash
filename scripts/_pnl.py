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
    """Añade columna `mes` (YYYY-MM string) y `region_norm` (con 'Sin región' y 'Otros').

    Excluye filas con `fecha_facturacion_venta` nula (NIDs sin facturar todavía).
    Aplica REGION_ALIASES (p.ej. CDMX → EDO MEX) antes de contar y normalizar.
    """
    out = df.copy()
    fecha = pd.to_datetime(out["fecha_facturacion_venta"])
    out = out.loc[fecha.notna()].copy()
    out["mes"] = pd.to_datetime(out["fecha_facturacion_venta"]).dt.to_period("M").astype(str)
    region_aliased = _apply_region_aliases(out["region"])
    counts_by_region = region_aliased.value_counts(dropna=False)
    out["region_norm"] = _normalize_region(region_aliased, counts_by_region)
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
    {"key": "rent_atribuible", "label": "Rent (atribuible por ciudad)", "parent": "rent", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "rent_wework_nl_jal", "label": "Rent NL + JAL (WeWork · no separable)", "parent": "rent", "type": "subcuenta", "sign": "cost", "extern": True, "only_total": True,
     "note": "WeWork agrupa las oficinas de Monterrey (NL) y Guadalajara (JAL) bajo un solo c_tercero en OPEX. El grano de la fuente (proveedor × mes × país) no permite separar el gasto entre las dos ciudades — se muestra combinado solo en el consolidado."},
    {"key": "rent_nacional", "label": "Rent Nacional / no atribuible", "parent": "rent", "type": "subcuenta", "sign": "cost", "extern": True, "only_total": True,
     "note": "Proveedores de servicios sin ciudad atribuible (telecoms, papelería, terceros nacionales). Vendors principales: AT&T Comunicaciones Digitales, México Red de Telecomunicaciones, A de A México, Manuel Gutierrez González, Du Papier, Daniel Sebastián Ávila Arroyo. Representa ~12% del Rent MX YTD según el mapeo de Danibot."},
    {"key": "rent", "label": "Rent", "parent": "local_opex", "type": "grupo", "sign": "cost", "extern": True},
    {"key": "marketing_city", "label": "Marketing (ciudad)", "parent": "local_opex", "type": "subcuenta", "sign": "cost", "extern": True,
     "note": "Marketing digital atribuido por área metropolitana (Facebook, Google, etc.). Sirve a MM y a Inmo — por eso se resta solo en el consolidado."},
    {"key": "local_opex", "label": "(-) Local OpEx", "parent": None, "type": "rubro", "sign": "cost", "extern": True,
     "note": "Payroll + Rent + Marketing city-level. Sirve a MM y a Inmo simultáneamente, por eso se aplica UNA sola vez sobre la Contribution Total (no sobre MM o Inmo por separado)."},
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
    lines["gmv_habi"] = _num(df["sell_price_financial"])
    lines["gmv_sin_hc100"] = _num(df["sell_price_MM_sin_HC100_financial"])
    lines["fee_hc100"] = lines["gmv_habi"] - lines["gmv_sin_hc100"]
    lines["purchase_price"] = -_num(df["buy_price_financial"])
    # Gross Profit se calcula sobre GMV Habi (con fee HC100 incluido).
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


# ─────────────────────────────────────────────────────────────────────────────
# inyección de Local OpEx (fuentes externas: payroll/rent/marketing)
# ─────────────────────────────────────────────────────────────────────────────

# Componentes que suman al `rent` (grupo). `rent_atribuible` es requerido; los otros
# dos son `only_total` (WeWork mezcla NL+JAL, Nacional son servicios sin ciudad) y
# se tratan como 0 cuando no están presentes.
_RENT_ONLY_TOTAL = ("rent_wework_nl_jal", "rent_nacional")


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

        # local_opex + net_city_contribution: requieren payroll + rent_atribuible.
        # Marketing = 0 si no está.
        if "payroll_local" in cells and "rent_atribuible" in cells:
            local_opex_val = (
                cells["payroll_local"]
                + cells["rent_atribuible"]
                + sum(cells.get(k, 0.0) for k in _RENT_ONLY_TOTAL)
                + cells.get("marketing_city", 0.0)
            )
            new_rows.append({"region": region, "mes": mes, "key": "local_opex", "valor": local_opex_val})
            new_rows.append({
                "region": region, "mes": mes,
                "key": "net_city_contribution",
                "valor": (cm_mm + cm_inmo) + local_opex_val,
            })

    return pd.DataFrame(new_rows) if new_rows else pd.DataFrame(columns=["region", "mes", "key", "valor"])
