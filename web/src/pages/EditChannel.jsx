import { useState, useEffect } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import { getChannelRegistryItem, updateChannelRegistryItem } from '../adapters/channels'
import AppShell from '../components/AppShell'
import { useI18n } from '../i18n'

function platformLabel(platform, t) {
  const labels = {
    telegram: 'Telegram',
    max: 'MAX',
    vk: 'VK',
    zen: 'RSS',
    rss: 'RSS',
    postbridge: 'Postbridge',
  }
  return labels[platform] || platform
}

export default function EditChannel() {
  const { t } = useI18n()
  const { workspaceId, channelId } = useParams()
  const navigate = useNavigate()
  const [channel, setChannel] = useState(null)
  const [title, setTitle] = useState('')
  const [canRead, setCanRead] = useState(false)
  const [canWrite, setCanWrite] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!workspaceId || !channelId) return
    getChannelRegistryItem(workspaceId, channelId)
      .then((ch) => {
        setChannel(ch)
        setTitle(ch.title || '')
        setCanRead(ch.can_read ?? false)
        setCanWrite(ch.can_write ?? false)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [workspaceId, channelId])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setSaving(true)
    try {
      await updateChannelRegistryItem(workspaceId, channelId, {
        title: title.trim() || channel?.platform_channel_id,
        can_read: canRead,
        can_write: canWrite,
      })
      navigate(`/workspaces/${workspaceId}/channels?success=channel_updated`)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <AppShell title={t('editChannel.title')} actions={<Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">{t('common.back')}</Link>}>
        <p className="muted">{t('common.loading')}</p>
      </AppShell>
    )
  }

  if (!channel) {
    return (
      <AppShell title={t('editChannel.title')} actions={<Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">{t('common.back')}</Link>}>
        <p className="error">{t('editChannel.notFound')}</p>
        <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary">{t('common.openWorkspace')}</Link>
      </AppShell>
    )
  }

  const displayId = channel.platform === 'rss' && channel.rss_feed_url
    ? channel.rss_feed_url
    : channel.platform_channel_id

  return (
    <AppShell
      title={t('editChannel.title')}
      subtitle={`${platformLabel(channel.platform, t)}: ${channel.title || displayId}`}
      actions={
        <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">
          {t('common.back')}
        </Link>
      }
    >
      <div className="card">
        <h3>{t('editChannel.settings.title')}</h3>
        <p className="muted" style={{ marginBottom: '1rem' }}>
          {t('editChannel.settings.text')}
        </p>
        {channel.platform === 'rss' && (channel.rss_feed_url || (channel.platform_channel_id?.startsWith('http') && channel.platform_channel_id)) && (
          <p className="section-copy" style={{ marginBottom: '1rem', fontSize: '0.9rem' }}>
            <strong>{t('common.link')}:</strong>{' '}
            <a href={channel.rss_feed_url || channel.platform_channel_id} target="_blank" rel="noopener noreferrer">
              {channel.rss_feed_url || channel.platform_channel_id}
            </a>
          </p>
        )}
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="edit-title">{t('common.title')}</label>
            <input
              id="edit-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={channel.platform_channel_id}
              className="form-control"
            />
          </div>
          {channel.platform !== 'postbridge' && (
            <div className="form-group">
              <div
                className="toggle-row"
                role="button"
                tabIndex={0}
                onClick={() => setCanRead((v) => !v)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setCanRead((v) => !v)
                  }
                }}
                aria-pressed={canRead}
              >
                <span className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={canRead}
                    onChange={(e) => setCanRead(e.target.checked)}
                    tabIndex={-1}
                    aria-hidden
                  />
                  <span className="toggle-track">
                    <span className="toggle-thumb" />
                  </span>
                </span>
                <span className="toggle-label">{t('channel.canRead')}</span>
              </div>
            </div>
          )}
          {channel.platform !== 'postbridge' && (
            <div className="form-group">
              <div
                className="toggle-row"
                role="button"
                tabIndex={0}
                onClick={() => setCanWrite((v) => !v)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setCanWrite((v) => !v)
                  }
                }}
                aria-pressed={canWrite}
              >
                <span className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={canWrite}
                    onChange={(e) => setCanWrite(e.target.checked)}
                    tabIndex={-1}
                    aria-hidden
                  />
                  <span className="toggle-track">
                    <span className="toggle-thumb" />
                  </span>
                </span>
                <span className="toggle-label">{t('channel.canWrite')}</span>
              </div>
            </div>
          )}
          {error && <p className="error">{error}</p>}
          <div className="inline-actions" style={{ marginTop: '1rem' }}>
            <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary">
              {t('common.cancel')}
            </Link>
            <button type="submit" className="btn" disabled={saving}>
              {saving ? t('common.saving') : t('common.save')}
            </button>
          </div>
        </form>
      </div>
    </AppShell>
  )
}
