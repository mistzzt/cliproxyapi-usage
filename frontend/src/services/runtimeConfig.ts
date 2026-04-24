interface UsageRuntimeConfig {
  basePath: string;
  apiBase: string;
}

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

export const runtimeConfig: UsageRuntimeConfig = {
  basePath,
  apiBase,
};

export function apiPath(path: string): string {
  return `${runtimeConfig.apiBase}${path}`;
}
