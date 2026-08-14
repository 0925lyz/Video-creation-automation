const {existsSync} = require("node:fs");
const {join} = require("node:path");
const {spawnSync} = require("node:child_process");

const root = join(__dirname, "..", "src", "jaguartv_factory", "remotion_template");
const tsc = join(root, "node_modules", "typescript", "bin", "tsc");

if (!existsSync(tsc)) {
  const install = spawnSync("npm", ["install", "--no-audit", "--no-fund"], {
    cwd: root,
    stdio: "inherit",
  });
  if (install.status !== 0) {
    process.exit(install.status || 1);
  }
}

const result = spawnSync(
  process.execPath,
  [
    tsc,
    "src/index.tsx",
    "--noEmit",
    "--jsx", "react-jsx",
    "--module", "esnext",
    "--moduleResolution", "node",
    "--target", "es2022",
    "--skipLibCheck",
  ],
  {cwd: root, stdio: "inherit"},
);

process.exit(result.status || 0);
