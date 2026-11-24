import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let server;
let Stage2;
let Stage3;
let ChatInterface;
before(async () => {
  server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  Stage2 = (await server.ssrLoadModule('/src/components/Stage2.jsx')).default;
  Stage3 = (await server.ssrLoadModule('/src/components/Stage3.jsx')).default;
  ChatInterface = (await server.ssrLoadModule('/src/components/ChatInterface.jsx')).default;
});
after(async () => { await server?.close(); });

const rankings = [{ model: 'provider/a', ranking: 'Raw critique', parsed_ranking: { ranking: ['Response A'] } }];
const aggregateRankings = [{ model: 'provider/a', vote_count: 0, rank: 1, borda_score: 1 }];
const renderStage2 = (state, overrides = {}) => renderToStaticMarkup(createElement(Stage2, {
  rankings, aggregateRankings, labelToModel: { 'Response A': 'provider/a' }, reviewStatus: { state }, ...overrides,
}));

test('empty review remains visible as an explicit failure', () => {
  const html = renderStage2('failed', { rankings: [] });
  assert.match(html, /No usable peer review/);
  assert.doesNotMatch(html, /Aggregate Rankings/);
});

test('invalid reviews retain raw text but not misleading rankings', () => {
  const html = renderStage2('failed');
  assert.match(html, /Raw critique/);
  assert.doesNotMatch(html, /Aggregate Rankings|Extracted Ranking/);
});

test('single-model skip is distinct from failure', () => {
  const html = renderStage2('skipped', { rankings: [] });
  assert.match(html, /skipped because only one model responded/);
  assert.doesNotMatch(html, /No usable peer review|Aggregate Rankings/);
});

test('partial review warns and zero-vote candidates are unranked', () => {
  const html = renderStage2('partial');
  assert.match(html, /Peer review is incomplete/);
  assert.match(html, /Unranked/);
  assert.doesNotMatch(html, /#1|Borda: 1\.000/);
});

test('complete review retains normal native aggregate display', () => {
  const html = renderStage2('completed', { aggregateRankings: [{ model: 'provider/a', vote_count: 2, rank: 1, borda_score: 0.75 }] });
  assert.match(html, /Aggregate Rankings/);
  assert.match(html, /Borda: 0\.750/);
  assert.doesNotMatch(html, /Peer review is incomplete/);
});

test('Chairman verdict is explicitly limited when peer review failed', () => {
  const html = renderToStaticMarkup(createElement(Stage3, {
    finalResponse: { model: 'provider/chairman', response: 'Synthesis' },
    verdict: { verdict: 'approved' }, reviewStatus: { state: 'failed' },
  }));
  assert.match(html, /not fully peer-validated/);
  assert.match(html, /Synthesis/);
});


const renderCouncil = (count, config) => renderToStaticMarkup(createElement(ChatInterface, {
  conversation: { messages: [{
    role: 'assistant',
    stage1: Array.from({ length: count }, (_, i) => ({ model: 'provider/model-' + i, response: 'Individual answer' })),
    stage3: { model: 'provider/chairman', response: 'Usable synthesis' },
    metadata: { config, status: count === 2 ? 'partial' : undefined },
  }] },
  isLoading: false,
}));

test('reduced council shows the actual response shortfall and keeps synthesis', () => {
  const html = renderCouncil(2, { council_size: 3, responses_received: 2 });
  assert.match(html, /Partial Council result:/);
  assert.match(html, /2 of 3 configured models returned an individual response/);
  assert.match(html, /Usable synthesis/);
});

test('full council has no reduced-council warning', () => {
  assert.doesNotMatch(renderCouncil(3, { council_size: 3, responses_received: 3 }), /Partial Council result:/);
});

test('legacy council without a configured size has no invented shortfall', () => {
  assert.doesNotMatch(renderCouncil(2, undefined), /Partial Council result:/);
});
