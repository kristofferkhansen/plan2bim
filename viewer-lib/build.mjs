// Writes plan2bim/vendor/three.bundle.min.js from the versions in package-lock.json. Run: npm ci && npm run build
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const here = (path) => fileURLToPath(new URL(path, import.meta.url));
const version = (name) => JSON.parse(readFileSync(here(`node_modules/${name}/package.json`), "utf8")).version;
const out = here("../plan2bim/vendor/three.bundle.min.js");

await build({
  entryPoints: [here("entry.js")],
  outfile: out,
  bundle: true,
  format: "iife",
  globalName: "THREE",
  minify: true,
  target: "es2020",
  legalComments: "none",
  // the viewer is one HTML file: the text of this file stands in a <script> element
  charset: "ascii",
  banner: {
    js: `/**\n * @license\n * three.js ${version("three")} - Copyright 2010-2026 Three.js Authors - SPDX-License-Identifier: MIT\n`
      + ` * N8AO ${version("n8ao")} - N8python - SPDX-License-Identifier: CC0-1.0\n */`,
  },
});

const text = readFileSync(out);
for (const mark of ["</script", "<!--"]) {
  if (text.includes(mark)) throw new Error(`the bundle holds '${mark}': it cannot stand in a <script> element`);
}
console.log(`${out}\n${text.length} bytes, sha256 ${createHash("sha256").update(text).digest("hex")}`);
