import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// Custom metrics for capacity and availability tracking
export const errorRate = new Rate('k6_custom_error_rate');
export const apiLatency = new Trend('k6_custom_api_latency');
export const successfulRequests = new Counter('k6_custom_success_requests');

export const options = {
  scenarios: {
    // Stage 1: Ramping VUs to discover maximum capacity ceiling
    capacity_stress_test: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '30s', target: 20 },   // Warm-up to 20 VUs
        { duration: '1m',  target: 100 },  // Ramp-up to 100 VUs
        { duration: '2m',  target: 300 },  // Stress test at 300 VUs
        { duration: '1m',  target: 500 },  // Peak capacity test at 500 VUs
        { duration: '30s', target: 0 },    // Cool-down
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],        // Error rate must be under 1%
    http_req_duration: ['p(95)<500'],      // 95% of requests must finish within 500ms
    k6_custom_error_rate: ['rate<0.05'],
  },
};

// Environment endpoints mapping
const TARGET_ENV = __ENV.TARGET_ENV || 'uat';
const BASE_URLS = {
  sit: 'https://console-sit.onwalk.net',
  uat: 'https://console-uat.onwalk.net',
  prod: 'https://console.xworkmate.com',
};

const baseUrl = BASE_URLS[TARGET_ENV] || BASE_URLS.uat;

export default function () {
  const params = {
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': 'k6-load-testing-agent/1.0',
      'X-Environment': TARGET_ENV,
      // Inject trace context headers for Trace-to-Logs & Metrics correlation
      'traceparent': `00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`,
    },
    tags: {
      environment: TARGET_ENV,
      test_type: 'capacity_stress_test',
    },
  };

  // 1. Health & Overview Endpoint
  const healthRes = http.get(`${baseUrl}/api/health`, params);
  const healthSuccess = check(healthRes, {
    'health status is 200': (r) => r.status === 200,
  });
  errorRate.add(!healthSuccess);
  if (healthSuccess) successfulRequests.add(1);

  sleep(0.5);

  // 2. Billing & Plans API Endpoint
  const plansRes = http.get(`${baseUrl}/api/billing/plans`, params);
  const plansSuccess = check(plansRes, {
    'plans status is 200': (r) => r.status === 200,
    'plans duration < 500ms': (r) => r.timings.duration < 500,
  });
  errorRate.add(!plansSuccess);
  apiLatency.add(plansRes.timings.duration);
  if (plansSuccess) successfulRequests.add(1);

  sleep(1);
}
