# mx-city-pnl-dash

Dashboard estático de **P&L por ciudad · México** (Market Maker · Habi).

- **Fuente**: `clients-domain-data-master.finance_wh_bi.finance_apartment_tracker_mx`
- **Cohorte**: `fecha_facturacion_venta` (mes de escritura)
- **Ciudad**: columna `region`
- **Currency**: MXN 000's
- **Alcance**: hasta Contribution Margin (CM)
- **Dos vistas**: ACC (columnas `_accounting`) · Sintético (`_ue` con fallback `_accounting` fila por fila)

## Comandos

```bash
make install     # una vez
make raw         # trae raw de BQ → data/raw_apartment_mx.parquet
make refresh     # raw + agrega P&L → site/data/kpi_pnl.json
make serve       # http://localhost:8001/site/
```

## Prerequisitos

```bash
gcloud auth application-default login
```

## Deploy

GitHub Pages sobre `main`. Workflow en `.github/workflows/pages.yml` publica solo `site/`.
