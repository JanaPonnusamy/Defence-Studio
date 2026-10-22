import test from 'node:test';
import assert from 'node:assert/strict';
import { createApiClient, ApiError } from '../js/api.js';

test('401 clears only the rejected current session and never retries without credentials', async () => {
  const original = globalThis.fetch;
  let token = 'old-session', invalidations = 0, calls = 0;
  globalThis.fetch = async () => { calls++; return new Response(JSON.stringify({detail:'Invalid bearer token'}), {status:401}); };
  try {
    const api = createApiClient(() => token, () => { invalidations++; token = ''; });
    await assert.rejects(api.listSources('existing-job'), e => e instanceof ApiError && e.status === 401);
    await assert.rejects(api.listClaims('existing-job'), /Sign in to continue/);
    assert.equal(invalidations, 1);
    assert.equal(calls, 1);
  } finally { globalThis.fetch = original; }
});

test('late 401 from old token preserves fresh login; 403 does not sign out', async () => {
  const original = globalThis.fetch;
  let token = 'old', invalidations = 0, complete;
  globalThis.fetch = () => new Promise(resolve => { complete = resolve; });
  try {
    const api = createApiClient(() => token, () => { invalidations++; });
    const pending = api.getJob('existing-job');
    token = 'new';
    complete(new Response(JSON.stringify({detail:'Invalid bearer token'}), {status:401}));
    await assert.rejects(pending, e => e.status === 401);
    assert.equal(invalidations, 0);
    globalThis.fetch = async () => new Response(JSON.stringify({detail:'Forbidden'}), {status:403});
    await assert.rejects(api.getJob('existing-job'), e => e.status === 403);
    assert.equal(invalidations, 0);
  } finally { globalThis.fetch = original; }
});

test('unavailable dev login does not invalidate a bearer session', async () => {
  const original = globalThis.fetch;
  let invalidations = 0;
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.headers.Authorization, undefined);
    return new Response(JSON.stringify({detail:'Not found'}), {status:401});
  };
  try {
    const api = createApiClient(() => 'current', () => { invalidations++; });
    await assert.rejects(api.devSession(), e => e.status === 401);
    assert.equal(invalidations, 0);
  } finally { globalThis.fetch = original; }
});
