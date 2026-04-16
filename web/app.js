function toNum(id) {
  return Number(document.getElementById(id).value);
}

function ensureFinite(value, name) {
  if (!Number.isFinite(value)) {
    throw new Error(`${name} 不是有效数字`);
  }
}

function frange(start, stop, step) {
  if (step <= 0) throw new Error("步长必须大于 0");
  const n = Math.round((stop - start) / step);
  if (n < 0) throw new Error("终点必须大于等于起点");
  const out = [];
  for (let i = 0; i <= n; i += 1) {
    out.push(Number((start + i * step).toFixed(6)));
  }
  return out;
}

function inferExchangeStrikeStep(spotIndex) {
  if (spotIndex < 2000) return 25;
  if (spotIndex < 5000) return 50;
  return 100;
}

function buildExchangeListedStrikes(spotIndex, listingDepth) {
  if (listingDepth < 1) throw new Error("挂牌挡位深度必须 >= 1");
  const step = inferExchangeStrikeStep(spotIndex);
  const atm = Math.round(spotIndex / step) * step;
  const arr = [];
  for (let i = -listingDepth; i <= listingDepth; i += 1) {
    const strike = atm + i * step;
    if (strike > 0) arr.push(Number(strike.toFixed(6)));
  }
  return [...new Set(arr)].sort((a, b) => a - b);
}

function parseDateYmd(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) throw new Error(`日期格式错误: ${s}，应为 YYYY-MM-DD`);
  const y = Number(m[1]);
  const mon = Number(m[2]);
  const d = Number(m[3]);
  const dt = new Date(y, mon - 1, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== mon - 1 || dt.getDate() !== d) {
    throw new Error(`日期无效: ${s}`);
  }
  return dt;
}

function formatYmd(dt) {
  const y = dt.getFullYear();
  const m = `${dt.getMonth() + 1}`.padStart(2, "0");
  const d = `${dt.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function formatYm(dt) {
  const y = dt.getFullYear();
  const m = `${dt.getMonth() + 1}`.padStart(2, "0");
  return `${y}-${m}`;
}

function addMonths(dt, n) {
  return new Date(dt.getFullYear(), dt.getMonth() + n, 1);
}

function defaultExchangeExpiryMonths(valuationDate) {
  const current = new Date(valuationDate.getFullYear(), valuationDate.getMonth(), 1);
  const next = addMonths(current, 1);
  const quarterMonths = [];
  let cursor = current;
  while (quarterMonths.length < 2) {
    cursor = addMonths(cursor, 1);
    const mm = cursor.getMonth() + 1;
    if ([3, 6, 9, 12].includes(mm)) quarterMonths.push(cursor);
  }
  return [...new Set([current, next, ...quarterMonths].map(formatYm))];
}

function parseYm(ym) {
  const m = /^(\d{4})-(\d{2})$/.exec(ym);
  if (!m) throw new Error(`到期月格式错误: ${ym}，应为 YYYY-MM`);
  const y = Number(m[1]);
  const mon = Number(m[2]);
  if (mon < 1 || mon > 12) throw new Error(`到期月无效: ${ym}`);
  return { year: y, month: mon };
}

function thirdFriday(year, month) {
  const first = new Date(year, month - 1, 1);
  const firstWeekday = first.getDay();
  const fridayOffset = (5 - firstWeekday + 7) % 7;
  const firstFriday = new Date(year, month - 1, 1 + fridayOffset);
  return new Date(year, month - 1, firstFriday.getDate() + 14);
}

function maturityYearsFromExchangeExpiry(valuationDate, expiryMonth) {
  const ym = parseYm(expiryMonth);
  const expiryDate = thirdFriday(ym.year, ym.month);
  const days = (expiryDate.getTime() - valuationDate.getTime()) / (24 * 3600 * 1000);
  if (days <= 0) {
    throw new Error(`到期月 ${expiryMonth} 的第三个周五不在估值日之后`);
  }
  return { maturityYears: days / 365, expiryDate };
}

function erf(x) {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const t = 1 / (1 + p * ax);
  const y = 1 - (((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t) * Math.exp(-ax * ax);
  return sign * y;
}

function normCdf(x) {
  return 0.5 * (1 + erf(x / Math.sqrt(2)));
}

function blackScholesPutPoints(spot, strike, r, q, vol, t) {
  const sqrtT = Math.sqrt(t);
  const d1 = (Math.log(spot / strike) + (r - q + 0.5 * vol * vol) * t) / (vol * sqrtT);
  const d2 = d1 - vol * sqrtT;
  return strike * Math.exp(-r * t) * normCdf(-d2) - spot * Math.exp(-q * t) * normCdf(-d1);
}

function parseManualCurve(text) {
  const map = new Map();
  const lines = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  lines.forEach((line, idx) => {
    if (line.startsWith("#")) return;
    const parts = line.split(/[,\s=]+/).filter(Boolean);
    if (parts.length < 2) {
      throw new Error(`手动曲线第 ${idx + 1} 行格式错误，应为“执行价,价格”`);
    }
    const strike = Number(parts[0]);
    const premiumPoints = Number(parts[1]);
    if (!Number.isFinite(strike) || !Number.isFinite(premiumPoints) || premiumPoints <= 0) {
      throw new Error(`手动曲线第 ${idx + 1} 行数据无效`);
    }
    map.set(Number(strike.toFixed(6)), premiumPoints);
  });
  if (map.size === 0) throw new Error("手动曲线不能为空");
  return map;
}

function interpolateManualCurve(strike, premiumMap) {
  const keys = [...premiumMap.keys()].sort((a, b) => a - b);
  const exact = keys.find((k) => Math.abs(k - strike) < 1e-9);
  if (exact !== undefined) return premiumMap.get(exact);
  if (strike < keys[0] || strike > keys[keys.length - 1]) {
    throw new Error(`执行价 ${strike} 超出手动曲线范围 [${keys[0]}, ${keys[keys.length - 1]}]`);
  }
  for (let i = 0; i < keys.length - 1; i += 1) {
    const left = keys[i];
    const right = keys[i + 1];
    if (left <= strike && strike <= right) {
      const pLeft = premiumMap.get(left);
      const pRight = premiumMap.get(right);
      const ratio = (strike - left) / (right - left);
      return pLeft + ratio * (pRight - pLeft);
    }
  }
  throw new Error(`无法为执行价 ${strike} 插值`);
}

function getPutPremiumPoints(strike, cfg) {
  if (cfg.pricingMode === "auto") {
    const bsPoints = blackScholesPutPoints(
      cfg.spotIndex,
      strike,
      cfg.riskFreeRate,
      cfg.dividendYield,
      cfg.volatility,
      cfg.maturityYears
    );
    return bsPoints * (1 + cfg.bsPremiumRate);
  }
  if (cfg.pricingMode === "manual_flat") {
    if (!(cfg.manualPremium > 0)) {
      throw new Error("手动统一价格模式需要填写 manual premium > 0（点/张）");
    }
    return cfg.manualPremium;
  }
  return interpolateManualCurve(strike, cfg.manualCurveMap);
}

function calcContracts(strike, cfg) {
  const targetExposure = cfg.portfolioValue * cfg.portfolioBeta * cfg.hedgeRatio;
  if (cfg.hedgeMethod === "delta") {
    if (!(cfg.putDeltaAbs > 0 && cfg.putDeltaAbs <= 1)) {
      throw new Error("Delta 法需要 putDeltaAbs 在 (0, 1] 内");
    }
    return Math.max(
      1,
      Math.ceil(targetExposure / (cfg.spotIndex * cfg.contractMultiplier * cfg.putDeltaAbs))
    );
  }
  return Math.max(1, Math.ceil(targetExposure / (strike * cfg.contractMultiplier)));
}

function buildMatrix(cfg) {
  const exposure = cfg.portfolioValue * cfg.portfolioBeta;
  const strikes = buildExchangeListedStrikes(cfg.spotIndex, cfg.listingDepth);
  const terminalsAsc = frange(cfg.terminalStart, cfg.terminalStop, cfg.terminalStep);
  const terminals = [...terminalsAsc].sort((a, b) => b - a);
  const strikeInfos = [];

  strikes.forEach((strike) => {
    const premiumPoints = getPutPremiumPoints(strike, cfg);
    const contracts = calcContracts(strike, cfg);
    const totalEntryCost = contracts * (
      premiumPoints * cfg.contractMultiplier + cfg.feePerContract + cfg.slippagePerContract
    );
    strikeInfos.push({
      strike,
      premiumPoints,
      contracts,
      totalEntryCost,
    });
  });

  const rows = terminals.map((terminalPrice) => {
    const scenarioReturn = terminalPrice / cfg.spotIndex - 1;
    const unhedgedTotalPnl = exposure * scenarioReturn;
    const cells = strikeInfos.map((info) => {
      const putPayoff = Math.max(info.strike - terminalPrice, 0) * cfg.contractMultiplier * info.contracts;
      const optionPnl = putPayoff - info.totalEntryCost;
      const hedgedTotalPnl = unhedgedTotalPnl + optionPnl;
      const improvementRatio = exposure > 0 ? optionPnl / exposure : 0;
      return {
        strike: info.strike,
        terminalPrice,
        scenarioReturn,
        unhedgedTotalPnl,
        putPayoff,
        hedgedTotalPnl,
        optionPnl,
        improvementRatio,
        isExercised: putPayoff > 0,
      };
    });
    return { terminalPrice, cells };
  });

  const hedgedPnls = rows.flatMap((r) => r.cells.map((c) => c.hedgedTotalPnl));
  const minHedgedTotalPnl = Math.min(...hedgedPnls);
  const maxHedgedTotalPnl = Math.max(...hedgedPnls);
  const maxAbsHedgedTotalPnl = Math.max(
    1e-8,
    Math.abs(minHedgedTotalPnl),
    Math.abs(maxHedgedTotalPnl)
  );

  return {
    exposure,
    strikes,
    strikeInfos,
    rows,
    minHedgedTotalPnl,
    maxHedgedTotalPnl,
    maxAbsHedgedTotalPnl,
  };
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  const value = h.length === 3
    ? h.split("").map((ch) => ch + ch).join("")
    : h;
  return {
    r: parseInt(value.slice(0, 2), 16),
    g: parseInt(value.slice(2, 4), 16),
    b: parseInt(value.slice(4, 6), 16),
  };
}

function rgbToHex(r, g, b) {
  const f = (x) => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, "0");
  return `#${f(r)}${f(g)}${f(b)}`;
}

function mixColor(fromHex, toHex, t) {
  const a = hexToRgb(fromHex);
  const b = hexToRgb(toHex);
  return rgbToHex(
    a.r + (b.r - a.r) * t,
    a.g + (b.g - a.g) * t,
    a.b + (b.b - a.b) * t
  );
}

function heatColor(value, maxAbs) {
  const neutral = "#f8f2e7";
  const positive = "#c14e3f";
  const negative = "#1a7d62";
  const safeMax = Math.max(1e-8, maxAbs);
  const clamped = Math.max(-safeMax, Math.min(safeMax, value));
  if (clamped >= 0) {
    const t = Math.pow(clamped / safeMax, 0.7);
    return mixColor(neutral, positive, t);
  }
  const t = Math.pow((-clamped) / safeMax, 0.7);
  return mixColor(neutral, negative, t);
}

function textColorForCell(value, maxAbs) {
  const safeMax = Math.max(1e-8, maxAbs);
  if (Math.abs(value) > safeMax * 0.62) return "#ffffff";
  return "#2d2a24";
}

const moneyFmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
const matrixTooltipEl = document.getElementById("matrixTooltip");

function fmtMoney(v) {
  const sign = v > 0 ? "+" : "";
  return `${sign}${moneyFmt.format(v)}`;
}

function fmtPercentSigned(v) {
  const sign = v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(2)}%`;
}

function buildCellTooltipHtml(cell) {
  return `
    <div class="matrix-tooltip-title">K=${cell.strike.toFixed(2)}, S_T=${cell.terminalPrice.toFixed(2)}</div>
    <div>标的收益率：${fmtPercentSigned(cell.scenarioReturn)}</div>
    <div>标的收益(元)：${fmtMoney(cell.unhedgedTotalPnl)}</div>
    <div>期权收益(元)：${fmtMoney(cell.optionPnl)}</div>
    <div>行权收益(元)：${fmtMoney(cell.putPayoff)}</div>
    <div>是否行权：${cell.isExercised ? "是" : "否"}</div>
    <div>对冲后总收益(元)：${fmtMoney(cell.hedgedTotalPnl)}</div>
  `;
}

function moveMatrixTooltip(clientX, clientY) {
  if (!matrixTooltipEl || matrixTooltipEl.classList.contains("hidden")) return;
  const margin = 12;
  const gap = 14;
  const tipW = matrixTooltipEl.offsetWidth;
  const tipH = matrixTooltipEl.offsetHeight;
  let left = clientX + gap;
  let top = clientY + gap;
  if (left + tipW + margin > window.innerWidth) {
    left = clientX - tipW - gap;
  }
  if (top + tipH + margin > window.innerHeight) {
    top = clientY - tipH - gap;
  }
  left = Math.max(margin, left);
  top = Math.max(margin, top);
  matrixTooltipEl.style.left = `${left}px`;
  matrixTooltipEl.style.top = `${top}px`;
}

function showMatrixTooltip(cell, clientX, clientY) {
  if (!matrixTooltipEl) return;
  matrixTooltipEl.innerHTML = buildCellTooltipHtml(cell);
  matrixTooltipEl.classList.remove("hidden");
  moveMatrixTooltip(clientX, clientY);
}

function hideMatrixTooltip() {
  if (!matrixTooltipEl) return;
  matrixTooltipEl.classList.add("hidden");
}

function renderSummaryCard(cfg, matrix) {
  const el = document.getElementById("summaryCard");
  const bsPremiumLine = cfg.pricingMode === "auto"
    ? `<div><b>BS 溢价率：</b>${fmtPercentSigned(cfg.bsPremiumRate)}</div>`
    : "";
  el.innerHTML = `
    <div><b>估值日：</b>${formatYmd(cfg.valuationDate)}</div>
    <div><b>到期月：</b>${cfg.expiryMonth}（到期日 ${formatYmd(cfg.expiryDate)}）</div>
    <div><b>到期年限：</b>${cfg.maturityYears.toFixed(6)} 年</div>
    ${bsPremiumLine}
    <div><b>挂牌执行价数量：</b>${matrix.strikes.length}</div>
    <div><b>到期价格情景数量：</b>${matrix.rows.length}</div>
    <div><b>对冲敞口(元)：</b>${moneyFmt.format(matrix.exposure)}</div>
    <div><b>颜色映射范围(总收益)：</b>${fmtMoney(matrix.minHedgedTotalPnl)} ~ ${fmtMoney(matrix.maxHedgedTotalPnl)}</div>
  `;
}

function renderLegend(minHedgedTotalPnl, maxHedgedTotalPnl) {
  const neg = document.getElementById("legendNeg");
  const pos = document.getElementById("legendPos");
  neg.textContent = fmtMoney(minHedgedTotalPnl);
  pos.textContent = fmtMoney(maxHedgedTotalPnl);
}

function renderMatrixTable(matrix) {
  const thead = document.querySelector("#matrixTable thead");
  const tbody = document.querySelector("#matrixTable tbody");
  const terminalRows = [...matrix.rows].sort((a, b) => a.terminalPrice - b.terminalPrice);

  const headRow = document.createElement("tr");
  const firstTh = document.createElement("th");
  firstTh.textContent = "K \\ S_T";
  firstTh.className = "sticky-col";
  headRow.appendChild(firstTh);
  terminalRows.forEach((row) => {
    const th = document.createElement("th");
    th.textContent = `S_T=${row.terminalPrice.toFixed(0)}`;
    headRow.appendChild(th);
  });
  thead.innerHTML = "";
  thead.appendChild(headRow);

  tbody.innerHTML = "";
  matrix.strikes.forEach((strike, strikeIdx) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = `K=${strike.toFixed(0)}`;
    th.className = "sticky-col";
    tr.appendChild(th);

    terminalRows.forEach((row) => {
      const cell = row.cells[strikeIdx];
      const td = document.createElement("td");
      td.className = "matrix-cell";
      td.style.backgroundColor = heatColor(cell.hedgedTotalPnl, matrix.maxAbsHedgedTotalPnl);
      td.style.color = textColorForCell(cell.hedgedTotalPnl, matrix.maxAbsHedgedTotalPnl);
      td.innerHTML = `
        <div class="matrix-main">${fmtMoney(cell.hedgedTotalPnl)}</div>
        <div class="matrix-sub">期权损益 ${fmtMoney(cell.optionPnl)}</div>
      `;
      td.addEventListener("mouseenter", (evt) => showMatrixTooltip(cell, evt.clientX, evt.clientY));
      td.addEventListener("mousemove", (evt) => moveMatrixTooltip(evt.clientX, evt.clientY));
      td.addEventListener("mouseleave", hideMatrixTooltip);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

function renderStrikeInfoTable(matrix) {
  const body = document.querySelector("#strikeInfoTable tbody");
  body.innerHTML = "";
  matrix.strikeInfos.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${item.strike.toFixed(2)}</td>
      <td>${item.premiumPoints.toFixed(4)}</td>
      <td>${item.contracts}</td>
      <td>${moneyFmt.format(item.totalEntryCost)}</td>
    `;
    body.appendChild(tr);
  });
}

function togglePricingMode() {
  const mode = document.getElementById("pricingMode").value;
  const isAuto = mode === "auto";
  const isFlat = mode === "manual_flat";
  const isCurve = mode === "manual_curve";

  document.getElementById("manualFlatWrap").classList.toggle("hidden", !isFlat);
  document.getElementById("manualCurveWrap").classList.toggle("hidden", !isCurve);
  document.getElementById("volWrap").classList.toggle("hidden", !isAuto);
  document.getElementById("rfWrap").classList.toggle("hidden", !isAuto);
  document.getElementById("divWrap").classList.toggle("hidden", !isAuto);
  document.getElementById("bsPremiumWrap").classList.toggle("hidden", !isAuto);
}

function toggleHedgeMethod() {
  const method = document.getElementById("hedgeMethod").value;
  document.getElementById("deltaWrap").classList.toggle("hidden", method !== "delta");
}

function applyDefaultExpiryByValuation() {
  const valuationDate = parseDateYmd(document.getElementById("valuationDate").value);
  const defaultMonths = defaultExchangeExpiryMonths(valuationDate);
  const expiryInput = document.getElementById("expiryMonth");
  if (!expiryInput.value.trim()) {
    expiryInput.value = defaultMonths[0];
  }
}

function buildConfig() {
  const cfg = {
    pricingMode: document.getElementById("pricingMode").value,
    hedgeMethod: document.getElementById("hedgeMethod").value,
    spotIndex: toNum("spotIndex"),
    portfolioValue: toNum("portfolioValue"),
    portfolioBeta: toNum("portfolioBeta"),
    hedgeRatio: toNum("hedgeRatio"),
    putDeltaAbs: toNum("putDeltaAbs"),
    contractMultiplier: toNum("contractMultiplier"),
    listingDepth: toNum("listingDepth"),
    valuationDate: parseDateYmd(document.getElementById("valuationDate").value || formatYmd(new Date())),
    expiryMonth: document.getElementById("expiryMonth").value.trim(),
    terminalStart: toNum("terminalStart"),
    terminalStop: toNum("terminalStop"),
    terminalStep: toNum("terminalStep"),
    volatility: toNum("volatility"),
    riskFreeRate: toNum("riskFreeRate"),
    dividendYield: toNum("dividendYield"),
    bsPremiumRate: toNum("bsPremiumRate"),
    manualPremium: toNum("manualPremium"),
    feePerContract: toNum("feePerContract"),
    slippagePerContract: toNum("slippagePerContract"),
    manualCurveMap: null,
    maturityYears: 0,
    expiryDate: null,
  };

  Object.entries(cfg).forEach(([k, v]) => {
    if (typeof v === "number") ensureFinite(v, k);
  });

  if (cfg.spotIndex <= 0) throw new Error("标的价格必须 > 0");
  if (cfg.portfolioValue <= 0) throw new Error("头寸金额必须 > 0");
  if (cfg.portfolioBeta <= 0) throw new Error("组合 Beta 必须 > 0");
  if (cfg.hedgeRatio <= 0) throw new Error("对冲比例必须 > 0");
  if (cfg.contractMultiplier <= 0) throw new Error("合约乘数必须 > 0");
  if (cfg.listingDepth < 1) throw new Error("挂牌挡位深度必须 >= 1");
  if (cfg.terminalStep <= 0) throw new Error("到期价格步长必须 > 0");
  if (cfg.terminalStop < cfg.terminalStart) throw new Error("到期价格终点必须 >= 起点");
  if (cfg.feePerContract < 0 || cfg.slippagePerContract < 0) throw new Error("手续费和滑点不能为负");
  if (cfg.bsPremiumRate <= -1) throw new Error("BS 溢价率必须 > -1");

  if (cfg.pricingMode === "manual_curve") {
    cfg.manualCurveMap = parseManualCurve(document.getElementById("manualCurve").value);
  }

  const defaultMonths = defaultExchangeExpiryMonths(cfg.valuationDate);
  if (!cfg.expiryMonth) {
    cfg.expiryMonth = defaultMonths[0];
    document.getElementById("expiryMonth").value = cfg.expiryMonth;
  }
  const maturity = maturityYearsFromExchangeExpiry(cfg.valuationDate, cfg.expiryMonth);
  cfg.maturityYears = maturity.maturityYears;
  cfg.expiryDate = maturity.expiryDate;

  return { cfg, defaultMonths };
}

function runAnalysis() {
  const errorEl = document.getElementById("errorMsg");
  const warnEl = document.getElementById("warnMsg");
  errorEl.textContent = "";
  warnEl.textContent = "";

  try {
    const { cfg, defaultMonths } = buildConfig();
    const warns = [];
    if (!defaultMonths.includes(cfg.expiryMonth)) {
      warns.push(`到期月 ${cfg.expiryMonth} 不在常见挂牌月份(${defaultMonths.join(", ")})，已按输入月份计算。`);
    }
    if (cfg.pricingMode === "manual_flat") {
      warns.push("手动统一价格会弱化不同执行价的真实报价差异，建议优先使用手动曲线。");
    }
    if (cfg.pricingMode === "auto" && cfg.bsPremiumRate !== 0) {
      warns.push(`BS 自动定价已启用溢价率 ${fmtPercentSigned(cfg.bsPremiumRate)}。`);
    }

    const matrix = buildMatrix(cfg);
    renderSummaryCard(cfg, matrix);
    renderLegend(matrix.minHedgedTotalPnl, matrix.maxHedgedTotalPnl);
    renderMatrixTable(matrix);
    renderStrikeInfoTable(matrix);

    warnEl.textContent = warns.join(" ");
  } catch (err) {
    errorEl.textContent = String(err.message || err);
  }
}

document.getElementById("runBtn").addEventListener("click", runAnalysis);
document.getElementById("pricingMode").addEventListener("change", togglePricingMode);
document.getElementById("hedgeMethod").addEventListener("change", toggleHedgeMethod);
document.getElementById("valuationDate").addEventListener("change", () => {
  const expiryInput = document.getElementById("expiryMonth");
  if (!expiryInput.value.trim()) {
    applyDefaultExpiryByValuation();
  }
});

const valuationInput = document.getElementById("valuationDate");
if (!valuationInput.value) valuationInput.value = formatYmd(new Date());
applyDefaultExpiryByValuation();
togglePricingMode();
toggleHedgeMethod();
runAnalysis();
