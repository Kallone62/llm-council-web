import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage2.css';

function modelId(value) {
  if (value && typeof value === 'object') {
    return value.model || 'unknown';
  }
  return value || 'unknown';
}

function modelShortName(value) {
  const id = modelId(value);
  return id.split('/')[1] || id;
}

function deAnonymizeText(text, labelToModel) {
  if (!labelToModel) return text;

  let result = text;
  Object.entries(labelToModel).forEach(([label, model]) => {
    result = result.replace(new RegExp(label, 'g'), `**${modelShortName(model)}**`);
  });
  return result;
}

function parsedRanking(value) {
  if (Array.isArray(value)) return value;
  if (value && Array.isArray(value.ranking)) return value.ranking;
  return [];
}

export default function Stage2({ rankings, labelToModel, aggregateRankings, reviewStatus }) {
  const [activeTab, setActiveTab] = useState(0);
  const state = reviewStatus?.state;
  const notice = {
    failed: 'No usable peer review was recorded. Any Chairman answer is not peer-validated.',
    partial: 'Peer review is incomplete. Any Chairman answer is only partially peer-reviewed.',
    skipped: 'Peer review was skipped because only one model responded. Any Chairman answer is not peer-validated.',
  }[state];
  const reviews = Array.isArray(rankings) ? rankings.filter(rank => rank && typeof rank === 'object') : [];
  const current = reviews[activeTab] || reviews[0];

  if (!current && !notice) {
    return null;
  }

  const extractedRanking = current && state !== 'failed' && !current.parsed_ranking?.abstained
    ? [...new Set(parsedRanking(current.parsed_ranking).filter(label =>
        typeof label === 'string' && (!labelToModel || Object.hasOwn(labelToModel, label))))]
    : [];

  return (
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Peer Rankings</h3>
      {notice && <p className="stage-description" role={state === 'skipped' ? 'note' : 'alert'}>{notice}</p>}

      {current && <>
      <h4>Raw Evaluations</h4>
      <p className="stage-description">
        Returned peer evaluations are shown below, including incomplete or unusable responses.
        Below, model names are shown in <strong>bold</strong> for readability, but the original evaluation used anonymous labels.
      </p>

      <div className="tabs">
        {reviews.map((rank, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''}`}
            onClick={() => setActiveTab(index)}
          >
            {modelShortName(rank.model)}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="ranking-model">
          {current.model}
        </div>
        <div className="ranking-content markdown-content">
          <ReactMarkdown>
            {deAnonymizeText(typeof current.ranking === 'string' ? current.ranking : '', labelToModel)}
          </ReactMarkdown>
        </div>

        {extractedRanking.length > 0 && (
          <div className="parsed-ranking">
            <strong>Extracted Ranking:</strong>
            <ol>
              {extractedRanking.map((label, i) => (
                <li key={i}>
                  {labelToModel && labelToModel[label]
                    ? modelShortName(labelToModel[label])
                    : label}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
      </>}

      {state !== 'failed' && state !== 'skipped' && aggregateRankings && aggregateRankings.length > 0 && (
        <div className="aggregate-rankings">
          <h4>Aggregate Rankings (Street Cred)</h4>
          <p className="stage-description">
            Amiable normalized Borda results (higher Borda score is better).
          </p>
          <div className="aggregate-list">
            {aggregateRankings.map((agg, index) => {
              const borda = typeof agg.borda_score === 'number' ? agg.borda_score : null;
              const avgPosition = typeof agg.average_position === 'number'
                ? agg.average_position
                : (typeof agg.average_rank === 'number' ? agg.average_rank : null);
              const votes = agg.vote_count ?? agg.rankings_count ?? 0;

              return (
                <div key={index} className="aggregate-item">
                  <span className="rank-position">{votes > 0 ? '#' + (agg.rank || index + 1) : 'Unranked'}</span>
                  <span className="rank-model">
                    {modelShortName(agg.model)}
                  </span>
                  <span className="rank-score">
                    {votes <= 0 ? 'Unranked' : borda !== null
                      ? `Borda: ${borda.toFixed(3)}`
                      : (avgPosition !== null ? `Avg pos: ${avgPosition.toFixed(2)}` : 'Unranked')}
                  </span>
                  <span className="rank-count">
                    ({votes} votes)
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
