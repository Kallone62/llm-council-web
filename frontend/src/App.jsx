import { useState, useEffect, useRef } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import Settings from './components/Settings';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [view, setView] = useState('chat');
  const activeConversationIdRef = useRef(null);
  const [apiKeyConfigured, setApiKeyConfigured] = useState(null);
  const [deletingConversationId, setDeletingConversationId] = useState(null);
  const [deleteError, setDeleteError] = useState('');
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('llm-council-theme') === 'dark' ? 'dark' : 'light';
    } catch {
      return 'light';
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem('llm-council-theme', theme);
    } catch {
      // Local browser storage may be unavailable; theme still works this session.
    }
  }, [theme]);

  useEffect(() => {
    loadConversations();
    loadSettingsStatus();
  }, []);

  useEffect(() => {
    activeConversationIdRef.current = currentConversationId;
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadSettingsStatus = async () => {
    try {
      const settings = await api.getSettings();
      setApiKeyConfigured(settings.api_key_configured);
    } catch (error) {
      console.error('Failed to load settings status:', error);
    }
  };

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      if (activeConversationIdRef.current === id) {
        setCurrentConversation(conv);
      }
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      activeConversationIdRef.current = newConv.id;
      setCurrentConversation(null);
      setCurrentConversationId(newConv.id);
      setView('chat');
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    activeConversationIdRef.current = id;
    setCurrentConversation(null);
    setCurrentConversationId(id);
    setView('chat');
  };

  const handleDeleteConversation = async (id) => {
    if (isLoading || deletingConversationId) return;
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return;
    const title = conversation.title || 'New Conversation';
    if (!window.confirm(`Delete "${title}" permanently? This cannot be undone.`)) return;

    setDeleteError('');
    setDeletingConversationId(id);
    try {
      await api.deleteConversation(id);
      setConversations((prev) => prev.filter((item) => item.id !== id));
      if (currentConversationId === id) {
        activeConversationIdRef.current = null;
        setCurrentConversationId(null);
        setCurrentConversation(null);
        setView('chat');
      }
    } catch (error) {
      console.error('Failed to delete conversation:', error);
      setDeleteError(error.message || 'Could not delete conversation.');
    } finally {
      setDeletingConversationId(null);
    }
  };

  const handleSendMessage = async (content, options = {}) => {
    if (!currentConversationId || isLoading) return;
    const sendConversationId = currentConversationId;
    const updateSentConversation = (updater) => {
      setCurrentConversation((prev) =>
        prev?.id === sendConversationId ? updater(prev) : prev
      );
    };
    let completed = false;

    setIsLoading(true);
    try {
      const userMessage = { role: 'user', content };
      updateSentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        engineProgress: [],
        error: null,
        loading: { stage1: false, stage2: false, stage3: false },
      };

      updateSentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      await api.sendMessageStream(sendConversationId, content, (eventType, event) => {
        switch (eventType) {
          case 'engine_progress':
            updateSentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.engineProgress = [...(lastMsg.engineProgress || []), event.data];
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;
          case 'stage1_start':
            updateSentConversation((prev) => {
              const messages = [...prev.messages];
              messages[messages.length - 1].loading.stage1 = true;
              return { ...prev, messages };
            });
            break;
          case 'stage1_complete':
            updateSentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage1 = event.data;
              lastMsg.loading.stage1 = false;
              return { ...prev, messages };
            });
            break;
          case 'stage2_start':
            updateSentConversation((prev) => {
              const messages = [...prev.messages];
              messages[messages.length - 1].loading.stage2 = true;
              return { ...prev, messages };
            });
            break;
          case 'stage2_complete':
            updateSentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage2 = event.data;
              lastMsg.metadata = event.metadata;
              lastMsg.loading.stage2 = false;
              return { ...prev, messages };
            });
            break;
          case 'stage3_start':
            updateSentConversation((prev) => {
              const messages = [...prev.messages];
              messages[messages.length - 1].loading.stage3 = true;
              return { ...prev, messages };
            });
            break;
          case 'stage3_complete':
            updateSentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage3 = event.data;
              lastMsg.metadata = event.metadata || lastMsg.metadata;
              lastMsg.loading.stage3 = false;
              return { ...prev, messages };
            });
            break;
          case 'title_complete':
            loadConversations();
            break;
          case 'complete':
            completed = true;
            loadConversations();
            break;
          case 'error':
            console.error('Stream error:', event.message);
            updateSentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.error = event.message || 'Council request failed.';
              lastMsg.loading = { stage1: false, stage2: false, stage3: false };
              return { ...prev, messages };
            });
            break;
          default:
            console.log('Unknown event type:', eventType);
        }
      }, options);
    } catch (error) {
      console.error('Failed to send message:', error);
      updateSentConversation((prev) => {
        const messages = [...prev.messages];
        const lastMsg = messages[messages.length - 1];
        if (lastMsg?.role === 'assistant') {
          messages[messages.length - 1] = {
            ...lastMsg,
            error: error.message || 'Could not complete the Council request.',
            loading: { stage1: false, stage2: false, stage3: false },
          };
        }
        return { ...prev, messages };
      });
    } finally {
      if (completed && activeConversationIdRef.current === sendConversationId) {
        await loadConversation(sendConversationId);
      }
      setIsLoading(false);
    }
  };

  return (
    <div className="app" data-theme={theme}>
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        onOpenSettings={() => setView('settings')}
        settingsActive={view === 'settings'}
        apiKeyConfigured={apiKeyConfigured}
        onDeleteConversation={handleDeleteConversation}
        deletingConversationId={deletingConversationId}
        deleteError={deleteError}
        deletionDisabled={isLoading}
        theme={theme}
        onToggleTheme={() => setTheme((prev) => prev === 'dark' ? 'light' : 'dark')}
      />
      {view === 'settings' ? (
        <Settings onApiKeyStatusChange={setApiKeyConfigured} />
      ) : (
        <ChatInterface
          conversation={currentConversation}
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
          apiKeyConfigured={apiKeyConfigured}
          onOpenSettings={() => setView('settings')}
        />
      )}
    </div>
  );
}

export default App;
