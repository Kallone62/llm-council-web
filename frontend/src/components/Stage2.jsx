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

export default function Stage2({ rankings, labelToModel, aggregateRankings }) {
  const [activeTab, setActiveTab] = useState(0);

  if (!rankings || rankings.length === 0) {
    return null;
  }

  const extractedRanking = parsedRanking(rankings[activeTab].parsed_ranking);

  return (
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Peer Rankings</h3>

      <h4>Raw Evaluations</h4>
      <p className="stage-description">
        Each model evaluated all responses (anonymized as Response A, B, C, etc.) and provided rankings.
        Below, model names are shown in <strong>bold</strong> for readability, but the original evaluation used anonymous labels.
      </p>

      <div className="tabs">
        {rankings.map((rank, index) => (
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
          {rankings[activeTab].model}
        </div>
        <div className="ranking-content markdown-content">
          <ReactMarkdown>
            {deAnonymizeText(rankings[activeTab].ranking, labelToModel)}
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

      {aggregateRankings && aggregateRankings.length > 0 && (
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
                  <span className="rank-position">#{agg.rank || index + 1}</span>
                  <span className="rank-model">
                    {modelShortName(agg.model)}
                  </span>
                  <span className="rank-score">
                    {borda !== null
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
