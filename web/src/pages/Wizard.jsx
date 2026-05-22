import { useState, useEffect, useMemo } from 'react'
import { Link, useParams, useNavigate, useLocation } from 'react-router-dom'
import { createChannelRegistryItem, createConnection, listChannelRegistry } from '../adapters/channels'
import AppShell from '../components/AppShell'
import { useI18n } from '../i18n'

function platformLabel(platform, t) {
  const labels = { telegram: 'TG', max: 'MAX', vk: 'VK', zen: 'RSS', rss: 'RSS', postbridge: 'Postbridge' }
  return labels[platform] || platform
}

export default function Wizard() {
  const { t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [channels, setChannels] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sourceChannelId, setSourceChannelId] = useState('')
  const [targetValue, setTargetValue] = useState('')
  const [creating, setCreating] = useState(false)
  const [creatingRssTarget, setCreatingRssTarget] = useState(false)

  useEffect(() => {
    if (!workspaceId) return
    setError('')
    listChannelRegistry(workspaceId)
      .then((res) => setChannels(res.items || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [workspaceId])

  const sourceChannels = useMemo(() => channels.filter((ch) => ch.can_read), [channels])
  const targetChannels = useMemo(() => channels.filter((ch) => ch.can_write), [channels])

  const sourceChannel = sourceChannels.find((ch) => ch.id === sourceChannelId)
  const targetChannel = targetValue ? targetChannels.find((ch) => ch.id === targetValue) : null

  useEffect(() => {
    if (!channels.length) return
    const externalTarget = targetChannels.find((ch) => ch.platform !== 'postbridge')
    const postbridgeSource = sourceChannels.find((ch) => ch.platform === 'postbridge')
    const externalSource = sourceChannels.find((ch) => ch.platform !== 'postbridge')
    if (!sourceChannelId && sourceChannels.length > 0) {
      const preferredSource = externalTarget
        ? postbridgeSource || externalSource || sourceChannels[0]
        : externalSource || postbridgeSource || sourceChannels[0]
      setSourceChannelId(preferredSource.id)
    }
    if (!targetValue) {
      setTargetValue(externalTarget?.id || targetChannels[0]?.id || '')
    }
  }, [channels, sourceChannelId, sourceChannels, targetChannels, targetValue])

  const handleCreate = async () => {
    if (!sourceChannel) return
    if (!targetChannel) return
    setError('')
    setCreating(true)
    try {
      const body = {
        source_platform: sourceChannel.platform,
        source_channel_id: sourceChannel.platform_channel_id,
        source_display: sourceChannel.title || sourceChannel.platform_channel_id,
        target_platform: targetChannel.platform,
        target_channel_id: targetChannel.platform_channel_id,
        target_display: targetChannel.title || targetChannel.platform_channel_id,
        requested_limit: 0,
      }
      if (sourceChannel.credentials_ref && sourceChannel.credentials_ref !== 'env') {
        body.source_credentials_id = sourceChannel.credentials_ref
      }
      if (targetChannel.credentials_ref && targetChannel.credentials_ref !== 'env') {
        body.target_credentials_id = targetChannel.credentials_ref
      }
      await createConnection(workspaceId, body)
      navigate(`/workspaces/${workspaceId}/channels?success=channel_connected`)
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  const handleCreateRssTarget = async () => {
    setError('')
    setCreatingRssTarget(true)
    try {
      const created = await createChannelRegistryItem(workspaceId, {
        platform: 'rss',
        platform_channel_id: 'rss',
        title: 'RSS',
        can_read: false,
        can_write: true,
      })
      const nextChannels = [...channels, created]
      setChannels(nextChannels)
      setTargetValue(created.id)
    } catch (e) {
      setError(e.message)
    } finally {
      setCreatingRssTarget(false)
    }
  }

  const canCreate = sourceChannel && targetChannel

  if (loading) {
    return (
      <AppShell
        title={t('wizard.title')}
        subtitle={t('wizard.loadingSubtitle')}
        actions={
          <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">
            {t('common.back')}
          </Link>
        }
      >
        <p className="muted">{t('common.loading')}</p>
      </AppShell>
    )
  }

  if (channels.length === 0) {
    return (
      <AppShell
        title={t('wizard.title')}
        subtitle={t('wizard.noChannelsSubtitle')}
        actions={
          <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">
            {t('common.back')}
          </Link>
        }
      >
        <div className="card empty-state">
          <h3>{t('wizard.noChannels.title')}</h3>
          <p>{t('wizard.noChannels.text')}</p>
          <Link to={`/workspaces/${workspaceId}/channels/add`} className="btn" style={{ marginTop: '1rem' }}>
            {t('addChannel.title')}
          </Link>
        </div>
      </AppShell>
    )
  }

  if (sourceChannels.length === 0) {
    return (
      <AppShell
        title={t('wizard.title')}
        subtitle={t('wizard.noSourcesSubtitle')}
        actions={
          <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">
            {t('common.back')}
          </Link>
        }
      >
        <div className="card empty-state">
          <h3>{t('wizard.noSources.title')}</h3>
          <p>{t('wizard.noSources.text')}</p>
          <Link to={`/workspaces/${workspaceId}/channels/add`} className="btn" style={{ marginTop: '1rem' }}>
            {t('addChannel.title')}
          </Link>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell
      title={t('wizard.title')}
      subtitle={t('wizard.subtitle')}
      actions={
        <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">
          {t('common.back')}
        </Link>
      }
    >
      <div className="wizard-layout">
        {new URLSearchParams(location.search).get('success') === 'channel_added' && (
          <div className="card" style={{ borderColor: 'var(--primary)', background: 'rgba(59, 130, 246, 0.08)', gridColumn: '1 / -1' }}>
            <p className="success">{t('wizard.channelAdded')}</p>
          </div>
        )}
        <div className="card">
          <h3>{t('wizard.create.title')}</h3>
          <p className="section-copy muted">
            {t('wizard.create.text')}
          </p>

          <div className="form-group">
            <label htmlFor="source-channel-id">{t('wizard.sourceChannel')}</label>
            <select
              id="source-channel-id"
              value={sourceChannelId}
              onChange={(e) => setSourceChannelId(e.target.value)}
              className="form-control"
            >
              <option value="">{t('common.select')}</option>
              {sourceChannels.map((ch) => (
                <option key={ch.id} value={ch.id}>
                  {platformLabel(ch.platform, t)} {ch.title || ch.platform_channel_id}
                </option>
              ))}
            </select>
            {sourceChannel?.live_sync_source_supported && (
              <p className="muted" style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>
                <span className="badge badge-running" style={{ marginRight: '0.35rem', verticalAlign: 'middle' }}>
                  Live sync
                </span>
                {t('wizard.liveSyncHint')}
              </p>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="target-channel-id">{t('wizard.targetChannel')}</label>
            <select
              id="target-channel-id"
              value={targetValue}
              onChange={(e) => setTargetValue(e.target.value)}
              className="form-control"
            >
              <option value="">{t('common.select')}</option>
              {targetChannels.map((ch) => (
                <option key={ch.id} value={ch.id}>
                  {platformLabel(ch.platform, t)} {ch.title || ch.platform_channel_id}
                </option>
              ))}
            </select>
            {targetChannels.length === 0 && (
              <p className="muted" style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>
                {t('wizard.noTargetHint')}
              </p>
            )}
            {targetChannels.length === 0 && (
              <div className="inline-actions" style={{ marginTop: '0.75rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleCreateRssTarget}
                  disabled={creatingRssTarget}
                >
                  {creatingRssTarget ? t('wizard.creatingRssTarget') : t('wizard.createRssTarget')}
                </button>
                <Link to={`/workspaces/${workspaceId}/channels/add`} className="btn btn-secondary">
                  {t('wizard.addRealTarget')}
                </Link>
              </div>
            )}
          </div>

          {error && <p className="error">{error}</p>}

          <div className="inline-actions" style={{ marginTop: '1rem' }}>
            <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary">
              {t('common.cancel')}
            </Link>
            <button
              type="button"
              className="btn"
              onClick={handleCreate}
              disabled={!canCreate || creating}
            >
              {creating ? t('wizard.creating') : t('wizard.createBridge')}
            </button>
          </div>
        </div>

        <div className="card">
          <h3>{t('wizard.before.title')}</h3>
          <ul className="check-list">
            <li>{t('wizard.before.item1')}</li>
            <li>{t('wizard.before.item2')}</li>
          </ul>
        </div>
      </div>
    </AppShell>
  )
}
