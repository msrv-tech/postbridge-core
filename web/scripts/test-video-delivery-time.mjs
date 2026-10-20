import assert from 'node:assert/strict'
import { test } from 'node:test'
import { resolveDeliveryTime, deliveryLocalInput } from '../src/videoDeliveryTime.js'

test('wall time uses the selected timezone regardless of the browser timezone', () => {
  assert.equal(resolveDeliveryTime('2026-10-20T09:00', 'Asia/Novosibirsk').iso, '2026-10-20T02:00:00.000Z')
  assert.equal(resolveDeliveryTime('2026-10-20T09:00', 'Asia/Kathmandu').iso, '2026-10-20T03:15:00.000Z')
  assert.equal(deliveryLocalInput('2026-10-20T03:15:00Z', 'Asia/Kathmandu'), '2026-10-20T09:00')
})

test('daylight-saving gaps and repeated hours require a different wall time', () => {
  assert.equal(resolveDeliveryTime('2027-03-14T02:30', 'America/New_York').status, 'missing')
  assert.equal(resolveDeliveryTime('2026-11-01T01:30', 'America/New_York').status, 'ambiguous')
  assert.equal(resolveDeliveryTime('2026-11-01T03:30', 'America/New_York').iso, '2026-11-01T08:30:00.000Z')
  assert.equal(resolveDeliveryTime('2026-10-04T02:15', 'Australia/Lord_Howe').status, 'missing')
})

test('bad dates and unknown timezones cannot become delivery instants', () => {
  assert.equal(resolveDeliveryTime('2027-02-30T09:00', 'UTC').status, 'invalid')
  assert.equal(resolveDeliveryTime('2027-02-01T09:00', 'Unknown/Zone').status, 'invalid')
  assert.equal(resolveDeliveryTime('', 'UTC').status, 'invalid')
})
