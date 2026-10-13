import './Sidebar.css';

export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  onOpenSettings,
  settingsActive,
  apiKeyConfigured,
  onDeleteConversation,
  deletingConversationId,
  deleteError,
  deletionDisabled,
  theme,
  onToggleTheme,
}) {
  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h1>LLM Council</h1>
        <button className="new-conversation-btn" onClick={onNewConversation}>
          + New Conversation
        </button>
      </div>

      <div className="conversation-list">
        {deleteError && <div className="sidebar-error" role="alert">{deleteError}</div>}
        {conversations.length === 0 ? (
          <div className="no-conversations">No conversations yet</div>
        ) : (
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={`conversation-item ${
                conv.id === currentConversationId && !settingsActive ? 'active' : ''
              }`}
            >
              <button
                type="button"
                className="conversation-select"
                onClick={() => onSelectConversation(conv.id)}
                aria-current={conv.id === currentConversationId && !settingsActive ? 'page' : undefined}
              >
                <span className="conversation-title">
                  {conv.title || 'New Conversation'}
                </span>
                <span className="conversation-meta">
                  {conv.message_count} messages
                </span>
              </button>
              <button
                type="button"
                className="conversation-delete-btn"
                title="Delete conversation"
                aria-label={`Delete conversation: ${conv.title || 'New Conversation'}`}
                disabled={deletionDisabled || deletingConversationId !== null}
                onClick={() => onDeleteConversation(conv.id)}
              >
                {deletingConversationId === conv.id ? '…' : '×'}
              </button>
            </div>
        )}
      </div>

      <div className="sidebar-footer">
        <button
          type="button"
          className="theme-toggle-btn"
          onClick={onToggleTheme}
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          <span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>
          <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
        </button>
        <button
          className={`settings-nav-btn ${settingsActive ? 'active' : ''}`}
          onClick={onOpenSettings}
        >
          <span>⚙ Settings</span>
          {apiKeyConfigured === false && <span className="settings-alert-dot" title="OpenRouter key missing" />}
        </button>
      </div>
    </div>
  );
}
