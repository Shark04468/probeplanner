import { I18N } from './i18n';

describe('i18n dictionaries', () => {
  test('cn and en expose the same key set', () => {
    const cnKeys = Object.keys(I18N.cn || {}).sort();
    const enKeys = Object.keys(I18N.en || {}).sort();
    expect(cnKeys).toEqual(enKeys);
  });

  test('all values are non-empty strings', () => {
    for (const lang of ['cn', 'en']) {
      for (const value of Object.values(I18N[lang] || {})) {
        expect(typeof value).toBe('string');
        expect(value.length).toBeGreaterThan(0);
        expect(value.includes('\uFFFD')).toBe(false);
      }
    }
  });
});
