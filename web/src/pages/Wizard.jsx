import { useState, useEffect } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import { createConnection, listChannelRegistry } from '../adapters/channels'
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
  const [channels, setChannels] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sourceChannelId, setSourceChannelId] = useState('')
  const [targetValue, setTargetValue] = useState('') // '' | 'rss' | channelId
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (!workspaceId) return
    setError('')
    listChannelRegistry(workspaceId)
      .then((res) => setChannels(res.items || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [workspaceId])

  const sourceChannels = channels.filter((ch) => ch.can_read)
  const targetChannels = channels.filter((ch) => ch.can_write)

  const sourceChannel = sourceChannels.find((ch) => ch.id === sourceChannelId)
  const targetRss = targetValue === 'rss'
  const targetChannel = targetValue && targetValue !== 'rss' ? targetChannels.find((ch) => ch.id === targetValue) : null

  const handleCreate = async () => {
    if (!sourceChannel) return
    if (!targetRss && !targetChannel) return
    setError('')
    setCreating(true)
    try {
      const body = {
        source_platform: sourceChannel.platform,
        source_channel_id: sourceChannel.platform_channel_id,
        source_display: sourceChannel.title || sourceChannel.platform_channel_id,
        target_platform: targetRss ? 'rss' : targetChannel.platform,
        target_channel_id: targetRss ? '' : targetChannel.platform_channel_id,
        target_display: targetRss ? 'RSS' : (targetChannel.title || targetChannel.platform_channel_id),
        requested_limit: 0,
      }
      if (sourceChannel.credentials_ref && sourceChannel.credentials_ref !== 'env') {
        body.source_credentials_id = sourceChannel.credentials_ref
      }
      if (!targetRss && targetChannel.credentials_ref && targetChannel.credentials_ref !== 'env') {
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

  const canCreate = sourceChannel && (targetRss || targetChannel)

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
              <option value="rss">{t('wizard.rssTarget')}</option>
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
