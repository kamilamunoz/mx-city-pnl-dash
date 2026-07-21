# mx-city-pnl-dash — comandos comunes
#
# Uso:
#   make install   instala dependencias con uv
#   make raw       corre la query BQ y guarda data/raw_apartment_mx.parquet
#   make refresh   raw + agrega P&L por (mes, region) y escribe site/data/kpi_pnl.json
#   make serve     abre el sitio en http://localhost:8001/site/
#   make lint      revisa el codigo Python con ruff
#   make clean     borra archivos generados de Python (no toca los JSON)

.PHONY: install raw refresh serve lint clean

install:
	uv sync

raw:
	uv run python -m scripts.fetch_raw

refresh:
	uv run python -m scripts.refresh_data

serve:
	@echo "Abre http://localhost:8001/site/ en el navegador"
	python3 -m http.server 8001

lint:
	uv run ruff check scripts/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
