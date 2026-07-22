// mx-city-pnl-dash — frontend
// Kamila mantiene sola. Vanilla JS, sin frameworks.

const PASSWORD = 'EYPx9AUmLsamlp5g';
const STORAGE_KEY = 'mx-pnl-auth';

// Webhook de Google Chat para reportar NIDs con signo raro a Jeff.
// Para rotarlo: Google Chat > Space > Manage webhooks > Regenerate URL, y pegar la nueva URL aquí.
const CHAT_WEBHOOK_URL = 'https://chat.googleapis.com/v1/spaces/AAQAH51TuFM/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=iHJpuzPgIZavlQIBLx6OAQe0MZ57xys6-8iPjHGZfbE';
const REPORTER_NAME = 'Kamila (dashboard MX P&L)';

const state = {
  data: null,          // kpi_pnl.json
  facts: null,         // kpi_pnl_facts.json
  // último drill abierto (para el botón de reporte)
  lastDrill: null,     // { row, contextLabel, alertItems, total, vista }
  // tab 1: P&L por región
  vista: 'acc',
  region: 'Total',
  rango: '12',         // '6' | '12' | 'all' | 'year' | 'range'
  year: null,          // int (cuando rango==='year')
  rangeFrom: null,     // 'YYYY-MM' (cuando rango==='range')
  rangeTo: null,       // 'YYYY-MM'
  // tab 2: comparativa
  activeTab: 'pnl',
  cmpVista: 'acc',
  cmpPeriodo: '3m',
  cmpRegiones: new Set(),
  cmpMetrica: 'abs',
};

// líneas NO clickables (son sumas o counts, no tienen NIDs propios)
const NON_DRILLABLE = new Set(['invoiced_sales']);

// ─── login ────────────────────────────────────────────────────────────
function unlockUI() {
  document.getElementById('loginGate').style.display = 'none';
  document.querySelector('.topbar').hidden = false;
  document.querySelector('.tabs').hidden = false;
  document.getElementById('tab-pnl').hidden = false;
}

function setupLogin() {
  const form = document.getElementById('loginForm');
  const err = document.getElementById('loginError');
  if (sessionStorage.getItem(STORAGE_KEY) === 'ok') {
    unlockUI();
    return true;
  }
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const pwd = document.getElementById('loginPwd').value;
    if (pwd === PASSWORD) {
      sessionStorage.setItem(STORAGE_KEY, 'ok');
      unlockUI();
      init();
    } else {
      err.hidden = false;
    }
  });
  return false;
}

// ─── data load ────────────────────────────────────────────────────────
async function loadData() {
  const [pnl, facts] = await Promise.all([
    fetch(`data/kpi_pnl.json?v=${Date.now()}`).then(r => r.json()),
    fetch(`data/kpi_pnl_facts.json?v=${Date.now()}`).then(r => r.json()),
  ]);
  state.data = pnl;
  state.facts = facts;

  // por default, seleccionar todas las regiones reales (sin Total) para comparativa
  state.cmpRegiones = new Set(
    state.data.regiones.filter(r => r.key !== 'Total').map(r => r.key)
  );
}

// ─── header ───────────────────────────────────────────────────────────
function renderHeader() {
  const m = state.data.meta;
  document.getElementById('contextLabel').textContent =
    `Market Maker · ${m.filas_incluidas.toLocaleString('es-MX')} NIDs facturados`;
  document.getElementById('rangoFechas').textContent =
    `${m.rango_fechas.min} → ${m.rango_fechas.max}`;
  const dt = new Date(m.generado_en);
  document.getElementById('refreshAt').textContent =
    dt.toLocaleString('es-MX', { dateStyle: 'medium', timeStyle: 'short' });
}

// ─── tab switching ────────────────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.addEventListener('click', () => {
      const t = b.dataset.tab;
      state.activeTab = t;
      document.querySelectorAll('.tab-btn').forEach(x => x.classList.toggle('active', x === b));
      document.getElementById('tab-pnl').hidden = t !== 'pnl';
      document.getElementById('tab-comparativa').hidden = t !== 'comparativa';
      if (t === 'comparativa') renderCmp();
    });
  });
}

// ─── controls (tab P&L) ───────────────────────────────────────────────
function renderRegionCtrl() {
  const el = document.getElementById('regionCtrl');
  el.innerHTML = '';
  for (const r of state.data.regiones) {
    const b = document.createElement('button');
    b.className = 'seg-btn' + (r.key === state.region ? ' active' : '');
    b.dataset.region = r.key;
    const filasStr = r.filas ? ` (${r.filas.toLocaleString('es-MX')})` : '';
    b.textContent = r.label + filasStr;
    b.addEventListener('click', () => {
      state.region = r.key;
      el.querySelectorAll('.seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderTable();
    });
    el.appendChild(b);
  }
}

function setupControls() {
  document.querySelectorAll('#vistaCtrl .seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.vista = b.dataset.vista;
      document.querySelectorAll('#vistaCtrl .seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderTable();
    });
  });
  document.querySelectorAll('#rangoCtrl .seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.rango = b.dataset.rango;
      document.querySelectorAll('#rangoCtrl .seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      // mostrar/ocultar sub-controles según el modo
      document.getElementById('yearSubCtrl').hidden = state.rango !== 'year';
      document.getElementById('rangeSubCtrl').hidden = state.rango !== 'range';
      renderTable();
    });
  });

  // sub-control: año — botones dinámicos según años presentes
  const yearCtrl = document.getElementById('yearCtrl');
  const years = Array.from(new Set(state.data.meses.map(m => m.slice(0, 4)))).sort();
  state.year = state.year || years[years.length - 1];
  yearCtrl.innerHTML = '';
  for (const y of years) {
    const b = document.createElement('button');
    b.className = 'seg-btn' + (y === state.year ? ' active' : '');
    b.dataset.year = y;
    b.textContent = y;
    b.addEventListener('click', () => {
      state.year = y;
      yearCtrl.querySelectorAll('.seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderTable();
    });
    yearCtrl.appendChild(b);
  }

  // sub-control: rango de fechas — selects de mes-año
  const fromSel = document.getElementById('rangeFrom');
  const toSel = document.getElementById('rangeTo');
  const opts = state.data.meses.map(m => `<option value="${m}">${m}</option>`).join('');
  fromSel.innerHTML = opts;
  toSel.innerHTML = opts;
  // defaults: primer y último mes
  state.rangeFrom = state.rangeFrom || state.data.meses[0];
  state.rangeTo = state.rangeTo || state.data.meses[state.data.meses.length - 1];
  fromSel.value = state.rangeFrom;
  toSel.value = state.rangeTo;
  fromSel.addEventListener('change', () => {
    state.rangeFrom = fromSel.value;
    if (state.rangeFrom > state.rangeTo) {
      state.rangeTo = state.rangeFrom;
      toSel.value = state.rangeTo;
    }
    renderTable();
  });
  toSel.addEventListener('change', () => {
    state.rangeTo = toSel.value;
    if (state.rangeTo < state.rangeFrom) {
      state.rangeFrom = state.rangeTo;
      fromSel.value = state.rangeFrom;
    }
    renderTable();
  });
}

// ─── tabla P&L (tab 1) ────────────────────────────────────────────────
function mesesToShow() {
  const all = state.data.meses;
  if (state.rango === 'all') return all;
  if (state.rango === 'year') {
    return all.filter(m => m.startsWith(state.year + '-'));
  }
  if (state.rango === 'range') {
    return all.filter(m => m >= state.rangeFrom && m <= state.rangeTo);
  }
  const n = parseInt(state.rango, 10);
  return all.slice(-n);
}

function fmt(v, isCount = false) {
  if (v === null || v === undefined) return '—';
  if (isCount) return Math.round(v).toLocaleString('es-MX');
  const inMil = v / 1000;
  return inMil.toLocaleString('es-MX', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

function fmtAbs(v) {
  if (v === null || v === undefined) return '—';
  return v.toLocaleString('es-MX', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function fmtPct(v) {
  if (v === null || v === undefined || !isFinite(v)) return '';
  return (v * 100).toLocaleString('es-MX', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%';
}

function renderTable() {
  const meses = mesesToShow();
  const structure = state.data.estructura;
  const dataRegion = state.data.vistas[state.vista][state.region] || {};

  const filaVisible = (row) => !row.vista || row.vista === state.vista;
  const structureFiltered = structure.filter(filaVisible);

  const head = document.getElementById('pnlHead');
  head.innerHTML = '';
  head.appendChild(th('P&L (MXN 000s)'));
  for (const m of meses) head.appendChild(th(m));

  const body = document.getElementById('pnlBody');
  body.innerHTML = '';

  const revByMonth = {};
  for (const m of meses) revByMonth[m] = (dataRegion[m] || {})['gmv_sin_hc100'] || 0;

  const showPctRow = (row) => !['invoiced_sales', 'gmv_habi', 'fee_hc100', 'gmv_sin_hc100'].includes(row.key);

  for (const row of structureFiltered) {
    const tr = document.createElement('tr');
    tr.className = `tipo-${row.type}`;
    if (row.pendiente) tr.classList.add('pendiente');
    tr.appendChild(td(row.label));

    for (const m of meses) {
      const cell = (dataRegion[m] || {})[row.key];
      const val = cell === undefined ? null : cell;
      const isCount = row.sign === 'count';
      const cellEl = document.createElement('td');
      cellEl.classList.add(`signo-${row.sign}`);

      if (showPctRow(row) && val !== null && revByMonth[m]) {
        const pct = val / revByMonth[m];
        cellEl.innerHTML = `${fmt(val, isCount)}<br><span class="pct">${fmtPct(pct)}</span>`;
      } else {
        cellEl.textContent = fmt(val, isCount);
      }

      if (!NON_DRILLABLE.has(row.key) && !row.pendiente && val !== null && val !== 0) {
        cellEl.classList.add('clickable');
        cellEl.addEventListener('click', () => openDrill(row, m));
      }
      tr.appendChild(cellEl);
    }
    body.appendChild(tr);
  }

  const pend = structureFiltered.filter(r => r.pendiente).map(r => r.label);
  document.getElementById('pendienteNota').innerHTML = pend.length
    ? `<span style="color:var(--warn)">⚠</span> Líneas pendientes de definir con contabilidad: <b>${pend.join(', ')}</b>.`
    : '';
}

function th(text) {
  const el = document.createElement('th');
  el.textContent = text;
  return el;
}
function td(text) {
  const el = document.createElement('td');
  el.textContent = text;
  return el;
}

// ─── DRILL DOWN ───────────────────────────────────────────────────────
function openDrill(row, mes) {
  const facts = state.facts[state.vista];
  const colIdx = facts.columnas.indexOf(row.key);
  if (colIdx < 0) return;

  const items = [];
  const totalNids = facts.nid.length;
  for (let i = 0; i < totalNids; i++) {
    const matchRegion = (state.region === 'Total') || (facts.region[i] === state.region);
    const matchMes = facts.mes[i] === mes;
    if (!matchRegion || !matchMes) continue;
    const v = facts.valores[colIdx][i];
    if (v === 0) continue;
    items.push({ nid: facts.nid[i], region: facts.region[i], valor: v });
  }

  items.sort((a, b) => Math.abs(b.valor) - Math.abs(a.valor));

  const total = items.reduce((s, x) => s + x.valor, 0);
  const isAlert = (v) => {
    if (row.sign === 'cost' && v > 0) return true;
    if (row.sign === 'income' && v < 0) return true;
    return false;
  };
  const alertCount = items.filter(x => isAlert(x.valor)).length;
  const alertSum = items.filter(x => isAlert(x.valor)).reduce((s, x) => s + x.valor, 0);

  const ctx = state.region === 'Total' ? `Todas las regiones · ${mes}` : `${state.region} · ${mes}`;
  document.getElementById('drillContext').textContent = ctx;
  document.getElementById('drillTitle').textContent = row.label;

  const totalCls = row.sign === 'cost' ? 'cost' : (row.sign === 'income' ? 'income' : '');
  document.getElementById('drillTotal').innerHTML =
    `<span class="amount ${totalCls}">${fmtAbs(total)}</span><span class="lbl">MXN · ${items.length.toLocaleString('es-MX')} NIDs</span>`;

  const summary = document.getElementById('drillSummary');
  const top5Sum = items.slice(0, 5).reduce((s, x) => s + x.valor, 0);
  const top5Pct = total !== 0 ? (top5Sum / total * 100) : 0;
  summary.innerHTML = `
    <div class="kpi"><div class="lbl">NIDs con gasto</div><div class="val">${items.length.toLocaleString('es-MX')}</div></div>
    <div class="kpi"><div class="lbl">Top 5 concentra</div><div class="val">${top5Pct.toFixed(1)}%</div></div>
    <div class="kpi"><div class="lbl">Con signo raro</div><div class="val" style="${alertCount ? 'color:var(--cost)' : ''}">${alertCount}${alertCount ? ` (${fmtAbs(alertSum)} MXN)` : ''}</div></div>
  `;

  // botón "Reportar a Jeff" — solo si hay alertas
  const alertItems = items.filter(x => isAlert(x.valor));
  updateReportButton({
    row, contextLabel: ctx, alertItems, total, alertSum, vista: state.vista, mes,
    scope: 'single-month',
  });

  const tbody = document.getElementById('drillTbody');
  tbody.innerHTML = '';
  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="drill-empty">Sin NIDs en esta celda.</td></tr>`;
  } else {
    items.forEach((it, idx) => {
      const tr = document.createElement('tr');
      const alerted = isAlert(it.valor);
      if (alerted) tr.classList.add('alert');
      const pct = total !== 0 ? (it.valor / total * 100) : 0;
      const valCls = it.valor < 0 ? 'cost' : 'income';
      const regionLbl = state.region === 'Total' ? ` <span style="color:var(--muted); font-size:10px">· ${it.region}</span>` : '';
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td class="nid"><a href="https://tu.habi.mx/nid/${it.nid}" target="_blank" rel="noopener">${it.nid}</a>${regionLbl}</td>
        <td class="val ${valCls}">${fmtAbs(it.valor)}</td>
        <td class="pct">${pct.toFixed(1)}%</td>
        <td class="flag" title="${alerted ? 'Signo contrario al esperado (posible reversión / ajuste)' : ''}">${alerted ? '🚩' : ''}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  document.getElementById('drillOverlay').hidden = false;
  document.getElementById('drillPanel').hidden = false;
}

function closeDrill() {
  document.getElementById('drillOverlay').hidden = true;
  document.getElementById('drillPanel').hidden = true;
}

function setupDrill() {
  document.getElementById('drillClose').addEventListener('click', closeDrill);
  document.getElementById('drillOverlay').addEventListener('click', closeDrill);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrill();
  });
}

// ─── COMPARATIVA (tab 2) ──────────────────────────────────────────────

function cmpMesesRange() {
  // Devuelve la lista de meses YYYY-MM incluidos en el período elegido.
  // Basado en state.data.meses (los meses con datos).
  const all = state.data.meses.slice();
  if (state.cmpPeriodo === '3m') return all.slice(-3);
  if (state.cmpPeriodo === '6m') return all.slice(-6);
  if (state.cmpPeriodo === '12m') return all.slice(-12);
  if (state.cmpPeriodo === 'q') {
    // último Q completo: buscamos el último trimestre que tenga sus 3 meses todos presentes
    // trimestres = ['2024-01','2024-02','2024-03'] (Q1) etc
    const monthsSet = new Set(all);
    // partiendo del mes MÁS RECIENTE, buscamos hacia atrás el último Q completo
    const last = all[all.length - 1]; // 'YYYY-MM'
    let [year, m] = last.split('-').map(Number);
    // determinar Q del mes actual
    let q = Math.ceil(m / 3);
    // vamos hacia atrás hasta encontrar un Q completo (3 meses presentes)
    for (let attempts = 0; attempts < 8; attempts++) {
      const start = (q - 1) * 3 + 1;
      const months = [start, start + 1, start + 2].map(mm =>
        `${year}-${String(mm).padStart(2, '0')}`);
      if (months.every(x => monthsSet.has(x))) return months;
      q -= 1;
      if (q < 1) { q = 4; year -= 1; }
    }
    // fallback: últimos 3
    return all.slice(-3);
  }
  return all.slice(-3);
}

function cmpMesLabel(meses) {
  if (meses.length === 0) return '';
  if (meses.length === 1) return meses[0];
  return `${meses[0]} → ${meses[meses.length - 1]} (${meses.length} meses)`;
}

function renderCmpRegionCtrl() {
  const el = document.getElementById('cmpRegionCtrl');
  el.innerHTML = '';
  for (const r of state.data.regiones) {
    if (r.key === 'Total') continue;  // Total no es seleccionable, siempre se muestra al final
    const b = document.createElement('button');
    b.className = 'seg-btn' + (state.cmpRegiones.has(r.key) ? ' active' : '');
    b.dataset.region = r.key;
    b.textContent = r.label + ` (${r.filas.toLocaleString('es-MX')})`;
    b.addEventListener('click', () => {
      if (state.cmpRegiones.has(r.key)) state.cmpRegiones.delete(r.key);
      else state.cmpRegiones.add(r.key);
      b.classList.toggle('active');
      renderCmp();
    });
    el.appendChild(b);
  }
}

function setupCmpControls() {
  document.querySelectorAll('#cmpVistaCtrl .seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.cmpVista = b.dataset.vista;
      document.querySelectorAll('#cmpVistaCtrl .seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderCmp();
    });
  });
  document.querySelectorAll('#cmpPeriodoCtrl .seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.cmpPeriodo = b.dataset.periodo;
      document.querySelectorAll('#cmpPeriodoCtrl .seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderCmp();
    });
  });
  document.querySelectorAll('#cmpMetricaCtrl .seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.cmpMetrica = b.dataset.metrica;
      document.querySelectorAll('#cmpMetricaCtrl .seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderCmp();
    });
  });
}

// Suma los valores de las líneas del P&L para una región en un rango de meses
function sumRegionInRange(region, meses, vista) {
  const dataR = (state.data.vistas[vista] || {})[region] || {};
  const acc = {};
  for (const m of meses) {
    const row = dataR[m] || {};
    for (const k of Object.keys(row)) {
      acc[k] = (acc[k] || 0) + row[k];
    }
  }
  return acc;
}

// Genera cajitas de insight comparando regiones seleccionadas.
// `higherIsBetter` define si mayor valor = mejor (ingresos) o peor (costos).
// Cada tarjeta encuentra la región líder vs rezagada + delta.
function renderCmpInsights(regionesSel, sums) {
  const el = document.getElementById('cmpInsights');
  el.innerHTML = '';

  if (regionesSel.length < 2) {
    el.innerHTML = '<div class="insights-empty">Selecciona al menos 2 regiones para ver comparativas.</div>';
    return;
  }

  // KPIs a comparar: key, label, mode ('income' o 'cost'), normalize (siempre pct del revenue)
  const KPIS = [
    { key: 'gross_profit', label: 'Gross Profit', mode: 'income', norm: 'pct' },
    { key: 'gp_sin_iva', label: 'Gross Profit sin IVA', mode: 'income', norm: 'pct' },
    { key: 'direct_costs', label: 'Direct Costs', mode: 'cost', norm: 'pct' },
    { key: 'contribution_margin', label: 'Contribution Margin', mode: 'income', norm: 'pct' },
  ];

  // valor para comparación (respeta la métrica global salvo para NIDs que siempre es abs)
  const valueFor = (region, kpi) => {
    const raw = sums[region][kpi.key];
    if (raw === undefined || raw === null) return null;
    if (kpi.key === 'invoiced_sales') return raw;
    if (kpi.norm === 'pct') {
      const rev = sums[region]['gmv_sin_hc100'] || 0;
      if (!rev) return null;
      return raw / rev;
    }
    return raw;
  };

  for (const kpi of KPIS) {
    // calcular valores de cada región seleccionada
    const entries = [];
    for (const r of regionesSel) {
      const v = valueFor(r, kpi);
      if (v !== null && isFinite(v)) entries.push({ region: r, valor: v });
    }
    if (entries.length < 2) continue;

    // ordenar
    // income: mayor = mejor
    // cost: menor absoluto (menos negativo) = mejor
    let best, worst;
    if (kpi.mode === 'income') {
      entries.sort((a, b) => b.valor - a.valor);
      best = entries[0]; worst = entries[entries.length - 1];
    } else {
      // costos: los valores son negativos; menos negativo = mejor
      entries.sort((a, b) => b.valor - a.valor);   // desc: menos negativo primero
      best = entries[0]; worst = entries[entries.length - 1];
    }

    // delta
    let deltaStr, deltaCls, cardCls;
    if (kpi.norm === 'pct') {
      // pp (percentage points)
      const dpp = (best.valor - worst.valor) * 100;
      deltaStr = `${dpp > 0 ? '+' : ''}${dpp.toFixed(1)}pp`;
      deltaCls = 'up';
      cardCls = 'good';
    } else {
      // absoluto: %
      const denom = Math.abs(worst.valor);
      const pct = denom > 0 ? ((best.valor - worst.valor) / denom) * 100 : 0;
      deltaStr = `${pct > 0 ? '+' : ''}${pct.toFixed(0)}%`;
      deltaCls = 'up';
      cardCls = kpi.mode === 'income' ? 'good' : 'neutral';
    }

    // formato de valores
    const fmtV = kpi.fmtVal
      ? kpi.fmtVal
      : (v) => kpi.norm === 'pct' ? fmtPct(v) : fmt(v);
    const bestStr = fmtV(best.valor);
    const worstStr = fmtV(worst.valor);

    const headline = kpi.mode === 'income'
      ? `<span class="region">${best.region}</span> supera a <span class="region">${worst.region}</span> por <span class="delta ${deltaCls}">${deltaStr}</span>`
      : `<span class="region">${best.region}</span> es más eficiente que <span class="region">${worst.region}</span> por <span class="delta ${deltaCls}">${deltaStr}</span>`;

    const card = document.createElement('div');
    card.className = `insight-card ${cardCls}`;
    card.innerHTML = `
      <div class="insight-title">${kpi.label}</div>
      <div class="insight-headline">${headline}</div>
      <div class="insight-detail">
        <div class="row-pair">
          <span class="lbl">Mejor · ${best.region}</span>
          <span class="val">${bestStr}</span>
        </div>
        <span class="arrow">→</span>
        <div class="row-pair" style="text-align:right; align-items:flex-end">
          <span class="lbl">${entries.length > 2 ? 'Peor' : 'Otro'} · ${worst.region}</span>
          <span class="val">${worstStr}</span>
        </div>
      </div>
    `;
    el.appendChild(card);
  }

  if (!el.children.length) {
    el.innerHTML = '<div class="insights-empty">Sin datos suficientes para calcular comparativas en el período seleccionado.</div>';
  }
}

function renderCmp() {
  const meses = cmpMesesRange();
  document.getElementById('cmpContext').textContent =
    `${state.cmpVista === 'acc' ? 'ACC' : 'Sintético'} · ${cmpMesLabel(meses)} · ${state.cmpMetrica === 'abs' ? 'MXN 000\'s' : state.cmpMetrica === 'pct' ? '% del Revenue de la región' : 'MXN por NID facturado'}`;

  const regionesSel = state.data.regiones
    .filter(r => r.key !== 'Total' && state.cmpRegiones.has(r.key))
    .map(r => r.key);

  // sumar por región + Total
  const sums = {};
  for (const r of regionesSel) sums[r] = sumRegionInRange(r, meses, state.cmpVista);
  sums['Total'] = sumRegionInRange('Total', meses, state.cmpVista);

  // insights (comparaciones entre las regiones seleccionadas)
  renderCmpInsights(regionesSel, sums);

  // header
  const head = document.getElementById('cmpHead');
  head.innerHTML = '';
  head.appendChild(th('P&L'));
  for (const r of regionesSel) head.appendChild(th(r));
  head.appendChild(th('Total'));

  // body
  const body = document.getElementById('cmpBody');
  body.innerHTML = '';
  // en comparativa mostramos SOLO conceptos grandes (kpi / rubro / total),
  // no subcuentas ni grupos
  const BIG_TYPES = new Set(['kpi', 'rubro', 'total']);
  const structure = state.data.estructura.filter(row =>
    (!row.vista || row.vista === state.cmpVista) && BIG_TYPES.has(row.type)
  );

  const revenueByRegion = {};
  const nidsByRegion = {};
  for (const r of [...regionesSel, 'Total']) {
    revenueByRegion[r] = sums[r]['gmv_sin_hc100'] || 0;
    nidsByRegion[r] = sums[r]['invoiced_sales'] || 0;
  }

  const applyMetric = (val, region, row) => {
    if (val === null || val === undefined) return null;
    if (state.cmpMetrica === 'pct') {
      const base = revenueByRegion[region];
      if (!base || !isFinite(base) || row.sign === 'count') return null;
      return val / base;
    }
    if (state.cmpMetrica === 'per_nid') {
      const n = nidsByRegion[region];
      if (!n || row.sign === 'count') return null;
      return val / n;
    }
    return val;
  };

  const fmtCell = (val, row) => {
    if (val === null || val === undefined) return '—';
    if (row.sign === 'count') return Math.round(val).toLocaleString('es-MX');
    if (state.cmpMetrica === 'pct') return fmtPct(val);
    if (state.cmpMetrica === 'per_nid') return fmtAbs(val);
    return fmt(val);
  };

  for (const row of structure) {
    const tr = document.createElement('tr');
    tr.className = `tipo-${row.type}`;
    if (row.pendiente) tr.classList.add('pendiente');
    tr.appendChild(td(row.label));

    // valores por región seleccionada (para calcular best/worst)
    const valsBySelRegion = {};
    for (const r of regionesSel) valsBySelRegion[r] = applyMetric(sums[r][row.key] ?? null, r, row);

    // best / worst: solo para líneas con signo definido (no count / net)
    let best = null, worst = null;
    if (row.sign === 'cost' || row.sign === 'income') {
      const arr = Object.entries(valsBySelRegion).filter(([, v]) => v !== null && isFinite(v));
      if (arr.length >= 2) {
        // Para PCT y ABS: en cost queremos el "mejor" = menos negativo (menor magnitud)
        // Para PCT (que es negativo para costs), best = mayor (más cercano a 0)
        // Para income: best = mayor
        arr.sort(([, a], [, b]) => a - b);   // ascendente
        if (row.sign === 'cost') {
          best = arr[arr.length - 1][0];   // menos negativo
          worst = arr[0][0];                // más negativo
        } else {
          best = arr[arr.length - 1][0];   // más grande
          worst = arr[0][0];                // más pequeño
        }
      }
    }

    for (const r of regionesSel) {
      const raw = sums[r][row.key];
      const val = raw === undefined ? null : applyMetric(raw, r, row);
      const cell = document.createElement('td');
      cell.className = `region-col signo-${row.sign}`;
      if (r === best) cell.classList.add('best');
      if (r === worst) cell.classList.add('worst');
      cell.textContent = fmtCell(val, row);
      if (!NON_DRILLABLE.has(row.key) && !row.pendiente && val !== null && val !== 0) {
        cell.classList.add('clickable');
        cell.addEventListener('click', () => openCmpDrill(row, r, meses));
      }
      tr.appendChild(cell);
    }
    // total
    const totalRaw = sums['Total'][row.key];
    const totalVal = totalRaw === undefined ? null : applyMetric(totalRaw, 'Total', row);
    const totalCell = document.createElement('td');
    totalCell.className = `region-col total-col signo-${row.sign}`;
    totalCell.textContent = fmtCell(totalVal, row);
    tr.appendChild(totalCell);

    body.appendChild(tr);
  }
}

// Drill desde la comparativa: sumamos NIDs a lo largo del período elegido
function openCmpDrill(row, region, meses) {
  const facts = state.facts[state.cmpVista];
  const colIdx = facts.columnas.indexOf(row.key);
  if (colIdx < 0) return;

  const mesSet = new Set(meses);
  // agrupar por NID sumando el valor en todos los meses del período
  const agg = new Map();
  const totalNids = facts.nid.length;
  for (let i = 0; i < totalNids; i++) {
    if (!mesSet.has(facts.mes[i])) continue;
    if (region !== 'Total' && facts.region[i] !== region) continue;
    const v = facts.valores[colIdx][i];
    if (v === 0) continue;
    const key = facts.nid[i];
    if (!agg.has(key)) {
      agg.set(key, { nid: facts.nid[i], region: facts.region[i], valor: 0, meses: [] });
    }
    const e = agg.get(key);
    e.valor += v;
    e.meses.push(facts.mes[i]);
  }
  const items = [...agg.values()].filter(x => x.valor !== 0);
  items.sort((a, b) => Math.abs(b.valor) - Math.abs(a.valor));

  const total = items.reduce((s, x) => s + x.valor, 0);
  const isAlert = (v) => {
    if (row.sign === 'cost' && v > 0) return true;
    if (row.sign === 'income' && v < 0) return true;
    return false;
  };
  const alertCount = items.filter(x => isAlert(x.valor)).length;
  const alertSum = items.filter(x => isAlert(x.valor)).reduce((s, x) => s + x.valor, 0);

  const cmpCtxLabel = `${region} · ${cmpMesLabel(meses)}`;
  document.getElementById('drillContext').textContent = cmpCtxLabel;
  document.getElementById('drillTitle').textContent = row.label;

  const totalCls = row.sign === 'cost' ? 'cost' : (row.sign === 'income' ? 'income' : '');
  document.getElementById('drillTotal').innerHTML =
    `<span class="amount ${totalCls}">${fmtAbs(total)}</span><span class="lbl">MXN · ${items.length.toLocaleString('es-MX')} NIDs (agregado)</span>`;

  const summary = document.getElementById('drillSummary');
  const top5Sum = items.slice(0, 5).reduce((s, x) => s + x.valor, 0);
  const top5Pct = total !== 0 ? (top5Sum / total * 100) : 0;
  summary.innerHTML = `
    <div class="kpi"><div class="lbl">NIDs con gasto</div><div class="val">${items.length.toLocaleString('es-MX')}</div></div>
    <div class="kpi"><div class="lbl">Top 5 concentra</div><div class="val">${top5Pct.toFixed(1)}%</div></div>
    <div class="kpi"><div class="lbl">Con signo raro</div><div class="val" style="${alertCount ? 'color:var(--cost)' : ''}">${alertCount}${alertCount ? ` (${fmtAbs(alertSum)} MXN)` : ''}</div></div>
  `;

  // botón "Reportar a Jeff"
  const alertItems = items.filter(x => isAlert(x.valor));
  updateReportButton({
    row, contextLabel: cmpCtxLabel, alertItems, total, alertSum, vista: state.cmpVista, meses,
    scope: 'range',
  });

  const tbody = document.getElementById('drillTbody');
  tbody.innerHTML = '';
  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="drill-empty">Sin NIDs en esta celda.</td></tr>`;
  } else {
    items.forEach((it, idx) => {
      const tr = document.createElement('tr');
      const alerted = isAlert(it.valor);
      if (alerted) tr.classList.add('alert');
      const pct = total !== 0 ? (it.valor / total * 100) : 0;
      const valCls = it.valor < 0 ? 'cost' : 'income';
      const regionLbl = region === 'Total' ? ` <span style="color:var(--muted); font-size:10px">· ${it.region}</span>` : '';
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td class="nid"><a href="https://tu.habi.mx/nid/${it.nid}" target="_blank" rel="noopener">${it.nid}</a>${regionLbl}</td>
        <td class="val ${valCls}">${fmtAbs(it.valor)}</td>
        <td class="pct">${pct.toFixed(1)}%</td>
        <td class="flag" title="${alerted ? 'Signo contrario al esperado' : ''}">${alerted ? '🚩' : ''}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  document.getElementById('drillOverlay').hidden = false;
  document.getElementById('drillPanel').hidden = false;
}

// ─── REPORTAR A JEFF ─────────────────────────────────────────────────

function updateReportButton(ctx) {
  const actionsEl = document.getElementById('drillActions');
  const btn = document.getElementById('reportBtn');
  const note = document.getElementById('reportNote');

  if (!ctx.alertItems || ctx.alertItems.length === 0) {
    actionsEl.hidden = true;
    state.lastDrill = null;
    return;
  }
  state.lastDrill = ctx;
  actionsEl.hidden = false;

  const n = ctx.alertItems.length;
  const sumAbs = fmtAbs(ctx.alertSum);
  btn.textContent = `🚩 Reportar ${n} NID${n === 1 ? '' : 's'} a Jeff`;

  if (!CHAT_WEBHOOK_URL) {
    btn.disabled = true;
    note.innerHTML = `Webhook no configurado. Pega la URL en <code>CHAT_WEBHOOK_URL</code> (app.js) para activar.`;
  } else {
    btn.disabled = false;
    note.textContent = `Se enviará al chat de Jeff: contexto + ${n} NID${n === 1 ? '' : 's'} (${sumAbs} MXN).`;
  }
}

function buildReportMessage(ctx) {
  const { row, contextLabel, alertItems, alertSum, vista } = ctx;
  const vistaLbl = vista === 'acc' ? 'ACC (Accounting)' : 'Sintético';

  const sign = row.sign === 'cost' ? 'gasto que aparece positivo (posible reversión/crédito)'
            : row.sign === 'income' ? 'ingreso que aparece negativo (posible devolución)'
            : 'signo contrario al esperado';

  const top = alertItems.slice(0, 20);
  const lines = top.map((x, i) => {
    const regionSuffix = ctx.scope === 'range' ? ` · ${x.region}` : '';
    return `${i + 1}. NID *${x.nid}*${regionSuffix} · ${fmtAbs(x.valor)} MXN`;
  }).join('\n');

  const extra = alertItems.length > top.length
    ? `\n_(+${alertItems.length - top.length} NIDs más no listados)_`
    : '';

  const text =
`🚩 *NIDs con signo raro · P&L MX*
*Contexto:* ${contextLabel}
*Línea:* ${row.label} · ${vistaLbl}
*Anomalía:* ${sign}
*Total anómalo:* ${fmtAbs(alertSum)} MXN en ${alertItems.length} NID${alertItems.length === 1 ? '' : 's'}

${lines}${extra}

_Reportado por ${REPORTER_NAME}_
Dashboard: ${window.location.origin}${window.location.pathname}`;

  return { text };
}

function showToast(msg, kind = '') {
  let el = document.getElementById('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = `toast show ${kind}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 3500);
}

async function sendReport() {
  if (!state.lastDrill) return;
  if (!CHAT_WEBHOOK_URL) {
    showToast('Webhook no configurado', 'error');
    return;
  }
  const btn = document.getElementById('reportBtn');
  btn.disabled = true;
  btn.textContent = 'Enviando…';

  const payload = buildReportMessage(state.lastDrill);
  try {
    // Google Chat webhooks aceptan simple request (text/plain) con body JSON.
    // Usamos no-cors porque la respuesta no la necesitamos leer y así evitamos
    // preflight CORS que Google Chat no soporta bien desde browser.
    await fetch(CHAT_WEBHOOK_URL, {
      method: 'POST',
      mode: 'no-cors',
      headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
      body: JSON.stringify(payload),
    });
    showToast('Reportado a Jeff ✓', 'success');
    btn.textContent = '✓ Enviado';
    setTimeout(() => updateReportButton(state.lastDrill), 2500);
  } catch (err) {
    console.error('Error enviando reporte:', err);
    showToast('Error al enviar. Revisa la consola.', 'error');
    updateReportButton(state.lastDrill);
  }
}

function setupReportButton() {
  document.getElementById('reportBtn').addEventListener('click', sendReport);
}

// ─── init ─────────────────────────────────────────────────────────────
async function init() {
  await loadData();
  renderHeader();
  renderRegionCtrl();
  setupControls();
  setupTabs();
  setupCmpControls();
  renderCmpRegionCtrl();
  setupDrill();
  setupReportButton();
  renderTable();
}

if (setupLogin()) {
  init();
}
