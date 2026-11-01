import { DEFAULT_GATE_CONFIG } from "../config/defaults.js";
import type { GateCheck, GateReport, PerfSummary } from "../types.js";
import { formatMs } from "../util/time.js";

function ratioImprovement(baseline: number | null, current: number | null): number | null {
  if (!Number.isFinite(baseline) || !Number.isFinite(current) || Number(baseline) <= 0) return null;
  return (Number(baseline) - Number(current)) / Number(baseline);
}

function ratioChange(baseline: number | null, current: number | null): number | null {
  if (!Number.isFinite(baseline) || !Number.isFinite(current) || Number(baseline) <= 0) return null;
  return (Number(current) - Number(baseline)) / Number(baseline);
}

function fmtPct(value: number | null): string {
  return Number.isFinite(value) ? `${Math.round(Number(value) * 1000) / 10}%` : "n/a";
}

function check(name: string, passed: boolean, expected: string, actual: string): GateCheck {
  return { name, passed, expected, actual };
}

export function evaluateGates(current: PerfSummary, baseline: PerfSummary): GateReport {
  const cfg = DEFAULT_GATE_CONFIG;
  const checks: GateCheck[] = [];
  const backendSkipped = current.runHealth.backendRunCount === 0 && baseline.runHealth.backendRunCount === 0;
  const sdfSemanticGate = current.cases.length > 0
    && current.cases.every((item) => item.expectedSdfRiskClasses.length > 0 && item.expectedSdfOutcomes.length > 0);
  if (backendSkipped) {
    checks.push(check(
      "backend metrics skipped",
      true,
      "3d-only run explicitly skips backend checks",
      "0 current and 0 baseline backend runs",
    ));
  } else {
    checks.push(check(
      "all backend runs succeeded",
      current.runHealth.backendRunCount > 0 && current.runHealth.backendFailedCount === 0,
      "0 backend failures",
      `${current.runHealth.backendFailedCount}/${current.runHealth.backendRunCount} failed`,
    ));
    const curMedian = current.suite.backendTotalMs.median;
    const baseMedian = baseline.suite.backendTotalMs.median;
    const curP95 = current.suite.backendTotalMs.p95;
    const baseP95 = baseline.suite.backendTotalMs.p95;
    const medianGain = ratioImprovement(baseMedian, curMedian);
    const p95Gain = ratioImprovement(baseP95, curP95);

    if (sdfSemanticGate) {
      checks.push(check(
        "backend suite relative improvement skipped for sdf gate",
        true,
        "SDF adversarial gate uses absolute timing plus reject/pass-control semantics",
        `median=${formatMs(curMedian)} p95=${formatMs(curP95)}`,
      ));
    } else {
      checks.push(check(
        "backend suite median improves",
        Number(medianGain) >= cfg.backendRelativeImprovement,
        `>= ${fmtPct(cfg.backendRelativeImprovement)}`,
        `${fmtPct(medianGain)} (${formatMs(baseMedian)} -> ${formatMs(curMedian)})`,
      ));
      checks.push(check(
        "backend suite p95 improves",
        Number(p95Gain) >= cfg.backendRelativeImprovement,
        `>= ${fmtPct(cfg.backendRelativeImprovement)}`,
        `${fmtPct(p95Gain)} (${formatMs(baseP95)} -> ${formatMs(curP95)})`,
      ));
    }
    checks.push(check(
      "backend p95 absolute",
      Number(curP95) <= cfg.backendAbsoluteP95Ms,
      `<= ${formatMs(cfg.backendAbsoluteP95Ms)}`,
      formatMs(curP95),
    ));
    checks.push(check(
      "scoring p95 absolute",
      Number.isFinite(current.suite.scoringMs.p95) && Number(current.suite.scoringMs.p95) <= cfg.scoringAbsoluteP95Ms,
      `<= ${formatMs(cfg.scoringAbsoluteP95Ms)}`,
      formatMs(current.suite.scoringMs.p95),
    ));
    checks.push(check(
      "solver p95 absolute",
      Number.isFinite(current.suite.solverMs.p95) && Number(current.suite.solverMs.p95) <= cfg.solverAbsoluteP95Ms,
      `<= ${formatMs(cfg.solverAbsoluteP95Ms)}`,
      formatMs(current.suite.solverMs.p95),
    ));

    const baselineByCase = new Map(baseline.cases.map((item) => [item.caseId, item]));
    for (const item of current.cases) {
      const base = baselineByCase.get(item.caseId);
      const curCase = item.backendTotalMs.median;
      const baseCase = base?.backendTotalMs.median ?? null;
      const regression = ratioImprovement(baseCase, curCase);
      const baselineMissing = !Number.isFinite(baseCase);
      checks.push(check(
        `case ${item.caseId.slice(0, 8)} no large regression`,
        baselineMissing ? Number.isFinite(curCase) : regression !== null && regression >= -cfg.maxPerCaseRegressionRatio,
        baselineMissing ? "baseline failed or missing; current must succeed" : `not slower by > ${fmtPct(cfg.maxPerCaseRegressionRatio)}`,
        baselineMissing
          ? `baseline n/a -> ${formatMs(curCase)}`
          : `${fmtPct(regression)} (${formatMs(baseCase)} -> ${formatMs(curCase)})`,
      ));
      const baseHasSuccessfulBackendRuns = Boolean(
        base && base.backendTotalMs.count > 0 && Number.isFinite(base.backendTotalMs.median),
      );
      if (baseHasSuccessfulBackendRuns && base) {
        const currentUncovered = item.atlasBackedUncoveredCountMedian ?? Number.POSITIVE_INFINITY;
        const baselineUncovered = base.atlasBackedUncoveredCountMedian ?? base.uncoveredCountMedian ?? 0;
        const fallbackTargets = item.fallbackTargetKeys ?? [];
        const excludedTargets = item.excludedTargetKeys ?? [];
        const fallbackNote = excludedTargets.length > 0
          ? `; excluded non-atlas targets: ${excludedTargets.join(",")}`
          : "";
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} coverage not worse`,
          Number(currentUncovered) <= Number(baselineUncovered),
          "atlas-backed uncovered median <= baseline",
          `${baselineUncovered ?? "n/a"} -> ${currentUncovered ?? "n/a"}${fallbackNote}`,
        ));
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} no runtime fallback targets`,
          fallbackTargets.length === 0,
          "native gate must not plan or report fallback target coverage",
          fallbackTargets.join(",") || "none",
        ));
      } else {
        const fallbackTargets = item.fallbackTargetKeys ?? [];
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} coverage baseline unavailable`,
          Number.isFinite(item.atlasBackedUncoveredCountMedian ?? item.uncoveredCountMedian ?? null),
          "baseline has no successful backend run; coverage is reported but not compared",
          `baseline n/a -> ${item.atlasBackedUncoveredCountMedian ?? item.uncoveredCountMedian ?? "n/a"}`,
        ));
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} no runtime fallback targets`,
          fallbackTargets.length === 0,
          "native gate must not plan or report fallback target coverage",
          fallbackTargets.join(",") || "none",
        ));
      }
      const requestedAlgorithms = (item.requestedAlgorithms.length > 0 ? item.requestedAlgorithms : [item.algorithm])
        .map((value) => value.toLowerCase());
      const effectiveAlgorithms = item.effectiveAlgorithms.map((value) => value.toLowerCase());
      const requestedDrccSolvers = item.requestedDrccSolvers.map((value) => value.toLowerCase());
      const effectiveDrccSolvers = item.effectiveDrccSolvers.map((value) => value.toLowerCase());
      if (requestedAlgorithms.includes("drcc")) {
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} drcc stays native`,
          effectiveAlgorithms.some((value) => value.includes("drcc") && value.includes("bitset")),
          "requested drcc must not silently downgrade to greedy",
          effectiveAlgorithms.join(", ") || "n/a",
        ));
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} no native misocp claim`,
          !requestedDrccSolvers.includes("misocp") && item.nativeDrccApproximation !== true,
          "native gate must not request or pass legacy-only MISOCP approximation",
          `requested=${requestedDrccSolvers.join(",") || "default"} approx=${item.nativeDrccApproximation}`,
        ));
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} drcc solver honored`,
          requestedDrccSolvers.every((solver) => effectiveDrccSolvers.includes(solver === "cvar" ? "cvar_relax" : solver)),
          "native DRCC solver mode must be honored; MISOCP is legacy-only and must fail closed",
          `requested=${requestedDrccSolvers.join(",") || "default"} effective=${effectiveDrccSolvers.join(",") || "n/a"} approx=${item.nativeDrccApproximation}`,
        ));
      }
      if (requestedAlgorithms.includes("exact")) {
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} exact stays native`,
          effectiveAlgorithms.some((value) => value.includes("exact") && value.includes("bitset")),
          "requested exact must run native exact bitset or exact reduced pool",
          effectiveAlgorithms.join(", ") || "n/a",
        ));
      }
      for (const expectedRiskClass of item.expectedSdfRiskClasses) {
        const observed = item.observedSdfRiskClasses.includes(expectedRiskClass)
          || item.nativeRejectReasonKeys.includes(`risk_${expectedRiskClass}`);
        const expectsReject = item.expectedSdfOutcomes.includes("reject");
        const expectsPass = item.expectedSdfOutcomes.includes("pass");
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} sdf ${expectedRiskClass} observed`,
          observed,
          "expected layered SDF class appears in diagnostics or native reject reasons",
          `observed=${item.observedSdfRiskClasses.join(",") || "none"} rejects=${item.nativeRejectReasonKeys.join(",") || "none"}`,
        ));
        if (expectsReject) {
          const rejectReasonObserved = item.nativeRejectReasonKeys.includes(`risk_${expectedRiskClass}`);
          const selectedRejected = Number(item.selectedCountMedian) === 0;
          checks.push(check(
            `case ${item.caseId.slice(0, 8)} sdf ${expectedRiskClass} rejects`,
            rejectReasonObserved && selectedRejected,
            "continuousRiskDistance=enforce must reject the near-boundary risk class and select no probe",
            `rejects=${item.nativeRejectReasonKeys.join(",") || "none"} selected=${item.selectedCountMedian ?? "n/a"}`,
          ));
        }
        if (expectsPass) {
          checks.push(check(
            `case ${item.caseId.slice(0, 8)} sdf ${expectedRiskClass} pass control`,
            item.observedSdfRiskClasses.includes(expectedRiskClass) && Number(item.selectedCountMedian) > 0,
            "permissive SDF threshold must leave a selectable plan with the class visible in selected diagnostics",
            `observed=${item.observedSdfRiskClasses.join(",") || "none"} rejects=${item.nativeRejectReasonKeys.join(",") || "none"} selected=${item.selectedCountMedian ?? "n/a"}`,
          ));
        }
      }
      if (item.expectedSdfOutcomes.includes("reject")) {
        continue;
      }
      for (const expectedShankCount of item.expectedShankCounts.filter((value) => value > 1)) {
        checks.push(check(
          `case ${item.caseId.slice(0, 8)} multi-shank runtime honored`,
          item.effectiveShankCounts.includes(expectedShankCount),
          `runtime shankCount must include requested ${expectedShankCount}`,
          item.effectiveShankCounts.join(",") || "none",
        ));
        if (Number(item.selectedCountMedian) > 0) {
          const payloadCountsOk = item.selectedShankCounts.length > 0
            && item.selectedShankCounts.every((value) => value === expectedShankCount);
          const offsetCountsOk = item.selectedShankOffsetCounts.length > 0
            && item.selectedShankOffsetCounts.every((value) => value === expectedShankCount);
          checks.push(check(
            `case ${item.caseId.slice(0, 8)} multi-shank selected payload`,
            payloadCountsOk && offsetCountsOk,
            `selected candidates must carry ${expectedShankCount} shanks and ${expectedShankCount} offsets`,
            `shanks=${item.selectedShankCounts.join(",") || "none"} offsets=${item.selectedShankOffsetCounts.join(",") || "none"}`,
          ));
        }
      }
    }
  }

  const cur3dLoad = current.browser3d.interactiveReadyMs.median;
  const base3dLoad = baseline.browser3d.interactiveReadyMs.median;
  const curFps = current.browser3d.fpsMedian.median;
  const baseFps = baseline.browser3d.fpsMedian.median;
  const curFrameP95Median = current.browser3d.frameMsP95.median;
  const curFrameP95Worst = current.browser3d.frameMsP95.max ?? current.browser3d.frameMsP95.p95 ?? curFrameP95Median;
  const loadChange = ratioChange(base3dLoad, cur3dLoad);
  const fpsChange = ratioChange(baseFps, curFps);
  const loadStrongAbsolute = Number(cur3dLoad) <= cfg.browserInteractiveReadyMs * 0.5;
  const fpsStrongAbsolute =
    Number(curFps) >= cfg.browserFpsMedianMin
    && Number(curFrameP95Worst) <= cfg.browserFrameP95MaxMs;
  const browserSkipped = current.runHealth.browser3dRunCount === 0 && baseline.runHealth.browser3dRunCount === 0;
  if (browserSkipped) {
    checks.push(check(
      "3d metrics skipped",
      true,
      "backend-only run explicitly skips 3d checks",
      "0 current and 0 baseline 3d runs",
    ));
    return {
      passed: checks.every((item) => item.passed),
      checks,
    };
  }

  checks.push(check(
    "all 3d runs succeeded",
    current.runHealth.browser3dRunCount > 0 && current.runHealth.browser3dFailedCount === 0,
    "0 3d failures",
    `${current.runHealth.browser3dFailedCount}/${current.runHealth.browser3dRunCount} failed`,
  ));

  checks.push(check(
    "3d interactive load absolute",
    Number(cur3dLoad) <= cfg.browserInteractiveReadyMs,
    `<= ${formatMs(cfg.browserInteractiveReadyMs)}`,
    formatMs(cur3dLoad),
  ));
  checks.push(check(
    "3d median fps absolute",
    Number(curFps) >= cfg.browserFpsMedianMin,
    `>= ${cfg.browserFpsMedianMin}fps`,
    Number.isFinite(curFps) ? `${Math.round(Number(curFps) * 10) / 10}fps` : "n/a",
  ));
  checks.push(check(
    "3d worst-run frame p95 absolute",
    Number(curFrameP95Worst) <= cfg.browserFrameP95MaxMs,
    `every run frame p95 <= ${formatMs(cfg.browserFrameP95MaxMs)}`,
    `median=${formatMs(curFrameP95Median)} p95=${formatMs(current.browser3d.frameMsP95.p95)} max=${formatMs(current.browser3d.frameMsP95.max)}`,
  ));
  checks.push(check(
    "3d interactive load stable",
    (loadChange !== null && loadChange <= cfg.browserMaxLoadRegressionRatio) || loadStrongAbsolute,
    `not slower by > ${fmtPct(cfg.browserMaxLoadRegressionRatio)} or <= ${formatMs(cfg.browserInteractiveReadyMs * 0.5)}`,
    `${fmtPct(loadChange)} (${formatMs(base3dLoad)} -> ${formatMs(cur3dLoad)}), strongAbsolute=${loadStrongAbsolute}`,
  ));
  checks.push(check(
    "3d fps not worse",
    (fpsChange !== null && fpsChange >= -cfg.browserMaxFpsRegressionRatio) || fpsStrongAbsolute,
    `not lower by > ${fmtPct(cfg.browserMaxFpsRegressionRatio)} or >= ${cfg.browserFpsMedianMin}fps with frame p95 <= ${formatMs(cfg.browserFrameP95MaxMs)}`,
    `${fmtPct(fpsChange)} (${baseFps ?? "n/a"} -> ${curFps ?? "n/a"}), strongAbsolute=${fpsStrongAbsolute}`,
  ));

  return {
    passed: checks.every((item) => item.passed),
    checks,
  };
}
