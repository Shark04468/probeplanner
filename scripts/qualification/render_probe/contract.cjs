const assert = require('node:assert/strict');

function inspect(result) {
  assert.equal(result.kind, 'synthetic_webgl_capability_only');
  assert.ok(['default_headless_shell', 'chromium_channel_headless'].includes(result.mode));
  const webgl = result.webgl;
  const pixelCorrect = Boolean(webgl?.centerPixel && [51, 153, 102, 255]
    .every((x, i) => Math.abs(x - webgl.centerPixel[i]) <= 1));
  const basicRendering = !result.error && webgl?.available === true &&
    webgl?.linked === true && webgl?.contextLost === false &&
    webgl?.drawError === 0 && webgl?.frames === 24 && pixelCorrect;
  return { basicRendering: Boolean(basicRendering), pixelCorrect,
    actualApplicationTested: false, fullMacWorkflowAccepted: false, anatomyAccepted: false };
}

module.exports = { inspect };
