import { Link, Navigate, useLocation } from 'react-router-dom'
import { BILLING_SUPPORT_EMAIL } from '../billingSupport'
import PublicLayout from '../components/PublicLayout'

const documents = {
  '/privacy': {
    title: 'Privacy Policy',
    updated: 'August 5, 2026',
    intro:
      'Postbridge helps users connect content channels, prepare posts, run publishing workflows, and manage workspace subscriptions.',
    sections: [
      {
        title: 'Information We Collect',
        body: [
          'Account information such as email address, OAuth profile identifiers, and workspace membership data.',
          'Connected channel metadata, credentials references, publishing settings, and content that users import, draft, schedule, or publish through the service.',
          'Operational data such as billing events, usage limits, audit events, request logs, and error diagnostics needed to run and secure Postbridge.',
        ],
      },
      {
        title: 'How We Use Information',
        body: [
          'To provide authentication, workspace management, channel connection, import, publishing, billing, support, and security features.',
          'To maintain service reliability, investigate errors, enforce usage limits, and prevent abuse.',
          'To communicate service, security, billing, and support messages.',
        ],
      },
      {
        title: 'Third-Party Providers',
        body: [
          'Postbridge may exchange data with providers selected by the user, including Google, Meta, Telegram, X, storage, email, payment, monitoring, and AI infrastructure providers.',
          'Provider access is limited to the scopes and actions required for the workflow configured by the user.',
        ],
      },
      {
        title: 'Retention and Security',
        body: [
          'We keep account, workspace, billing, and operational data while it is needed to provide the service, meet legal obligations, resolve disputes, and protect the platform.',
          'Sensitive credentials are stored using server-side controls and are not exposed in the browser after configuration.',
        ],
      },
      {
        title: 'User Choices',
        body: [
          'Users can disconnect channels, change workspace settings, cancel subscriptions, and request account or data deletion.',
          'For privacy requests, contact support using the email below.',
        ],
      },
    ],
  },
  '/terms': {
    title: 'Terms of Service',
    updated: 'August 5, 2026',
    intro:
      'These terms govern access to and use of Postbridge. By using the service, you agree to use it responsibly and only for lawful content workflows.',
    sections: [
      {
        title: 'Service',
        body: [
          'Postbridge provides tools for content preparation, channel connection, import, publishing, automation, AI assistance, and workspace billing.',
          'Features may vary by workspace plan, provider availability, region, platform policy, and third-party API limits.',
        ],
      },
      {
        title: 'User Responsibilities',
        body: [
          'You are responsible for the content, channels, credentials, automation rules, and publishing actions configured in your workspace.',
          'You must comply with applicable law and the terms and policies of connected platforms.',
          'You must not use Postbridge to send spam, infringe rights, bypass platform restrictions, or publish unlawful content.',
        ],
      },
      {
        title: 'Billing',
        body: [
          'Paid plans and one-time services are charged through the payment methods shown in the product.',
          'For Telegram Stars subscriptions, payment and renewal behavior follows the Telegram Stars flow presented to the user before payment.',
          'You can cancel or downgrade a workspace plan from workspace settings when available.',
        ],
      },
      {
        title: 'Availability and Changes',
        body: [
          'We aim to provide a reliable service, but availability can be affected by maintenance, third-party providers, platform API changes, and incidents.',
          'We may update features, limits, prices, and these terms as the service evolves.',
        ],
      },
      {
        title: 'Support',
        body: ['For service, billing, cancellation, or refund questions, contact support using the email below.'],
      },
    ],
  },
  '/data-deletion': {
    title: 'Data Deletion Instructions',
    updated: 'August 5, 2026',
    intro:
      'You can request deletion of your Postbridge account, workspace data, connected channel data, and imported or drafted content.',
    sections: [
      {
        title: 'How to Request Deletion',
        body: [
          `Send a deletion request to ${BILLING_SUPPORT_EMAIL} from the email associated with your account.`,
          'Include the workspace name or workspace ID if your request concerns a specific workspace.',
          'If you authenticated through an OAuth provider, include the provider name so we can locate linked account records.',
        ],
      },
      {
        title: 'What We Delete',
        body: [
          'Account profile data, workspace memberships, connected channel records, credentials references, imported content, draft content, scheduled publication data, and automation settings associated with the request.',
          'We also remove provider tokens or disconnect provider access where applicable.',
        ],
      },
      {
        title: 'What May Be Retained',
        body: [
          'Some billing, security, audit, backup, or legal records may be retained for the period required by law, fraud prevention, dispute resolution, or platform security.',
          'Published content that already exists on third-party platforms may need to be removed directly on those platforms.',
        ],
      },
      {
        title: 'Processing Time',
        body: [
          'We normally process verified deletion requests within 30 days, unless a longer period is required for legal, security, or abuse-prevention reasons.',
        ],
      },
    ],
  },
}

export default function LegalPage() {
  const { pathname } = useLocation()
  const document = documents[pathname]
  if (!document) return <Navigate to="/" replace />

  return (
    <PublicLayout>
      <section className="section">
        <div className="container legal-page">
          <p className="eyebrow">Postbridge</p>
          <h1>{document.title}</h1>
          <p className="muted">Last updated: {document.updated}</p>
          <p className="lead">{document.intro}</p>

          <div className="legal-sections">
            {document.sections.map((section) => (
              <section key={section.title}>
                <h2>{section.title}</h2>
                {section.body.map((item) => (
                  <p key={item}>{item}</p>
                ))}
              </section>
            ))}
          </div>

          <div className="legal-contact">
            <h2>Contact</h2>
            <p>
              Email: <a href={`mailto:${BILLING_SUPPORT_EMAIL}`}>{BILLING_SUPPORT_EMAIL}</a>
            </p>
          </div>

          <p className="legal-related">
            <Link to="/privacy">Privacy Policy</Link>
            <span aria-hidden="true"> · </span>
            <Link to="/terms">Terms of Service</Link>
            <span aria-hidden="true"> · </span>
            <Link to="/data-deletion">Data Deletion</Link>
          </p>
        </div>
      </section>
    </PublicLayout>
  )
}
