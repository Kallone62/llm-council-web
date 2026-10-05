import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import './Settings.css';

const PROFILE_LABELS = {
  quick: 'Quick',
  balanced: 'Balanced',
  high: 'High',
  reasoning: 'Reasoning',
};

const PROFILE_HELP = {
  quick: 'Fastest and cheapest profile for simple questions.',
  balanced: 'Good quality with lower latency and cost.',
  high: 'Full council quality. Recommended default.',
  reasoning: 'Slower profile for deep reasoning tasks.',
};

function Toggle({ checked, onChange, disabled = false }) {
  return (
    <button
      type="button"
      className={`toggle ${checked ? 'on' : ''}`}
      onClick={() => !disabled && onChange(!checked)}
      disabled={disabled}
      aria-pressed={checked}
    >
      <span className="toggle-knob" />
    </button>
  );
}

export default function Settings({ onApiKeyStatusChange }) {
  const [settings, setSettings] = useState(null);
  const [draft, setDraft] = useState(null);
  const [customModel, setCustomModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [keySaving, setKeySaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getSettings();
      setSettings(data);
      setDraft({ ...data });
      onApiKeyStatusChange?.(data.api_key_configured);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const profileInfo = draft?.options?.profiles?.[draft.profile];
  const effectiveModels = draft?.use_profile_models
    ? profileInfo?.models || []
    : draft?.models || [];

  const availableModels = useMemo(() => {
    if (!draft) return [];
    const values = new Set(draft.options?.available_models || []);
    (draft.models || []).forEach((model) => values.add(model));
    return Array.from(values).sort();
  }, [draft]);

  const update = (key, value) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setNotice('');
    setError('');
  };

  const toggleModel = (model) => {
    const current = draft.models || [];
    const models = current.includes(model)
      ? current.filter((item) => item !== model)
      : [...current, model];
    update('models', models);
  };

  const addCustomModel = () => {
    const model = customModel.trim();
    if (!model) return;
    if (!(draft.models || []).includes(model)) {
      update('models', [...(draft.models || []), model]);
    }
    setCustomModel('');
  };

  const saveSettings = async () => {
    if (!draft.use_profile_models && (draft.models || []).length === 0) {
      setError('Custom model mode needs at least one council model.');
      return;
    }

    setSaving(true);
    setNotice('');
    setError('');
    try {
      const payload = {
        profile: draft.profile,
        use_profile_models: draft.use_profile_models,
        models: draft.models || [],
        chairman: draft.chairman || null,
        synthesis_mode: draft.synthesis_mode,
        exclude_self_votes: draft.exclude_self_votes,
        style_normalization: draft.style_normalization,
        max_reviewers:
          draft.max_reviewers === '' || draft.max_reviewers == null
            ? null
            : Number(draft.max_reviewers),
        rubric_enabled: draft.rubric_enabled,
        safety_enabled: draft.safety_enabled,
        bias_audit_enabled: draft.bias_audit_enabled,
        cache_enabled: draft.cache_enabled,
        cache_ttl_seconds: Number(draft.cache_ttl_seconds || 0),
        timeout_multiplier: Number(draft.timeout_multiplier || 1),
      };
      const data = await api.updateSettings(payload);
      setSettings(data);
      setDraft({ ...data });
      setNotice('Settings saved. New council requests will use them immediately.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const saveApiKey = async () => {
    if (!apiKey.trim()) {
      setError('Enter an OpenRouter API key first.');
      return;
    }
    setKeySaving(true);
    setError('');
    setNotice('');
    try {
      const result = await api.updateOpenRouterKey(apiKey);
      setApiKey('');
      setDraft((prev) => ({ ...prev, api_key_configured: result.configured }));
      setSettings((prev) => ({ ...prev, api_key_configured: result.configured }));
      onApiKeyStatusChange?.(result.configured);
      setNotice('OpenRouter key saved locally.');
    } catch (err) {
      setError(err.message);
    } finally {
      setKeySaving(false);
    }
  };

  if (loading) {
    return (
      <div className="settings-page">
        <div className="settings-loading">Loading settings…</div>
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="settings-page">
        <div className="settings-error">{error || 'Settings could not be loaded.'}</div>
      </div>
    );
  }

  return (
    <div className="settings-page">
      <div className="settings-shell">
        <div className="settings-heading">
          <div>
            <h2>Settings</h2>
            <p>
              These controls write Amiable&apos;s native <code>llm_council.yaml</code> and
              hot-reload the engine. Per-question Jury Mode stays on the chat screen.
            </p>
          </div>
          <button className="settings-save-btn" onClick={saveSettings} disabled={saving}>
            {saving ? 'Saving…' : 'Save settings'}
          </button>
        </div>

        {draft.environment_overrides?.length > 0 && (
          <div className="settings-warning">
            Environment overrides are active: {draft.environment_overrides.join(', ')}. They
            may take precedence over values saved here.
          </div>
        )}

        {error && <div className="settings-error">{error}</div>}
        {notice && <div className="settings-notice">{notice}</div>}

        <section className="settings-card">
          <div className="settings-card-title">
            <div>
              <h3>Council model profile</h3>
              <p>Choose one of Amiable&apos;s bundled model pools for this direct council run.</p>
            </div>
          </div>

          <div className="settings-grid two-col">
            <label className="field-block">
              <span>Profile</span>
              <select value={draft.profile} onChange={(e) => update('profile', e.target.value)}>
                {Object.keys(PROFILE_LABELS).map((name) => (
                  <option key={name} value={name}>
                    {PROFILE_LABELS[name]}
                  </option>
                ))}
              </select>
              <small>{PROFILE_HELP[draft.profile]}</small>
            </label>

            <div className="field-block">
              <span>Profile model pool</span>
              <div className="toggle-row compact">
                <div>
                  <strong>Use profile defaults</strong>
                  <small>
                    Turn off to select your own OpenRouter model IDs.
                  </small>
                </div>
                <Toggle
                  checked={draft.use_profile_models}
                  onChange={(value) => update('use_profile_models', value)}
                />
              </div>
            </div>
          </div>

          <div className="profile-summary">
            <div className="profile-summary-label">Active council</div>
            <div className="model-chips">
              {effectiveModels.map((model) => (
                <span className="model-chip" key={model}>{model}</span>
              ))}
            </div>
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-card-title">
            <div>
              <h3>Council models</h3>
              <p>
                Select exact models when profile defaults are disabled. Known models come from
                Amiable&apos;s bundled pools; custom OpenRouter IDs are also accepted.
              </p>
            </div>
          </div>

          <div className={`model-selector ${draft.use_profile_models ? 'disabled' : ''}`}>
            {availableModels.map((model) => (
              <label className="model-option" key={model}>
                <input
                  type="checkbox"
                  checked={(draft.models || []).includes(model)}
                  onChange={() => toggleModel(model)}
                  disabled={draft.use_profile_models}
                />
                <span>{model}</span>
              </label>
            ))}
          </div>

          <div className="add-model-row">
            <input
              type="text"
              value={customModel}
              placeholder="provider/model-id"
              onChange={(e) => setCustomModel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addCustomModel();
                }
              }}
              disabled={draft.use_profile_models}
            />
            <button type="button" onClick={addCustomModel} disabled={draft.use_profile_models}>
              Add model
            </button>
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-card-title">
            <div>
              <h3>Chairman & council behavior</h3>
              <p>These map directly to Amiable&apos;s council configuration.</p>
            </div>
          </div>

          <datalist id="known-models">
            {availableModels.map((model) => <option value={model} key={model} />)}
          </datalist>

          <div className="settings-grid two-col">
            <label className="field-block">
              <span>Chairman</span>
              <input
                list="known-models"
                value={draft.chairman || ''}
                placeholder={`Auto — ${draft.options?.profiles?.[draft.profile]?.chairman || 'profile default'}`}
                onChange={(e) => update('chairman', e.target.value)}
              />
              <small>Leave blank to let Amiable choose the profile-matched chairman.</small>
            </label>

            <label className="field-block">
              <span>Synthesis mode</span>
              <select
                value={draft.synthesis_mode}
                onChange={(e) => update('synthesis_mode', e.target.value)}
              >
                <option value="consensus">Consensus</option>
                <option value="debate">Debate</option>
              </select>
              <small>Consensus merges agreement; debate emphasizes competing arguments.</small>
            </label>

            <label className="field-block">
              <span>Style normalization</span>
              <select
                value={draft.style_normalization}
                onChange={(e) => update('style_normalization', e.target.value)}
              >
                <option value="off">Off</option>
                <option value="auto">Auto</option>
                <option value="on">On</option>
              </select>
              <small>Can reduce stylistic clues before peer review.</small>
            </label>

            <label className="field-block">
              <span>Max reviewers</span>
              <input
                type="number"
                min="1"
                max="10"
                placeholder="All"
                value={draft.max_reviewers ?? ''}
                onChange={(e) => update('max_reviewers', e.target.value)}
              />
              <small>Blank means Amiable may use all available reviewers.</small>
            </label>
          </div>

          <div className="toggle-row">
            <div>
              <strong>Exclude self votes</strong>
              <small>Prevents a model from ranking its own answer.</small>
            </div>
            <Toggle
              checked={draft.exclude_self_votes}
              onChange={(value) => update('exclude_self_votes', value)}
            />
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-card-title">
            <div>
              <h3>Evaluation</h3>
              <p>Optional Amiable evaluation features applied during council deliberation.</p>
            </div>
          </div>

          <div className="toggle-row">
            <div>
              <strong>Rubric scoring</strong>
              <small>Structured scoring across accuracy, relevance, completeness, conciseness and clarity.</small>
            </div>
            <Toggle checked={draft.rubric_enabled} onChange={(v) => update('rubric_enabled', v)} />
          </div>

          <div className="toggle-row">
            <div>
              <strong>Bias audit</strong>
              <small>Checks evaluator behavior for length/position bias during the session.</small>
            </div>
            <Toggle checked={draft.bias_audit_enabled} onChange={(v) => update('bias_audit_enabled', v)} />
          </div>

          <div className="toggle-row">
            <div>
              <strong>Safety gate</strong>
              <small>Runs Amiable&apos;s response safety check before final scoring.</small>
            </div>
            <Toggle checked={draft.safety_enabled} onChange={(v) => update('safety_enabled', v)} />
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-card-title">
            <div>
              <h3>Performance</h3>
              <p>Local caching and timeout controls. These do not change the decision algorithm.</p>
            </div>
          </div>

          <div className="toggle-row">
            <div>
              <strong>Response cache</strong>
              <small>Reuse identical council results locally when available.</small>
            </div>
            <Toggle checked={draft.cache_enabled} onChange={(v) => update('cache_enabled', v)} />
          </div>

          <div className="settings-grid two-col">
            <label className="field-block">
              <span>Cache TTL (seconds)</span>
              <input
                type="number"
                min="0"
                max="2592000"
                value={draft.cache_ttl_seconds}
                disabled={!draft.cache_enabled}
                onChange={(e) => update('cache_ttl_seconds', e.target.value)}
              />
              <small>0 means no expiry.</small>
            </label>

            <label className="field-block">
              <span>Timeout multiplier</span>
              <select
                value={String(draft.timeout_multiplier)}
                onChange={(e) => update('timeout_multiplier', Number(e.target.value))}
              >
                <option value="0.5">0.5×</option>
                <option value="1">1.0×</option>
                <option value="1.5">1.5×</option>
                <option value="2">2.0×</option>
                <option value="3">3.0×</option>
              </select>
              <small>Applies to the Stage 2 / Stage 3 model budget for new runs.</small>
            </label>
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-card-title">
            <div>
              <h3>OpenRouter</h3>
              <p>The key is stored only in the local <code>.env</code> file and is never returned to the browser.</p>
            </div>
            <span className={`status-pill ${draft.api_key_configured ? 'ok' : 'missing'}`}>
              {draft.api_key_configured ? 'Configured' : 'Not configured'}
            </span>
          </div>

          <div className="api-key-row">
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              placeholder={draft.api_key_configured ? 'Enter a new key to replace it' : 'sk-or-v1-…'}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <button type="button" onClick={saveApiKey} disabled={keySaving || !apiKey.trim()}>
              {keySaving ? 'Saving…' : draft.api_key_configured ? 'Replace key' : 'Save key'}
            </button>
          </div>
        </section>

        <section className="settings-card about-card">
          <div className="settings-card-title">
            <div>
              <h3>About</h3>
              <p>This build intentionally stays pinned instead of auto-updating upstream repositories.</p>
            </div>
          </div>
          <div className="about-grid">
            <div><span>App build</span><strong>{draft.about?.app_version}</strong></div>
            <div><span>Amiable engine</span><strong>{draft.about?.engine_version}</strong></div>
            <div><span>UI base</span><strong>{draft.about?.ui_base}</strong></div>
            <div><span>Engine source</span><strong>{draft.about?.engine}</strong></div>
          </div>
          <div className="config-path">Config: {draft.config_path}</div>
          <p className="advanced-note">
            Rare/experimental Amiable options remain available by editing <code>llm_council.yaml</code>
            manually. The Settings screen only exposes options that are useful in this direct
            <code>run_full_council()</code> integration.
          </p>
        </section>

        <div className="settings-footer-actions">
          <button className="settings-save-btn" onClick={saveSettings} disabled={saving}>
            {saving ? 'Saving…' : 'Save settings'}
          </button>
          {settings && (
            <button className="secondary-btn" onClick={() => setDraft({ ...settings })} disabled={saving}>
              Reset unsaved changes
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
