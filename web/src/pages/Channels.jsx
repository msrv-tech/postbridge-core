import { useState, useEffect, useRef, useMemo } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { clearToken } from '../adapters/sessionToken'
import { useAuth } from '../useAuth'
import AppShell from '../components/AppShell'
import { BILLING_SUPPORT_EMAIL, formatSubscriptionPeriod } from '../billingSupport'
import {
  cancelSubscription,
  createSubscription,
  initMigrationPayment,
  isBillingEnabled,
  requestBillingEmail,
  verifyBillingEmail,
} from '../adapters/billing'
import { deleteBridge, listBridges, updateBridge } from '../adapters/bridges'
import { deleteChannelRegistryItem, listChannelRegistry } from '../adapters/channels'
import { getDashboardSummary, listDashboardJobs } from '../adapters/dashboard'
import { startHistoricalImportJob } from '../adapters/jobs'
import { useI18n } from '../i18n'

function channelUrl(platform, platformChannelId, rssFeedUrl) {
  if (!platformChannelId && !rssFeedUrl) return null
  const raw = String(platformChannelId || '')
  if (platform === 'telegram') {
    if (/^-?\d+$/.test(raw.replace(/^-100/, ''))) {
      return `https://t.me/c/${raw.replace(/^-100/, '')}`
    }
    return `https://t.me/${raw.replace(/^@/, '')}`
  }
  if (platform === 'max') return `https://web.max.ru/${raw}`
  if (platform === 'vk') {
    const m = raw.match(/^-?(\d+)$/)
    return m ? `https://vk.com/club${m[1]}` : null
  }
  if (platform === 'linkedin') {
    const org = raw.match(/^urn:li:organization:(.+)$/)
    if (org) return `https://www.linkedin.com/company/${org[1]}`
    const person = raw.match(/^urn:li:person:(.+)$/)
    if (person) return `https://www.linkedin.com/in/${person[1]}`
  }
  if (platform === 'zen') return raw.startsWith('http') ? raw : null
  if (platform === 'rss') {
    if (rssFeedUrl) return rssFeedUrl
    return raw.startsWith('http') ? raw : null
  }
  return null
}

function platformBadgeLabel(platform, t) {
  const labels = {
    telegram: 'TG',
    max: 'MAX',
    vk: 'VK',
    linkedin: 'IN',
    zen: t('platform.rss'),
    rss: t('platform.rss'),
  }
  return labels[platform] || platform
}

function adaptationModeHint(mode, t) {
  if (mode === 'ai_auto') {
    return t('channels.adaptation.hint.aiAuto')
  }
  if (mode === 'ai_review') {
    return t('channels.adaptation.hint.aiReview')
  }
  return t('channels.adaptation.hint.ruleOnly')
}

function bridgeLinkBackDraft(ch, drafts) {
  return drafts[ch.id] ?? {
    enabled: Boolean(ch.link_back_enabled),
    siteUrl: ch.link_back_site_url || '',
  }
}

function channelsErrorText(err, t) {
  if (err?.code === 'BILLING_AI_ADAPT_PAID_ONLY') {
    return t('channels.adaptation.paidOnly')
  }
  return err.message
}

function responseItems(response) {
  return Array.isArray(response?.items) ? response.items : []
}

function bridgeDisplayItems(bridgeResponse, channelRegistryResponse) {
  const registryItems = responseItems(channelRegistryResponse)
  const registryById = new Map(registryItems.map((item) => [item.id, item]))
  return responseItems(bridgeResponse)
    .filter((bridge) => bridge.mode === 'live_sync' && bridge.status === 'active')
    .map((bridge) => {
      const source = registryById.get(bridge.source_channel_id)
      const target = registryById.get(bridge.target_channel_id)
      if ((!source || !target) && (!bridge.source_display || !bridge.target_display)) return null
      return {
        ...bridge,
        source_platform: source?.platform || bridge.source_platform,
        target_platform: target?.platform || bridge.target_platform,
        source_display: source?.title || source?.platform_channel_id || bridge.source_display,
        target_display: target?.title || target?.platform_channel_id || bridge.target_display,
        source_platform_channel_id: source?.platform_channel_id || bridge.source_platform_channel_id,
        target_platform_channel_id: target?.platform_channel_id || bridge.target_platform_channel_id,
        live_sync_source_supported:
          source?.live_sync_source_supported === true ||
          bridge.live_sync_source_supported === true,
      }
    })
    .filter(Boolean)
}

export default function Channels() {
  const { locale, t } = useI18n();
  const { user, refreshUser } = useAuth();
  const { workspaceId: workspaceIdParam } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [summary, setSummary] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [channels, setChannels] = useState([]);
  const [channelRegistry, setChannelRegistry] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [historyModal, setHistoryModal] = useState(null);
  const [historyLimit, setHistoryLimit] = useState(20);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [historyPaymentNeeded, setHistoryPaymentNeeded] = useState(false);
  const [historyPaymentUrl, setHistoryPaymentUrl] = useState(null);
  const [historyInvoiceUrl, setHistoryInvoiceUrl] = useState(null);
  const [expandedChannels, setExpandedChannels] = useState(new Set());
  const [tariffModal, setTariffModal] = useState(false);
  const [plans, setPlans] = useState([]);
  const [tariffLoading, setTariffLoading] = useState(false);
  const [tariffError, setTariffError] = useState('');
  const [tariffStarsWaiting, setTariffStarsWaiting] = useState(null); // plan_code при ожидании оплаты credits
  const [disableLoadingId, setDisableLoadingId] = useState(null);
  const [deleteChannelLoadingId, setDeleteChannelLoadingId] = useState(null);
  const [adaptationLoadingId, setAdaptationLoadingId] = useState(null);
  const [adaptationDrafts, setAdaptationDrafts] = useState({});
  const [linkBackDrafts, setLinkBackDrafts] = useState({});
  const openHistoryHandled = useRef(false);
  const pendingTbankAfterEmailRef = useRef(null);
  const [billingEmailModal, setBillingEmailModal] = useState(null);
  /** Промежуточный шаг: согласие на рекуррент только при оплате ₽ (card payment), не при credits. */
  const [tbankSubscriptionConsentModal, setTbankSubscriptionConsentModal] = useState(null);
  const [tbankSubscriptionConsentChecked, setTbankSubscriptionConsentChecked] = useState(false);
  const workspaceId = workspaceIdParam || user?.workspaces?.[0]?.id || '';
  const billingEnabled = isBillingEnabled(user);
  const canUseAiPlatformAdapt = summary?.billing?.ai_platform_adapt_enabled === true;

  const channelIdsInBridges = useMemo(
    () => new Set(channels.flatMap((c) => [c.source_channel_id, c.target_channel_id]).filter(Boolean)),
    [channels]
  );

  const refetchData = () => {
    if (!workspaceId) return;
    Promise.all([
      getDashboardSummary(workspaceId),
      listDashboardJobs(workspaceId),
      listChannelRegistry(workspaceId),
      listBridges(workspaceId),
    ])
      .then(([s, j, reg, bridges]) => {
        setSummary(s);
        setJobs(responseItems(j));
        setChannelRegistry(responseItems(reg));
        setChannels(bridgeDisplayItems(bridges, reg));
      })
      .catch((e) => setError(e.message));
  };

  const handleDisableSync = async (ch) => {
    if (!window.confirm(t('channels.confirm.disableBridge', { source: ch.source_display, target: ch.target_display }))) return;
    setDisableLoadingId(ch.id);
    setError('');
    try {
      await deleteBridge(workspaceId, ch.id);
      refetchData();
    } catch (e) {
      setError(e.message);
    } finally {
      setDisableLoadingId(null);
    }
  };

  const handleDeleteChannel = async (ch) => {
    const display = ch.title || ch.platform_channel_id;
    if (!window.confirm(t('channels.confirm.deleteChannel', { channel: display }))) return;
    setDeleteChannelLoadingId(ch.id);
    setError('');
    try {
      await deleteChannelRegistryItem(workspaceId, ch.id);
      refetchData();
    } catch (e) {
      setError(e.message);
    } finally {
      setDeleteChannelLoadingId(null);
    }
  };

  const handleBridgeAdaptationSave = async (ch, nextMode, nextInstructions, nextLinkBack = null) => {
    setAdaptationLoadingId(ch.id);
    setError('');
    const linkBack = nextLinkBack || bridgeLinkBackDraft(ch, linkBackDrafts)
    try {
      const updated = await updateBridge(workspaceId, ch.id, {
        adaptation_mode: nextMode,
        adaptation_instructions: nextInstructions,
        link_back_enabled: Boolean(linkBack.enabled),
        link_back_site_url: linkBack.siteUrl,
      });
      setChannels((items) =>
        items.map((item) =>
          item.id === ch.id
            ? {
                ...item,
                adaptation_mode: updated.adaptation_mode,
                adaptation_instructions: updated.adaptation_instructions,
                link_back_enabled: updated.link_back_enabled,
                link_back_site_url: updated.link_back_site_url,
              }
            : item
        )
      );
      setAdaptationDrafts((drafts) => {
        const next = { ...drafts };
        delete next[ch.id];
        return next;
      });
      setLinkBackDrafts((drafts) => {
        const next = { ...drafts };
        delete next[ch.id];
        return next;
      });
    } catch (e) {
      setError(channelsErrorText(e, t));
    } finally {
      setAdaptationLoadingId(null);
    }
  };

  const toggleChannel = (chId) => {
    setExpandedChannels((prev) => {
      const next = new Set(prev);
      if (next.has(chId)) next.delete(chId);
      else next.add(chId);
      return next;
    });
  };

  const getJobsForChannel = (ch) => {
    const srcId = ch.source_channel_id;
    const tgtId = ch.target_channel_id;
    if (!srcId || !tgtId) return [];
    return jobs.filter(
      (j) => j.source_channel_id === srcId && j.target_channel_id === tgtId
    );
  };

  useEffect(() => {
    if (!workspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    Promise.all([
      getDashboardSummary(workspaceId),
      listDashboardJobs(workspaceId),
      listChannelRegistry(workspaceId),
      listBridges(workspaceId),
    ])
      .then(([s, j, reg, bridges]) => {
        setSummary(s);
        const channelItems = bridgeDisplayItems(bridges, reg);
        const jobItems = responseItems(j);
        setJobs(jobItems);
        setChannels(channelItems);
        setChannelRegistry(responseItems(reg));
        // Раскрыть каналы с историческими переносами, чтобы результат был виден сразу
        const withJobs = new Set(
          channelItems.filter(
            (ch) =>
              ch.source_channel_id &&
              ch.target_channel_id &&
              jobItems.some(
                (job) =>
                  job.source_channel_id === ch.source_channel_id &&
                  job.target_channel_id === ch.target_channel_id
              )
          ).map((ch) => ch.id)
        );
        if (withJobs.size > 0) {
          setExpandedChannels(withJobs);
        }
      })
      .catch((e) => {
        setSummary(null);
        setJobs([]);
        setChannels([]);
        setChannelRegistry([]);
        setError(e.message);
      })
      .finally(() => setLoading(false));
  }, [workspaceId]);

  // При загрузке с openHistory=channel_id — открыть модалку исторического переноса
  useEffect(() => {
    if (loading || !channels.length || openHistoryHandled.current) return;
    const params = new URLSearchParams(location.search);
    const openHistoryId = params.get('openHistory');
    if (!openHistoryId) return;
    const ch = channels.find((c) => String(c.id) === openHistoryId);
    if (ch) {
      openHistoryHandled.current = true;
      setHistoryModal(ch);
      setHistoryLimit(20);
      setHistoryError('');
      setHistoryPaymentNeeded(false);
      setHistoryPaymentUrl(null);
      setHistoryInvoiceUrl(null);
      params.delete('openHistory');
      const newSearch = params.toString();
      navigate(location.pathname + (newSearch ? '?' + newSearch : ''), { replace: true });
    }
  }, [loading, channels, location.search, location.pathname, navigate]);

  const handleLogout = () => {
    clearToken();
    navigate('/');
  };

  useEffect(() => {
    if (new URLSearchParams(location.search).get('billing') === 'migration_paid') {
      setHistoryPaymentNeeded(false);
      setHistoryPaymentUrl(null);
      setHistoryInvoiceUrl(null);
      setHistoryError('');
    }
  }, [location.search]);

  // Polling статуса подписки после оплаты credits (invoice открыт в новой вкладке)
  useEffect(() => {
    if (!tariffStarsWaiting || !workspaceId) return;
    const interval = setInterval(async () => {
      try {
        const s = await getDashboardSummary(workspaceId);
        setSummary(s);
        if (s?.billing?.plan_code === tariffStarsWaiting && s?.billing?.status === 'active') {
          setTariffStarsWaiting(null);
          setTariffModal(false);
          clearInterval(interval);
        }
      } catch {
        // ignore
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [tariffStarsWaiting, workspaceId]);

  const openHistoryModal = (ch) => {
    setHistoryModal(ch);
    setHistoryLimit(20);
    setHistoryError('');
    setHistoryPaymentNeeded(false);
    setHistoryPaymentUrl(null);
    setHistoryInvoiceUrl(null);
  };

  const closeHistoryModal = () => {
    setHistoryModal(null);
  };

  const handleHistoryStart = async () => {
    if (!historyModal || !workspaceId) return;
    setHistoryError('');
    setHistoryLoading(true);
    try {
      const job = await startHistoricalImportJob(workspaceId, {
        bridge_id: historyModal.id,
        requested_limit: historyLimit,
      });
      closeHistoryModal();
      navigate(`/workspaces/${workspaceId}/channels/jobs/${job.id}`);
    } catch (e) {
      if (e.code === 'BILLING_HISTORICAL_MIGRATION_PAID_ONLY' && e.status === 402) {
        setHistoryPaymentNeeded(true);
        setHistoryError(t('channels.history.paymentNeeded'));
      } else {
        setHistoryError(e.message);
      }
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleHistoryPaymentTbank = async () => {
    if (!billingEnabled) return;
    setHistoryError('');
    setHistoryLoading(true);
    try {
      const init = await initMigrationPayment(workspaceId, { provider: 'tbank', bridgeId: historyModal.id });
      if (init.payment_url) {
        window.open(init.payment_url, '_blank', 'noopener,noreferrer');
      }
      setHistoryPaymentUrl(init.payment_url);
      setHistoryInvoiceUrl(null);
    } catch (payErr) {
      setHistoryError(payErr.message || t('channels.history.tbankFailed'));
    } finally {
      setHistoryLoading(false);
    }
  };

  const openBillingEmailModalThen = (action) => {
    if (!user?.billing_email_required) {
      void action();
      return;
    }
    pendingTbankAfterEmailRef.current = action;
    setBillingEmailModal({ step: 'email', email: '', code: '', error: '' });
  };

  const submitBillingEmailRequest = async () => {
    const em = (billingEmailModal?.email || '').trim();
    if (!em) {
      setBillingEmailModal((m) => (m ? { ...m, error: t('channels.billingEmail.empty') } : m));
      return;
    }
    setBillingEmailModal((m) => (m ? { ...m, error: '' } : m));
    try {
      const r = await requestBillingEmail({ email: em });
      setBillingEmailModal((m) =>
        m ? { ...m, step: 'code', error: '', devCode: r.code || null } : m
      );
    } catch (e) {
      setBillingEmailModal((m) => (m ? { ...m, error: e.message } : m));
    }
  };

  const submitBillingEmailVerify = async () => {
    const raw = (billingEmailModal?.code || '').trim();
    if (raw.length !== 4 || !/^\d+$/.test(raw)) {
      setBillingEmailModal((m) => (m ? { ...m, error: t('channels.billingEmail.badCode') } : m));
      return;
    }
    setBillingEmailModal((m) => (m ? { ...m, error: '' } : m));
    try {
      await verifyBillingEmail({ code: raw });
      await refreshUser();
      const run = pendingTbankAfterEmailRef.current;
      pendingTbankAfterEmailRef.current = null;
      setBillingEmailModal(null);
      if (run) await run();
    } catch (e) {
      setBillingEmailModal((m) => (m ? { ...m, error: e.message } : m));
    }
  };

  const closeBillingEmailModal = () => {
    pendingTbankAfterEmailRef.current = null;
    setBillingEmailModal(null);
  };

  const executeSubscriptionCreate = async (p, provider) => {
    if (!billingEnabled) return;
    setTariffError('');
    setTariffLoading(true);
    try {
      const currentCode = summary?.billing?.plan_code || '';
      const currentPlan = plans.find((x) => x.code === currentCode);
      const targetPrice = p.price_rub || p.price_stars || 0;
      const currentPrice = currentPlan?.price_rub || currentPlan?.price_stars || 0;
      if (targetPrice > currentPrice) {
        const runCreate = async () => {
          const res = await createSubscription(workspaceId, { plan_code: p.code, provider });
          if (res.payment_url) window.location.href = res.payment_url;
          else if (res.invoice_url) {
            window.open(res.invoice_url, '_blank');
            setTariffStarsWaiting(p.code);
          } else setTariffModal(false);
        };
        if (provider === 'tbank' && user?.billing_email_required) {
          setTbankSubscriptionConsentModal(null);
          openBillingEmailModalThen(runCreate);
          return;
        }
        await runCreate();
      } else {
        await cancelSubscription(workspaceId, { target_plan_code: p.code });
        setTariffModal(false);
        window.location.reload();
      }
    } catch (e) {
      setTariffError(e.message);
    } finally {
      setTariffLoading(false);
    }
  };

  const handlePlanSelect = (p, provider) => {
    if (!billingEnabled) return;
    setTariffError('');
    const currentCode = summary?.billing?.plan_code || '';
    const currentPlan = plans.find((x) => x.code === currentCode);
    const targetPrice = p.price_rub || p.price_stars || 0;
    const currentPrice = currentPlan?.price_rub || currentPlan?.price_stars || 0;
    const isPaidUpgrade = targetPrice > currentPrice;
    if (provider === 'tbank' && p.price_rub && isPaidUpgrade) {
      setTbankSubscriptionConsentChecked(false);
      setTbankSubscriptionConsentModal({ plan: p });
      return;
    }
    void executeSubscriptionCreate(p, provider);
  };

  const handleHistoryPaymentStars = async () => {
    if (!billingEnabled) return;
    setHistoryError('');
    setHistoryLoading(true);
    try {
      const init = await initMigrationPayment(workspaceId, { provider: 'stars', bridgeId: historyModal.id });
      if (init.invoice_url) {
        window.open(init.invoice_url, '_blank');
        setHistoryInvoiceUrl(init.invoice_url);
      }
      setHistoryPaymentUrl(null);
    } catch (payErr) {
      setHistoryError(payErr.message || t('channels.history.starsFailed'));
    } finally {
      setHistoryLoading(false);
    }
  };

  if (!user) return null;

  return (
    <AppShell
      title={t('channels.title')}
      workspaceId={workspaceId}
      user={user}
      onLogout={handleLogout}
      showAdminLink={user.is_platform_admin}
    >
      {user.workspaces?.length > 0 && workspaceId && (
        <div className="toolbar">
          <div className="toolbar-actions">
            {channelRegistry.length > 0 && (
              <Link to={`/workspaces/${workspaceId}/migrate`} className="btn">
                {t('wizard.title')}
              </Link>
            )}
            <Link to={`/workspaces/${workspaceId}/channels/add`} className="btn btn-secondary">
              {t('addChannel.title')}
            </Link>
          </div>
        </div>
      )}

      {!user.workspaces?.length && (
        <div className="card empty-state">
          <h3>{t('channels.noWorkspace.title')}</h3>
          <p>{t('channels.noWorkspace.text')}</p>
        </div>
      )}

      {loading && <p className="muted">{t('common.loading')}</p>}
      {!loading && error && <p className="error">{error}</p>}

      {!loading && new URLSearchParams(location.search).get('vk_oauth_error') && (
        <div className="card" style={{ borderColor: 'var(--error)', background: 'rgba(239, 68, 68, 0.08)' }}>
          <p className="error">
            {t('channels.vk.error', { error: new URLSearchParams(location.search).get('vk_oauth_error') })}
          </p>
        </div>
      )}

      {!loading && new URLSearchParams(location.search).get('success') === 'vk_connected' && (
        <div className="card" style={{ borderColor: 'var(--primary)', background: 'rgba(59, 130, 246, 0.08)' }}>
          <p className="success">{t('channels.vk.connected')}</p>
        </div>
      )}

      {!loading && new URLSearchParams(location.search).get('success') === 'channel_connected' && (
        <div className="card" style={{ borderColor: 'var(--primary)', background: 'rgba(59, 130, 246, 0.08)' }}>
          <p className="success">{t('channels.bridge.connected')}</p>
        </div>
      )}

      {!loading && billingEnabled && new URLSearchParams(location.search).get('billing') === 'migration_paid' && (
        <div className="card" style={{ borderColor: 'var(--primary)', background: 'rgba(59, 130, 246, 0.08)' }}>
          <p className="success">{t('channels.migrationPaid')}</p>
        </div>
      )}

      {!loading && new URLSearchParams(location.search).get('success') === 'channel_added' && (
        <div className="card" style={{ borderColor: 'var(--primary)', background: 'rgba(59, 130, 246, 0.08)' }}>
          <p className="success">{t('channels.channelAdded')}</p>
        </div>
      )}

      {!loading && new URLSearchParams(location.search).get('success') === 'channel_updated' && (
        <div className="card" style={{ borderColor: 'var(--primary)', background: 'rgba(59, 130, 246, 0.08)' }}>
          <p className="success">{t('channels.channelUpdated')}</p>
        </div>
      )}

      {!loading && channelRegistry.length === 0 && user.workspaces?.length > 0 && workspaceId && (
        <div className="card empty-state">
          <h3>{t('channels.empty.title')}</h3>
          <p>{t('channels.empty.text')}</p>
          <Link to={`/workspaces/${workspaceId}/channels/add`} className="btn" style={{ marginTop: '1rem' }}>
            {t('addChannel.title')}
          </Link>
        </div>
      )}

      {!loading && channelRegistry.length > 0 && (
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.75rem' }}>
            <h3 style={{ margin: 0 }}>{t('channels.registry.title')}</h3>
            <Link to={`/workspaces/${workspaceId}/channels/add`} className="btn btn-secondary btn-small">
              {t('addChannel.title')}
            </Link>
          </div>
          <div className="sync-tree-channel-links" style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
            {channelRegistry.map((ch) => {
              const link = channelUrl(ch.platform, ch.platform_channel_id, ch.rss_feed_url);
              const label = platformBadgeLabel(ch.platform, t);
              const display = ch.title || ch.platform_channel_id;
              const rssUrl = ch.platform === 'rss' ? (ch.rss_feed_url || (ch.platform_channel_id?.startsWith('http') ? ch.platform_channel_id : null)) : null;
              const badgeClass = `channel-link-badge channel-link-badge-${ch.platform}`;
              const rights = [ch.can_read && t('channels.right.read'), ch.can_write && t('channels.right.write')].filter(Boolean).join(', ');
              const ChannelContent = () => (
                <>
                  <span className="channel-link-badge-label">{label}</span>
                  <span className="channel-link-badge-name">{display}</span>
                  {rssUrl && (
                    <span className="channel-rss-url" style={{ fontSize: '0.65rem', opacity: 0.9, marginLeft: '0.25rem', maxWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={rssUrl}>
                      {rssUrl}
                    </span>
                  )}
                  {rights && (
                    <span className="channel-rights" style={{ fontSize: '0.7rem', opacity: 0.85, marginLeft: '0.25rem' }} title={t('channels.rightsTitle', { rights })}>
                      ({rights})
                    </span>
                  )}
                </>
              );
              const canDelete = !channelIdsInBridges.has(ch.id);
              return (
                <span key={ch.id} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                  {link ? (
                    <a
                      href={link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={badgeClass}
                      onClick={(e) => {
                        e.preventDefault();
                        window.open(link, '_blank', 'noopener,noreferrer');
                      }}
                    >
                      <ChannelContent />
                    </a>
                  ) : (
                    <span className={badgeClass}>
                      <ChannelContent />
                    </span>
                  )}
                  <Link
                    to={`/workspaces/${workspaceId}/channels/${ch.id}/edit`}
                    className="btn btn-secondary btn-small"
                    style={{ padding: '0.15rem 0.4rem', fontSize: '0.75rem' }}
                    title={t('channels.editTitle')}
                    aria-label={t('channels.editTitle')}
                  >
                    ✎
                  </Link>
                  {canDelete && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-small"
                      style={{ padding: '0.15rem 0.4rem', fontSize: '0.75rem' }}
                      onClick={() => handleDeleteChannel(ch)}
                      disabled={deleteChannelLoadingId === ch.id}
                      title={t('channels.deleteTitle')}
                      aria-label={t('channels.deleteTitle')}
                    >
                      {deleteChannelLoadingId === ch.id ? '…' : '✕'}
                    </button>
                  )}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {!loading && summary && channels.length > 0 && (
        <div className="card">
          <h3>{t('channels.bridges.title')}</h3>
          <div className="sync-tree">
            {channels.map((ch) => {
              const srcLink = channelUrl(ch.source_platform, ch.source_platform_channel_id)
              const tgtLink = channelUrl(ch.target_platform, ch.target_platform_channel_id)
              const channelJobs = getJobsForChannel(ch)
              const isExpanded = expandedChannels.has(ch.id)
              const srcLabel = platformBadgeLabel(ch.source_platform, t)
              const tgtLabel = platformBadgeLabel(ch.target_platform, t)
              const badgeClass = (p) => `channel-link-badge channel-link-badge-${p}`
              const adaptationMode = ch.adaptation_mode || 'rule_only'
              const aiAdaptationLocked = !canUseAiPlatformAdapt
              const adaptationInstructions =
                adaptationDrafts[ch.id] ?? ch.adaptation_instructions ?? ''
              const linkBack = bridgeLinkBackDraft(ch, linkBackDrafts)
              return (
                <div key={ch.id} className="sync-tree-item">
                  <div className="sync-tree-header">
                    <button
                      type="button"
                      className="sync-tree-toggle"
                      onClick={() => toggleChannel(ch.id)}
                      aria-expanded={isExpanded}
                    >
                      {channelJobs.length > 0 ? (isExpanded ? '▼' : '▶') : ''}
                    </button>
                    <div className="sync-tree-channel sync-tree-channel-links">
                      {srcLink ? (
                        <a
                          href={srcLink}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={badgeClass(ch.source_platform)}
                          onClick={(e) => {
                            e.stopPropagation();
                            e.preventDefault();
                            window.open(srcLink, '_blank', 'noopener,noreferrer');
                          }}
                        >
                          <span className="channel-link-badge-label">{srcLabel}</span>
                          <span className="channel-link-badge-name">{ch.source_display}</span>
                        </a>
                      ) : (
                        <span className={badgeClass(ch.source_platform)}>
                          <span className="channel-link-badge-label">{srcLabel}</span>
                          <span className="channel-link-badge-name">{ch.source_display}</span>
                        </span>
                      )}
                      {tgtLink ? (
                        <a
                          href={tgtLink}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={badgeClass(ch.target_platform)}
                          onClick={(e) => {
                            e.stopPropagation();
                            e.preventDefault();
                            window.open(tgtLink, '_blank', 'noopener,noreferrer');
                          }}
                        >
                          <span className="channel-link-badge-label">{tgtLabel}</span>
                          <span className="channel-link-badge-name">{ch.target_display}</span>
                        </a>
                      ) : (
                        <span className={badgeClass(ch.target_platform)}>
                          <span className="channel-link-badge-label">{tgtLabel}</span>
                          <span className="channel-link-badge-name">{ch.target_display}</span>
                        </span>
                      )}
                    </div>
                    {ch.live_sync_source_supported ? (
                      <span
                        className="badge badge-running bridge-live-sync-badge"
                        title={t('channels.liveSync.title')}
                      >
                        Live sync
                      </span>
                    ) : (
                      <span
                        className="bridge-live-sync-off muted"
                        title={t('channels.liveSync.offTitle')}
                      >
                        {t('channels.liveSync.off')}
                      </span>
                    )}
                    <button
                      type="button"
                      className="btn btn-secondary btn-small"
                      onClick={() => openHistoryModal(ch)}
                    >
                      {t('channels.history.button')}
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary btn-small"
                      onClick={() => handleDisableSync(ch)}
                      disabled={disableLoadingId === ch.id}
                    >
                      {disableLoadingId === ch.id ? t('channels.disable.loading') : t('channels.disable')}
                    </button>
                  </div>
                  <div className="bridge-settings-panel">
                    <div className="bridge-adaptation-settings">
                      <div className="bridge-adaptation-mode">
                        <label htmlFor={`bridge-adaptation-${ch.id}`}>{t('channels.adaptation.mode')}</label>
                        <select
                          id={`bridge-adaptation-${ch.id}`}
                          value={adaptationMode}
                          disabled={adaptationLoadingId === ch.id}
                          onChange={(e) =>
                            handleBridgeAdaptationSave(
                              ch,
                              e.target.value,
                              adaptationInstructions
                            )
                          }
                        >
                          <option value="rule_only">{t('channels.adaptation.ruleOnly')}</option>
                          <option value="ai_auto" disabled={aiAdaptationLocked}>
                            {t('channels.adaptation.aiAuto')}
                          </option>
                          <option value="ai_review" disabled={aiAdaptationLocked}>
                            {t('channels.adaptation.aiReview')}
                          </option>
                        </select>
                        <p>{adaptationModeHint(adaptationMode, t)}</p>
                        {aiAdaptationLocked && billingEnabled && (
                          <p className="bridge-adaptation-upgrade">
                            {t('channels.adaptation.upgradeHint')}{' '}
                            <Link to={`/workspaces/${workspaceId}/settings?billing=change-plan&plan=pro`}>
                              {t('common.upgradePlan')}
                            </Link>
                          </p>
                        )}
                      </div>
                      {adaptationMode === 'rule_only' ? (
                        <div className="bridge-adaptation-note">
                          {t('channels.adaptation.instructionUnused')}
                        </div>
                      ) : (
                        <>
                          <div className="bridge-adaptation-instructions">
                            <label htmlFor={`bridge-adaptation-instructions-${ch.id}`}>
                              {t('channels.adaptation.instructions')}
                            </label>
                            <textarea
                              id={`bridge-adaptation-instructions-${ch.id}`}
                              rows={2}
                              value={adaptationInstructions}
                              disabled={adaptationLoadingId === ch.id}
                              placeholder={t('channels.adaptation.placeholder')}
                              onChange={(e) =>
                                setAdaptationDrafts((drafts) => ({
                                  ...drafts,
                                  [ch.id]: e.target.value,
                                }))
                              }
                            />
                          </div>
                          <button
                            type="button"
                            className="btn btn-secondary btn-small"
                            disabled={adaptationLoadingId === ch.id}
                            onClick={() =>
                              handleBridgeAdaptationSave(
                                ch,
                                adaptationMode,
                                adaptationInstructions
                              )
                            }
                          >
                            {adaptationLoadingId === ch.id ? t('common.saving') : t('channels.adaptation.saveInstructions')}
                          </button>
                        </>
                      )}
                    </div>
                    <div className="bridge-link-back-settings">
                      <div className="bridge-link-back-head">
                        <div>
                          <span className="bridge-link-back-title">{t('channels.linkBack.title')}</span>
                          <p>
                            {t('channels.linkBack.text')}
                          </p>
                        </div>
                        <label className="bridge-link-back-checkbox">
                          <input
                            type="checkbox"
                            checked={Boolean(linkBack.enabled)}
                            disabled={adaptationLoadingId === ch.id}
                            onChange={(e) => {
                              const checked = e.target.checked
                              setLinkBackDrafts((drafts) => ({
                                ...drafts,
                                [ch.id]: { ...linkBack, enabled: checked },
                              }))
                              handleBridgeAdaptationSave(
                                ch,
                                adaptationMode,
                                adaptationInstructions,
                                { ...linkBack, enabled: checked },
                              )
                            }}
                          />
                          <span>{t('channels.linkBack.toggle')}</span>
                        </label>
                      </div>
                      <div className="bridge-link-back-controls">
                        <label htmlFor={`bridge-link-back-site-${ch.id}`}>
                          {t('channels.linkBack.url')}
                        </label>
                        <input
                          id={`bridge-link-back-site-${ch.id}`}
                          className="bridge-link-back-url"
                          type="url"
                          value={linkBack.siteUrl}
                          disabled={adaptationLoadingId === ch.id || !linkBack.enabled}
                          placeholder="https://site.ru/news"
                          onChange={(e) =>
                            setLinkBackDrafts((drafts) => ({
                              ...drafts,
                              [ch.id]: { ...linkBack, siteUrl: e.target.value },
                            }))
                          }
                        />
                        <button
                          type="button"
                          className="btn btn-secondary btn-small"
                          disabled={adaptationLoadingId === ch.id || !linkBack.enabled}
                          onClick={() =>
                            handleBridgeAdaptationSave(
                              ch,
                              adaptationMode,
                              adaptationInstructions,
                              linkBack,
                            )
                          }
                        >
                          {adaptationLoadingId === ch.id ? t('common.saving') : t('channels.linkBack.save')}
                        </button>
                      </div>
                      <p className="bridge-adaptation-note">
                        {t('channels.linkBack.note', {
                          example: '/news/chto-takoe-postbridge-a1b2c3d4',
                          section: '/news',
                        })}
                      </p>
                    </div>
                  </div>
                  {isExpanded && channelJobs.length > 0 && (
                    <div className="sync-tree-children">
                      {channelJobs.map((job) => {
                        const dateStr = job.created_at
                          ? new Date(job.created_at).toLocaleDateString(locale, {
                              day: 'numeric',
                              month: 'short',
                              year: 'numeric',
                            })
                          : '';
                        const countStr =
                          job.status === 'completed' || job.processed_posts > 0
                            ? t('channels.job.count', { processed: job.processed_posts, limit: job.requested_limit })
                            : job.status === 'failed'
                              ? t('channels.job.failed')
                              : t('channels.job.processing');
                        return (
                          <Link
                            key={job.id}
                            to={`/workspaces/${workspaceId}/channels/jobs/${job.id}`}
                            className="sync-tree-job jobs-list-link"
                          >
                            <span>
                              {t('jobDetail.title', { date: dateStr || '—' })}
                            </span>
                            <span className="list-meta">{countStr}</span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {historyModal && (
        <div className="modal-overlay" onClick={closeHistoryModal}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>{t('channels.history.button')}</h3>
            <p className="section-copy">
              «{historyModal.source_display}» → «{historyModal.target_display}»
            </p>
            {historyModal.source_platform === 'postbridge' && summary?.billing?.plan_code === 'free' && (
              <p className="section-copy muted" style={{ marginBottom: '0.75rem' }}>
                {t('channels.history.freePostbridge')}
              </p>
            )}
            <div className="form-group">
              <label htmlFor="history-limit">{t('channels.history.limit')}</label>
              <input
                id="history-limit"
                type="number"
                value={historyLimit}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  setHistoryLimit(isNaN(v) ? 20 : Math.max(1, Math.min(1000, v)));
                }}
                min={1}
                max={1000}
              />
            </div>
            {historyError && <p className="error">{historyError}</p>}
            <div className="inline-actions" style={{ marginTop: '1rem' }}>
              <button type="button" className="btn btn-secondary" onClick={closeHistoryModal}>
                {t('common.cancel')}
              </button>
              {historyPaymentNeeded && billingEnabled && !historyPaymentUrl && !historyInvoiceUrl ? (
                <>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => openBillingEmailModalThen(handleHistoryPaymentTbank)}
                    disabled={historyLoading}
                  >
                    {t('channels.history.payRub', { price: summary?.migration_product?.price_rub ?? 500 })}
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={handleHistoryPaymentStars} disabled={historyLoading}>
                    {t('channels.history.payStars', { price: summary?.migration_product?.price_stars ?? 280 })}
                  </button>
                </>
              ) : (
                <>
                  {historyPaymentUrl && (
                    <a
                      href={historyPaymentUrl}
                      className="btn btn-secondary"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {t('channels.history.openTbank')}
                    </a>
                  )}
                  {historyInvoiceUrl && (
                    <a href={historyInvoiceUrl} target="_blank" rel="noopener noreferrer" className="btn btn-secondary">
                      {t('channels.history.openStars')}
                    </a>
                  )}
                  <button type="button" className="btn" onClick={handleHistoryStart} disabled={historyLoading}>
                    {historyLoading ? t('common.running') : t('common.run')}
                  </button>
                </>
              )}
            </div>
            {(historyPaymentUrl || historyInvoiceUrl) && (
              <p className="section-copy muted" style={{ marginTop: '1rem' }}>
                {t('channels.history.paymentHint')}
              </p>
            )}
          </div>
        </div>
      )}

      {billingEnabled && billingEmailModal && (
        <div className="modal-overlay" onClick={closeBillingEmailModal}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>{t('channels.billingEmail.title')}</h3>
            <p className="section-copy muted">
              {t('channels.billingEmail.text')}
            </p>
            {billingEmailModal.step === 'email' ? (
              <div className="form-group">
                <label htmlFor="billing-email">Email</label>
                <input
                  id="billing-email"
                  type="email"
                  autoComplete="email"
                  value={billingEmailModal.email}
                  onChange={(e) =>
                    setBillingEmailModal((m) => (m ? { ...m, email: e.target.value } : m))
                  }
                />
              </div>
            ) : (
              <>
                <div className="form-group">
                  <label htmlFor="billing-code">{t('channels.billingEmail.code')}</label>
                  <input
                    id="billing-code"
                    inputMode="numeric"
                    maxLength={4}
                    value={billingEmailModal.code}
                    onChange={(e) =>
                      setBillingEmailModal((m) =>
                        m ? { ...m, code: e.target.value.replace(/\D/g, '').slice(0, 4) } : m
                      )
                    }
                  />
                </div>
                {billingEmailModal.devCode && (
                  <p className="muted section-copy">{t('channels.billingEmail.devCode', { code: billingEmailModal.devCode })}</p>
                )}
              </>
            )}
            {billingEmailModal.error && <p className="error">{billingEmailModal.error}</p>}
            <div className="inline-actions" style={{ marginTop: '1rem' }}>
              <button type="button" className="btn btn-secondary" onClick={closeBillingEmailModal}>
                {t('common.cancel')}
              </button>
              {billingEmailModal.step === 'email' ? (
                <button type="button" className="btn" onClick={() => void submitBillingEmailRequest()}>
                  {t('channels.billingEmail.send')}
                </button>
              ) : (
                <button type="button" className="btn" onClick={() => void submitBillingEmailVerify()}>
                  {t('common.confirm')}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {billingEnabled && tariffModal && (
        <div
          className="modal-overlay"
          onClick={() => {
            setTariffModal(false);
            setTariffStarsWaiting(null);
            setTbankSubscriptionConsentModal(null);
          }}
        >
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>{t('channels.tariff.title')}</h3>
            {tariffStarsWaiting && (
              <p className="muted" style={{ marginBottom: '1rem' }}>
                {t('channels.tariff.waitingStars')}
              </p>
            )}
            {tariffLoading && !tariffStarsWaiting && <p className="muted">{t('channels.tariff.loadingPlans')}</p>}
            {tariffError && <p className="error">{tariffError}</p>}
            {!tariffLoading && plans.length > 0 && (
              <div className="plan-list">
                {plans.map((p) => {
                  const isCurrent = summary?.billing?.plan_code === p.code;
                  const hasBothPrices = p.price_rub && p.price_stars;
                  return (
                    <div key={p.code} className="plan-item">
                      <div className="plan-item-top">
                        <div className="plan-info">
                          <strong>{p.display_name || p.code}</strong>
                          <span className="plan-price">
                            {p.price_rub && p.price_stars
                              ? `${p.price_rub}₽ ${t('common.or')} ${p.price_stars}⭐`
                              : p.price_rub
                                ? `${p.price_rub}₽`
                                : p.price_stars
                                  ? `${p.price_stars}⭐`
                                  : t('common.free')}
                            {p.period && p.period !== 'month' ? ` / ${p.period}` : t('channels.tariff.priceMonthly')}
                          </span>
                        </div>
                        {!isCurrent && (
                          <div className="plan-actions">
                            {hasBothPrices ? (
                              <>
                                <button
                                  type="button"
                                  className="btn btn-small"
                                  onClick={() => handlePlanSelect(p, 'tbank')}
                                  disabled={tariffLoading || tariffStarsWaiting}
                                >
                                  {p.price_rub}₽
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-small btn-secondary"
                                  onClick={() => handlePlanSelect(p, 'stars')}
                                  disabled={tariffLoading || tariffStarsWaiting}
                                >
                                  {p.price_stars}⭐
                                </button>
                              </>
                            ) : p.price_rub ? (
                              <button
                                type="button"
                                className="btn btn-small"
                                onClick={() => handlePlanSelect(p, 'tbank')}
                                disabled={tariffLoading || tariffStarsWaiting}
                              >
                                {t('common.choose')}
                              </button>
                            ) : p.price_stars ? (
                              <button
                                type="button"
                                className="btn btn-small"
                                onClick={() => handlePlanSelect(p, 'stars')}
                                disabled={tariffLoading || tariffStarsWaiting}
                              >
                                {t('common.choose')}
                              </button>
                            ) : null}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="inline-actions" style={{ marginTop: '1rem' }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  setTariffModal(false);
                  setTariffStarsWaiting(null);
                  setTbankSubscriptionConsentModal(null);
                }}
              >
                {t('common.close')}
              </button>
            </div>
          </div>
        </div>
      )}

      {billingEnabled && tbankSubscriptionConsentModal && (
        <div
          className="modal-overlay"
          onClick={() => {
            if (!tariffLoading) setTbankSubscriptionConsentModal(null);
          }}
        >
          <div className="modal-content tbank-consent-modal" onClick={(e) => e.stopPropagation()}>
            <h3>{t('channels.tbank.title')}</h3>
            <p className="muted section-copy" style={{ marginBottom: '1rem' }}>
              {t('channels.tbank.text')}
            </p>
            <div className="tbank-consent-plan-box" role="status">
              <strong>{tbankSubscriptionConsentModal.plan.display_name || tbankSubscriptionConsentModal.plan.code}</strong>
              {' — '}
              <strong>{tbankSubscriptionConsentModal.plan.price_rub} ₽</strong>
              <br />
              {t('channels.tbank.period', {
                period: formatSubscriptionPeriod(tbankSubscriptionConsentModal.plan.period, locale),
              })}
            </div>
            <label className="tbank-consent-checkbox-row" htmlFor="tbank-subscription-recurrent-consent">
              <input
                id="tbank-subscription-recurrent-consent"
                type="checkbox"
                checked={tbankSubscriptionConsentChecked}
                onChange={(e) => setTbankSubscriptionConsentChecked(e.target.checked)}
              />
              <span>
                {t('channels.tbank.consent')}
              </span>
            </label>
            <p className="billing-support-hint" style={{ marginTop: '1.25rem' }}>
              {t('channels.tbank.support', { email: BILLING_SUPPORT_EMAIL })}
            </p>
            <div className="inline-actions tbank-consent-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={tariffLoading}
                onClick={() => setTbankSubscriptionConsentModal(null)}
              >
                {t('common.back')}
              </button>
              <button
                type="button"
                className="btn"
                disabled={!tbankSubscriptionConsentChecked || tariffLoading}
                onClick={() => {
                  const plan = tbankSubscriptionConsentModal.plan;
                  setTbankSubscriptionConsentModal(null);
                  void executeSubscriptionCreate(plan, 'tbank');
                }}
              >
                {t('channels.tbank.pay')}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
