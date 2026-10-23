import assert from 'node:assert/strict'
import test from 'node:test'
import { estimateVideoUnits } from '../src/videoBudget.js'

const caps = { reservation_units: 500000, video_scene_reserve_units: 321071, reservation_available_units: 2000000 }

test('native estimate uses the gateway quote and fits the remaining workspace pool', () => {
  const video = { mode: 'autopilot', generated_visuals: 'video', duration_seconds: 10 }
  assert.equal(estimateVideoUnits(caps, video), 1142142)
  assert.equal(estimateVideoUnits({ ...caps, reservation_available_units: 703592 }, video), 703592)
})

test('manual native scenes do not reserve an unrelated AI allowance', () => {
  assert.equal(estimateVideoUnits(caps, { mode: 'manual', scenes: [{ generation_kind: 'video' }] }), 321071)
  assert.equal(estimateVideoUnits(caps, { mode: 'manual', scenes: [{ asset_id: 'own-media' }] }), 0)
})
