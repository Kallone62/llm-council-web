import ReactMarkdown from 'react-markdown';
import './Stage3.css';

export default function Stage3({ finalResponse, verdict }) {
  if (!finalResponse) {
    return null;
  }

  const model = finalResponse.model || 'unknown';
  // Old beta conversations may have persisted native error text without an
  // error_status field. Never present those as a successful final answer.
  const failed = Boolean(finalResponse.error_status)
    || /^Error: Unable to generate final synthesis/i.test(finalResponse.response || '');

  return (
    <div className={`stage stage3 ${failed ? 'stage3-failed' : ''}`}>
      <h3 className="stage-title">{failed ? 'Stage 3: Chairman unavailable' : 'Stage 3: Final Council Answer'}</h3>
      <div className="final-response">
        <div className="chairman-label">
          Chairman: {model.split('/')[1] || model}
        </div>

        {failed && (
          <div className="stage3-error" role="alert">
            <strong>Final synthesis could not be generated</strong>
            <p>{finalResponse.response}</p>
          </div>
        )}

        {!failed && verdict && (
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

        {!failed && (
          <div className="final-text markdown-content">
            <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
