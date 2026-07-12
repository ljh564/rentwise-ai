import { useCallback, useEffect, useRef, useState } from 'react';
import { Building2 } from 'lucide-react';
import { defaultPreferences, SearchWizard } from './components/SearchWizard';
import { Results } from './components/Results';
import type { Preferences, SearchResponse } from './types';
import './styles.css';
import './helper.css';

const ID_KEY = 'rentwise_anonymous_user_id';
const TOKEN_KEY = 'rentwise_anonymous_access_token';
const credentials = () => ({ 'X-Anonymous-User-ID': localStorage.getItem(ID_KEY) || '', 'X-Anonymous-Access-Token': localStorage.getItem(TOKEN_KEY) || '' });

export default function App() {
  const [lang, setLang] = useState<'zh' | 'en'>('zh');
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [profile, setProfile] = useState<Preferences>(defaultPreferences);
  const [memoryReady, setMemoryReady] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const saveTimer = useRef<number | null>(null);

  useEffect(() => {
    async function createSession() {
      const response = await fetch('/api/anonymous/session', { method: 'POST' });
      if (!response.ok) throw new Error('session failed');
      const session = await response.json();
      localStorage.setItem(ID_KEY, session.anonymous_user_id);
      localStorage.setItem(TOKEN_KEY, session.access_token);
    }
    async function initializeMemory() {
      try {
        if (!credentials()['X-Anonymous-User-ID'] || !credentials()['X-Anonymous-Access-Token']) await createSession();
        let response = await fetch('/api/profile', { headers: credentials() });
        if (response.status === 401) { localStorage.removeItem(ID_KEY); localStorage.removeItem(TOKEN_KEY); await createSession(); response = await fetch('/api/profile', { headers: credentials() }); }
        if (!response.ok) throw new Error('profile failed');
        setProfile((await response.json()) || defaultPreferences);
      } catch { setSaveState('error'); }
      finally { setMemoryReady(true); }
    }
    initializeMemory();
  }, []);

  const saveProfile = useCallback((preferences: Preferences) => {
    setProfile(preferences);
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    setSaveState('saving');
    saveTimer.current = window.setTimeout(async () => {
      try {
        const response = await fetch('/api/profile', { method: 'PUT', headers: { ...credentials(), 'Content-Type': 'application/json' }, body: JSON.stringify(preferences) });
        if (!response.ok) throw new Error('save failed');
        setSaveState('saved');
      } catch { setSaveState('error'); }
    }, 800);
  }, []);

  async function search(preferences: Preferences) {
    setLoading(true);
    try {
      const response = await fetch('/api/search', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(preferences) });
      if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.detail || '真实通勤暂不可用，请稍后重试。'); }
      setData(await response.json());
    } catch (error) { const message = error instanceof Error ? error.message : ''; alert(lang === 'zh' ? (message || '真实通勤暂不可用，请稍后重试。') : 'Real commute data is temporarily unavailable. Please try again later.'); }
    finally { setLoading(false); }
  }

  const memoryLabel = saveState === 'saving' ? (lang === 'zh' ? '正在保存…' : 'Saving…') : saveState === 'saved' ? (lang === 'zh' ? '偏好已保存' : 'Preferences saved') : saveState === 'error' ? (lang === 'zh' ? '记忆暂不可用' : 'Memory unavailable') : '';
  return <><a className="skip" href="#main">Skip</a><header><a className="brand" href="/"><Building2/><span>Rent<span>Wise</span></span></a><div className="mode"><span className={`memory-state ${saveState}`}>{memoryLabel}</span><span>{data ? '02 / DECIDE' : '01 / DEFINE'}</span><button onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}>{lang === 'zh' ? 'EN' : '中文'}</button></div></header><div id="main">{!memoryReady ? <div className="memory-loading">{lang === 'zh' ? '正在恢复你的租房偏好…' : 'Restoring your preferences…'}</div> : data ? <Results data={data} lang={lang} onReset={() => setData(null)}/> : <SearchWizard onSubmit={search} loading={loading} lang={lang} initialPreferences={profile} onPreferencesChange={saveProfile}/>}</div><footer><span>RentWise AI</span><p>{lang === 'zh' ? '基于证据的租房决策，不替代线下核验与专业意见。' : 'Evidence-led rental decisions. Always verify offline.'}</p></footer></>;
}
