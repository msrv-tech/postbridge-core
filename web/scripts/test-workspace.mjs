import assert from 'node:assert/strict'
import { beforeEach, test } from 'node:test'
import { canManageWorkspaceBilling, preferredWorkspaceId, rememberWorkspace, workspaceEntryPath, workspaceSwitchPath } from '../src/adapters/workspace.js'
const storage = new Map()
globalThis.localStorage = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) }
const user = { user_id: 'alice', workspaces: [{ id: 'one', role: 'owner' }, { id: 'two', role: 'member' }] }
beforeEach(() => storage.clear())
test('remembers membership per account and gives explicit links precedence', () => {
  rememberWorkspace(user, 'two')
  assert.equal(workspaceEntryPath(user), '/workspaces/two/content')
  assert.equal(preferredWorkspaceId(user, 'one'), 'one')
  assert.equal(preferredWorkspaceId({ ...user, user_id: 'bob' }), 'one')
  assert.equal(preferredWorkspaceId({ ...user, workspaces: [{ id: 'one' }] }), 'one')
  rememberWorkspace(user, 'outsider')
  assert.equal(preferredWorkspaceId(user), 'two')
})
test('switches to the matching section without object IDs or payment state', () => {
  for (const [source, section] of [
    ['content/post-old', 'content'], ['content/new', 'content'], ['content/videos/project-old?version=3', 'content/videos'],
    ['content/video-campaigns?source=old', 'content/video-campaigns'], ['channels/old/edit', 'channels'],
    ['settings?checkout=old&billing=lava_return', 'settings'], ['agents/candidates/old', 'agents/candidates'],
    ['agents/runs/old', 'agents/topic-scout'], ['agents/videos', 'agents/videos'],
  ]) assert.equal(workspaceSwitchPath(`/workspaces/one/${source}`, 'two'), `/workspaces/two/${section}`)
})
test('billing permissions match each membership, including a platform admin with a member role', () => {
  assert.equal(canManageWorkspaceBilling(user, 'one'), true)
  assert.equal(canManageWorkspaceBilling(user, 'two'), false)
  assert.equal(canManageWorkspaceBilling({ ...user, is_platform_admin: true }, 'two'), false)
  assert.equal(canManageWorkspaceBilling({ ...user, workspaces: [{ id: 'one', role: 'owner', can_manage_billing: false }] }, 'one'), false)
})
