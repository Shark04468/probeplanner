export const CONSTRAINT_KEYS = [
  "maxAngle",
  "minEntrySpacing",
  "minClearance",
  "lambdaBoundary",
  "brainBounds",
  "continuousRiskDistance",
  "hemisphere",
];

export const CONSTRAINT_MODES = ["enforce", "warn", "off"];

export const DEFAULT_CONSTRAINTS = {
  maxAngle: "enforce",
  minEntrySpacing: "enforce",
  minClearance: "enforce",
  lambdaBoundary: "enforce",
  brainBounds: "enforce",
  continuousRiskDistance: "enforce",
  hemisphere: "warn",
};

export function sanitizeConstraintModes(input = {}) {
  const out = {};
  for (const key of CONSTRAINT_KEYS) {
    const mode = input[key];
    out[key] = CONSTRAINT_MODES.includes(mode) ? mode : DEFAULT_CONSTRAINTS[key];
  }
  return out;
}

export function validateConstraintModes(input = {}) {
  const errors = [];
  const keys = Object.keys(input || {});
  for (const key of keys) {
    if (!CONSTRAINT_KEYS.includes(key)) {
      errors.push(`Unknown constraint key: ${key}`);
    }
  }
  for (const key of CONSTRAINT_KEYS) {
    if (!(key in input)) {
      errors.push(`Missing constraint key: ${key}`);
      continue;
    }
    if (!CONSTRAINT_MODES.includes(input[key])) {
      errors.push(
        `Invalid mode for ${key}: ${input[key]} (expected one of ${CONSTRAINT_MODES.join(", ")})`
      );
    }
  }
  return errors;
}
