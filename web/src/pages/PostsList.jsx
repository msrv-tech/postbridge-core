import { useState, useEffect } from 'react'
import { Link, useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { deleteContentItem, listContentItems } from '../adapters/content'
import { clearToken } from '../adapters/sessionToken'
import { useAuth } from '../useAuth'
import AppShell from '../components/AppShell'
import { useI18n } from '../i18n'

const LIMIT = 20

function formatDate(d, locale) {
  if (!d) return '—'
  return new Date(d).toLocaleDateString(locale === 'en' ? 'en-US' : 'ru-RU', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function previewText(text, maxLen = 100) {
  if (!text) return ''
  const plain = text.replace(/#{1,6}\s/g, '').replace(/\*\*|__|\*/g, '').trim()
  return plain.length > maxLen ? plain.slice(0, maxLen) + '…' : plain
}

export default function PostsList() {
  const { locale, t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { user } = useAuth()
  const [posts, setPosts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const [deleteLoadingId, setDeleteLoadingId] = useState(null)

  const statusFilter = searchParams.get('status') || ''

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  useEffect(() => {
    if (!workspaceId) return
    setError('')
    setLoading(true)
    listContentItems(workspaceId, { status: statusFilter, limit: LIMIT, offset })
      .then((r) => setPosts(r.items || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [workspaceId, statusFilter, offset])

  const setStatusFilter = (v) => {
    setSearchParams(v ? { status: v } : {})
    setOffset(0)
  }

  const handleDelete = async (post) => {
    const preview = post.title || previewText(post.content_md, 50)
    if (!window.confirm(t('posts.confirmDelete', { title: preview }))) return
    setDeleteLoadingId(post.id)
    setError('')
    try {
      await deleteContentItem(workspaceId, post.id)
      setPosts((prev) => prev.filter((p) => p.id !== post.id))
    } catch (e) {
      setError(e.message)
    } finally {
      setDeleteLoadingId(null)
    }
  }

  return (
    <AppShell
      title={t('posts.title')}
      subtitle={t('posts.subtitle')}
      user={user}
      onLogout={handleLogout}
      actions={
        <>
          <Link to={`/workspaces/${workspaceId}/agents/topic-scout`} className="btn btn-small">
            {t('agents.nav.topicScout')}
          </Link>
          <Link to={`/workspaces/${workspaceId}/agents/candidates`} className="btn btn-secondary btn-small">
            {t('agents.nav.candidates')}
          </Link>
          <Link to={`/workspaces/${workspaceId}/content/new`} className="btn btn-secondary btn-small">
            {t('posts.create')}
          </Link>
        </>
      }
    >
      <div className="card">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center', marginBottom: '1rem' }}>
          <span className="muted">{t('common.filter')}</span>
          <button
            type="button"
            className={`btn btn-small ${!statusFilter ? 'btn' : 'btn-secondary'}`}
            onClick={() => setStatusFilter('')}
          >
            {t('common.all')}
          </button>
          <button
            type="button"
            className={`btn btn-small ${statusFilter === 'draft' ? 'btn' : 'btn-secondary'}`}
            onClick={() => setStatusFilter('draft')}
          >
            {t('posts.filters.drafts')}
          </button>
          <button
            type="button"
            className={`btn btn-small ${statusFilter === 'published' ? 'btn' : 'btn-secondary'}`}
            onClick={() => setStatusFilter('published')}
          >
            {t('posts.filters.published')}
          </button>
        </div>

        {error && <p className="error">{error}</p>}
        {loading && <p className="muted">{t('common.loading')}</p>}

        {!loading && posts.length === 0 && (
          <div className="empty-state">
            <p className="muted">{t('posts.empty')}</p>
            <Link to={`/workspaces/${workspaceId}/content/new`} className="btn" style={{ marginTop: '0.5rem' }}>
              {t('posts.create')}
            </Link>
          </div>
        )}

        {!loading && posts.length > 0 && (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {posts.map((post) => (
              <li
                key={post.id}
                style={{
                  padding: '1rem',
                  borderBottom: '1px solid var(--border)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{ margin: '0 0 0.25rem 0', whiteSpace: 'pre-wrap' }}>
                      {post.title ? post.title : previewText(post.content_md)}
                    </p>
                    <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
                      <span
                        className={post.status === 'published' ? '' : 'muted'}
                        style={{
                          fontSize: '0.85rem',
                          padding: '0.15rem 0.4rem',
                          borderRadius: '4px',
                          background: post.status === 'published' ? 'var(--success)' : 'var(--surface-strong)',
                          color: post.status === 'published' ? 'white' : 'var(--text-muted)',
                        }}
                      >
                        {post.status === 'published' ? t('posts.status.published') : t('common.draft')}
                      </span>
                      {post.status === 'draft' && post.scheduled_publish_at && (
                        <span
                          className="muted"
                          style={{
                            fontSize: '0.85rem',
                            padding: '0.15rem 0.4rem',
                            borderRadius: '4px',
                            background: 'var(--surface-strong)',
                          }}
                          title={t('posts.scheduledTitle')}
                        >
                          {t('posts.scheduledAt', { date: formatDate(post.scheduled_publish_at, locale) })}
                        </span>
                      )}
                      <span className="muted" style={{ fontSize: '0.85rem' }}>
                        {formatDate(post.created_at, locale)}
                      </span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <Link to={`/workspaces/${workspaceId}/content/${post.id}`} className="btn btn-secondary btn-small">
                      {t('common.edit')}
                    </Link>
                    <button
                      type="button"
                      className="btn btn-secondary btn-small"
                      onClick={() => handleDelete(post)}
                      disabled={deleteLoadingId === post.id}
                    >
                      {deleteLoadingId === post.id ? '…' : t('common.delete')}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}

        {!loading && posts.length >= LIMIT && (
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem', justifyContent: 'center' }}>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => setOffset((o) => Math.max(0, o - LIMIT))}
              disabled={offset === 0}
            >
              {t('common.back')}
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => setOffset((o) => o + LIMIT)}
            >
              {t('common.next')}
            </button>
          </div>
        )}
      </div>
    </AppShell>
  )
}
