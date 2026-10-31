import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeAuthReturnTo } from '../src/adapters/authReturnTo.js'
globalThis.window = { location: { origin: 'https://postbridge.ru' } }
test('auth returns only to approved same-origin authorization pages', () => {
  assert.equal(normalizeAuthReturnTo('/external-apps/moysklad-app/authorize?request=abc'), '/external-apps/moysklad-app/authorize?request=abc')
  assert.equal(normalizeAuthReturnTo('/oauth/authorize?state=123'), '/oauth/authorize?state=123')
  for (const unsafe of ['//evil.example/oauth/authorize', '/\\evil.example/oauth/authorize', 'https://evil.example', '/workspaces/anything', '/external-apps/moysklad-app/authorize/evil', 'javascript:alert(1)']) assert.equal(normalizeAuthReturnTo(unsafe), null)
})
