import { catalogs, DEFAULT_LOCALE } from '../i18n/catalogs'
import { CORE_APP_BASE, isSelfhostMode } from './runtime'
import { SELFHOST_WORKSPACE_ID, buildSelfhostWorkspace } from './workspace'

const API_BASE = '';
export { isSelfhostMode } from './runtime'
export { SELFHOST_WORKSPACE_ID } from './workspace'

function safeJsonBody(options = {}) {
  if (!options.body || options.body instanceof FormData) return null;
  try {
    return JSON.parse(options.body);
  } catch {
    return null;
  }
}

function mapSelfhostPath(path) {
  if (!isSelfhostMode()) return path;
  if (path === '/me') return `${CORE_APP_BASE}/session`;
  if (path === '/auth/providers') return `${CORE_APP_BASE}/auth/providers`;
  if (path.startsWith('/auth/magic-link/')) return `${CORE_APP_BASE}${path}`;
  if (path.startsWith('/auth/telegram-web/')) return `${CORE_APP_BASE}${path}`;
  if (path === '/me/billing-email/request') return `${CORE_APP_BASE}/billing-email/request`;
  if (path === '/me/billing-email/verify') return `${CORE_APP_BASE}/billing-email/verify`;
  if (path.startsWith('/api/news/')) return `${CORE_APP_BASE}${path.replace(/^\/api/, '')}`;
  if (path.startsWith('/api/news')) return `${CORE_APP_BASE}${path.replace(/^\/api/, '')}`;
  if (path.startsWith(CORE_APP_BASE)) return path;

  const workspaceMatch = path.match(/^\/workspaces\/([^/]+)(\/.*)?$/);
  if (!workspaceMatch) return path;
  const rest = workspaceMatch[2] || '';

  const mappings = [
    [/^\/dashboard\/summary(\?.*)?$/, '/dashboard/summary$1'],
    [/^\/dashboard\/jobs(\?.*)?$/, '/dashboard/jobs$1'],
    [/^\/settings(\?.*)?$/, '/settings$1'],
    [/^\/installation-secrets(\?.*)?$/, '/installation-secrets$1'],
    [/^\/installation-secrets\/([^/?]+)(\?.*)?$/, '/installation-secrets/$1$2'],
    [/^\/billing\/plans(\?.*)?$/, '/billing/plans$1'],
    [/^\/billing\/(.+)$/, '/billing/$1'],
    [/^\/posts\/platform-previews(\?.*)?$/, '/platform-previews$1'],
    [/^\/posts\/([^/?]+)\/publication-targets(\?.*)?$/, '/content-items/$1/publication-targets$2'],
    [/^\/posts\/([^/?]+)\/generate(\?.*)?$/, '/content-items/generate$2'],
    [/^\/posts\/([^/?]+)\/adapt(\?.*)?$/, '/content-items/$1/adapt$2'],
    [/^\/posts\/([^/?]+)\/translate(\?.*)?$/, '/content-items/$1/translate$2'],
    [/^\/posts\/([^/?]+)\/ai-chat(\?.*)?$/, '/content-items/$1/ai-chat$2'],
    [/^\/posts(\?.*)?$/, '/content-items$1'],
    [/^\/posts\/([^/?]+)(\?.*)?$/, '/content-items/$1$2'],
    [/^\/channel-registry(\?.*)?$/, '/channels$1'],
    [/^\/channel-registry\/validate(\?.*)?$/, '/channel-registry/validate$1'],
    [/^\/channel-registry\/max\/request-verification(\?.*)?$/, '/channel-registry/max/request-verification$1'],
    [/^\/channel-registry\/max\/verify(\?.*)?$/, '/channel-registry/max/verify$1'],
    [/^\/channel-registry\/([^/?]+)(\?.*)?$/, '/channels/$1$2'],
    [/^\/credentials\/linkedin\/authorize-url(\?.*)?$/, '/credentials/linkedin/authorize-url$1'],
    [/^\/credentials\/linkedin\/organizations(\?.*)?$/, '/credentials/linkedin/organizations$1'],
    [/^\/credentials\/linkedin\/access-token(\?.*)?$/, '/credentials/linkedin/access-token$1'],
    [/^\/credentials\/vk\/community-token(\?.*)?$/, '/credentials/vk/community-token$1'],
    [/^\/connections\/create(\?.*)?$/, '/connections/create$1'],
    [/^\/jobs\/start(\?.*)?$/, '/jobs/start$1'],
    [/^\/jobs(\?.*)?$/, '/jobs$1'],
    [/^\/jobs\/([^/?]+)(\?.*)?$/, '/jobs/$1$2'],
    [/^\/jobs\/([^/?]+)\/([^/?]+)(\?.*)?$/, '/jobs/$1/$2$3'],
    [/^\/channels(\?.*)?$/, '/bridges$1'],
    [/^\/channels\/([^/?]+)(\?.*)?$/, '/bridges/$1$2'],
    [/^\/bridges(\?.*)?$/, '/bridges$1'],
    [/^\/bridges\/([^/?]+)(\?.*)?$/, '/bridges/$1$2'],
    [/^\/core-publication-targets(\?.*)?$/, '/publication-targets$1'],
    [/^\/core-publication-targets\/([^/?]+)\/dispatch(\?.*)?$/, '/publication-targets/$1/dispatch$2'],
    [/^\/media\/upload(\?.*)?$/, '/media/upload$1'],
    [/^\/media\/generation-jobs(\?.*)?$/, '/media/generation-jobs$1'],
    [/^\/media\/generation-jobs\/([^/?]+)(\?.*)?$/, '/media/generation-jobs/$1$2'],
    [/^\/agent\/review-queue(\?.*)?$/, '/review-queue$1'],
    [/^\/agent\/review-queue\/([^/?]+)(\?.*)?$/, '/review-queue/$1$2'],
    [/^\/agent\/review-queue\/([^/?]+)\/resolve(\?.*)?$/, '/review-queue/$1/resolve$2'],
    [/^\/agent\/(.+)$/, '/agent/$1'],
  ];
  for (const [pattern, replacement] of mappings) {
    if (pattern.test(rest)) return `${CORE_APP_BASE}${rest.replace(pattern, replacement)}`;
  }
  return path;
}

function normalizeSelfhostOptions(originalPath, mappedPath, options) {
  if (!isSelfhostMode()) return options;
  if (!options.body || options.body instanceof FormData) return options;
  if (mappedPath === `${CORE_APP_BASE}/channels` && (options.method || 'GET').toUpperCase() === 'POST') {
    const body = safeJsonBody(options);
    if (!body) return options;
    return {
      ...options,
      body: JSON.stringify({
        platform: body.platform,
        title: body.title || body.display || body.platform_channel_id || body.external_id || body.platform,
        platform_channel_id: body.platform_channel_id || body.external_id,
        can_read: body.can_read,
        can_write: body.can_write,
        credentials_ref: body.credentials_ref,
        config: body.config,
        capabilities: body.capabilities,
        kind: body.kind,
      }),
    };
  }
  return options;
}

function normalizeSelfhostResponse(originalPath, data) {
  if (!isSelfhostMode()) return data;
  if (originalPath === '/me') {
    if (data?.authenticated) {
      return {
        ...data.user,
        authenticated: true,
        is_platform_admin: true,
        workspaces: [buildSelfhostWorkspace(data.tenant)],
        current_workspace_id: SELFHOST_WORKSPACE_ID,
        app_mode: data.app_mode,
        tenant: data.tenant,
      };
    }
    if (data?.app_mode === 'selfhost') {
      return {
        __selfhost_auth_status: true,
        authenticated: false,
        bootstrapped: Boolean(data.bootstrapped),
        setup_required: Boolean(data.setup_required),
        app_mode: data.app_mode,
        tenant: data.tenant || null,
        workspaces: data.tenant ? [buildSelfhostWorkspace(data.tenant)] : [],
        current_workspace_id: SELFHOST_WORKSPACE_ID,
      };
    }
  }
  return data;
}

// Error text from FastAPI JSON (detail) or AppError (message).
function browserLocale() {
  if (typeof window === 'undefined') return DEFAULT_LOCALE
  const locale = window.localStorage.getItem('postbridge.locale') || document.documentElement.lang || DEFAULT_LOCALE
  return String(locale).toLowerCase().startsWith('ru') ? 'ru' : DEFAULT_LOCALE
}

function localizedClientText(key) {
  const locale = browserLocale()
  return catalogs[locale]?.[key] ?? catalogs[DEFAULT_LOCALE]?.[key] ?? key
}

function interpolate(template, params) {
  if (!params || typeof template !== 'string') return template
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key) => {
    const value = params[key]
    return value == null ? match : String(value)
  })
}

function localizedApiErrorMessage(code, fallback, details = {}) {
  if (!code || typeof code !== 'string') return fallback
  const locale = browserLocale()
  const key = `api.error.${code}`
  const template = catalogs[locale]?.[key] ?? catalogs[DEFAULT_LOCALE]?.[key]
  return template ? interpolate(template, details) : fallback
}

function messageFromValidationDetails(data) {
  const errors = data?.details?.errors;
  if (!Array.isArray(errors) || errors.length === 0) return '';
  const first = errors[0];
  if (!first || typeof first !== 'object') return '';
  const rawLoc = Array.isArray(first.loc) ? first.loc.filter(Boolean).join('.') : '';
  const msg = typeof first.msg === 'string' ? first.msg.trim() : '';
  if (!msg) return '';
  return rawLoc ? `${rawLoc}: ${msg}` : msg;
}

function errorMessageFromResponseBody(data, status) {
  if (!data || typeof data !== 'object') return `HTTP ${status}`;
  const validationMessage = messageFromValidationDetails(data);
  if (validationMessage) return validationMessage;
  if (typeof data.message === 'string' && data.message) return data.message;
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail) && data.detail.length) {
    const first = data.detail[0];
    if (typeof first === 'string') return first;
    if (first && typeof first.msg === 'string') return first.msg;
  }
  return `HTTP ${status}`;
}

function normalizeApiErrorMessage(message, status, details = {}) {
  const raw = typeof message === 'string' ? message.trim() : '';
  const genericMessages = new Set([
    '',
    'Unhandled API error',
    'unexpected internal error',
    'core request failed',
    'HTTP 500',
    'HTTP 502',
    'HTTP 503',
  ]);
  if (!genericMessages.has(raw)) return raw || `HTTP ${status}`;

  const exception = typeof details.exception === 'string' ? details.exception.trim() : '';
  if (exception) return exception;

  if (status >= 500) {
    return localizedClientText('api.error.genericTemporary')
  }
  return raw || `HTTP ${status}`
}

export function getToken() {
  return localStorage.getItem('postbridge_token');
}

export async function api(path, options = {}) {
  const token = getToken();
  const headers = {
    'X-Requested-With': 'XMLHttpRequest',
    ...options.headers,
  };
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const mappedPath = mapSelfhostPath(path);
  const mappedOptions = normalizeSelfhostOptions(path, mappedPath, options);
  const res = await fetch(`${API_BASE}${mappedPath}`, { ...mappedOptions, headers });
  const data = res.ok ? await res.json().catch(() => null) : await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg =
      data?.details?.required ||
      errorMessageFromResponseBody(data, res.status);
    msg = normalizeApiErrorMessage(msg, res.status, data?.details || {});
    msg = localizedApiErrorMessage(data?.code, msg, data?.details || {});
    const err = new Error(msg);
    err.status = res.status;
    err.code = data?.code;
    err.details = data?.details || {};
    throw err;
  }
  return normalizeSelfhostResponse(path, data);
}

export function setToken(token) {
  localStorage.setItem('postbridge_token', token);
}

export function clearToken() {
  localStorage.removeItem('postbridge_token');
}

export function isAuthenticated() {
  return isSelfhostMode() || !!getToken();
}
