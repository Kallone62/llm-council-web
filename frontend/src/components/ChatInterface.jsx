import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage3 from './Stage3';
import './ChatInterface.css';

const ENGINE_STAGE_LABELS = {
  memory: 'Conversation memory · AI summary',
  stage1: 'Stage 1 · Individual responses',
  stage1_5: 'Stage 1.5 · Style normalization',
  stage2: 'Stage 2 · Peer review',
  stage3: 'Stage 3 · Chairman synthesis',
};

function progressMessage(item) {
  const stage = ENGINE_STAGE_LABELS[item.stage] || item.stage;
  const status = item.state === 'started' ? 'running'
    : item.state === 'completed' ? 'completed'
    : item.state;
  const count = item.state === 'completed'
    ? (item.responses != null ? ` · ${item.responses} responses`
      : item.reviews != null ? ` · ${item.reviews} reviews` : '')
    : '';
  const model = item.model ? ` · ${item.model}` : '';
  const elapsed = typeof item.elapsed_seconds === 'number' ? ` · ${item.elapsed_seconds}s` : '';
  return `${stage}: ${status}${count}${model}${elapsed}`;
}

function FreeProfileNotice({ onOpenSettings }) {
  return (
    <div className="free-profile-notice" role="note">
      <div className="free-profile-notice-text">
        <strong>You're using Free models.</strong>{' '}
        This is a good way to try the Council, but free models have usage limits
        and some optional features (style normalization and extra evaluations)
        are unavailable. Other profiles offer more model choices and may provide
        better results, but can incur OpenRouter charges.
      </div>
      <button type="button" onClick={onOpenSettings}>View profiles</button>
    </div>
  );
}

export default function ChatInterface({
  conversation,
  onSendMessage,
  isLoading,
  apiKeyConfigured,
  activeProfile,
  onOpenSettings,
}) {
  const [input, setInput] = useState('');
  const [verdictType, setVerdictType] = useState('synthesis');
  const [includeDissent, setIncludeDissent] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [conversation]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim() && !isLoading) {
      onSendMessage(input, { verdictType, includeDissent });
      setInput('');
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (!conversation) {
    return (
      <div className="chat-interface">
        {activeProfile === 'free' && <FreeProfileNotice onOpenSettings={onOpenSettings} />}
        <div className="empty-state">
          <h2>Welcome to LLM Council</h2>
          <p>Create a new conversation to get started</p>
          {apiKeyConfigured === false && (
            <button className="configure-key-btn" onClick={onOpenSettings}>
              Configure OpenRouter in Settings
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="chat-interface">
      {activeProfile === 'free' && <FreeProfileNotice onOpenSettings={onOpenSettings} />}
      <div className="messages-container">
        {conversation.messages.length === 0 ? (
          <div className="empty-state">
            <h2>Start a conversation</h2>
            <p>Ask a question to consult the LLM Council</p>
            {apiKeyConfigured === false && (
              <div className="api-key-reminder">
                OpenRouter is not configured yet.
                <button type="button" onClick={onOpenSettings}>Open Settings</button>
              </div>
            )}
          </div>
        ) : (
          conversation.messages.map((msg, index) => (
            <div key={index} className="message-group">
              {msg.role === 'user' ? (
                <div className="user-message">
                  <div className="message-label">You</div>
                  <div className="message-content">
                    <div className="markdown-content">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="assistant-message">
                  <div className="message-label">LLM Council</div>

                  {msg.error && (
                    <div className="council-error">
                      <strong>Council request failed</strong>
                      <span>{msg.error}</span>
                    </div>
                  )}

                  {(msg.engineProgress?.length > 0 || msg.metadata?.ui_runtime_progress?.length > 0) && (
                    <div className="engine-progress" aria-live="polite">
                      <strong>Live engine progress</strong>
                      {(msg.engineProgress?.length ? msg.engineProgress : msg.metadata?.ui_runtime_progress || []).map((event, progressIndex) => (
                        <div key={progressIndex} className="engine-progress-event">
                          {event.state === 'completed' ? '✓' :
                            event.state === 'failed' ? '✕' : '·'}{' '}
                          {progressMessage(event)}
                        </div>
                      ))}
                    </div>
                  )}

                  {msg.loading?.stage1 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 1: Collecting individual responses...</span>
                    </div>
                  )}
                  {msg.stage1 && <Stage1 responses={msg.stage1} />}

                  {msg.loading?.stage2 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 2: Peer rankings...</span>
                    </div>
                  )}
                  {msg.stage2 && (
                    <Stage2
                      rankings={msg.stage2}
                      labelToModel={msg.metadata?.label_to_model}
                      aggregateRankings={msg.metadata?.aggregate_rankings}
                    />
                  )}

                  {msg.loading?.stage3 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 3: Final synthesis...</span>
                    </div>
                  )}
                  {msg.stage3 && (
                    <Stage3
                      finalResponse={msg.stage3}
                      verdict={msg.metadata?.verdict}
                    />
                  )}
                </div>
              )}
            </div>
          ))
        )}

        {isLoading && (
          <div className="loading-indicator">
            <div className="spinner"></div>
            <span>Council engine running…</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <form className="input-form" onSubmit={handleSubmit}>
          <div className="message-entry">
            <textarea
              className="message-input"
              placeholder="Ask a question or continue this conversation... (Shift+Enter for new line)"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isLoading}
              rows={3}
            />
            <div className="council-options">
              <label>
                Mode
                <select
                  value={verdictType}
                  onChange={(e) => setVerdictType(e.target.value)}
                  disabled={isLoading}
                >
                  <option value="synthesis">Synthesis</option>
                  <option value="binary">Binary decision</option>
                  <option value="tie_breaker">Tie-breaker</option>
                </select>
              </label>
              <label className="dissent-option">
                <input
                  type="checkbox"
                  checked={includeDissent}
                  onChange={(e) => setIncludeDissent(e.target.checked)}
                  disabled={isLoading}
                />
                Include dissent
              </label>
            </div>
          </div>
          <button
            type="submit"
            className="send-button"
            disabled={!input.trim() || isLoading}
          >
            Send
          </button>
        </form>
    </div>
  );
}
