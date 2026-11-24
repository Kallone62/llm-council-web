import assert from 'node:assert/strict';
import { test } from 'node:test';
import { api } from '../src/api.js';

const metadata = { status: 'partial', peer_review: { state: 'failed' } };
const stage1 = { type: 'stage1_complete', data: [{ model: 'a', response: 'Türkçe ✓' }] };
const stage2 = { type: 'stage2_complete', data: [], metadata };
const stage3 = { type: 'stage3_complete', data: { model: 'c', response: 'Final' }, metadata };
const complete = { type: 'complete', metadata };
const title = { type: 'title_complete', data: { title: 'Council title' } };
const progress = { type: 'engine_progress', data: { stage: 'stage2', state: 'failed' } };
const frame = event => `data: ${JSON.stringify(event)}\n\n`;
const frames = (...events) => events.map(frame).join('');
const valid = frames(stage1, stage2, stage3, complete);
const encode = text => new TextEncoder().encode(text);

async function drive(t, text, { bytewise = false, failRead = false, contentType = 'text/event-stream', onEvent, rawBytes, noBody = false } = {}) {
  const events = [];
  const bytes = rawBytes || encode(text);
  let sent = false;
  const body = noBody ? null : new ReadableStream({
    pull(controller) {
      if (!sent) {
        sent = true;
        if (bytewise) for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
        else controller.enqueue(bytes);
      } else if (failRead) controller.error(new Error('Connection lost'));
      else controller.close();
    },
  });
  const response = new Response(body, { headers: { 'Content-Type': contentType } });
  t.mock.method(globalThis, 'fetch', async () => response);
  const promise = api.sendMessageStream('test-id', 'Question', (type, event) => {
    assert.equal(type, event.type);
    events.push(event);
    onEvent?.(type, event);
  });
  return { promise, events, response };
}

test('valid stream with split UTF-8, CRLF, keepalive and optional title', async t => {
  const text = (': keepalive\n\n' + frames(progress, stage1, stage2, stage3, complete, title)).replaceAll('\n', '\r\n');
  const { promise, events } = await drive(t, text, { bytewise: true });
  await promise;
  assert.deepEqual(events, [progress, stage1, stage2, stage3, complete, title]);
});

test('legacy stage-start events remain compatible between results', async t => {
  const starts = ['stage1_start', 'stage2_start', 'stage3_start'].map(type => ({ type }));
  const expected = [starts[0], stage1, starts[1], stage2, starts[2], stage3, complete];
  const { promise, events } = await drive(t, frames(...expected));
  await promise;
  assert.deepEqual(events, expected);
});

test('valid failed Chairman result is a completed transport, not a successful Council', async t => {
  const failedMetadata = { status: 'failed' };
  const failed = { ...stage3, data: { model: 'c', response: 'No answer', error_status: 'empty_synthesis' }, metadata: failedMetadata };
  const terminal = { ...complete, metadata: failedMetadata };
  const { promise, events } = await drive(t, frames(stage1, { ...stage2, metadata: failedMetadata }, failed, terminal));
  await promise;
  assert.deepEqual(events.slice(-2), [failed, terminal]);
});

for (const [name, text] of [
  ['empty stream', ''],
  ['missing terminal', frames(stage1, stage2, stage3)],
  ['missing stage 2', frames(stage1, stage3, complete)],
  ['missing stage 3', frames(stage1, stage2, complete)],
  ['out of order results', frames(stage2, stage1, stage3, complete)],
  ['duplicate stage', frames(stage1, stage1, stage2, stage3, complete)],
  ['duplicate terminal', valid + frame(complete)],
  ['malformed JSON', frames(stage1, stage2) + 'data: {broken\n\n' + frames(stage3, complete)],
  ['malformed event object', frames(stage1, stage2) + 'data: null\n\n' + frames(stage3, complete)],
  ['unknown event', frames(stage1, stage2, { type: 'unexpected' }, stage3, complete)],
  ['empty Stage 3 object', frames(stage1, stage2, { ...stage3, data: {} }, complete)],
  ['missing stage metadata', frames(stage1, { type: 'stage2_complete', data: [] }, stage3, complete)],
  ['invalid progress', frames({ type: 'engine_progress', data: {} }, stage1, stage2, stage3, complete)],
  ['wrong stage data shape', frames({ ...stage1, data: {} }, stage2, stage3, complete)],
  ['missing terminal metadata', frames(stage1, stage2, stage3, { type: 'complete' })],
  ['wrong terminal metadata shape', frames(stage1, stage2, stage3, { ...complete, metadata: [] })],
  ['truncated terminal frame', valid.slice(0, -1)],
  ['trailing broken frame', valid + 'data: {'],
  ['result after terminal', valid + frame(stage3)],
  ['title before terminal', frames(stage1, stage2, stage3, title, complete)],
  ['duplicate title', valid + frames(title, title)],
]) {
  test(`reject ${name} without publishing a final result`, async t => {
    const { promise, events } = await drive(t, text);
    await assert.rejects(promise, /Council stream/);
    assert.equal(events.some(e => e.type === 'complete' || e.type === 'stage3_complete'), false);
  });
}

test('explicit backend error is delivered and rejects', async t => {
  const error = { type: 'error', message: 'Memory unavailable' };
  const { promise, events } = await drive(t, frames(progress, error));
  await assert.rejects(promise, /Memory unavailable/);
  assert.deepEqual(events, [progress, error]);
});

test('backend error after terminal is rejected without publishing success', async t => {
  const { promise, events } = await drive(t, valid + frame({ type: 'error', message: 'Late error' }));
  await assert.rejects(promise, /Council stream/);
  assert.equal(events.some(e => e.type === 'complete' || e.type === 'stage3_complete'), false);
});

test('network error after terminal does not publish final result', async t => {
  const { promise, events } = await drive(t, valid, { failRead: true });
  await assert.rejects(promise, /Connection lost/);
  assert.equal(events.some(e => e.type === 'complete' || e.type === 'stage3_complete'), false);
});

test('invalid UTF-8 is rejected instead of silently replacing bytes', async t => {
  const bytes = Uint8Array.from([...encode(valid), 0xff]);
  const { promise, events } = await drive(t, '', { rawBytes: bytes });
  await assert.rejects(promise);
  assert.equal(events.some(e => e.type === 'complete' || e.type === 'stage3_complete'), false);
});

test('missing body rejects clearly', async t => {
  const { promise } = await drive(t, '', { noBody: true });
  await assert.rejects(promise, /Council stream/);
});

test('wrong content type rejects', async t => {
  const { promise } = await drive(t, valid, { contentType: 'application/json' });
  await assert.rejects(promise, /Council stream/);
});

test('callback exceptions are not swallowed and reader lock is released', async t => {
  const { promise, events, response } = await drive(t, valid, { onEvent(type) {
    if (type === 'stage2_complete') throw new Error('Callback failed');
  } });
  await assert.rejects(promise, /Callback failed/);
  assert.equal(events.some(e => e.type === 'complete' || e.type === 'stage3_complete'), false);
  assert.equal(response.body.locked, false);
});
