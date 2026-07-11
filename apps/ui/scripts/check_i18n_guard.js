#!/usr/bin/env node
/* eslint-disable no-console */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const SRC_ROOT = path.join(ROOT, 'src');

function collectSourceFiles(dir) {
  const out = [];
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === '__pycache__') continue;
        stack.push(full);
        continue;
      }
      if (!/\.(js|jsx)$/.test(entry.name)) continue;
      if (/\.test\.(js|jsx)$/.test(entry.name)) continue;
      out.push(full);
    }
  }
  return out;
}

const TARGETS = collectSourceFiles(SRC_ROOT)
  .filter((file) => {
    const rel = path.relative(SRC_ROOT, file).replace(/\\/g, '/');
    if (rel === 'ProbeplannerGUI.jsx') return true;
    if (rel.startsWith('components/')) return true;
    return false;
  })
  .filter((file) => !file.endsWith(path.join('constants', 'i18n.js')));

const RULES = [
  {
    id: 'no-i18n-index',
    pattern: /\bI18N\s*\[\s*lang\s*\]/g,
    message: 'Do not index I18N by lang directly in components; use t(key, lang).',
  },
  {
    id: 'no-copy-by-lang',
    pattern: /\bcopyByLang\b/g,
    message: 'Do not use copyByLang; all text must come from i18n keys.',
  },
  {
    id: 'no-lang-branch',
    pattern: /\blang\s*([!=]==?)\s*['"](cn|en)['"]/g,
    message: 'Do not hard-branch by lang in UI copy; use t(key, lang).',
  },
  {
    id: 'no-local-i18n-map',
    pattern: /\bconst\s+\w+\s*=\s*\{\s*cn\s*:/g,
    message: 'Do not define local cn/en maps in component files; centralize in constants/i18n.js.',
  },
  {
    id: 'no-hardcoded-cjk-string',
    // Allow explicit ignore marker for unavoidable literals:
    //   // i18n-guard-ignore-next-line
    pattern: /(['"`])(?:\\.|(?!\1).)*[\u3400-\u9FFF](?:\\.|(?!\1).)*\1/g,
    message: 'Detected hardcoded CJK string literal. Move it to constants/i18n.js and use t().',
    allowIgnoreMarker: true,
  },
];

function lineNumberAt(content, index) {
  let line = 1;
  for (let i = 0; i < index; i += 1) {
    if (content.charCodeAt(i) === 10) line += 1;
  }
  return line;
}

async function main() {
  let failed = false;
  for (const file of TARGETS) {
    const content = fs.readFileSync(file, 'utf8');
    const rel = path.relative(ROOT, file);
    const lines = content.split(/\r?\n/);

    for (const rule of RULES) {
      rule.pattern.lastIndex = 0;
      let match;
      while ((match = rule.pattern.exec(content)) !== null) {
        const line = lineNumberAt(content, match.index);
        if (rule.allowIgnoreMarker && line > 1) {
          const prev = lines[line - 2] || '';
          if (prev.includes('i18n-guard-ignore-next-line')) continue;
        }
        console.error(`[i18n-guard] ${rel}:${line}: ${rule.message} (${rule.id})`);
        failed = true;
      }
    }
  }

  const usedI18nKeys = new Set();
  const keyPattern = /\b(?:t|tr)\(\s*['"]([^'"]+)['"]/g;
  for (const file of TARGETS) {
    const content = fs.readFileSync(file, 'utf8');
    keyPattern.lastIndex = 0;
    let match;
    while ((match = keyPattern.exec(content)) !== null) {
      usedI18nKeys.add(match[1]);
    }
  }

  const i18nModulePath = path.join(SRC_ROOT, 'constants', 'i18n.js');
  const { I18N } = await import(pathToFileURL(i18nModulePath).href);
  const allowSameCnEn = new Set([
    'angleUnit',
    'atlasModeAllenSdk',
    'atlasModeQiu2018',
    'brain3dBregma',
    'brain3dLambda',
    'brain3dSliceCoordAp',
    'brain3dSliceCoordDv',
    'brain3dSliceCoordMl',
    'calibrationBrierLabel',
    'calibrationEceLabel',
  ]);

  for (const key of usedI18nKeys) {
    const cn = I18N.cn?.[key];
    const en = I18N.en?.[key];
    if (!cn || !en) continue;
    if (cn === en && !allowSameCnEn.has(key)) {
      console.error(`[i18n-guard] key="${key}" has identical cn/en text. Add CN translation in constants/i18n.js.`);
      failed = true;
    }
  }

  if (failed) process.exit(1);
  console.log(`[i18n-guard] OK (${TARGETS.length} files, ${usedI18nKeys.size} keys)`);
}

main().catch((err) => {
  console.error('[i18n-guard] fatal:', err && err.stack ? err.stack : err);
  process.exit(1);
});
