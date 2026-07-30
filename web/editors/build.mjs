import { build } from "esbuild";
import { createHash } from "node:crypto";
import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const output = join(root, "dist");

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });

const buildResult = await build({
  entryPoints: {
    "docx-editor": join(root, "src", "docx-editor.js"),
    "sheet-editor": join(root, "src", "sheet-editor.js")
  },
  outdir: output,
  bundle: true,
  minify: true,
  sourcemap: false,
  metafile: true,
  legalComments: "none",
  platform: "browser",
  format: "iife",
  target: ["chrome120"],
  entryNames: "[name]",
  assetNames: "[name]",
  loader: {
    ".woff": "dataurl",
    ".woff2": "dataurl",
    ".ttf": "dataurl",
    ".svg": "dataurl",
    ".png": "dataurl"
  }
});

for (const name of [
  "docx.html",
  "sheet.html",
  "html.html",
  "html-editor.js",
  "editor.css"
]) {
  await cp(join(root, "static", name), join(output, name));
}

for (const [source, target] of [
  [join(root, "node_modules", "@hufe921", "canvas-editor", "LICENSE"), "LICENSE-CANVAS-EDITOR.txt"],
  [join(root, "node_modules", "@hufe921", "canvas-editor-plugin-docx", "LICENSE"), "LICENSE-CANVAS-EDITOR-DOCX.txt"],
  [join(root, "node_modules", "@univerjs", "preset-sheets-core", "LICENSE"), "LICENSE-UNIVER.txt"],
  [join(root, "node_modules", "buffer", "LICENSE"), "LICENSE-BUFFER.txt"]
]) {
  await cp(source, join(output, target));
}

function packageRootFromInput(inputPath) {
  const absolute = (isAbsolute(inputPath) ? inputPath : resolve(inputPath))
    .replaceAll("\\", "/");
  const marker = "/node_modules/";
  const markerIndex = absolute.lastIndexOf(marker);
  if (markerIndex < 0) return "";
  const packageStart = markerIndex + marker.length;
  const remainder = absolute.slice(packageStart);
  const parts = remainder.split("/");
  const packageParts = parts[0].startsWith("@") ? parts.slice(0, 2) : parts.slice(0, 1);
  return absolute.slice(0, packageStart) + packageParts.join("/");
}

const packageRoots = new Set();
for (const inputPath of Object.keys(buildResult.metafile.inputs)) {
  const packageRoot = packageRootFromInput(inputPath);
  if (packageRoot) packageRoots.add(packageRoot);
}

const packages = [];
const licenseSections = new Map();

function personText(person) {
  if (!person) return "";
  if (typeof person === "string") return person;
  return [
    person.name || "",
    person.email ? `<${person.email}>` : "",
    person.url ? `(${person.url})` : ""
  ].filter(Boolean).join(" ");
}

function mitTermsForPackage(manifest) {
  const holders = [
    personText(manifest.author),
    ...(Array.isArray(manifest.contributors)
      ? manifest.contributors.map(personText)
      : [])
  ].filter(Boolean);
  const attribution = holders.length
    ? [...new Set(holders)].join("; ")
    : `${manifest.name} contributors`;
  return `Copyright (c) ${attribution}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.`;
}

function iscTermsForPackage(manifest) {
  const holder = personText(manifest.author) || `${manifest.name} contributors`;
  return `Copyright (c) ${holder}

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.`;
}

for (const packageRoot of [...packageRoots].sort()) {
  const manifest = JSON.parse(await readFile(join(packageRoot, "package.json"), "utf8"));
  const entries = await readdir(packageRoot, { withFileTypes: true });
  const licenseFiles = entries
    .filter(
      (entry) =>
        entry.isFile() &&
        /^(?:license|licence|copying|notice)(?:$|[._-])/i.test(entry.name)
    )
    .map((entry) => entry.name)
    .sort();
  let declaredLicense = String(manifest.license || "UNDECLARED");
  const licenseSources = licenseFiles.map((name) => ({
    path: join(packageRoot, name),
    label: name
  }));
  if (
    !licenseSources.length &&
    String(manifest.name || "").startsWith("@univerjs/") &&
    declaredLicense === "Apache-2.0"
  ) {
    licenseSources.push({
      path: join(root, "node_modules", "@univerjs", "core", "LICENSE"),
      label: "@univerjs/core/LICENSE (same Univer monorepo)"
    });
  }
  if (!licenseSources.length && declaredLicense === "MIT") {
    licenseSources.push({
      content: mitTermsForPackage(manifest),
      label: "package.json attribution + MIT terms"
    });
  }
  if (!licenseSources.length && declaredLicense === "ISC") {
    licenseSources.push({
      content: iscTermsForPackage(manifest),
      label: "package.json attribution + ISC terms"
    });
  }
  if (!licenseSources.length) {
    throw new Error(
      `Bundled dependency ${manifest.name}@${manifest.version} has no packaged license file.`
    );
  }
  if (declaredLicense === "UNDECLARED") {
    const probe = (
      licenseSources[0].content ||
      await readFile(licenseSources[0].path, "utf8")
    );
    if (/Apache License\s+Version 2\.0/i.test(probe)) {
      declaredLicense = "Apache-2.0 (LICENSE; package.json omitted)";
    } else if (/Permission is hereby granted, free of charge/i.test(probe)) {
      declaredLicense = "MIT (LICENSE; package.json omitted)";
    } else {
      throw new Error(
        `Bundled dependency ${manifest.name}@${manifest.version} does not declare a recognizable license.`
      );
    }
  }
  const packageIdentity = `${manifest.name}@${manifest.version}`;
  packages.push({
    identity: packageIdentity,
    license: declaredLicense,
    source: licenseSources.map((item) => item.label).join(", ")
  });
  for (const licenseSource of licenseSources) {
    const content = (
      licenseSource.content ||
      await readFile(licenseSource.path, "utf8")
    ).trim();
    const digest = createHash("sha256").update(content).digest("hex");
    const section = licenseSections.get(digest) || {
      content,
      sources: []
    };
    section.sources.push(`${packageIdentity}/${licenseSource.label}`);
    licenseSections.set(digest, section);
  }
}

packages.sort((left, right) => left.identity.localeCompare(right.identity, "en"));
const notices = [
  "# 文件编辑器第三方许可",
  "",
  "运行时只分发离线构建产物，不包含 `node_modules`、源码映射或开发工具。",
  "下表由构建脚本根据 esbuild 实际进入 bundle 的输入自动生成。",
  "",
  "| 依赖 | 声明许可证 | 许可文本来源 |",
  "| --- | --- | --- |",
  ...packages.map(
    (item) => `| \`${item.identity}\` | ${item.license} | ${item.source} |`
  ),
  "",
  "完整许可与 NOTICE 文本见 `THIRD_PARTY_LICENSES.txt`。构建会在任何实际打包依赖缺少许可文件时失败。",
  ""
].join("\n");
await writeFile(join(output, "THIRD_PARTY_NOTICES.md"), notices, "utf8");

const licenseText = [
  "DeepSeek Cowork offline deliverable editors — third-party license texts",
  "Generated from the exact packages included by esbuild.",
  "",
  ...[...licenseSections.values()].flatMap((section, index) => [
    "=".repeat(78),
    `License text ${index + 1}`,
    `Used by: ${section.sources.sort().join(", ")}`,
    "=".repeat(78),
    section.content,
    ""
  ])
].join("\n");
await writeFile(join(output, "THIRD_PARTY_LICENSES.txt"), licenseText, "utf8");
