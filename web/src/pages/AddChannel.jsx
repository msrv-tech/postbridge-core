import { useState, useEffect } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import {
  createChannelRegistryItem,
  createLinkedinAccessTokenCredential,
  createVkCommunityCredential,
  getLinkedinAuthorizeUrl,
  listLinkedinOrganizations,
  requestMaxChannelVerification,
  validateChannelRegistryItem,
  verifyMaxChannel,
} from '../adapters/channels'
import AppShell from '../components/AppShell'
import TelegramDeepLinkField from '../components/TelegramDeepLinkField'
import { listInstallationSecrets, upsertInstallationSecret } from '../adapters/installationSecrets'
import { isSelfhostMode } from '../adapters/runtime'
import { fetchTelegramWebLinkStatus, startTelegramWebLinkSession } from '../telegramWebLinkFlow'
import { useI18n } from '../i18n'

// VK: community token from the group settings.
const VK_OAUTH_ADD_ENABLED = true

const PLATFORMS = [
  { id: 'telegram', label: 'Telegram' },
  { id: 'max', label: 'MAX' },
  ...(VK_OAUTH_ADD_ENABLED ? [{ id: 'vk', label: 'VK' }] : []),
  { id: 'linkedin', label: 'LinkedIn' },
  { id: 'rss', label: 'RSS' },
  { id: 'postbridge', label: 'Postbridge' },
]

const DEFAULT_TARGET_PLATFORMS = new Set(['telegram', 'max', 'vk'])

const PLACEHOLDERS = {
  telegram: '@channel_name or -1001234567890',
  max: 'https://web.max.ru/-71838... or -71838691591553',
  vk: '-123456789 or vk.com/club123456789',
  linkedin: 'urn:li:organization:123456 or organization:123456',
  rss: 'https://example.com/feed.xml',
  postbridge: 'Workspace is used automatically',
}

const DEFAULT_TELEGRAM_BOT_NAME = typeof import.meta.env.VITE_TELEGRAM_BOT_NAME === 'string'
  ? import.meta.env.VITE_TELEGRAM_BOT_NAME.trim().replace(/^@/, '')
  : ''
const MAX_BOT_LINK = import.meta.env.VITE_MAX_BOT_URL || ''

export default function AddChannel() {
  const { t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const [platform, setPlatform] = useState('telegram')
  const [channelId, setChannelId] = useState('')
  const [title, setTitle] = useState('')
  const [asTarget, setAsTarget] = useState(DEFAULT_TARGET_PLATFORMS.has('telegram'))
  const [rssMode, setRssMode] = useState('source')
  const [validatedRead, setValidatedRead] = useState(false)
  const [validatedWrite, setValidatedWrite] = useState(false)
  const [validatedDisplay, setValidatedDisplay] = useState(null)
  const [loading, setLoading] = useState(false)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState('')
  // MAX: verification with a unique code.
  const [maxCode, setMaxCode] = useState(null)
  const [maxDeeplink, setMaxDeeplink] = useState(null)
  const [maxCodeRequested, setMaxCodeRequested] = useState(false)
  // Telegram: link email users through the bot.
  const [showLinkTelegram, setShowLinkTelegram] = useState(false)
  const [tgBindDeepLink, setTgBindDeepLink] = useState(null)
  const [tgBindSessionToken, setTgBindSessionToken] = useState(null)
  const [bindSuccess, setBindSuccess] = useState('')
  const [telegramBotName, setTelegramBotName] = useState(DEFAULT_TELEGRAM_BOT_NAME)
  const [telegramBotFormOpen, setTelegramBotFormOpen] = useState(false)
  const [telegramBotDraft, setTelegramBotDraft] = useState({ bot_token: '', bot_username: '' })
  const [telegramBotSaving, setTelegramBotSaving] = useState(false)
  const [telegramBotError, setTelegramBotError] = useState('')
  const [telegramBotSuccess, setTelegramBotSuccess] = useState('')
  // VK: community token, access_token and credential_ref after validation.
  const [vkAccessToken, setVkAccessToken] = useState('')
  const [vkCredentialsRef, setVkCredentialsRef] = useState('')
  const [linkedinAccessToken, setLinkedinAccessToken] = useState('')
  const [linkedinCredentialsRef, setLinkedinCredentialsRef] = useState('')
  const [linkedinOrganizations, setLinkedinOrganizations] = useState([])
  const isRssSource = platform === 'rss' && rssMode === 'source'
  const isRssTarget = platform === 'rss' && rssMode === 'target'
  const hasValidatedAccess = platform === 'linkedin' ? validatedWrite : isRssTarget ? validatedWrite : validatedRead
  const telegramBotLink = telegramBotName ? `https://t.me/${telegramBotName}` : ''
  const rssTargetFeedId = (channelId.trim() || 'rss').replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'rss'
  const rssTargetUrl = `/rss/${encodeURIComponent(rssTargetFeedId)}.xml`
  const validationMessage = (errors, fallback) => {
    const message = (errors || [])
      .map((item) => String(item || '').trim())
      .filter(Boolean)
      .map((item) => item.startsWith('connections.validation.') ? t(item) : item)
      .join(' ')
    return message || fallback
  }

  useEffect(() => {
    if (!isSelfhostMode() || !workspaceId) return
    listInstallationSecrets(workspaceId)
      .then((result) => {
        const telegramBot = (result.items || []).find((item) => item.category === 'telegram_bot')
        const name = String(telegramBot?.config?.bot_username || '').trim().replace(/^@/, '')
        setTelegramBotName(name)
        if (name) {
          setTelegramBotDraft((current) => ({ ...current, bot_username: name }))
        }
      })
      .catch(() => setTelegramBotName(''))
  }, [workspaceId])

  const handleTelegramBotSave = async () => {
    const botToken = telegramBotDraft.bot_token.trim()
    const botUsername = telegramBotDraft.bot_username.trim().replace(/^@/, '')
    setTelegramBotError('')
    setTelegramBotSuccess('')
    if (!botToken || !botUsername) {
      setTelegramBotError(t('addChannel.telegram.botFormRequired'))
      return
    }
    setTelegramBotSaving(true)
    try {
      await upsertInstallationSecret(workspaceId, 'telegram_bot', {
        config: { bot_username: botUsername },
        secret: { bot_token: botToken },
      })
      setTelegramBotName(botUsername)
      setTelegramBotDraft({ bot_token: '', bot_username: botUsername })
      setTelegramBotFormOpen(false)
      setTelegramBotSuccess(t('addChannel.telegram.botSaved'))
    } catch (e) {
      setTelegramBotError(e.message)
    } finally {
      setTelegramBotSaving(false)
    }
  }

  const handleValidate = async () => {
    setError('')
    setBindSuccess('')
    setValidating(true)
    setValidatedRead(false)
    setValidatedWrite(false)
    setValidatedDisplay(null)
    try {
      if (platform === 'postbridge') {
        setValidatedRead(true)
        setValidatedDisplay('Postbridge')
        setValidatedWrite(false)
        setValidating(false)
        return
      }
      if (isRssTarget) {
        setChannelId(rssTargetFeedId)
        setValidatedRead(false)
        setValidatedWrite(true)
        setValidatedDisplay(title.trim() || 'RSS')
        setValidating(false)
        return
      }
      if (platform === 'max') {
        // MAX: request the code first if it has not been requested yet.
        if (!maxCodeRequested) {
          const res = await requestMaxChannelVerification(workspaceId, channelId.trim())
          setMaxCode(res.code)
          setMaxDeeplink(res.deeplink)
          setMaxCodeRequested(true)
          setError('')
          setValidating(false)
          return
        }
        // MAX: verify the code in the channel.
        const verifyRes = await verifyMaxChannel(workspaceId, {
          platform_channel_id: channelId.trim(),
          code: maxCode,
        })
        if (!verifyRes?.ok) {
          setError(verifyRes?.errors?.join(' ') || t('addChannel.errors.maxCodeNotFound'))
          return
        }
        setValidatedRead(true)
        setValidatedDisplay(verifyRes.display || channelId.trim())
        // MAX: publishing the code in the channel confirms write access.
        setValidatedWrite(true)
      } else if (platform === 'vk') {
        // VK: validate the community token and create a credential.
        const credRes = await createVkCommunityCredential(workspaceId, {
          group_id: channelId.trim(),
          access_token: vkAccessToken.trim(),
        })
        setChannelId(credRes.platform_channel_id)
        setVkCredentialsRef(credRes.id)
        setValidatedRead(true)
        setValidatedWrite(true)
        setValidatedDisplay(credRes.display || credRes.platform_channel_id)
      } else if (platform === 'linkedin') {
        if (!channelId.trim()) {
          const orgRes = await listLinkedinOrganizations(workspaceId, {
            access_token: linkedinAccessToken.trim(),
          })
          const items = orgRes?.items || []
          setLinkedinOrganizations(items)
          if (!items.length) {
            setError(t('addChannel.linkedin.noOrganizations'))
            setValidating(false)
            return
          }
          setChannelId(items[0].author_urn)
          setValidatedDisplay(items[0].name || items[0].author_urn)
          setBindSuccess(t('addChannel.linkedin.chooseOrganization'))
          setValidating(false)
          return
        }
        const selectedOrg = linkedinOrganizations.find((item) => item.author_urn === channelId.trim())
        const credRes = await createLinkedinAccessTokenCredential(workspaceId, {
          author_id: channelId.trim(),
          access_token: linkedinAccessToken.trim(),
          display: selectedOrg?.name,
        })
        setChannelId(credRes.platform_channel_id)
        setLinkedinCredentialsRef(credRes.id)
        setValidatedWrite(true)
        setValidatedDisplay(credRes.display || credRes.platform_channel_id)
      } else {
        const readRes = await validateChannelRegistryItem(workspaceId, {
          platform,
          platform_channel_id: channelId.trim(),
          role: 'source',
        })
        if (!readRes?.ok) {
          const errMsg = validationMessage(readRes?.errors, t('addChannel.errors.noReadAccess'))
          setError(errMsg)
          if (errMsg.includes(t('addChannel.errors.telegramLinkNeedle')) || errMsg.includes('Link Telegram')) {
            setShowLinkTelegram(true)
          }
          return
        }
        setValidatedRead(true)
        setValidatedDisplay(readRes.display || channelId.trim())
        if (asTarget && !isRssSource) {
          const writeRes = await validateChannelRegistryItem(workspaceId, {
            platform,
            platform_channel_id: channelId.trim(),
            role: 'target',
          })
          setValidatedWrite(writeRes.ok)
          if (!writeRes.ok) {
            setError(validationMessage(writeRes.errors, t('addChannel.errors.noWriteAccess')))
          }
        }
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setValidating(false)
    }
  }

  const handlePlatformChange = (newPlatform) => {
    setPlatform(newPlatform)
    setMaxCode(null)
    setMaxDeeplink(null)
    setMaxCodeRequested(false)
    setShowLinkTelegram(false)
    setTgBindDeepLink(null)
    setTgBindSessionToken(null)
    setBindSuccess('')
    setVkAccessToken('')
    setVkCredentialsRef('')
    setLinkedinAccessToken('')
    setLinkedinCredentialsRef('')
    setLinkedinOrganizations([])
    setRssMode('source')
    setValidatedRead(false)
    setValidatedWrite(false)
    setValidatedDisplay(null)
    setError('')
    setAsTarget(DEFAULT_TARGET_PLATFORMS.has(newPlatform))
    if (newPlatform === 'postbridge' && workspaceId) {
      setChannelId(workspaceId)
      setAsTarget(false)
    } else if (newPlatform === 'rss') {
      setChannelId('')
      setAsTarget(false)
    }
  }

  useEffect(() => {
    if (platform === 'postbridge' && workspaceId) {
      setChannelId(workspaceId)
    }
  }, [platform, workspaceId])

  useEffect(() => {
    if (!showLinkTelegram || platform !== 'telegram') return undefined
    let cancelled = false
    setTgBindDeepLink(null)
    setTgBindSessionToken(null)
    setBindSuccess('')
    ;(async () => {
      try {
        const res = await startTelegramWebLinkSession()
        if (!cancelled) {
          setTgBindDeepLink(res.deep_link)
          setTgBindSessionToken(res.session_token)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [showLinkTelegram, platform])

  useEffect(() => {
    if (!tgBindSessionToken) return undefined
    const id = setInterval(async () => {
      try {
        const s = await fetchTelegramWebLinkStatus(tgBindSessionToken)
        if (s.status === 'done') {
          setTgBindSessionToken(null)
          setTgBindDeepLink(null)
          setShowLinkTelegram(false)
          setError('')
          setBindSuccess(t('addChannel.telegram.bindSuccess'))
        }
        if (s.status === 'failed') {
          setError(s.message || t('addChannel.telegram.bindFailed'))
          setTgBindSessionToken(null)
          setTgBindDeepLink(null)
        }
        if (s.status === 'expired') {
          setError(t('addChannel.telegram.linkExpired'))
          setTgBindSessionToken(null)
          setTgBindDeepLink(null)
        }
      } catch (e) {
        setError(e.message)
      }
    }, 2000)
    return () => clearInterval(id)
  }, [tgBindSessionToken, t])

  const handleLinkedinOAuth = async () => {
    setError('')
    setValidating(true)
    try {
      const res = await getLinkedinAuthorizeUrl(workspaceId)
      if (res?.authorize_url) {
        window.location.href = res.authorize_url
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setValidating(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!hasValidatedAccess) {
      setError(platform === 'linkedin' ? t('addChannel.errors.validatePublishFirst') : t('addChannel.errors.validateFirst'))
      return
    }
    setError('')
    setLoading(true)
    try {
      const createPayload = {
        platform,
        platform_channel_id: isRssTarget ? rssTargetFeedId : channelId.trim(),
        title: (title.trim() || validatedDisplay) || (isRssTarget ? 'RSS' : channelId.trim()),
        can_read: platform === 'linkedin' || isRssTarget ? false : true,
        can_write: platform === 'linkedin' || isRssTarget ? true : asTarget ? validatedWrite : false,
      }
      if (platform === 'vk' && vkCredentialsRef) {
        createPayload.credentials_ref = vkCredentialsRef
      }
      if (platform === 'linkedin' && linkedinCredentialsRef) {
        createPayload.credentials_ref = linkedinCredentialsRef
      }
      await createChannelRegistryItem(workspaceId, createPayload)
      if (platform === 'postbridge') {
        navigate(`/workspaces/${workspaceId}/channels?success=channel_added`)
      } else {
        navigate(`/workspaces/${workspaceId}/migrate?success=channel_added`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <AppShell
      title={t('addChannel.title')}
      subtitle={t('addChannel.subtitle')}
      actions={
        <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary btn-small">
          {t('common.back')}
        </Link>
      }
    >
      <div className="card">
        <h3>{t('addChannel.newChannel')}</h3>
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="platform">{t('common.platform')}</label>
            <select
              id="platform"
              value={platform}
              onChange={(e) => handlePlatformChange(e.target.value)}
              className="form-control"
            >
              {PLATFORMS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id === 'zen' ? t('platform.zen') : p.label}
                </option>
              ))}
            </select>
          </div>
          {platform === 'telegram' && (
            <p className="section-copy">
              {t('addChannel.botAdminPrefix')}{' '}
              {telegramBotLink ? (
                <a href={telegramBotLink} target="_blank" rel="noopener noreferrer">{t('addChannel.openBot')}</a>
              ) : (
                t('addChannel.telegram.botNotConfigured')
              )}
            </p>
          )}
          {platform === 'telegram' && !telegramBotLink && (
            <div className="card" style={{ marginBottom: '1rem', padding: '1rem', background: 'var(--surface-strong)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
                <p className="section-copy" style={{ margin: 0 }}>
                  {t('addChannel.telegram.botSetupText')}
                </p>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  onClick={() => setTelegramBotFormOpen((value) => !value)}
                >
                  {telegramBotFormOpen ? t('common.collapse') : t('addChannel.telegram.configureBot')}
                </button>
              </div>
              {telegramBotFormOpen && (
                <div style={{ display: 'grid', gap: '0.75rem', marginTop: '1rem' }}>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label htmlFor="telegram-bot-token">{t('settings.integrations.telegramBot.token')}</label>
                    <input
                      id="telegram-bot-token"
                      type="password"
                      value={telegramBotDraft.bot_token}
                      onChange={(e) => setTelegramBotDraft((current) => ({ ...current, bot_token: e.target.value }))}
                      placeholder="123456:ABC..."
                      className="form-control"
                    />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label htmlFor="telegram-bot-username">{t('settings.integrations.telegramBot.username')}</label>
                    <input
                      id="telegram-bot-username"
                      type="text"
                      value={telegramBotDraft.bot_username}
                      onChange={(e) => setTelegramBotDraft((current) => ({ ...current, bot_username: e.target.value }))}
                      placeholder="postbridge_bot"
                      className="form-control"
                    />
                  </div>
                  {telegramBotError && <p className="error" style={{ margin: 0 }}>{telegramBotError}</p>}
                  <div className="inline-actions">
                    <button
                      type="button"
                      className="btn"
                      onClick={handleTelegramBotSave}
                      disabled={telegramBotSaving}
                    >
                      {telegramBotSaving ? t('common.saving') : t('addChannel.telegram.saveBot')}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
          {platform === 'telegram' && telegramBotSuccess && <p className="success">{telegramBotSuccess}</p>}
          {platform === 'telegram' && showLinkTelegram && (
            <div className="card" style={{ marginBottom: '1rem', padding: '1rem', background: 'var(--bg-secondary, #f5f5f5)' }}>
              <p className="section-copy" style={{ marginBottom: '0.5rem' }}>
                {t('addChannel.telegram.bindHint')}
              </p>
              {tgBindDeepLink ? (
                <>
                  <TelegramDeepLinkField url={tgBindDeepLink} />
                  <a href={tgBindDeepLink} target="_blank" rel="noopener noreferrer" className="btn btn-block">
                    {t('login.telegram.open')}
                  </a>
                </>
              ) : (
                <p className="muted">{t('addChannel.telegram.preparingLink')}</p>
              )}
            </div>
          )}
          {platform === 'max' && (
            <p className="section-copy">
              {t('addChannel.botAdminPrefix')}{' '}
              {MAX_BOT_LINK ? (
                <a href={MAX_BOT_LINK} target="_blank" rel="noopener noreferrer">{t('addChannel.openBot')}</a>
              ) : (
                t('addChannel.openBot')
              )}
              {maxCodeRequested && (
                <>
                  {' '}{t('addChannel.max.publishCodeHint')}
                </>
              )}
            </p>
          )}
          {platform === 'max' && maxCodeRequested && maxCode && (
            <div className="card" style={{ marginBottom: '1rem', padding: '1rem', background: 'var(--surface-strong)' }}>
              <p className="section-copy" style={{ marginBottom: '0.5rem' }}>
                <strong>{t('addChannel.max.publishCodeTitle')}</strong>
              </p>
              <p
                style={{
                  fontSize: '1.25rem',
                  fontFamily: 'monospace',
                  letterSpacing: '0.1em',
                  marginBottom: '0.5rem',
                  padding: '0.75rem 1rem',
                  background: 'var(--bg)',
                  color: 'var(--text)',
                  borderRadius: '8px',
                  border: '1px solid var(--border)',
                }}
              >
                {maxCode}
              </p>
              {maxDeeplink && (
                <p style={{ marginBottom: '0.5rem' }}>
                  <a href={maxDeeplink} target="_blank" rel="noopener noreferrer">{t('addChannel.max.openChannel')}</a>
                </p>
              )}
              <p className="muted" style={{ fontSize: '0.85rem' }}>
                {t('addChannel.max.afterPublish')}
              </p>
            </div>
          )}
          {platform === 'vk' && (
            <p className="section-copy">
              {t('addChannel.vk.instructions')}
            </p>
          )}
          {platform === 'linkedin' && (
            <p className="section-copy">
              {t('addChannel.linkedin.instructions')}
            </p>
          )}
          {platform === 'postbridge' && (
            <p className="section-copy">
              {t('addChannel.postbridge.instructions')}
            </p>
          )}
          {platform === 'rss' && (
            <>
              <div className="form-group">
                <label htmlFor="rss-mode">{t('addChannel.rss.mode')}</label>
                <select
                  id="rss-mode"
                  value={rssMode}
                  onChange={(e) => {
                    const nextMode = e.target.value
                    setRssMode(nextMode)
                    setChannelId(nextMode === 'target' ? 'rss' : '')
                    setValidatedRead(false)
                    setValidatedWrite(false)
                    setValidatedDisplay(null)
                    setError('')
                  }}
                  className="form-control"
                >
                  <option value="source">{t('addChannel.rss.mode.source')}</option>
                  <option value="target">{t('addChannel.rss.mode.target')}</option>
                </select>
              </div>
              <p className="section-copy">
                {isRssTarget ? t('addChannel.rss.targetText') : t('addChannel.rss.sourceText')}
              </p>
              {isRssTarget && (
                <p className="muted" style={{ fontSize: '0.9rem', marginTop: '-0.5rem', marginBottom: '1rem' }}>
                  {t('addChannel.rss.targetUrl')} <code>{rssTargetUrl}</code>
                </p>
              )}
            </>
          )}
          {platform === 'vk' && (
            <>
              <div className="form-group">
                <label htmlFor="channel-id">{t('addChannel.vk.groupId')}</label>
                <input
                  id="channel-id"
                  type="text"
                  value={channelId}
                  onChange={(e) => setChannelId(e.target.value)}
                  placeholder="-123456789 or vk.com/club123456789"
                  className="form-control"
                  required
                />
              </div>
              <div className="form-group">
                <label htmlFor="vk-access-token">{t('addChannel.vk.accessToken')}</label>
                <input
                  id="vk-access-token"
                  type="password"
                  value={vkAccessToken}
                  onChange={(e) => setVkAccessToken(e.target.value)}
                  placeholder={t('addChannel.vk.accessTokenPlaceholder')}
                  className="form-control"
                  required
                />
              </div>
            </>
          )}
          {platform === 'linkedin' && (
            <>
              <div className="inline-actions" style={{ marginBottom: '1rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleLinkedinOAuth}
                  disabled={validating}
                >
                  {t('addChannel.linkedin.connectOAuth')}
                </button>
              </div>
              <div className="form-group">
                <label htmlFor="channel-id">{t('addChannel.linkedin.authorId')}</label>
                {linkedinOrganizations.length > 0 ? (
                  <select
                    id="channel-id"
                    value={channelId}
                    onChange={(e) => setChannelId(e.target.value)}
                    className="form-control"
                    required
                  >
                    {linkedinOrganizations.map((item) => (
                      <option key={item.author_urn} value={item.author_urn}>
                        {item.name || item.author_urn}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id="channel-id"
                    type="text"
                    value={channelId}
                    onChange={(e) => setChannelId(e.target.value)}
                    placeholder={PLACEHOLDERS.linkedin}
                    className="form-control"
                  />
                )}
              </div>
              <div className="form-group">
                <label htmlFor="linkedin-access-token">{t('addChannel.linkedin.accessToken')}</label>
                <input
                  id="linkedin-access-token"
                  type="password"
                  value={linkedinAccessToken}
                  onChange={(e) => setLinkedinAccessToken(e.target.value)}
                  placeholder={t('addChannel.linkedin.accessTokenPlaceholder')}
                  className="form-control"
                  required
                />
              </div>
              <p className="muted" style={{ fontSize: '0.85rem' }}>
                {t('addChannel.linkedin.manualTokenHint')}
              </p>
            </>
          )}
          {platform !== 'postbridge' && platform !== 'vk' && platform !== 'linkedin' && (
          <div className="form-group">
            <label htmlFor="channel-id">
              {platform === 'telegram'
                ? t('addChannel.channelId.telegram')
                : platform === 'rss' && isRssTarget
                  ? t('addChannel.channelId.rssTarget')
                  : platform === 'rss'
                    ? t('addChannel.channelId.rss')
                    : t('addChannel.channelId.generic')}
            </label>
            <input
              id="channel-id"
              type="text"
              value={channelId}
              onChange={(e) => setChannelId(e.target.value)}
              placeholder={isRssTarget ? 'rss' : PLACEHOLDERS[platform]}
              className="form-control"
              required
            />
            {platform === 'telegram' && (
              <p className="toggle-hint">
                {t('addChannel.telegram.channelIdHint')}
              </p>
            )}
          </div>
          )}
          <div className="form-group">
            <label htmlFor="title">{t('addChannel.titleOptional')}</label>
            <input
              id="title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t('addChannel.displayNamePlaceholder')}
              className="form-control"
            />
          </div>
          {platform !== 'postbridge' && platform !== 'linkedin' && platform !== 'rss' && (
          <div className="form-group">
            <div
              className="toggle-row"
              role="button"
              tabIndex={0}
              onClick={() => setAsTarget((v) => !v)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setAsTarget((v) => !v)
                }
              }}
              aria-pressed={asTarget}
              aria-describedby="as-target-hint"
            >
              <span className="toggle-switch">
                <input
                  type="checkbox"
                  checked={asTarget}
                  onChange={(e) => setAsTarget(e.target.checked)}
                  tabIndex={-1}
                  aria-hidden
                />
                <span className="toggle-track">
                  <span className="toggle-thumb" />
                </span>
              </span>
              <span className="toggle-label">{t('channel.canWrite')}</span>
            </div>
            <p id="as-target-hint" className="toggle-hint">
              {t('addChannel.asTargetHint')}
            </p>
          </div>
          )}
          {hasValidatedAccess && (
            <p className="success" style={{ marginBottom: '0.5rem' }}>
              {platform === 'linkedin'
                ? t('addChannel.validation.publishOk')
                : (
                  <>
                    {t('addChannel.validation.readOk')}
                    {validatedWrite && t('addChannel.validation.writeOkSuffix')}
                  </>
                )}
            </p>
          )}
          {bindSuccess && <p className="success">{bindSuccess}</p>}
          {error && <p className="error">{error}</p>}
          <div className="inline-actions">
            <Link to={`/workspaces/${workspaceId}/channels`} className="btn btn-secondary">
              {t('common.cancel')}
            </Link>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleValidate}
              disabled={
                validating ||
                (platform !== 'postbridge' && platform !== 'linkedin' && !channelId.trim()) ||
                (platform === 'vk' && !vkAccessToken.trim()) ||
                (platform === 'linkedin' && !linkedinAccessToken.trim())
              }
            >
              {validating ? t('common.checking') : platform === 'max' && !maxCodeRequested ? t('login.email.requestCode') : t('common.check')}
            </button>
            <button
              type="submit"
              className="btn"
              disabled={
                loading ||
                !hasValidatedAccess ||
                (platform !== 'postbridge' && platform !== 'linkedin' && !isRssTarget && !isRssSource && asTarget && !validatedWrite)
              }
            >
              {loading ? t('addChannel.adding') : t('addChannel.title')}
            </button>
          </div>
        </form>
      </div>
    </AppShell>
  )
}
