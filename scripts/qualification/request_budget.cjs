const { performance } = require('node:perf_hooks');

function remainingBudget(startedMs, nowMs, budgetMs) {
  if (![startedMs, nowMs, budgetMs].every(Number.isFinite) || budgetMs <= 0 || nowMs < startedMs)
    throw Error('Invalid request budget clock');
  return Math.max(0, budgetMs - (nowMs - startedMs));
}

async function withinRequestBudget(promise, startedMs, budgetMs, clock = () => performance.now()) {
  const remaining = remainingBudget(startedMs, clock(), budgetMs);
  if (!remaining) throw Error('Actual request budget exhausted');
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(Error('Actual request budget exhausted')), remaining);
    })]);
  } finally { clearTimeout(timer); }
}

module.exports = { remainingBudget, withinRequestBudget };
