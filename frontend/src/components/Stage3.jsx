import ReactMarkdown from 'react-markdown';
import './Stage3.css';

export default function Stage3({ finalResponse, verdict }) {
  if (!finalResponse) {
    return null;
  }

  const model = finalResponse.model || 'unknown';
  // Old beta conversations may have persisted native error text without an
  // error_status field. Never present those as a successful final answer.
  const noResponses = finalResponse.error_status === 'no_responses'
    || (finalResponse.model === 'error' && /all models failed to respond/i.test(finalResponse.response || ''));
  const failed = Boolean(finalResponse.error_status)
    || noResponses
    || /^Error: Unable to generate final synthesis/i.test(finalResponse.response || '');

  return (
    <div className={`stage stage3 ${failed ? 'stage3-failed' : ''}`}>
      <h3 className="stage-title">{noResponses ? 'Council unavailable: no model responses' : failed ? 'Stage 3: Chairman unavailable' : 'Stage 3: Final Council Answer'}</h3>
      <div className="final-response">
        {!noResponses && (
          <div className="chairman-label">
            Chairman: {model.split('/')[1] || model}
          </div>
        )}

        {failed && (
          <div className="stage3-error" role="alert">
            <strong>{noResponses ? 'Stage 1 failed: no usable model responses' : 'Final synthesis could not be generated'}</strong>
            <p>{finalResponse.response}</p>
            {Array.isArray(finalResponse.model_failures) && finalResponse.model_failures.length > 0 && (
              <ul className="stage3-model-failures">
                {finalResponse.model_failures.map((failure, index) => (
                  <li key={index}>
                    <code>{failure.model}</code>: {failure.status}
                    {failure.http_status ? ` (HTTP ${failure.http_status})` : ''}
                    {failure.retry_after_seconds != null ? ` · retry after ~${failure.retry_after_seconds}s` : ''}
                  </li>
                ))}
              </ul>
            )}
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
