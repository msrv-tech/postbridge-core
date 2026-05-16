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
import { fetchTelegramWebLinkStatus, startTelegramWebLinkSession } from '../telegramWebLinkFlow'
import { useI18n } from '../i18n'

// VK: community token из настроек группы (Контур 2).
const VK_OAUTH_ADD_ENABLED = true

const PLATFORMS = [
  { id: 'telegram', label: 'Telegram' },
  { id: 'max', label: 'MAX' },
  ...(VK_OAUTH_ADD_ENABLED ? [{ id: 'vk', label: 'VK' }] : []),
  { id: 'linkedin', label: 'LinkedIn' },
  { id: 'rss', label: 'RSS' },
  { id: 'postbridge', label: 'Postbridge' },
]

const PLACEHOLDERS = {
  telegram: 'https://t.me/c/1234567890 or -1001234567890',
  max: 'https://web.max.ru/-71838... or -71838691591553',
  vk: '-123456789 or vk.com/club123456789',
  linkedin: 'urn:li:organization:123456 or organization:123456',
  rss: 'https://example.com/feed.xml',
  postbridge: 'Workspace is used automatically',
}

const TELEGRAM_BOT_LINK = `https://t.me/${import.meta.env.VITE_TELEGRAM_BOT_NAME || 'postbridge_bot'}`
const MAX_BOT_LINK = import.meta.env.VITE_MAX_BOT_URL || ''

export default function AddChannel() {
  const { t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const [platform, setPlatform] = useState('telegram')
  const [channelId, setChannelId] = useState('')
  const [title, setTitle] = useState('')
  const [asTarget, setAsTarget] = useState(false)
  const [validatedRead, setValidatedRead] = useState(false)
  const [validatedWrite, setValidatedWrite] = useState(false)
  const [validatedDisplay, setValidatedDisplay] = useState(null)
  const [loading, setLoading] = useState(false)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState('')
  // MAX: верификация через уникальный код
  const [maxCode, setMaxCode] = useState(null)
  const [maxDeeplink, setMaxDeeplink] = useState(null)
  const [maxCodeRequested, setMaxCodeRequested] = useState(false)
  // Telegram: привязка для email-пользователей (ссылка в боте)
  const [showLinkTelegram, setShowLinkTelegram] = useState(false)
  const [tgBindDeepLink, setTgBindDeepLink] = useState(null)
  const [tgBindSessionToken, setTgBindSessionToken] = useState(null)
  const [bindSuccess, setBindSuccess] = useState('')
  // VK: community token — access_token и credential_ref после проверки
  const [vkAccessToken, setVkAccessToken] = useState('')
  const [vkCredentialsRef, setVkCredentialsRef] = useState('')
  const [linkedinAccessToken, setLinkedinAccessToken] = useState('')
  const [linkedinCredentialsRef, setLinkedinCredentialsRef] = useState('')
  const [linkedinOrganizations, setLinkedinOrganizations] = useState([])
  const hasValidatedAccess = platform === 'linkedin' ? validatedWrite : validatedRead

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
      if (platform === 'max') {
        // MAX: сначала запросить код (если ещё не запрашивали)
        if (!maxCodeRequested) {
          const res = await requestMaxChannelVerification(workspaceId, channelId.trim())
          setMaxCode(res.code)
          setMaxDeeplink(res.deeplink)
          setMaxCodeRequested(true)
          setError('')
          setValidating(false)
          return
        }
        // MAX: проверить код в канале
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
        // MAX: публикация кода в канале подтверждает доступ на запись
        setValidatedWrite(true)
      } else if (platform === 'vk') {
        // VK: community token — проверка и создание credential
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
          const errMsg = readRes?.errors?.join(' ') || t('addChannel.errors.noReadAccess')
          setError(errMsg)
          if (errMsg.includes('Привяжите Telegram') || errMsg.includes('Link Telegram')) {
            setShowLinkTelegram(true)
          }
          return
        }
        setValidatedRead(true)
        setValidatedDisplay(readRes.display || channelId.trim())
        if (asTarget) {
          const writeRes = await validateChannelRegistryItem(workspaceId, {
            platform,
            platform_channel_id: channelId.trim(),
            role: 'target',
          })
          setValidatedWrite(writeRes.ok)
          if (!writeRes.ok) {
            setError((readRes.errors || []).concat(writeRes.errors || []).join(' ') || t('addChannel.errors.noWriteAccess'))
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
    setValidatedRead(false)
    setValidatedWrite(false)
    setValidatedDisplay(null)
    setError('')
    if (newPlatform === 'postbridge' && workspaceId) {
      setChannelId(workspaceId)
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
        platform_channel_id: channelId.trim(),
        title: (title.trim() || validatedDisplay) || channelId.trim(),
        can_read: platform === 'linkedin' ? false : true,
        can_write: platform === 'linkedin' ? true : asTarget ? validatedWrite : false,
      }
      if (platform === 'vk' && vkCredentialsRef) {
        createPayload.credentials_ref = vkCredentialsRef
      }
      if (platform === 'linkedin' && linkedinCredentialsRef) {
        createPayload.credentials_ref = linkedinCredentialsRef
      }
      await createChannelRegistryItem(workspaceId, createPayload)
      navigate(`/workspaces/${workspaceId}/channels?success=channel_added`)
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
              {t('addChannel.botAdminPrefix')} <a href={TELEGRAM_BOT_LINK} target="_blank" rel="noopener noreferrer">{t('addChannel.openBot')}</a>
            </p>
          )}
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
              {platform === 'telegram' ? t('addChannel.channelId.telegram') : platform === 'rss' ? t('addChannel.channelId.rss') : t('addChannel.channelId.generic')}
            </label>
            <input
              id="channel-id"
              type="text"
              value={channelId}
              onChange={(e) => setChannelId(e.target.value)}
              placeholder={PLACEHOLDERS[platform]}
              className="form-control"
              required
            />
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
          {platform !== 'postbridge' && platform !== 'linkedin' && (
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
                (platform !== 'postbridge' && platform !== 'linkedin' && asTarget && !validatedWrite)
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
