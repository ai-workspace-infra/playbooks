import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import tempo from 'https://jslib.k6.io/http-instrumentation-tempo/1.0.1/index.js';

// Propagate a unique W3C trace context through the environment ingress and
// downstream services. The application must have OpenTelemetry tracing
// enabled for the request to become a stored VictoriaTraces span.
tempo.instrumentHTTP({ propagator: 'w3c' });

const TARGET_ENV = __ENV.TARGET_ENV || 'uat';
const TEST_PROFILE = __ENV.K6_TEST_PROFILE || 'smoke';
const TEST_ID = __ENV.K6_TEST_ID || 'local-k6-run';

if (!['sit', 'uat', 'prod'].includes(TARGET_ENV)) {
  throw new Error(`Unsupported TARGET_ENV: ${TARGET_ENV}`);
}
if (!['smoke', 'capacity'].includes(TEST_PROFILE)) {
  throw new Error(`Unsupported K6_TEST_PROFILE: ${TEST_PROFILE}`);
}

// Custom metrics for capacity, availability, and API latency tracking.
export const errorRate = new Rate('k6_custom_error_rate');
export const apiLatency = new Trend('k6_custom_api_latency');
export const successfulRequests = new Counter('k6_custom_success_requests');

const profileStages = {
  smoke: [
    { duration: '15s', target: 1 },
    { duration: '30s', target: 5 },
    { duration: '15s', target: 0 },
  ],
  capacity: [
    { duration: '30s', target: 20 },
    { duration: '1m', target: 100 },
    { duration: '2m', target: 300 },
    { duration: '1m', target: 500 },
    { duration: '30s', target: 0 },
  ],
};

export const options = {
  scenarios: {
    capacity_stress_test: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: profileStages[TEST_PROFILE] || profileStages.smoke,
      gracefulRampDown: '10s',
    },
  },
  tags: {
    environment: TARGET_ENV,
    test_profile: TEST_PROFILE,
    testid: TEST_ID,
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],        // Error rate must be under 1%
    http_req_duration: ['p(95)<500'],      // 95% of requests must finish within 500ms
    k6_custom_error_rate: ['rate<0.05'],
    k6_custom_api_latency: ['p(95)<500'],
  },
};

// Environment endpoints mapping
const BASE_URLS = {
  sit: 'https://console-sit.onwalk.net',
  uat: 'https://console-uat.onwalk.net',
  prod: 'https://console.xworkmate.com',
};

const baseUrl = (__ENV.TARGET_BASE_URL || BASE_URLS[TARGET_ENV] || BASE_URLS.uat).replace(/\/+$/, '');
const apiToken = __ENV.K6_API_TOKEN || '';

function requestParams(endpoint) {
  const headers = {
    'Content-Type': 'application/json',
    'User-Agent': 'k6-load-testing-agent/1.0',
    'X-Environment': TARGET_ENV,
    'X-K6-Test-ID': TEST_ID,
  };
  if (apiToken) {
    headers.Authorization = `Bearer ${apiToken}`;
  }
  return {
    headers,
    tags: {
      environment: TARGET_ENV,
      endpoint,
      test_profile: TEST_PROFILE,
      testid: TEST_ID,
    },
  };
}

export default function () {
  // 1. Health & Overview Endpoint
  const healthRes = http.get(`${baseUrl}/api/health`, requestParams('health'));
  const healthSuccess = check(healthRes, {
    'health status is 200': (r) => r.status === 200,
  });
  errorRate.add(!healthSuccess, { endpoint: 'health' });
  if (healthSuccess) successfulRequests.add(1, { endpoint: 'health' });

  sleep(0.5);

  // 2. Billing & Plans API Endpoint. This public catalog endpoint exercises
  // the API-to-PostgreSQL read path in sit/uat/prod.
  const plansRes = http.get(`${baseUrl}/api/billing/plans`, requestParams('billing_plans'));
  const plansSuccess = check(plansRes, {
    'plans status is 200': (r) => r.status === 200,
    'plans duration < 500ms': (r) => r.timings.duration < 500,
  });
  errorRate.add(!plansSuccess, { endpoint: 'billing_plans' });
  apiLatency.add(plansRes.timings.duration, { endpoint: 'billing_plans' });
  if (plansSuccess) successfulRequests.add(1, { endpoint: 'billing_plans' });

  sleep(1);
}
