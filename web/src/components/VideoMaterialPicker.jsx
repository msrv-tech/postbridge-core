import { useEffect, useState } from 'react'
import { listVideoMaterials, updateVideoMaterial } from '../adapters/video'
import { useI18n } from '../i18n'

export default function VideoMaterialPicker({ workspaceId, onChoose, disabled = false }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [data, setData] = useState({ items: [], folders: [] })
  const [search, setSearch] = useState('')
  const [folder, setFolder] = useState('*')
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null)
  const [name, setName] = useState('')
  const [newFolder, setNewFolder] = useState('')
  useEffect(() => {
    if (!open) return
    let active = true
    setLoading(true)
    listVideoMaterials(workspaceId, { search, folder: folder === '*' ? undefined : folder, offset })
      .then((value) => { if (active) { setData(value); setError('') } })
      .catch((e) => { if (active) setError(e.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [workspaceId, open, search, folder, offset])
  const save = async () => {
    setLoading(true)
    try {
      const item = await updateVideoMaterial(workspaceId, editing, { name, folder: newFolder })
      setData((old) => ({ ...old, items: old.items.map((row) => row.asset_id === item.asset_id ? item : row),
        folders: [...new Set([...old.folders, item.folder])].sort() }))
      setEditing(null); setError('')
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }
  return <details className="video-library" open={open} onToggle={(e) => setOpen(e.currentTarget.open)} onKeyDown={(event) => {
    if (event.key !== 'Enter' || event.target.tagName !== 'INPUT') return
    event.preventDefault()
    if (event.target.type !== 'search' && editing && name.trim() && !loading && !disabled) void save()
  }}>
    <summary>{t('videoLibrary.title')}</summary>
    {open && <div>
      <label>{t('videoLibrary.search')}<input type="search" value={search} maxLength={180} onChange={(e) => { setSearch(e.target.value); setOffset(0) }} /></label>
      <label>{t('videoLibrary.folder')}<select value={folder} onChange={(e) => { setFolder(e.target.value); setOffset(0) }}><option value="*">{t('videoLibrary.all')}</option>{data.folders.map((value) => <option key={value} value={value}>{value || t('videoLibrary.noFolder')}</option>)}</select></label>
      {error && <p role="alert" className="error">{error}</p>}
      {loading && <p role="status">{t('common.loading')}</p>}
      {!loading && !data.items.length && <p className="muted">{t('videoLibrary.empty')}</p>}
      <div className="video-library-grid">{data.items.map((item) => <div key={item.asset_id} className="video-library-item">
        {item.kind === 'video' ? <video src={item.url} preload="metadata" controls /> : <img src={item.url} alt={item.name} loading="lazy" />}
        <p>{item.name}</p><small>{item.folder}</small>
        <div className="inline-actions"><button type="button" className="btn btn-secondary btn-small" disabled={disabled || loading} onClick={() => onChoose({ ...item, duration_seconds: Math.max(1, Math.min(5, item.duration_seconds || 5)), caption: '' })}>{t('videoLibrary.use')}</button>
          <button type="button" className="btn btn-secondary btn-small" disabled={disabled || loading} onClick={() => { setEditing(item.asset_id); setName(item.name); setNewFolder(item.folder) }}>{t('videoLibrary.organize')}</button></div>
        {editing === item.asset_id && <div>
          <label>{t('videoLibrary.name')}<input maxLength={180} value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>{t('videoLibrary.folder')}<input maxLength={120} value={newFolder} onChange={(e) => setNewFolder(e.target.value)} /></label>
          <button type="button" className="btn btn-secondary" disabled={disabled || loading || !name.trim()} onClick={save}>{t('common.save')}</button>
        </div>}
      </div>)}</div>
      <div className="inline-actions">{offset > 0 && <button type="button" className="btn btn-secondary" onClick={() => setOffset((n) => n - 20)}>{t('common.back')}</button>}{data.has_more && <button type="button" className="btn btn-secondary" onClick={() => setOffset((n) => n + 20)}>{t('common.next')}</button>}</div>
    </div>}
  </details>
}
