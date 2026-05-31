const fs = require("fs");
const path = require("path");

const projectRoot = path.resolve(__dirname, "..");

function patchFile(filePath, transform) {
  if (!fs.existsSync(filePath)) return { changed: false, reason: "missing" };
  const original = fs.readFileSync(filePath, "utf8");
  const updated = transform(original);
  if (updated === original) return { changed: false, reason: "already" };
  fs.writeFileSync(filePath, updated, "utf8");
  return { changed: true, reason: "patched" };
}

function patchCheckRequiredFiles() {
  const filePath = path.join(
    projectRoot,
    "node_modules",
    "react-dev-utils",
    "checkRequiredFiles.js"
  );
  return patchFile(filePath, (src) =>
    src.replace("fs.accessSync(filePath, fs.F_OK);", "fs.accessSync(filePath, fs.constants.F_OK);")
  );
}

function patchWebpackDevServerConfig() {
  const filePath = path.join(
    projectRoot,
    "node_modules",
    "react-scripts",
    "config",
    "webpackDevServer.config.js"
  );
  return patchFile(filePath, (src) => {
    if (src.includes("setupMiddlewares(middlewares, devServer)")) return src;
    const blockRegex =
      /onBeforeSetupMiddleware\(devServer\)\s*\{[\s\S]*?\n\s*\},\s*onAfterSetupMiddleware\(devServer\)\s*\{[\s\S]*?\n\s*\},/m;
    if (!blockRegex.test(src)) return src;
    const replacement = `setupMiddlewares(middlewares, devServer) {
      // Keep \`evalSourceMapMiddleware\` before redirect middleware.
      devServer.app.use(evalSourceMapMiddleware(devServer));

      if (fs.existsSync(paths.proxySetup)) {
        // This registers user provided middleware for proxy reasons.
        require(paths.proxySetup)(devServer.app);
      }

      // Redirect to \`PUBLIC_URL\` or \`homepage\` from \`package.json\` if url not match.
      devServer.app.use(redirectServedPath(paths.publicUrlOrPath));

      // Reset any previously registered service worker in development.
      devServer.app.use(noopServiceWorkerMiddleware(paths.publicUrlOrPath));

      return middlewares;
    },`;
    return src.replace(blockRegex, replacement);
  });
}

function main() {
  const results = [];
  results.push(["checkRequiredFiles.js", patchCheckRequiredFiles()]);
  results.push(["webpackDevServer.config.js", patchWebpackDevServerConfig()]);

  for (const [name, info] of results) {
    console.log(`[fix-react-warnings] ${name}: ${info.reason}${info.changed ? "" : " (no change)"}`);
  }
}

main();

