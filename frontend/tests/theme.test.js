import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

import {
  THEME_STORAGE_KEY, applyTheme, otherTheme, readSavedTheme, resolveTheme, saveTheme, toggleLabel,
} from '../src/theme.js'

function memoryStorage(initial = {}) {
  const data = { ...initial }
  return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v) }, data }
}
const brokenStorage = { getItem() { throw new Error('denied') }, setItem() { throw new Error('denied') } }

test('first visit follows the operating system', () => {
  assert.equal(resolveTheme(null, true), 'dark')
  assert.equal(resolveTheme(null, false), 'light')
})

test('a saved choice wins over the operating system; junk is ignored', () => {
  assert.equal(resolveTheme('light', true), 'light')
  assert.equal(resolveTheme('dark', false), 'dark')
  assert.equal(resolveTheme('purple', true), 'dark')
})

test('choices are saved and read back; unavailable storage is harmless', () => {
  const storage = memoryStorage()
  assert.equal(readSavedTheme(storage), null)
  assert.equal(saveTheme(storage, 'dark'), true)
  assert.equal(storage.data[THEME_STORAGE_KEY], 'dark')
  assert.equal(readSavedTheme(storage), 'dark')
  assert.equal(readSavedTheme(memoryStorage({ [THEME_STORAGE_KEY]: 'neon' })), null)
  assert.equal(readSavedTheme(brokenStorage), null)
  assert.equal(saveTheme(brokenStorage, 'dark'), false)
  assert.equal(readSavedTheme(undefined), null)
})

test('toggle flips the theme and its label names the target', () => {
  assert.equal(otherTheme('light'), 'dark')
  assert.equal(otherTheme('dark'), 'light')
  assert.equal(toggleLabel('light'), 'Switch to dark mode')
  assert.equal(toggleLabel('dark'), 'Switch to light mode')
})

test('applyTheme sets the attribute and native colour scheme', () => {
  const root = { dataset: {}, style: {} }
  applyTheme(root, 'dark')
  assert.deepEqual([root.dataset.theme, root.style.colorScheme], ['dark', 'dark'])
})

// The pre-paint script must make the same decision as resolveTheme.
function runInitScript({ saved, prefersDark, storageThrows = false }) {
  const attrs = {}
  const documentElement = { style: {}, setAttribute: (k, v) => { attrs[k] = v } }
  const context = {
    window: {
      localStorage: storageThrows ? brokenStorage : memoryStorage(saved ? { [THEME_STORAGE_KEY]: saved } : {}),
      matchMedia: (q) => ({ matches: q === '(prefers-color-scheme: dark)' && prefersDark }),
    },
    document: { documentElement },
  }
  vm.runInNewContext(readFileSync(new URL('../public/theme-init.js', import.meta.url), 'utf8'), context)
  return [attrs['data-theme'], documentElement.style.colorScheme]
}

test('pre-paint script agrees with resolveTheme in every case', () => {
  for (const saved of [null, 'light', 'dark', 'junk']) {
    for (const prefersDark of [true, false]) {
      const expected = resolveTheme(saved, prefersDark)
      assert.deepEqual(runInitScript({ saved, prefersDark }), [expected, expected], `${saved}/${prefersDark}`)
    }
  }
  assert.deepEqual(runInitScript({ prefersDark: true, storageThrows: true }), ['dark', 'dark'])
})
