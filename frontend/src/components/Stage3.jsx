import ReactMarkdown from 'react-markdown';
import './Stage3.css';

export default function Stage3({ finalResponse, verdict }) {
  if (!finalResponse) {
    return null;
  }

  const model = finalResponse.model || 'unknown';

  return (
    <div className="stage stage3">
      <h3 className="stage-title">Stage 3: Final Council Answer</h3>
      <div className="final-response">
        <div className="chairman-label">
          Chairman: {model.split('/')[1] || model}
        </div>

        {verdict && (
          <div className="verdict-summary">
            <div className="verdict-heading">
              Decision: <strong>{verdict.verdict}</strong>
              {typeof verdict.confidence === 'number' && (
                <span> · Confidence: {(verdict.confidence * 100).toFixed(0)}%</span>
              )}
            </div>
            {verdict.rationale && <p>{verdict.rationale}</p>}
            {verdict.dissent && (
              <div className="verdict-dissent">
                <strong>Dissent:</strong> {verdict.dissent}
              </div>
            )}
          </div>
        )}

        <div className="final-text markdown-content">
          <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
