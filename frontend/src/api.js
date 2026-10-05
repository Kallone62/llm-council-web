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

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          try {
            const event = JSON.parse(data);
            onEvent(event.type, event);
          } catch (e) {
            console.error('Failed to parse SSE event:', e);
          }
        }
      }
    }
  },
};
