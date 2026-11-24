/**
 * API client for the LLM Council backend.
 */

const API_BASE = 'http://localhost:8001';

async function readJson(response, fallbackMessage) {
  if (!response.ok) {
    let detail = fallbackMessage;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // Keep fallback message.
    }
    throw new Error(detail);
  }
  return response.json();
}

export const api = {
  async getSettings() {
    const response = await fetch(`${API_BASE}/api/settings`);
    return readJson(response, 'Failed to load settings');
  },

  async updateSettings(settings) {
    const response = await fetch(`${API_BASE}/api/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    return readJson(response, 'Failed to save settings');
  },

  async updateOpenRouterKey(apiKey) {
    const response = await fetch(`${API_BASE}/api/settings/openrouter-key`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey }),
    });
    return readJson(response, 'Failed to update OpenRouter key');
  },

  /** List all conversations. */
  async listConversations() {
    const response = await fetch(`${API_BASE}/api/conversations`);
    return readJson(response, 'Failed to list conversations');
  },

  /** Create a new conversation. */
  async createConversation() {
    const response = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    return readJson(response, 'Failed to create conversation');
  },

  /** Permanently delete a locally stored conversation. */
  async deleteConversation(conversationId) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${encodeURIComponent(conversationId)}`,
      { method: 'DELETE' }
    );
    if (!response.ok) {
      let detail = 'Failed to delete conversation';
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch {
        // Keep fallback error.
      }
      throw new Error(detail);
    }
  },

  /** Get a specific conversation. */
  async getConversation(conversationId) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`);
    return readJson(response, 'Failed to get conversation');
  },

  /** Send a message in a conversation. */
  async sendMessage(conversationId, content, options = {}) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content,
          verdict_type: options.verdictType || 'synthesis',
          include_dissent: options.includeDissent || false,
        }),
      }
    );
    return readJson(response, 'Failed to send message');
  },

  /**
   * Send a message and receive streaming updates.
   * @param {string} conversationId - The conversation ID
   * @param {string} content - The message content
   * @param {function} onEvent - Callback function for each event: (eventType, data) => void
   * @returns {Promise<void>}
   */
  async sendMessageStream(conversationId, content, onEvent, options = {}) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content,
          verdict_type: options.verdictType || 'synthesis',
          include_dissent: options.includeDissent || false,
        }),
      }
    );

    if (!response.ok) {
      let detail = 'Failed to send message';
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch {
        // Keep fallback.
      }
      throw new Error(detail);
    }

    if (!response.body || !/^text\/event-stream(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) {
      throw new Error('Council stream: expected an SSE response.');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8', { fatal: true });
    const resultTypes = ['stage1_complete', 'stage2_complete', 'stage3_complete'];
    const liveTypes = ['engine_progress', 'stage1_start', 'stage2_start', 'stage3_start'];
    const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    const invalid = reason => new Error(`Council stream: ${reason}`);
    let buffer = '';
    let nextStage = 0;
    let finalResult;
    let terminal;
    let title;

    const consumeFrame = frame => {
      // SSE comments (including keepalives) do not represent model progress.
      const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).replace(/^ /, '')).join('\n');
      if (!data) {
        if (frame.split(/\r?\n/).every(line => !line || line.startsWith(':'))) return;
        throw invalid('missing event data.');
      }
      let event;
      try {
        event = JSON.parse(data);
      } catch {
        throw invalid('invalid event JSON.');
      }
      if (!isObject(event) || typeof event.type !== 'string') {
        throw invalid('invalid event object.');
      }
      const { type } = event;
      if (terminal) {
        if (type !== 'title_complete' || title || !isObject(event.data) || typeof event.data.title !== 'string') {
          throw invalid('unexpected event after completion.');
        }
        title = event;
        return;
      }
      if (type === 'error') {
        if (typeof event.message !== 'string' || !event.message.trim()) throw invalid('invalid error event.');
        onEvent(type, event);
        throw new Error(event.message);
      }
      if (resultTypes.includes(type)) {
        if (type !== resultTypes[nextStage]) throw invalid('missing, duplicate or out-of-order stage result.');
        const validData = type === 'stage3_complete'
          ? isObject(event.data) && ((typeof event.data.response === 'string' && event.data.response.trim()) ||
            (typeof event.data.error_status === 'string' && event.data.error_status.trim()))
          : Array.isArray(event.data);
        if (!validData || (type !== 'stage1_complete' && !isObject(event.metadata))) {
          throw invalid('invalid stage result.');
        }
        nextStage++;
        if (type === 'stage3_complete') finalResult = event;
        else onEvent(type, event);
      } else if (type === 'complete') {
        if (nextStage !== resultTypes.length || !isObject(event.metadata)) {
          throw invalid('incomplete or invalid terminal result.');
        }
        terminal = event;
      } else if (liveTypes.includes(type) && nextStage < resultTypes.length) {
        if (type === 'engine_progress' && (!isObject(event.data) || typeof event.data.stage !== 'string' || typeof event.data.state !== 'string')) {
          throw invalid('invalid progress event.');
        }
        onEvent(type, event);
      } else {
        throw invalid('unexpected event.');
      }
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          buffer += decoder.decode();
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || '';
        for (const frame of frames) consumeFrame(frame);
      }
      if (buffer.trim()) throw invalid('truncated event frame.');
      if (!terminal) throw invalid('connection closed before completion.');
      // Publish the final answer and terminal event only after the entire
      // stream is verified. A disconnect must not trigger a success reload.
      onEvent(finalResult.type, finalResult);
      onEvent(terminal.type, terminal);
      if (title) onEvent(title.type, title);
    } catch (error) {
      try { await reader.cancel(); } catch { /* Preserve the original error. */ }
      throw error;
    } finally {
      reader.releaseLock();
    }
  },
};
