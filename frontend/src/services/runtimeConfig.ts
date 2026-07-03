interface UsageRuntimeConfig {
  basePath: string;
  apiBase: string;
  title: string;
}

const DEFAULT_TITLE = 'CLIProxyAPI Usage Dashboard';

declare global {
  interface Window {
    __CLIPROXY_USAGE_CONFIG__?: Partial<UsageRuntimeConfig>;
  }
}

function normalizeBasePath(value: string | undefined): string {
  if (value === undefined || value === '' || value === '/') {
    return '/';
  }
  return value.endsWith('/') ? value.slice(0, -1) : value;
}

const rawConfig = window.__CLIPROXY_USAGE_CONFIG__ ?? {};
const basePath = normalizeBasePath(rawConfig.basePath);
const apiBase = rawConfig.apiBase ?? (basePath === '/' ? '/api' : `${basePath}/api`);
const title = rawConfig.title ?? DEFAULT_TITLE;

export const runtimeConfig: UsageRuntimeConfig = {
  basePath,
  apiBase,
  title,
};

export function apiPath(path: string): string {
  return `${runtimeConfig.apiBase}${path}`;
}
