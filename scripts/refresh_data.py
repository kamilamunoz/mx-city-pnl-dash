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
    PNL_STRUCTURE_CONSOLIDATED,
    REGION_ALIASES,
    aggregate_all_regions,
    build_consolidated_long,
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
RAW_MARKETING_PATH = REPO_ROOT / "data" / "raw_marketing_mx.parquet"
OUT_PATH = REPO_ROOT / "site" / "data" / "kpi_pnl.json"
OUT_FACTS_PATH = REPO_ROOT / "site" / "data" / "kpi_pnl_facts.json"
OUT_CONSOLIDATED_PATH = REPO_ROOT / "site" / "data" / "kpi_pnl_consolidated.json"

# JSON del dashboard Inmo MX (repo hermano). Se lee para el consolidado.
INMO_JSON_PATH = Path.home() / "Finanzas-Habi" / "mx-inmo-pnl-dash" / "site" / "data" / "kpi_pnl.json"

# Fuentes externas (Lis para payroll, Danibot para rent) — se leen del repo blt-dashboard.
# Marketing sale de BigQuery directo (query de Kamila sobre sellers-main-prod.bi_mx),
# extraído por `make raw_mkt` a data/raw_marketing_mx.parquet.
BLT_DASHBOARD_DATA = Path.home() / "Finanzas-Habi" / "blt-dashboard" / "data"
FX_MXN_PER_USD = 18.5  # el mismo escalar exacto que usa Danibot en Seguimiento Terceros

# Rent MX — mapeo vendor → destino (según docs/agrupaciones_por_ciudad.md de Danibot):
# - "PUBLICO REFORMA 333" (~71% Rent MX) → CDMX → fusionado a EDO MEX (REGION_ALIASES)
# - "ALDEA COWORKING" (coworking) → NUEVO LEON
# - "Wework méxico Co S de RL de CV" → NL+JAL no separable → clave rent_wework_nl_jal en Total
# - todo lo demás → rent_nacional en Total (servicios sin ciudad: AT&T, telecoms, papelería, etc.)
# El mapeo vendor→region canónica se resuelve luego contra REGION_ALIASES del pipeline.
RENT_MX_VENDOR_TO_REGION = {
    "PUBLICO REFORMA 333": "CDMX",
    "ALDEA COWORKING": "NUEVO LEON",
}
RENT_MX_VENDOR_WEWORK = "Wework méxico Co S de RL de CV"


def _alias_region(region: str) -> str:
    """Aplica REGION_ALIASES a un string (CDMX → EDO MEX)."""
    return REGION_ALIASES.get(region, region)


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


def _load_local_opex_mx() -> tuple[pd.DataFrame | None, dict]:
    """Lee payroll (Lis) y rent (Danibot) del repo blt-dashboard.

    Devuelve (long_df, meta) donde:
      - long_df tiene [region, mes, key, valor] con las 4 sublíneas externas
        (payroll_local, rent_atribuible, rent_wework_nl_jal, rent_nacional).
        Valores en MXN, signo negativo (son costos).
      - meta trae fechas de refresh y coberturas para el JSON de salida.

    Reglas:
    - Payroll: MXN absoluto ya por región canónica de Pau. `mx_sin_ciudad`
      (RFCs sin match, marginal) se agrega solo al Total, no a una región.
    - Rent: USD_K en la fuente; se multiplica por FX_MXN_PER_USD × 1000 → MXN.
      Vendors mapeados → region individual. WeWork → clave especial en Total.
      Resto → rent_nacional en Total.
    - Meses con `0` posteriores a `ytd_month_2026` se ignoran (fillers, no cero real).
    """
    payroll_path = BLT_DASHBOARD_DATA / "payroll_by_city.json"
    opex_path = BLT_DASHBOARD_DATA / "opex_terceros.json"

    if not payroll_path.exists() or not opex_path.exists():
        log.warning("blt-dashboard data no disponible (%s existe=%s, %s existe=%s) — se omite Local OpEx",
                    payroll_path, payroll_path.exists(), opex_path, opex_path.exists())
        return None, {}

    meta: dict = {}
    rows: list[dict] = []

    # ── Payroll (Lis) ─────────────────────────────────────────────────
    payroll = json.loads(payroll_path.read_text(encoding="utf-8"))
    meta["payroll_generado_en"] = payroll["_meta"].get("generated")
    meta["payroll_owner"] = payroll["_meta"].get("owner")

    # Acumular por región (post-aliasing) y luego emitir. Necesario porque
    # REGION_ALIASES fusiona CDMX en EDO MEX: los dos bloques originales de Lis
    # ("CDMX" con sedes=[CIUDAD DE MEXICO] y "EDO MEX" con sedes=[]) colapsan
    # al mismo destino.
    payroll_by_region_mes: dict[tuple[str, str], float] = {}
    sedes_by_region: dict[str, list[str]] = {}
    payroll_max_mes: str | None = None
    for block in payroll["mx"]:
        region_final = _alias_region(block["city_pnl"])
        sedes_by_region.setdefault(region_final, []).extend(block.get("sedes") or [])
        for mes, val in block["costo_empresa_mxn_mensual"].items():
            payroll_by_region_mes[(region_final, mes)] = (
                payroll_by_region_mes.get((region_final, mes), 0.0) + float(val)
            )
            if payroll_max_mes is None or mes > payroll_max_mes:
                payroll_max_mes = mes

    total_payroll_by_mes: dict[str, float] = {}
    regiones_sin_sede = [r for r, sedes in sedes_by_region.items() if not sedes]
    for (region, mes), val in payroll_by_region_mes.items():
        # Regiones sin sede en BBDD (típicamente HIDALGO tras la fusión CDMX→EDO MEX):
        # payroll estructural = 0 en toda la serie. Emitimos 0 explícito para que
        # local_opex y net_city_contribution puedan computarse limpiamente.
        no_sedes = not sedes_by_region.get(region)
        valor_signado = 0.0 if no_sedes else -val
        rows.append({"region": region, "mes": mes, "key": "payroll_local", "valor": valor_signado})
        if not no_sedes:
            total_payroll_by_mes[mes] = total_payroll_by_mes.get(mes, 0.0) + val

    # mx_sin_ciudad → solo al Total (no atribuible a región)
    sin_ciudad = payroll.get("mx_sin_ciudad", {}).get("costo_empresa_mxn_mensual", {})
    for mes, val in sin_ciudad.items():
        total_payroll_by_mes[mes] = total_payroll_by_mes.get(mes, 0.0) + float(val)

    for mes, val in total_payroll_by_mes.items():
        rows.append({"region": "Total", "mes": mes, "key": "payroll_local", "valor": -val})

    meta["payroll_cobertura_hasta"] = payroll_max_mes
    meta["payroll_regiones_sin_sede"] = regiones_sin_sede

    # ── Rent (Danibot) ────────────────────────────────────────────────
    opex = json.loads(opex_path.read_text(encoding="utf-8"))
    meta["rent_generado_en"] = opex["meta"].get("generated_at")
    ytd_month = int(opex["meta"].get("ytd_month_2026", 12))
    vendors = opex["data"]["Mexico"]["Rent"]["vendors"]

    # (region, mes, fact_key) → USD_K acumulado
    rent_acum: dict[tuple[str, str, str], float] = {}

    def _add(region: str, mes: str, fact_key: str, val_usdk: float) -> None:
        k = (region, mes, fact_key)
        rent_acum[k] = rent_acum.get(k, 0.0) + val_usdk

    for v in vendors:
        name = v["name"]
        # Determinar destino (aplicar REGION_ALIASES si mapea a una región canónica)
        if name == RENT_MX_VENDOR_WEWORK:
            dest_region, fact_key = "Total", "rent_wework_nl_jal"
        elif name in RENT_MX_VENDOR_TO_REGION:
            dest_region = _alias_region(RENT_MX_VENDOR_TO_REGION[name])
            fact_key = "rent_atribuible"
        else:
            dest_region, fact_key = "Total", "rent_nacional"

        # Recorrer series a2025, a2026 (actuals). budgets/forecasts se ignoran.
        for series_key, months in v.items():
            if not (isinstance(series_key, str) and series_key.startswith("a") and len(series_key) == 5):
                continue
            try:
                year = int(series_key[1:])
            except ValueError:
                continue
            if not isinstance(months, list):
                continue
            for i, val_usdk in enumerate(months):
                if val_usdk == 0.0:
                    continue
                # Clip meses futuros de 2026 rellenados con 0 → ya filtrados arriba,
                # pero por seguridad: si year==2026 y i >= ytd_month, ignorar aunque
                # el valor sea != 0 (defensivo contra fillers no-cero).
                if year == 2026 and i >= ytd_month:
                    continue
                mes = f"{year}-{i + 1:02d}"
                _add(dest_region, mes, fact_key, float(val_usdk))

    # Emitir rent atribuible / wework / nacional
    total_atrib_by_mes: dict[str, float] = {}
    rent_max_mes: str | None = None
    for (region, mes, fact_key), val_usdk in rent_acum.items():
        val_mxn = val_usdk * FX_MXN_PER_USD * 1000.0
        rows.append({"region": region, "mes": mes, "key": fact_key, "valor": val_mxn})
        if fact_key == "rent_atribuible" and region != "Total":
            total_atrib_by_mes[mes] = total_atrib_by_mes.get(mes, 0.0) + val_usdk
        if rent_max_mes is None or mes > rent_max_mes:
            rent_max_mes = mes

    # rent_atribuible en Total = suma de todas las regiones atribuibles
    for mes, val_usdk in total_atrib_by_mes.items():
        rows.append({
            "region": "Total", "mes": mes, "key": "rent_atribuible",
            "valor": val_usdk * FX_MXN_PER_USD * 1000.0,
        })

    meta["rent_cobertura_hasta"] = rent_max_mes
    meta["fx_mxn_per_usd"] = FX_MXN_PER_USD

    # ── Marketing (query de Kamila sobre sellers-main-prod.bi_mx) ─────
    # Valores en USD absolutos. Convertir a MXN con FX 18.5. Signo cost.
    # OJO: mapeo de área metropolitana → región de Kamila NO idéntico al de payroll.
    #      Aquí "Valle de México" → EDO MEX (mientras que en payroll el staff área
    #      metro se etiqueta CDMX). Por eso CDMX no aparece en marketing.
    mkt_max_mes: str | None = None
    if RAW_MARKETING_PATH.exists():
        mkt_df = pd.read_parquet(RAW_MARKETING_PATH)
        meta["marketing_filas"] = int(len(mkt_df))
        total_mkt_by_mes: dict[str, float] = {}
        for row in mkt_df.itertuples():
            mes = pd.to_datetime(row.ir_mes_inversion).strftime("%Y-%m")
            region = row.ir_area_metropolitana
            val_usd = float(row.ir_spend)
            val_mxn = -val_usd * FX_MXN_PER_USD  # spend ya es USD absoluto, no USD_K
            rows.append({"region": region, "mes": mes, "key": "marketing_city", "valor": val_mxn})
            total_mkt_by_mes[mes] = total_mkt_by_mes.get(mes, 0.0) + val_usd
            if mkt_max_mes is None or mes > mkt_max_mes:
                mkt_max_mes = mes
        for mes, val_usd in total_mkt_by_mes.items():
            rows.append({"region": "Total", "mes": mes, "key": "marketing_city",
                         "valor": -val_usd * FX_MXN_PER_USD})
        meta["marketing_cobertura_hasta"] = mkt_max_mes
    else:
        log.warning("data/raw_marketing_mx.parquet no existe — corre `make raw_mkt`. Marketing = 0.")
        meta["marketing_cobertura_hasta"] = None

    # Backfill: para (region, mes) donde hay payroll pero no rent_atribuible,
    # emitir rent_atribuible=0 para que local_opex pueda computarse.
    regions_with_rent_atr: set[tuple[str, str]] = {
        (r, m) for (r, m, fk) in rent_acum.keys() if fk == "rent_atribuible"
    }
    regions_with_rent_atr |= {("Total", m) for m in total_atrib_by_mes.keys()}
    regions_with_payroll: set[tuple[str, str]] = set()
    for row in rows:
        if row["key"] == "payroll_local":
            regions_with_payroll.add((row["region"], row["mes"]))
    for (r, m) in regions_with_payroll - regions_with_rent_atr:
        rows.append({"region": r, "mes": m, "key": "rent_atribuible", "valor": 0.0})

    if not rows:
        return None, meta
    return pd.DataFrame(rows), meta


def _load_inmo_mx() -> tuple[pd.DataFrame | None, dict]:
    """Lee el JSON del dashboard mx-inmo-pnl-dash y produce un long DF con las
    keys necesarias para el consolidado: contribution_margin, gmv_inmo100,
    gmv_trad, properties_total (por region, mes, vista).

    Aplica REGION_ALIASES (CDMX → EDO MEX) sumando los valores de CDMX en EDO MEX.

    Devuelve dict {vista → long_df} y meta con la fecha de generación del JSON.
    """
    if not INMO_JSON_PATH.exists():
        log.warning("mx-inmo-pnl-dash JSON no encontrado en %s — consolidado sin Inmo", INMO_JSON_PATH)
        return None, {}

    inmo = json.loads(INMO_JSON_PATH.read_text(encoding="utf-8"))
    meta = {
        "inmo_generado_en": inmo.get("meta", {}).get("generado_en"),
        "inmo_regiones_fuente": [r["key"] for r in inmo.get("regiones", [])],
    }

    keys_of_interest = {"contribution_margin", "gmv_inmo100", "gmv_trad", "properties_total"}

    out_by_vista: dict[str, pd.DataFrame] = {}
    for vista, data_region in inmo["vistas"].items():
        # (region_final, mes, key) → valor acumulado (con fusión CDMX→EDO MEX)
        acc: dict[tuple[str, str, str], float] = {}
        for region_orig, months in data_region.items():
            region_final = _alias_region(region_orig)
            for mes, row in months.items():
                for k, v in row.items():
                    if k not in keys_of_interest:
                        continue
                    key = (region_final, mes, k)
                    acc[key] = acc.get(key, 0.0) + float(v)
        rows = [
            {"region": r, "mes": m, "key": k, "valor": v}
            for (r, m, k), v in acc.items()
        ]
        out_by_vista[vista] = pd.DataFrame(rows)

    return out_by_vista, meta


def _write_consolidated(
    long_by_vista_mm: dict[str, pd.DataFrame],
    inmo_by_vista: dict[str, pd.DataFrame] | None,
    local_opex_df: pd.DataFrame | None,
    regiones: list[dict],
    meses_mm: list[str],
    local_opex_meta: dict,
    inmo_meta: dict,
) -> None:
    """Construye kpi_pnl_consolidated.json (MM + Inmo + Local OpEx aplicado 1 vez)."""
    vistas_out: dict[str, dict] = {}
    all_meses: set[str] = set(meses_mm)

    for vista, mm_long in long_by_vista_mm.items():
        inmo_long = None if inmo_by_vista is None else inmo_by_vista.get(vista)
        cons_long = build_consolidated_long(mm_long, inmo_long, local_opex_df)
        vistas_out[vista] = _long_to_nested(cons_long)
        if inmo_long is not None and len(inmo_long) > 0:
            all_meses.update(inmo_long["mes"].astype(str).unique().tolist())

    # Unir meses de MM + Inmo (Inmo puede tener meses posteriores)
    meses_ordenados = sorted(all_meses)

    payload = {
        "meta": {
            "generado_en": datetime.now().isoformat(timespec="seconds"),
            "descripcion": (
                "Waterfall consolidado MM + Inmo por región×mes. El Local OpEx "
                "(payroll+rent+marketing) se aplica UNA sola vez sobre la Contribution "
                "Total porque sirve a ambas líneas de negocio."
            ),
            "local_opex": local_opex_meta,
            "inmo": inmo_meta,
            "currency": "MXN",
            "unidad": "unidades absolutas (el frontend divide por 1000 para mostrar en 000's)",
        },
        "estructura": PNL_STRUCTURE_CONSOLIDATED,
        "regiones": regiones,
        "meses": meses_ordenados,
        "vistas": vistas_out,
    }

    OUT_CONSOLIDATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CONSOLIDATED_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log.info("Escrito → %s (%.1f KB)", OUT_CONSOLIDATED_PATH, OUT_CONSOLIDATED_PATH.stat().st_size / 1024)


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

    # Local OpEx e Inmo se usan SOLO para el consolidado (no para el waterfall MM,
    # que ahora cierra en Contribution Margin). Sirven a ambas líneas de negocio.
    log.info("Cargando Local OpEx (payroll Lis + rent Danibot + marketing BQ) ...")
    local_opex_df, local_opex_meta = _load_local_opex_mx()
    if local_opex_df is not None:
        log.info("Local OpEx: %d filas, payroll hasta %s, rent hasta %s, marketing hasta %s",
                 len(local_opex_df),
                 local_opex_meta.get("payroll_cobertura_hasta"),
                 local_opex_meta.get("rent_cobertura_hasta"),
                 local_opex_meta.get("marketing_cobertura_hasta"))

    log.info("Cargando Inmo MX (JSON de mx-inmo-pnl-dash) ...")
    inmo_by_vista, inmo_meta = _load_inmo_mx()
    if inmo_by_vista is not None:
        log.info("Inmo: generado_en=%s, regiones fuente=%s",
                 inmo_meta.get("inmo_generado_en"), inmo_meta.get("inmo_regiones_fuente"))

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

    # ── kpi_pnl_consolidated.json: waterfall MM + Inmo + Local OpEx ──
    log.info("Construyendo consolidado MM + Inmo ...")
    _write_consolidated(
        long_by_vista_mm={"acc": long_acc, "sintetico": long_sint},
        inmo_by_vista=inmo_by_vista,
        local_opex_df=local_opex_df,
        regiones=regiones,
        meses_mm=meses,
        local_opex_meta=local_opex_meta,
        inmo_meta=inmo_meta,
    )


if __name__ == "__main__":
    main()
