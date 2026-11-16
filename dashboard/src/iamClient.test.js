import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { createIamClient } from './iamClient.js';

// Build deterministic fetch responses without contacting the client service.
function mockFetch(...responses) {
  const calls = [];
  const fetchImpl = async (...args) => {
    calls.push(args);
    return responses.shift();
  };
  return { fetchImpl, calls };
}

describe('createIamClient', () => {
  it('sends requests without IAM headers when IAM is disabled', async () => {
    const { fetchImpl, calls } = mockFetch({ status: 200 });
    const iamFetch = createIamClient({ enabled: false, baseUrl: 'http://client', getSession: () => null, refresh: async () => null, onExpired: () => {}, fetchImpl });

    await iamFetch('/agents', { headers: { Accept: 'application/json' } });

    assert.deepEqual(calls, [['http://client/agents', { headers: { Accept: 'application/json' } }]]);
  });

  it('adds the access token when IAM is enabled', async () => {
    const { fetchImpl, calls } = mockFetch({ status: 200 });
    const iamFetch = createIamClient({ enabled: true, baseUrl: 'http://client', getSession: () => ({ access_token: 'access-1' }), refresh: async () => null, onExpired: () => {}, fetchImpl });

    await iamFetch('/chat', { method: 'POST' });

    assert.deepEqual(calls, [['http://client/chat', { method: 'POST', headers: { Authorization: 'Bearer access-1' } }]]);
  });

  it('refreshes once after 401 and retries with the new token', async () => {
    const { fetchImpl, calls } = mockFetch({ status: 401 }, { status: 200 });
    let refreshCalls = 0;
    const refresh = async () => { refreshCalls += 1; return { access_token: 'access-2' }; };
    const iamFetch = createIamClient({ enabled: true, baseUrl: 'http://client', getSession: () => ({ access_token: 'access-1' }), refresh, onExpired: () => {}, fetchImpl });

    const response = await iamFetch('/chat', { headers: { 'Content-Type': 'application/json' } });

    assert.equal(response.status, 200);
    assert.equal(refreshCalls, 1);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[1][1].headers, { 'Content-Type': 'application/json', Authorization: 'Bearer access-2' });
  });

  it('notifies expiry when refresh fails and returns the original response', async () => {
    const unauthorized = { status: 401 };
    const { fetchImpl, calls } = mockFetch(unauthorized);
    let expiredCalls = 0;
    const onExpired = () => { expiredCalls += 1; };
    const iamFetch = createIamClient({ enabled: true, baseUrl: 'http://client', getSession: () => ({ access_token: 'access-1' }), refresh: async () => null, onExpired, fetchImpl });

    const response = await iamFetch('/chat');

    assert.equal(response, unauthorized);
    assert.equal(calls.length, 1);
    assert.equal(expiredCalls, 1);
  });
});
