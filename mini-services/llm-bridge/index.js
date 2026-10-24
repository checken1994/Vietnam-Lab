/**
 * Canonical Node.js ESM entrypoint for SCP LLM Bridge.
 *
 * Boot order:
 *   1. ./dist/index.js (prebuilt bundle) when present.
 *   2. ./core.ts loaded at runtime through jiti — supports jiti v1 AND v2
 *      export shapes. jiti is resolved from, in order:
 *        a. mini-services/llm-bridge/node_modules (self-contained install),
 *        b. dashboard/node_modules (shared-workspace fallback — jiti is a
 *           transitive dependency there, see README "Boot paths & jiti"),
 *        c. the historical dashboard jiti bundle path (last resort).
 *   If no usable jiti is found, exit with ONE explicit message instead of
 *   a raw TypeError / ERR_MODULE_NOT_FOUND (fail loud, fail clear).
 */
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const distFile = join(__dirname, "dist", "index.js");

if (existsSync(distFile)) {
  await import("./dist/index.js");
} else {
  const createJiti = await loadCreateJiti();
  const jiti = createJiti(__filename);
  await jiti.import("./core.ts");
}

/**
 * Resolve jiti's createJiti factory across jiti v1/v2 export shapes:
 *   - require("jiti")            -> { createJiti }                       (v1, v2)
 *   - import(".../lib/jiti.cjs") -> { createJiti, default: createJiti }  (v2)
 *   - import(".../dist/jiti.cjs")-> { default: createJiti }              (v2 bundle, no named export)
 *   - import(".../dist/jiti.cjs")-> { createJiti } / { default: { createJiti } } (v1)
 * Returns null when no candidate yields a function; caller fails loud.
 */
async function loadCreateJiti() {
  const candidates = [];

  // Package-aware resolution (respects the jiti "exports" map of v1/v2).
  for (const base of [__dirname, join(__dirname, "..", "..", "dashboard")]) {
    candidates.push({
      label: `require("jiti") from ${base}`,
      load: async () => createRequire(join(base, "package.json"))("jiti"),
    });
  }

  // Historical explicit path kept as a last-resort fallback for layouts
  // where package resolution is unavailable but the bundle file exists.
  const legacyBundle = join(
    __dirname, "..", "..", "dashboard", "node_modules", "jiti", "dist", "jiti.cjs",
  );
  candidates.push({
    label: `import("${legacyBundle}")`,
    load: async () => await import(pathToFileURL(legacyBundle).href),
  });

  for (const candidate of candidates) {
    let mod;
    try {
      mod = await candidate.load();
    } catch {
      continue; // jiti not installed for this candidate — try the next one
    }
    const createJiti =
      mod?.createJiti ??
      (typeof mod?.default === "function" ? mod.default : undefined) ??
      mod?.default?.createJiti ??
      mod?.default?.default;
    if (typeof createJiti === "function") return createJiti;
  }

  console.error(
    [
      "[scp-llm-bridge] FATAL: cannot load jiti, which is required to run ./core.ts when dist/index.js is absent.",
      "[scp-llm-bridge] Fix (choose one):",
      "[scp-llm-bridge]   1. cd mini-services/llm-bridge && npm install        (installs declared dep jiti — self-contained, recommended)",
      "[scp-llm-bridge]   2. cd dashboard && npm install                       (dashboard provides jiti as a shared fallback)",
      '[scp-llm-bridge]   3. Build the prebuilt bundle instead (see README "Build / Start").',
      "[scp-llm-bridge] Neither mini-services/llm-bridge/node_modules nor dashboard/node_modules contained a usable jiti (v1 or v2).",
    ].join("\n"),
  );
  process.exit(1);
}
