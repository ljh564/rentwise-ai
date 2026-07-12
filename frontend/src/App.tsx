import { useState } from 'react';
import { Building2 } from 'lucide-react';
import { SearchWizard } from './components/SearchWizard';
import { Results } from './components/Results';
import type { Preferences, SearchResponse } from './types';
import './styles.css';
import './helper.css';

export default function App() {
  const [lang, setLang] = useState<'zh' | 'en'>('zh');
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);

  async function search(preferences: Preferences) {
    setLoading(true);
    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(preferences),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || '真实通勤暂不可用，请稍后重试。');
      }
      setData(await response.json());
    } catch (error) {
      const message = error instanceof Error ? error.message : '';
      alert(lang === 'zh' ? (message || '真实通勤暂不可用，请稍后重试。') : 'Real commute data is temporarily unavailable. Please try again later.');
    } finally {
      setLoading(false);
    }
  }

  return <><a className="skip" href="#main">Skip</a><header><a className="brand" href="/"><Building2/><span>Rent<span>Wise</span></span></a><div className="mode"><span>{data ? '02 / DECIDE' : '01 / DEFINE'}</span><button onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}>{lang === 'zh' ? 'EN' : '中文'}</button></div></header><div id="main">{data ? <Results data={data} lang={lang} onReset={() => setData(null)}/> : <SearchWizard onSubmit={search} loading={loading} lang={lang}/>}</div><footer><span>RentWise AI</span><p>{lang === 'zh' ? '基于证据的租房决策，不替代线下核验与专业意见。' : 'Evidence-led rental decisions. Always verify offline.'}</p></footer></>;
}
