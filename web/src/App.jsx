import { Component, lazy, Suspense, useEffect, useRef } from 'react'
import { Routes, Route, Navigate, useNavigate, useLocation, useParams } from 'react-router-dom'
import { isSelfhostMode } from './adapters/runtime'
import { workspaceEntryPath } from './adapters/workspace'

function LegacyDeliveryStatusRedirect() {
  const { workspaceId } = useParams()
  return <Navigate to={`/workspaces/${workspaceId}/settings`} replace />
}
import { useAuth } from './useAuth'
import LoadingSkeleton from './components/LoadingSkeleton'
import MiniAppAuthGate from './components/MiniAppAuthGate'
import { setToken } from './adapters/sessionToken'
import { consumeAuthReturnTo } from './adapters/authReturnTo'
import { hitMetrika, reachMetrikaGoal } from './metrika'
import { useI18n } from './i18n'

const Home = lazy(() => import('./pages/Home'))
const News = lazy(() => import('./pages/News'))
const NewsDetail = lazy(() => import('./pages/NewsDetail'))
const Landing = lazy(() => import('./pages/Landing'))
const CaseLanding = lazy(() => import('./pages/CaseLanding'))
const Pricing = lazy(() => import('./pages/Pricing'))
const LegalPage = lazy(() => import('./pages/LegalPage'))
const Channels = lazy(() => import('./pages/Channels'))
const Admin = lazy(() => import('./pages/Admin'))
const Wizard = lazy(() => import('./pages/Wizard'))
const AddChannel = lazy(() => import('./pages/AddChannel'))
const EditChannel = lazy(() => import('./pages/EditChannel'))
const JobDetail = lazy(() => import('./pages/JobDetail'))
const PostsList = lazy(() => import('./pages/PostsList'))
const PostEditor = lazy(() => import('./pages/PostEditor'))
const AgentReviewQueue = lazy(() => import('./pages/AgentReviewQueue'))
const AgentCandidatePage = lazy(() => import('./pages/AgentCandidatePage'))
const AgentRunDetail = lazy(() => import('./pages/AgentRunDetail'))
const TopicScout = lazy(() => import('./pages/TopicScout'))
const AgentEditor = lazy(() => import('./pages/AgentEditor'))
const AgentFaq = lazy(() => import('./pages/AgentFaq'))
const AgentOps = lazy(() => import('./pages/AgentOps'))
const WorkspaceSettings = lazy(() => import('./pages/WorkspaceSettings'))
const SettingsRedirect = lazy(() => import('./pages/SettingsRedirect'))
const SelfhostSetup = lazy(() => import('./pages/SelfhostSetup'))

const CHUNK_RELOAD_STORAGE_KEY = 'postbridge.chunk-reload-attempted'

function isChunkLoadError(error) {
  const text = `${error?.name || ''} ${error?.message || ''}`.toLowerCase()
  return (
    text.includes('failed to fetch dynamically imported module') ||
    text.includes('importing a module script failed') ||
    text.includes('error loading dynamically imported module') ||
    text.includes('chunkloaderror')
  )
}

class AppErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error) {
    if (!isChunkLoadError(error)) return
    const attemptedAt = Number(window.sessionStorage.getItem(CHUNK_RELOAD_STORAGE_KEY) || 0)
    if (Date.now() - attemptedAt < 60000) return
    window.sessionStorage.setItem(CHUNK_RELOAD_STORAGE_KEY, String(Date.now()))
    window.location.reload()
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="public-shell">
        <main className="public-main">
          <div className="container">
            <div className="card" style={{ maxWidth: '36rem', margin: '4rem auto' }}>
              <h1 style={{ marginTop: 0 }}>{this.props.errorTitle}</h1>
              <p className="muted">
                {this.props.errorText}
              </p>
              <button type="button" className="btn" onClick={() => window.location.reload()}>
                {this.props.reloadLabel}
              </button>
            </div>
          </div>
        </main>
      </div>
    )
  }
}

function HomeRoute() {
  const { user, loading } = useAuth();
  if (loading) return <LoadingSkeleton />;
  if (!user && isSelfhostMode()) return <Navigate to="/setup" replace />;
  if (!user) return <Home />;
  return <Navigate to={workspaceEntryPath(user)} replace />;
}

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <LoadingSkeleton />;
  if (!user) return <Navigate to="/" replace />;
  return children;
}

function AdminRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <LoadingSkeleton />;
  if (!user) return <Navigate to="/" replace />;
  if (!user.is_platform_admin) return <Navigate to={workspaceEntryPath(user)} replace />;
  return children;
}

function GuestRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <LoadingSkeleton />;
  if (user) return <Navigate to={workspaceEntryPath(user)} replace />;
  return children;
}

function RedirectAiCanvasToEditor() {
  const { workspaceId } = useParams();
  return <Navigate to={`/workspaces/${workspaceId}/content/new`} replace />;
}

function ConnectVkLegacyRedirect() {
  const { workspaceId } = useParams();
  return <Navigate to={`/workspaces/${workspaceId}/channels`} replace />;
}

function MetrikaRouteTracker() {
  const location = useLocation();
  useEffect(() => {
    hitMetrika(location.pathname + location.search + location.hash);
  }, [location.pathname, location.search, location.hash]);
  return null;
}

function OAuthTokenHandler({ children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const handledTokenRef = useRef(false);
  // Read the hash token before ProtectedRoute renders, otherwise useAuth redirects
  // to the home page before useEffect can persist the token.
  const hash = window.location.hash || '';
  const tokenMatch = hash.match(/[#&]token=([^&]+)/);
  if (tokenMatch) {
    setToken(tokenMatch[1]);
  }
  const hadToken = !!tokenMatch;
  useEffect(() => {
    if (hadToken && !handledTokenRef.current) {
      handledTokenRef.current = true;
      const authReturnTo = consumeAuthReturnTo();
      reachMetrikaGoal('auth_completed', { method: 'oauth_hash' });
      window.history.replaceState(null, '', location.pathname + location.search);
      if (authReturnTo) {
        window.location.replace(authReturnTo);
      } else {
        navigate('/', { replace: true });
      }
    }
  }, [navigate, location.pathname, location.search, hadToken]);
  return children;
}

export default function App() {
  const { t } = useI18n()

  return (
    <AppErrorBoundary
      errorTitle={t('app.errorBoundary.title')}
      errorText={t('app.errorBoundary.text')}
      reloadLabel={t('app.errorBoundary.reload')}
    >
    <OAuthTokenHandler>
    <MiniAppAuthGate>
      <MetrikaRouteTracker />
      <Suspense fallback={<LoadingSkeleton />}>
        <Routes>
          <Route path="/" element={<HomeRoute />} />
          <Route path="/setup" element={<SelfhostSetup />} />
          <Route path="/home" element={<Home />} />
          <Route path="/news" element={isSelfhostMode() ? <Navigate to="/" replace /> : <News />} />
          <Route path="/news/:slug" element={isSelfhostMode() ? <Navigate to="/" replace /> : <NewsDetail />} />
          <Route path="/cases/:slug" element={<CaseLanding />} />
          <Route path="/agents/help" element={<AgentFaq publicView />} />
          <Route path="/pricing" element={isSelfhostMode() ? <Navigate to="/" replace /> : <Pricing />} />
          <Route path="/privacy" element={<LegalPage />} />
          <Route path="/terms" element={<LegalPage />} />
          <Route path="/data-deletion" element={<LegalPage />} />
          <Route path="/login" element={
            isSelfhostMode() ? <SelfhostSetup /> : (
              <GuestRoute>
                <Landing />
              </GuestRoute>
            )
          } />
          <Route path="/settings" element={
            <ProtectedRoute>
              <SettingsRedirect />
            </ProtectedRoute>
          } />
          <Route path="/admin" element={
            <ProtectedRoute>
              <Admin />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/migrate" element={
            <ProtectedRoute>
              <Wizard />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/connect-vk" element={
            <ProtectedRoute>
              <ConnectVkLegacyRedirect />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/channels/add" element={
            <ProtectedRoute>
              <AddChannel />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/channels/:channelId/edit" element={
            <ProtectedRoute>
              <EditChannel />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/channels" element={
            <ProtectedRoute>
              <Channels />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/channels/jobs/:jobId" element={
            <ProtectedRoute>
              <JobDetail />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/content" element={
            <ProtectedRoute>
              <PostsList />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/content/new" element={
            <ProtectedRoute>
              <PostEditor />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/content/:postId" element={
            <ProtectedRoute>
              <PostEditor />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/agents/topic-scout" element={
            <ProtectedRoute>
              <TopicScout />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/agents/editor" element={
            <ProtectedRoute>
              <AgentEditor />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/agents/help" element={
            <ProtectedRoute>
              <AgentFaq />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/agents/ops" element={
            <AdminRoute>
              <AgentOps />
            </AdminRoute>
          } />
          <Route path="/workspaces/:workspaceId/agents/runs/:runId" element={
            <ProtectedRoute>
              <AgentRunDetail />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/agents/candidates" element={
            <ProtectedRoute>
              <AgentReviewQueue />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/agents/candidates/:reviewItemId" element={
            <ProtectedRoute>
              <AgentCandidatePage />
            </ProtectedRoute>
          } />
          <Route path="/workspaces/:workspaceId/settings" element={
            <ProtectedRoute>
              <WorkspaceSettings />
            </ProtectedRoute>
          } />
          <Route
            path="/workspaces/:workspaceId/delivery-status"
            element={
              <ProtectedRoute>
                <LegacyDeliveryStatusRedirect />
              </ProtectedRoute>
            }
          />
          <Route
            path="/workspaces/:workspaceId/ai-canvas"
            element={
              <ProtectedRoute>
                <RedirectAiCanvasToEditor />
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </MiniAppAuthGate>
    </OAuthTokenHandler>
    </AppErrorBoundary>
  );
}
