import { ExternalLink, MapPin, Sparkles, TrainFront, WalletCards } from 'lucide-react';
import type { SearchResponse } from '../types';

export function Results({ data, lang, onReset }: { data: SearchResponse; lang: 'zh' | 'en'; onReset: () => void }) {
  const cn = lang === 'zh';
  return <main className="results">
    <div className="results-head"><div><p className="eyebrow"><Sparkles/>{cn ? 'Agent 决策结果' : 'Agent decision'}</p><h1>{cn ? '不是最便宜，而是最适合' : 'Not the cheapest. The best fit.'}</h1><p>{cn ? `已分析 ${data.total_candidates} 套候选房源。结果同时考虑真实成本、硬性条件和通勤。` : `Analysed ${data.total_candidates} listings across true cost, constraints and commute.`}</p></div><button className="secondary" onClick={onReset}>{cn ? '调整需求' : 'Edit brief'}</button></div>
    <div className="assumption-rail">{data.assumptions.map(x => <span key={x}>{x}</span>)}</div>
    <div className="listing-grid">{data.recommendations.map((r, i) => <article key={r.listing.id} className={`listing ${!r.hard_constraints_passed ? 'dimmed' : ''}`}>
      <div className="image-wrap"><img src={r.listing.image_url} alt=""/><span className="rank">#{i + 1}</span><span className={r.hard_constraints_passed ? 'pass' : 'fail'}>{r.hard_constraints_passed ? (cn ? '硬条件通过' : 'Constraints pass') : (cn ? '存在冲突' : 'Has conflicts')}</span></div>
      <div className="listing-body"><div className="title-row"><div><p>{r.listing.district} · {r.listing.neighborhood}</p><h2>{r.listing.title}</h2></div><div className="recommendation-score" title={cn ? '内部综合排序分：结合成本、通勤、偏好和硬条件计算，不代表概率。' : 'Internal ranking score based on cost, commute, preferences and constraints; not a probability.'}><span>{cn ? '推荐分' : 'Score'}</span><strong>{r.score}</strong></div></div>
        <div className="metrics"><div><WalletCards/><span>{cn ? '真实月成本' : 'True monthly'}<b>¥{r.monthly_true_cost.toLocaleString()}</b></span></div><div><TrainFront/><span>{cn ? '加权通勤' : 'Weighted commute'}<b>{r.weighted_commute_minutes} min</b></span></div><div><MapPin/><span>{cn ? '面积 / 户型' : 'Area / rooms'}<b>{r.listing.area_sqm}m² · {r.listing.bedrooms}{cn ? '室' : ' bd'}</b></span></div></div>
        <div className="why"><h3>{cn ? '为什么推荐' : 'Why this one'}</h3><ul>{r.reasons.map(x => <li key={x}>{x}</li>)}</ul></div><div className="tradeoff"><h3>{cn ? '你需要接受' : 'Trade-offs'}</h3><ul>{r.tradeoffs.map(x => <li key={x}>{x}</li>)}</ul></div><div className="tags">{r.listing.tags.map(x => <span key={x}>{x}</span>)}</div><a className="contact" href={r.listing.source_url} target="_blank" rel="noreferrer">{cn ? '一键联系原平台' : 'Contact on source'} <ExternalLink/></a>
      </div>
    </article>)}</div>
  </main>;
}
