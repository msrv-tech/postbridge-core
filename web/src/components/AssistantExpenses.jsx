import { useEffect, useState } from 'react'
import { getAssistantExpenses } from '../adapters/supportAssistant'
import { useI18n } from '../i18n'

export default function AssistantExpenses({ workspaceId }) {
  const { t, locale } = useI18n()
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return undefined
    let active = true
    setData(null); setError('')
    getAssistantExpenses(workspaceId).then((value) => { if (active) setData(value) })
      .catch((e) => { if (active) setError(e.message) })
    return () => { active = false }
  }, [workspaceId, open])
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{t('assistant.expenses.title')}</summary>
    <p className="muted">{t('assistant.expenses.hint')}</p>
    {error && <p className="error">{error}</p>}
    {data && <>
      {data.groups.map((group) => <p key={`${group.feature}/${group.payer}`}>
        {t(`assistant.expenses.${group.feature}`)}: {group.known_expense_units.toLocaleString(locale)} · {t(`assistant.expenses.${group.payer}`)}
      </p>)}
      {!data.groups.length && <p>{t('assistant.expenses.empty')}</p>}
      {data.unresolved_requests > 0 && <p>{t('assistant.expenses.unresolved', { count: data.unresolved_requests })}</p>}
      <details><summary>{t('assistant.expenses.recent')}</summary>
        {data.items.map((item) => <p key={item.id}>
          {new Date(item.created_at).toLocaleString(locale)} · {t(`assistant.expenses.${item.feature}`)} · {item.model_id || '—'} · {item.gitsell_units ?? '…'}
          <br /><small>{item.user_id || '—'} · {t(`assistant.expenses.${item.payer}`)} · {t(`assistant.expenses.status.${item.status}`)}</small>
        </p>)}
      </details>
    </>}
  </details>
}
