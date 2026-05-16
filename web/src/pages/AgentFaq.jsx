import { Link, useNavigate, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import PublicLayout from '../components/PublicLayout'
import { clearToken } from '../adapters/sessionToken'
import { useAuth } from '../useAuth'
import AgentSectionLayout from '../components/AgentSectionLayout'
import { useI18n } from '../i18n'

function getFaqItems(t) {
  return [
  {
    question: t('agentFaq.difference.question'),
    answer: t('agentFaq.difference.answer'),
  },
  {
    question: t('agentFaq.webSearch.question'),
    answer: t('agentFaq.webSearch.answer'),
  },
  {
    question: t('agentFaq.goal.question'),
    answer: t('agentFaq.goal.answer'),
  },
  {
    question: t('agentFaq.instructions.question'),
    answer: t('agentFaq.instructions.answer'),
  },
  {
    question: t('agentFaq.seeds.question'),
    answer: t('agentFaq.seeds.answer'),
  },
  {
    question: t('agentFaq.auto.question'),
    answer: t('agentFaq.auto.answer'),
  },
  {
    question: t('agentFaq.reviewFallback.question'),
    answer: t('agentFaq.reviewFallback.answer'),
  },
  {
    question: t('agentFaq.images.question'),
    answer: t('agentFaq.images.answer'),
  },
  {
    question: t('agentFaq.results.question'),
    answer: t('agentFaq.results.answer'),
  },
  {
    question: t('agentFaq.multipleTasks.question'),
    answer: t('agentFaq.multipleTasks.answer'),
  },
]
}

function AgentFaqList() {
  const { t } = useI18n()
  const faqItems = getFaqItems(t)
  return (
    <div className="faq-list">
      {faqItems.map((item) => (
        <article className="card faq-card" key={item.question}>
          <h3 style={{ marginTop: 0 }}>{item.question}</h3>
          <p>{item.answer}</p>
        </article>
      ))}
    </div>
  )
}

export default function AgentFaq({ publicView = false }) {
  const { t } = useI18n()
  const { workspaceId } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()

  const handleLogout = () => {
    clearToken()
    navigate('/')
  }

  if (publicView) {
    return (
      <PublicLayout>
        <section className="section">
          <div className="container">
            <div className="section-heading">
              <span className="eyebrow">{t('home.faq.eyebrow')}</span>
              <h1 className="page-title">{t('agentFaq.publicTitle')}</h1>
              <p className="page-subtitle">
                {t('agentFaq.publicSubtitle')}
              </p>
            </div>
            <AgentFaqList />
          </div>
        </section>
      </PublicLayout>
    )
  }

  return (
    <AppShell
      title={t('agentFaq.title')}
      subtitle={t('agentFaq.subtitle')}
      user={user}
      onLogout={handleLogout}
      actions={
        <>
          <Link to={`/workspaces/${workspaceId}/content`} className="btn btn-secondary btn-small">
            {t('common.toContent')}
          </Link>
        </>
      }
    >
      <AgentSectionLayout workspaceId={workspaceId} activeItem="faq">
      <AgentFaqList />
      </AgentSectionLayout>
    </AppShell>
  )
}
