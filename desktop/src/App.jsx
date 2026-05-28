import { useCallback, useEffect, useMemo, useState } from 'react'
import { getRuntimeLogs, getRuntimeStatus, openPostbridge, startRuntime, stopRuntime } from './runtimeApi'
import './styles.css'

const services = [
  ['postgres', 'PostgreSQL + pgvector'],
  ['queue', 'Queue'],
  ['migrate', 'Database migrations'],
  ['api', 'Core API'],
  ['worker', 'Worker + scheduler'],
  ['web', 'Web UI'],
]

function Badge({ state }) {
  return <span className={`badge badge-${state}`}>{state}</span>
}

function ServiceRow({ id, label, status }) {
  const service = status.services?.[id] || { state: 'stopped' }
  return (
    <tr>
      <td>
        <strong>{label}</strong>
        {service.detail && <span className="detail">{service.detail}</span>}
      </td>
      <td><Badge state={service.state || 'stopped'} /></td>
      <td>{service.port ? `127.0.0.1:${service.port}` : 'local'}</td>
    </tr>
  )
}

export default function App() {
  const [status, setStatus] = useState(null)
  const [logs, setLogs] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    const [nextStatus, nextLogs] = await Promise.all([getRuntimeStatus(), getRuntimeLogs()])
    setStatus(nextStatus)
    setLogs(nextLogs)
  }, [])

  useEffect(() => {
    refresh().catch((err) => setError(err.message || String(err)))
    const timer = window.setInterval(() => {
      refresh().catch((err) => setError(err.message || String(err)))
    }, 1500)
    return () => window.clearInterval(timer)
  }, [refresh])

  const running = status?.state === 'running'
  const apiRunning = status?.services?.api?.state === 'running'
  const title = useMemo(() => {
    if (!status) return 'Checking runtime'
    if (running) return 'Postbridge is running locally'
    if (status.state === 'missing_runtime') return 'Runtime bundle is incomplete'
    if (status.state === 'starting') return 'Starting Postbridge'
    if (status.state === 'stopping') return 'Stopping Postbridge'
    return 'Postbridge local runtime is stopped'
  }, [running, status])

  const starting = status?.state === 'starting'
  const active = running || starting

  async function perform(action) {
    setBusy(true)
    setError('')
    try {
      if (action === 'start') await startRuntime()
      if (action === 'stop') await stopRuntime()
      await refresh()
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleOpenPostbridge() {
    setError('')
    try {
      await openPostbridge()
    } catch (err) {
      setError(err.message || String(err))
    }
  }

  return (
    <main className="desktop-shell">
      <section className="hero">
        <div>
          <p className="eyebrow">Postbridge Desktop</p>
          <h1>{title}</h1>
          <p className="lede">
            This shell supervises the full self-host runtime: PostgreSQL, queue,
            Core API, worker, scheduler, migrations, and the existing Postbridge web UI.
          </p>
        </div>
        <div className="actions">
          <button type="button" onClick={() => perform('start')} disabled={busy || active}>
            Start runtime
          </button>
          <button type="button" className="secondary" onClick={handleOpenPostbridge} disabled={!apiRunning}>
            Open Postbridge
          </button>
          <button type="button" className="secondary" onClick={() => perform('stop')} disabled={busy || !active}>
            Stop runtime
          </button>
        </div>
      </section>

      {error && <p className="error">{error}</p>}

      <section className="panel">
        <div className="panel-header">
          <h2>Runtime status</h2>
          <Badge state={status?.state || 'checking'} />
        </div>
        <table>
          <thead>
            <tr>
              <th>Service</th>
              <th>Status</th>
              <th>Endpoint</th>
            </tr>
          </thead>
          <tbody>
            {services.map(([id, label]) => (
              <ServiceRow key={id} id={id} label={label} status={status || { services: {} }} />
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Runtime contract</h2>
          <span className="muted">{status?.platform || 'unknown platform'}</span>
        </div>
        <div className="grid">
          <div>
            <h3>Data directory</h3>
            <p>
              {status?.data_dir || 'Data directory not initialized yet'}
            </p>
          </div>
          <div>
            <h3>Runtime bundle</h3>
            <p>
              {status?.runtime_dir || 'Runtime directory not detected yet'}
            </p>
          </div>
        </div>
      </section>

      <section className="panel logs-panel">
        <div className="panel-header">
          <h2>Logs</h2>
          <button type="button" className="ghost" onClick={refresh}>Refresh</button>
        </div>
        <pre>{logs.join('\n') || 'No logs yet.'}</pre>
      </section>
    </main>
  )
}
