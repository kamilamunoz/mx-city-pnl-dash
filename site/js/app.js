// mx-city-pnl-dash — frontend
// Kamila mantiene sola. Vanilla JS, sin frameworks.

const PASSWORD = 'p&L_M3x1c0*C12d4d';
const STORAGE_KEY = 'mx-pnl-auth';

// Webhook de Google Chat para reportar NIDs con signo raro a Jeff.
// Para rotarlo: Google Chat > Space > Manage webhooks > Regenerate URL, y pegar la nueva URL aquí.
const CHAT_WEBHOOK_URL = 'https://chat.googleapis.com/v1/spaces/AAQAH51TuFM/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=iHJpuzPgIZavlQIBLx6OAQe0MZ57xys6-8iPjHGZfbE';
const REPORTER_NAME = 'Kamila (dashboard MX P&L)';

const state = {
  data: null,          // kpi_pnl.json
  facts: null,         // kpi_pnl_facts.json
  consData: null,      // kpi_pnl_consolidated.json (MM + Inmo + Local OpEx)
  corpFacts: null,     // kpi_pnl_corp_facts.json (drill de OpEx Corp por tercero)
  // último drill abierto (para el botón de reporte)
  lastDrill: null,     // { row, contextLabel, alertItems, total, vista }
  // tab 1: P&L por región
  vista: 'acc',
  region: 'Total',
  rango: '12',         // '6' | '12' | 'all' | 'year' | 'range'
  year: null,          // int (cuando rango==='year')
  rangeFrom: null,     // 'YYYY-MM' (cuando rango==='range')
  rangeTo: null,       // 'YYYY-MM'
  // rubros/grupos expandidos (drill-down por niveles). Vacío = todo colapsado.
  expanded: new Set(),
  // tab 2: comparativa
  activeTab: 'pnl',
  cmpSource: 'mm',       // 'mm' | 'consolidado'
  cmpVista: 'acc',
  cmpPeriodo: '3m',
  cmpRegiones: new Set(),
  cmpMetrica: 'abs',
  // tab 3: consolidado MM + Inmo
  consVista: 'acc',
  consRegion: 'Total',
  consRango: '12',        // '6' | '12' | 'all' | 'year' | 'range'
  consYear: null,
  consRangeFrom: null,
  consRangeTo: null,
  consExpanded: new Set(),
};

// líneas NO clickables (son sumas o counts, no tienen NIDs propios)
const NON_DRILLABLE = new Set(['invoiced_sales', 'fee_hc100']);

// Sub-líneas de Corp OpEx que abren drill-down por tercero (nuevo modal alternativo).
const CORP_OPEX_DRILLABLE_KEYS = new Set([
  'corp_opex_sales_ops', 'corp_opex_tech', 'corp_opex_prof_fees',
  'corp_opex_courier', 'corp_opex_travel', 'corp_opex_empl_rel',
  'corp_opex_other', 'corp_opex_nacional',
]);

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
  const [pnl, facts, cons, corpFacts] = await Promise.all([
    fetch(`data/kpi_pnl.json?v=${Date.now()}`).then(r => r.json()),
    fetch(`data/kpi_pnl_facts.json?v=${Date.now()}`).then(r => r.json()),
    fetch(`data/kpi_pnl_consolidated.json?v=${Date.now()}`).then(r => r.ok ? r.json() : null).catch(() => null),
    fetch(`data/kpi_pnl_corp_facts.json?v=${Date.now()}`).then(r => r.ok ? r.json() : null).catch(() => null),
  ]);
  state.data = pnl;
  state.facts = facts;
  state.consData = cons;
  state.corpFacts = corpFacts;

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
      document.getElementById('tab-consolidado').hidden = t !== 'consolidado';
      if (t === 'comparativa') renderCmp();
      if (t === 'consolidado') renderConsolidated();
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

  // Índice y set de rows con hijos → habilita el toggle expand/collapse.
  const byKey = {};
  for (const r of structure) byKey[r.key] = r;
  const hasChildren = new Set();
  for (const r of structure) if (r.parent) hasChildren.add(r.parent);

  // Una row es visible sólo si toda su cadena de ancestros está expandida.
  const chainExpanded = (row) => {
    let cur = row;
    while (cur && cur.parent) {
      if (!state.expanded.has(cur.parent)) return false;
      cur = byKey[cur.parent];
    }
    return true;
  };

  // `only_total`: sublíneas de rent que no cuadran con una región individual
  // (WeWork mezcla NL+JAL, Nacional son servicios sin ciudad) — se ocultan
  // salvo en la vista consolidada.
  const isTotalView = state.region === 'Total';
  const filaVisible = (row) =>
    (!row.vista || row.vista === state.vista) &&
    (!row.only_total || isTotalView) &&
    chainExpanded(row);
  const structureFiltered = structure.filter(filaVisible);

  const head = document.getElementById('pnlHead');
  head.innerHTML = '';
  head.appendChild(th('P&L (MXN 000s)'));
  for (const m of meses) head.appendChild(th(m));

  const body = document.getElementById('pnlBody');
  body.innerHTML = '';

  const revByMonth = {};
  for (const m of meses) revByMonth[m] = (dataRegion[m] || {})['gmv_habi'] || 0;

  const showPctRow = (row) => !['invoiced_sales', 'gmv_habi', 'fee_hc100', 'gmv_sin_hc100'].includes(row.key);

  for (const row of structureFiltered) {
    const tr = document.createElement('tr');
    tr.className = `tipo-${row.type}`;
    if (row.pendiente) tr.classList.add('pendiente');

    // Primera celda: etiqueta + toggle si es expandible (rubro/grupo con hijos)
    // + ícono ⓘ con tooltip si la row tiene `note` en el schema.
    const labelTd = document.createElement('td');
    if (hasChildren.has(row.key)) {
      const isExp = state.expanded.has(row.key);
      const tog = document.createElement('span');
      tog.className = 'toggle';
      tog.textContent = isExp ? '▼' : '▶';
      labelTd.appendChild(tog);
      labelTd.appendChild(document.createTextNode(' ' + row.label));
      labelTd.classList.add('expandable');
      labelTd.addEventListener('click', () => {
        if (state.expanded.has(row.key)) state.expanded.delete(row.key);
        else state.expanded.add(row.key);
        renderTable();
      });
    } else {
      labelTd.textContent = row.label;
    }
    if (row.note) {
      const info = document.createElement('span');
      info.className = 'row-note';
      info.textContent = 'ⓘ';
      info.title = row.note;
      labelTd.appendChild(info);
    }
    tr.appendChild(labelTd);

    for (const m of meses) {
      const cell = (dataRegion[m] || {})[row.key];
      const val = cell === undefined ? null : cell;
      const isCount = row.sign === 'count';
      const cellEl = document.createElement('td');
      cellEl.classList.add(`signo-${row.sign}`);

      // Highlight verde para valores POSITIVOS en líneas total con sign=net
      // (gross_profit, gp_sin_iva, unlevered_profit, contribution_margin,
      // net_city_contribution). Lectura rápida de ganancia.
      if (row.type === 'total' && row.sign === 'net' && val !== null && val > 0) {
        cellEl.classList.add('positivo-highlight');
      }

      if (showPctRow(row) && val !== null && revByMonth[m]) {
        const pct = val / revByMonth[m];
        cellEl.innerHTML = `${fmt(val, isCount)}<br><span class="pct">${fmtPct(pct)}</span>`;
      } else {
        cellEl.textContent = fmt(val, isCount);
      }

      // Tooltip para meses sin payroll (o local_opex incompleto)
      if (row.extern && val === null) {
        cellEl.title = 'Sin dato en fuente externa (Lis/Danibot) para este mes';
      }
      // Tooltip específico: payroll = 0 para regiones sin sede en BBDD (HIDALGO tras la fusión CDMX→EDO MEX)
      const sinSede = ((state.data.meta || {}).local_opex || {}).payroll_regiones_sin_sede || [];
      if (row.key === 'payroll_local' && val === 0 && sinSede.includes(state.region)) {
        cellEl.title = 'Sin sede propia en BBDD para esta región';
      }

      // Líneas externas (payroll/rent/marketing/local_opex/net_contrib) no drillean
      const drillable = !NON_DRILLABLE.has(row.key)
        && !row.pendiente
        && !row.extern
        && val !== null && val !== 0;
      if (drillable) {
        cellEl.classList.add('clickable');
        cellEl.addEventListener('click', () => openDrill(row, m));
      }
      tr.appendChild(cellEl);
    }
    body.appendChild(tr);
  }

  const pend = structureFiltered.filter(r => r.pendiente).map(r => r.label);
  const localOpexMeta = (state.data.meta || {}).local_opex || {};
  const notas = [];
  if (pend.length) {
    notas.push(`<span style="color:var(--warn)">⚠</span> Líneas pendientes: <b>${pend.join(', ')}</b>.`);
  }
  if (localOpexMeta.payroll_cobertura_hasta || localOpexMeta.rent_cobertura_hasta || localOpexMeta.marketing_cobertura_hasta || localOpexMeta.corp_opex_cobertura_hasta) {
    const bits = [];
    if (localOpexMeta.payroll_cobertura_hasta) bits.push(`payroll hasta ${localOpexMeta.payroll_cobertura_hasta} (Lis)`);
    if (localOpexMeta.rent_cobertura_hasta) bits.push(`rent hasta ${localOpexMeta.rent_cobertura_hasta} (Danibot, FX ${localOpexMeta.fx_mxn_per_usd})`);
    if (localOpexMeta.marketing_cobertura_hasta) bits.push(`marketing hasta ${localOpexMeta.marketing_cobertura_hasta} (BQ · FX ${localOpexMeta.fx_mxn_per_usd})`);
    if (localOpexMeta.corp_opex_cobertura_hasta) bits.push(`corp opex hasta ${localOpexMeta.corp_opex_cobertura_hasta} (bet_data_p2)`);
    notas.push(`<span style="color:var(--muted)">ⓘ</span> Local OpEx: ${bits.join(' · ')}. Meses posteriores = '—'.`);
  }
  document.getElementById('pendienteNota').innerHTML = notas.join('<br>');
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

  // Restaurar tabla NID + limpiar tabla Corp si quedó de un drill anterior
  const body = document.querySelector('.drill-body');
  const nidTable = body.querySelector('.drill-table:not(.drill-corp-table)');
  if (nidTable) nidTable.style.display = '';
  const corpTable = body.querySelector('.drill-corp-table');
  if (corpTable) corpTable.remove();

  // Base de % por-NID = gmv_habi (con fee HC100 incluido).
  const gmvIdx = facts.columnas.indexOf('gmv_habi');

  const items = [];
  const totalNids = facts.nid.length;
  for (let i = 0; i < totalNids; i++) {
    const matchRegion = (state.region === 'Total') || (facts.region[i] === state.region);
    const matchMes = facts.mes[i] === mes;
    if (!matchRegion || !matchMes) continue;
    const v = facts.valores[colIdx][i];
    if (v === 0) continue;
    const gmv = gmvIdx >= 0 ? facts.valores[gmvIdx][i] : 0;
    items.push({ nid: facts.nid[i], region: facts.region[i], valor: v, gmv });
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
    tbody.innerHTML = `<tr><td colspan="7" class="drill-empty">Sin NIDs en esta celda.</td></tr>`;
  } else {
    items.forEach((it, idx) => {
      const tr = document.createElement('tr');
      const alerted = isAlert(it.valor);
      if (alerted) tr.classList.add('alert');
      const pctLinea = total !== 0 ? (it.valor / total * 100) : 0;
      // % del GMV del NID individual — key insight solicitado por Kamila
      const pctGmv = (it.gmv && it.gmv !== 0) ? (it.valor / it.gmv * 100) : null;
      const valCls = it.valor < 0 ? 'cost' : 'income';
      const regionLbl = state.region === 'Total' ? ` <span style="color:var(--muted); font-size:10px">· ${it.region}</span>` : '';
      const gmvStr = it.gmv ? fmtAbs(it.gmv) : '—';
      const pctGmvStr = pctGmv !== null
        ? `<span class="pct-strong">${pctGmv >= 0 ? '+' : ''}${pctGmv.toFixed(1)}%</span>`
        : '—';
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td class="nid"><a href="https://tu.habi.mx/nid/${it.nid}" target="_blank" rel="noopener">${it.nid}</a>${regionLbl}</td>
        <td class="val ${valCls}">${fmtAbs(it.valor)}</td>
        <td class="val">${gmvStr}</td>
        <td class="pct">${pctGmvStr}</td>
        <td class="pct">${pctLinea.toFixed(1)}%</td>
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

// Drill de Corp OpEx: reusa el drillPanel pero renderiza tabla por tercero.
// `key` = sub-métrica (ej: 'corp_opex_travel'). `mes` = 'YYYY-MM'. `region` = key región.
function openCorpDrill(row, mes, region) {
  if (!state.corpFacts) return;
  const entries = ((state.corpFacts.data || {})[region] || {})[mes]?.[row.key] || [];
  const total = entries.reduce((s, e) => s + (e.monto || 0), 0);

  const regionLabel = (state.consData.regiones.find(r => r.key === region) || {}).label || region;
  document.getElementById('drillContext').textContent = `${regionLabel} · ${mes}`;
  document.getElementById('drillTitle').textContent = row.label;
  document.getElementById('drillTotal').innerHTML =
    `Total: <b>${fmt(total, false)}</b> MXN · ${entries.length} tercero${entries.length === 1 ? '' : 's'}`;
  document.getElementById('drillSummary').innerHTML = '';
  document.getElementById('drillActions').hidden = true;

  // Ocultar tabla NID original y (re-)crear tabla Corp
  const body = document.querySelector('.drill-body');
  const nidTable = body.querySelector('.drill-table:not(.drill-corp-table)');
  if (nidTable) nidTable.style.display = 'none';
  const oldCorpTable = body.querySelector('.drill-corp-table');
  if (oldCorpTable) oldCorpTable.remove();
  const table = document.createElement('table');
  table.className = 'drill-table drill-corp-table';
  table.innerHTML = `
    <thead>
      <tr>
        <th style="text-align:left">#</th>
        <th style="text-align:left">Tercero</th>
        <th style="text-align:left">Cuenta</th>
        <th style="text-align:left">Descripción</th>
        <th style="text-align:right"># filas</th>
        <th style="text-align:right">Monto (MXN)</th>
        <th style="text-align:right">% del total</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  body.appendChild(table);
  const tbody = table.querySelector('tbody');
  if (entries.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="drill-empty">Sin terceros en esta celda.</td></tr>`;
  } else {
    entries.forEach((e, i) => {
      const tr = document.createElement('tr');
      const pct = total !== 0 ? (e.monto / total * 100) : 0;
      const cleanDesc = (e.cuenta_desc || '').replace(/^\d+\.\s*Cuenta:\s*\d+\s*/, '').replace(/^\d+\.\s*Explicación\s*Other:\s*/, '');
      tr.innerHTML = `
        <td>${i + 1}</td>
        <td>${e.tercero || '(sin tercero)'}</td>
        <td>${e.cuenta || ''}</td>
        <td class="small">${cleanDesc}</td>
        <td style="text-align:right">${e.filas}</td>
        <td style="text-align:right">${fmt(e.monto, false)}</td>
        <td style="text-align:right">${pct.toFixed(1)}%</td>
      `;
      tbody.appendChild(tr);
    });
  }

  document.getElementById('drillOverlay').hidden = false;
  document.getElementById('drillPanel').hidden = false;
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

// Config por fuente para la tab de comparativa. `state.cmpSource` decide cuál se usa.
function cmpSourceConfig() {
  if (state.cmpSource === 'consolidado' && state.consData) {
    return {
      data: state.consData,
      revenueKey: 'cons_gmv_total',
      nidsKey: 'cons_props_total',
      pctExcluded: new Set([
        'cons_props_mm', 'cons_props_inmo', 'cons_props_total',
        'cons_gmv_mm', 'cons_gmv_inmo', 'cons_gmv_total',
      ]),
      insightKPIs: [
        { key: 'cons_cm_mm',           label: 'CM MM',                mode: 'income', norm: 'pct' },
        { key: 'cons_cm_inmo',         label: 'CM Inmo',              mode: 'income', norm: 'pct' },
        { key: 'cons_cm_total',        label: 'CM Total',             mode: 'income', norm: 'pct' },
        { key: 'net_city_contribution', label: 'Net City Contribution', mode: 'income', norm: 'pct' },
      ],
      profitLines: new Set(['cons_cm_mm', 'cons_cm_inmo', 'cons_cm_total', 'net_city_contribution']),
      drillable: false, // consolidado no tiene facts por-NID
    };
  }
  return {
    data: state.data,
    revenueKey: 'gmv_habi',
    nidsKey: 'invoiced_sales',
    pctExcluded: new Set(['invoiced_sales', 'gmv_habi', 'fee_hc100', 'gmv_sin_hc100']),
    insightKPIs: [
      { key: 'gross_profit',       label: 'Gross Profit',         mode: 'income', norm: 'pct' },
      { key: 'gp_sin_iva',         label: 'Gross Profit sin IVA', mode: 'income', norm: 'pct' },
      { key: 'direct_costs',       label: 'Direct Costs',         mode: 'cost',   norm: 'pct' },
      { key: 'contribution_margin', label: 'Contribution Margin', mode: 'income', norm: 'pct' },
    ],
    profitLines: new Set(),   // usa el criterio genérico type=total & sign=net
    drillable: true,
  };
}

function renderCmpRegionCtrl() {
  const el = document.getElementById('cmpRegionCtrl');
  el.innerHTML = '';
  const cfg = cmpSourceConfig();
  for (const r of cfg.data.regiones) {
    if (r.key === 'Total') continue;  // Total no es seleccionable, siempre se muestra al final
    const b = document.createElement('button');
    b.className = 'seg-btn' + (state.cmpRegiones.has(r.key) ? ' active' : '');
    b.dataset.region = r.key;
    const filasStr = r.filas ? ` (${r.filas.toLocaleString('es-MX')})` : '';
    b.textContent = r.label + filasStr;
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
  document.querySelectorAll('#cmpSourceCtrl .seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.cmpSource = b.dataset.source;
      document.querySelectorAll('#cmpSourceCtrl .seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      // Rebuild region control (mismas 7 canónicas, pero por si acaso)
      renderCmpRegionCtrl();
      renderCmp();
    });
  });
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

// Suma los valores de las líneas del P&L para una región en un rango de meses.
// `dataObj` puede ser state.data (MM) o state.consData (Consolidado).
function sumRegionInRange(region, meses, vista, dataObj) {
  dataObj = dataObj || state.data;
  const dataR = (dataObj.vistas[vista] || {})[region] || {};
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
function renderCmpInsights(regionesSel, sums, cfg) {
  const el = document.getElementById('cmpInsights');
  el.innerHTML = '';

  if (regionesSel.length < 2) {
    el.innerHTML = '<div class="insights-empty">Selecciona al menos 2 regiones para ver comparativas.</div>';
    return;
  }

  const KPIS = cfg.insightKPIs;
  const revenueKey = cfg.revenueKey;

  // valor para comparación (respeta la métrica global salvo para NIDs que siempre es abs)
  const valueFor = (region, kpi) => {
    const raw = sums[region][kpi.key];
    if (raw === undefined || raw === null) return null;
    if (kpi.norm === 'pct') {
      const rev = sums[region][revenueKey] || 0;
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
  const cfg = cmpSourceConfig();
  const meses = cmpMesesRange();
  const sourceLabel = state.cmpSource === 'consolidado' ? 'Consolidado (MM + Inmo)' : 'MM';
  document.getElementById('cmpContext').textContent =
    `${sourceLabel} · ${state.cmpVista === 'acc' ? 'ACC' : 'Sintético'} · ${cmpMesLabel(meses)} · ${state.cmpMetrica === 'abs' ? 'MXN 000\'s (con % del Revenue debajo)' : state.cmpMetrica === 'pct' ? '% del Revenue de la región' : 'MXN por transacción'}`;

  const regionesSel = cfg.data.regiones
    .filter(r => r.key !== 'Total' && state.cmpRegiones.has(r.key))
    .map(r => r.key);

  // sumar por región + Total
  const sums = {};
  for (const r of regionesSel) sums[r] = sumRegionInRange(r, meses, state.cmpVista, cfg.data);
  sums['Total'] = sumRegionInRange('Total', meses, state.cmpVista, cfg.data);

  // insights (comparaciones entre las regiones seleccionadas)
  renderCmpInsights(regionesSel, sums, cfg);

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
  const structure = cfg.data.estructura.filter(row =>
    (!row.vista || row.vista === state.cmpVista) && BIG_TYPES.has(row.type)
  );

  const revenueByRegion = {};
  const nidsByRegion = {};
  for (const r of [...regionesSel, 'Total']) {
    revenueByRegion[r] = sums[r][cfg.revenueKey] || 0;
    nidsByRegion[r] = sums[r][cfg.nidsKey] || 0;
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

  // Cuando la métrica activa es 'abs', mostrar también el % del Revenue debajo
  // (mismo patrón que la tabla principal). Base = revenueKey del config.
  const showPctBelow = state.cmpMetrica === 'abs';
  const pctExcluded = cfg.pctExcluded;
  const renderCellValue = (rawVal, val, row, region) => {
    if (val === null || val === undefined) return '—';
    const base = fmtCell(val, row);
    if (!showPctBelow) return base;
    if (pctExcluded.has(row.key) || row.sign === 'count') return base;
    const rev = revenueByRegion[region];
    if (!rev || !isFinite(rev)) return base;
    const pct = rawVal / rev;
    return `${base}<br><span class="pct">${fmtPct(pct)}</span>`;
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

    // Highlight verde: (a) totales netos genéricos, (b) profitLines del config
    // (para consolidado: CM MM, CM Inmo, CM Total, Net City Contribution).
    const isProfitTotal =
      (row.type === 'total' && row.sign === 'net') ||
      cfg.profitLines.has(row.key);

    for (const r of regionesSel) {
      const raw = sums[r][row.key];
      const val = raw === undefined ? null : applyMetric(raw, r, row);
      const cell = document.createElement('td');
      cell.className = `region-col signo-${row.sign}`;
      if (r === best) cell.classList.add('best');
      if (r === worst) cell.classList.add('worst');
      if (isProfitTotal && val !== null && val > 0) cell.classList.add('positivo-highlight');
      cell.innerHTML = renderCellValue(raw, val, row, r);
      // Drill solo disponible en fuente MM (consolidado no tiene facts por-NID)
      if (cfg.drillable && !NON_DRILLABLE.has(row.key) && !row.pendiente && val !== null && val !== 0) {
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
    if (isProfitTotal && totalVal !== null && totalVal > 0) totalCell.classList.add('positivo-highlight');
    totalCell.innerHTML = renderCellValue(totalRaw, totalVal, row, 'Total');
    tr.appendChild(totalCell);

    body.appendChild(tr);
  }
}

// Drill desde la comparativa: sumamos NIDs a lo largo del período elegido
function openCmpDrill(row, region, meses) {
  const facts = state.facts[state.cmpVista];
  const colIdx = facts.columnas.indexOf(row.key);
  if (colIdx < 0) return;

  // Base de % por-NID = gmv_habi (con fee HC100 incluido).
  const gmvIdx = facts.columnas.indexOf('gmv_habi');

  const mesSet = new Set(meses);
  // agrupar por NID sumando el valor Y el GMV en todos los meses del período
  const agg = new Map();
  const totalNids = facts.nid.length;
  for (let i = 0; i < totalNids; i++) {
    if (!mesSet.has(facts.mes[i])) continue;
    if (region !== 'Total' && facts.region[i] !== region) continue;
    const v = facts.valores[colIdx][i];
    if (v === 0) continue;
    const key = facts.nid[i];
    if (!agg.has(key)) {
      agg.set(key, { nid: facts.nid[i], region: facts.region[i], valor: 0, gmv: 0, meses: [] });
    }
    const e = agg.get(key);
    e.valor += v;
    if (gmvIdx >= 0) e.gmv += facts.valores[gmvIdx][i];
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
    tbody.innerHTML = `<tr><td colspan="7" class="drill-empty">Sin NIDs en esta celda.</td></tr>`;
  } else {
    items.forEach((it, idx) => {
      const tr = document.createElement('tr');
      const alerted = isAlert(it.valor);
      if (alerted) tr.classList.add('alert');
      const pctLinea = total !== 0 ? (it.valor / total * 100) : 0;
      const pctGmv = (it.gmv && it.gmv !== 0) ? (it.valor / it.gmv * 100) : null;
      const valCls = it.valor < 0 ? 'cost' : 'income';
      const regionLbl = region === 'Total' ? ` <span style="color:var(--muted); font-size:10px">· ${it.region}</span>` : '';
      const gmvStr = it.gmv ? fmtAbs(it.gmv) : '—';
      const pctGmvStr = pctGmv !== null
        ? `<span class="pct-strong">${pctGmv >= 0 ? '+' : ''}${pctGmv.toFixed(1)}%</span>`
        : '—';
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td class="nid"><a href="https://tu.habi.mx/nid/${it.nid}" target="_blank" rel="noopener">${it.nid}</a>${regionLbl}</td>
        <td class="val ${valCls}">${fmtAbs(it.valor)}</td>
        <td class="val">${gmvStr}</td>
        <td class="pct">${pctGmvStr}</td>
        <td class="pct">${pctLinea.toFixed(1)}%</td>
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

// ─── tab 3: consolidado MM + Inmo ─────────────────────────────────────
function renderConsRegionCtrl() {
  const el = document.getElementById('consRegionCtrl');
  if (!el || !state.consData) return;
  el.innerHTML = '';
  for (const r of state.consData.regiones) {
    const b = document.createElement('button');
    b.className = 'seg-btn' + (r.key === state.consRegion ? ' active' : '');
    b.dataset.region = r.key;
    b.textContent = r.label;
    b.addEventListener('click', () => {
      state.consRegion = r.key;
      el.querySelectorAll('.seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderConsolidated();
    });
    el.appendChild(b);
  }
}

function setupConsControls() {
  if (!state.consData) return;
  document.querySelectorAll('#consVistaCtrl .seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.consVista = b.dataset.vista;
      document.querySelectorAll('#consVistaCtrl .seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderConsolidated();
    });
  });
  document.querySelectorAll('#consRangoCtrl .seg-btn').forEach(b => {
    b.addEventListener('click', () => {
      state.consRango = b.dataset.rango;
      document.querySelectorAll('#consRangoCtrl .seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      document.getElementById('consYearSubCtrl').hidden = state.consRango !== 'year';
      document.getElementById('consRangeSubCtrl').hidden = state.consRango !== 'range';
      renderConsolidated();
    });
  });

  // sub-control: año
  const yearCtrl = document.getElementById('consYearCtrl');
  const years = Array.from(new Set(state.consData.meses.map(m => m.slice(0, 4)))).sort();
  state.consYear = state.consYear || years[years.length - 1];
  yearCtrl.innerHTML = '';
  for (const y of years) {
    const b = document.createElement('button');
    b.className = 'seg-btn' + (y === state.consYear ? ' active' : '');
    b.dataset.year = y;
    b.textContent = y;
    b.addEventListener('click', () => {
      state.consYear = y;
      yearCtrl.querySelectorAll('.seg-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      renderConsolidated();
    });
    yearCtrl.appendChild(b);
  }

  // sub-control: rango
  const fromSel = document.getElementById('consRangeFrom');
  const toSel = document.getElementById('consRangeTo');
  const opts = state.consData.meses.map(m => `<option value="${m}">${m}</option>`).join('');
  fromSel.innerHTML = opts;
  toSel.innerHTML = opts;
  state.consRangeFrom = state.consRangeFrom || state.consData.meses[0];
  state.consRangeTo = state.consRangeTo || state.consData.meses[state.consData.meses.length - 1];
  fromSel.value = state.consRangeFrom;
  toSel.value = state.consRangeTo;
  fromSel.addEventListener('change', () => {
    state.consRangeFrom = fromSel.value;
    if (state.consRangeFrom > state.consRangeTo) {
      state.consRangeTo = state.consRangeFrom;
      toSel.value = state.consRangeTo;
    }
    renderConsolidated();
  });
  toSel.addEventListener('change', () => {
    state.consRangeTo = toSel.value;
    if (state.consRangeTo < state.consRangeFrom) {
      state.consRangeFrom = state.consRangeTo;
      fromSel.value = state.consRangeFrom;
    }
    renderConsolidated();
  });
}

function consMesesToShow() {
  if (!state.consData) return [];
  const all = state.consData.meses;
  if (state.consRango === 'all') return all;
  if (state.consRango === 'year') {
    return all.filter(m => m.startsWith(state.consYear + '-'));
  }
  if (state.consRango === 'range') {
    return all.filter(m => m >= state.consRangeFrom && m <= state.consRangeTo);
  }
  const n = parseInt(state.consRango, 10);
  return all.slice(-n);
}

function renderConsolidated() {
  if (!state.consData) {
    const body = document.getElementById('consBody');
    if (body) body.innerHTML = '<tr><td colspan="99">No hay datos consolidados. Corre <code>make refresh</code>.</td></tr>';
    return;
  }
  const meses = consMesesToShow();
  const structure = state.consData.estructura;
  const dataRegion = (state.consData.vistas[state.consVista] || {})[state.consRegion] || {};

  // Toggle expand/collapse igual que la tabla MM
  const byKey = {};
  for (const r of structure) byKey[r.key] = r;
  const hasChildren = new Set();
  for (const r of structure) if (r.parent) hasChildren.add(r.parent);
  const chainExpanded = (row) => {
    let cur = row;
    while (cur && cur.parent) {
      if (!state.consExpanded.has(cur.parent)) return false;
      cur = byKey[cur.parent];
    }
    return true;
  };

  const isTotalView = state.consRegion === 'Total';
  const filaVisible = (row) =>
    (!row.only_total || isTotalView) && chainExpanded(row);
  const structureFiltered = structure.filter(filaVisible);

  const head = document.getElementById('consHead');
  head.innerHTML = '';
  head.appendChild(th('P&L Consolidado (MXN 000s)'));
  for (const m of meses) head.appendChild(th(m));

  const body = document.getElementById('consBody');
  body.innerHTML = '';

  // Base de % = cons_gmv_total (GMV MM + GMV Inmo) para lectura consistente
  const revByMonth = {};
  for (const m of meses) revByMonth[m] = (dataRegion[m] || {})['cons_gmv_total'] || 0;

  const showPctRow = (row) =>
    !['cons_props_mm', 'cons_props_inmo', 'cons_props_total',
      'cons_gmv_mm', 'cons_gmv_inmo', 'cons_gmv_total'].includes(row.key)
    && row.sign !== 'count';

  for (const row of structureFiltered) {
    const tr = document.createElement('tr');
    tr.className = `tipo-${row.type}`;

    // label + toggle + nota
    const labelTd = document.createElement('td');
    if (hasChildren.has(row.key)) {
      const isExp = state.consExpanded.has(row.key);
      const tog = document.createElement('span');
      tog.className = 'toggle';
      tog.textContent = isExp ? '▼' : '▶';
      labelTd.appendChild(tog);
      labelTd.appendChild(document.createTextNode(' ' + row.label));
      labelTd.classList.add('expandable');
      labelTd.addEventListener('click', () => {
        if (state.consExpanded.has(row.key)) state.consExpanded.delete(row.key);
        else state.consExpanded.add(row.key);
        renderConsolidated();
      });
    } else {
      labelTd.textContent = row.label;
    }
    if (row.note) {
      const info = document.createElement('span');
      info.className = 'row-note';
      info.textContent = 'ⓘ';
      info.title = row.note;
      labelTd.appendChild(info);
    }
    tr.appendChild(labelTd);

    for (const m of meses) {
      const cell = (dataRegion[m] || {})[row.key];
      const val = cell === undefined ? null : cell;
      const isCount = row.sign === 'count';
      const cellEl = document.createElement('td');
      cellEl.classList.add(`signo-${row.sign}`);

      // Verde en positivos para totales netos + los tres CM (MM/Inmo/Total)
      const isProfitLine =
        (row.type === 'total' && row.sign === 'net') ||
        row.key === 'cons_cm_mm' || row.key === 'cons_cm_inmo';
      if (isProfitLine && val !== null && val > 0) {
        cellEl.classList.add('positivo-highlight');
      }

      if (showPctRow(row) && val !== null && revByMonth[m]) {
        const pct = val / revByMonth[m];
        cellEl.innerHTML = `${fmt(val, isCount)}<br><span class="pct">${fmtPct(pct)}</span>`;
      } else {
        cellEl.textContent = fmt(val, isCount);
      }

      if (row.extern && val === null) {
        cellEl.title = row.key === 'headcount_local'
          ? 'Sin dato de headcount (Aline/Lis) para este mes'
          : 'Sin dato en fuente externa (Lis/Danibot/BQ) para este mes';
      }

      // Drill-down por tercero para Corp OpEx
      if (CORP_OPEX_DRILLABLE_KEYS.has(row.key) && val !== null && val !== 0 && state.corpFacts) {
        const hasFacts = ((state.corpFacts.data || {})[state.consRegion] || {})[m]?.[row.key];
        if (hasFacts && hasFacts.length > 0) {
          cellEl.classList.add('clickable');
          cellEl.title = 'Clic para ver desglose por tercero';
          cellEl.addEventListener('click', () => openCorpDrill(row, m, state.consRegion));
        }
      }

      tr.appendChild(cellEl);
    }
    body.appendChild(tr);
  }

  // Nota de coberturas
  const lo = ((state.consData.meta || {}).local_opex) || {};
  const inmoMeta = ((state.consData.meta || {}).inmo) || {};
  const bits = [];
  if (lo.payroll_cobertura_hasta) bits.push(`payroll hasta ${lo.payroll_cobertura_hasta} (Lis)`);
  if (lo.headcount_cobertura_hasta) bits.push(`HC hasta ${lo.headcount_cobertura_hasta} (${lo.headcount_owner || 'Aline/Lis'})`);
  if (lo.rent_cobertura_hasta) bits.push(`rent hasta ${lo.rent_cobertura_hasta} (Danibot, FX ${lo.fx_mxn_per_usd})`);
  if (lo.marketing_cobertura_hasta) bits.push(`marketing hasta ${lo.marketing_cobertura_hasta} (BQ)`);
  if (lo.corp_opex_cobertura_hasta) bits.push(`corp opex hasta ${lo.corp_opex_cobertura_hasta} (bet_data_p2)`);
  if (inmoMeta.inmo_generado_en) bits.push(`Inmo generado ${inmoMeta.inmo_generado_en.slice(0,10)}`);
  document.getElementById('consNota').innerHTML =
    bits.length ? `<span style="color:var(--muted)">ⓘ</span> ${bits.join(' · ')}. Meses posteriores = '—'.` : '';
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
  setupConsControls();
  renderConsRegionCtrl();
  setupDrill();
  setupReportButton();
  renderTable();
}

if (setupLogin()) {
  init();
}
