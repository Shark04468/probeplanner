import fs from 'fs';
import path from 'path';

function read(relPath) {
  return fs.readFileSync(path.resolve(__dirname, '..', '..', relPath), 'utf8');
}

describe('i18n hardcode guard', () => {
  test('ResultsPanel uses i18n keys instead of inline bilingual literals', () => {
    const content = read('src/components/ResultsPanel.jsx');
    expect(content).not.toMatch(/\blang\s*===\s*['"]cn['"]\s*\?/);
    expect(content).not.toMatch(/\bfunction\s+l\s*\(/);
  });

  test('Brain3DView uses i18n keys instead of inline bilingual maps', () => {
    const content = read('src/components/Brain3DView.jsx');
    expect(content).not.toMatch(/\blang\s*===\s*['"]cn['"]\s*\?/);
    expect(content).not.toMatch(/\bconst\s+i18n\s*=\s*lang\s*===/);
  });
});
