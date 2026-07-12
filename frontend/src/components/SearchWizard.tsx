import { useEffect, useState } from 'react';
import { ArrowRight, BriefcaseBusiness, CircleAlert, House, Plus, SlidersHorizontal, Trash2 } from 'lucide-react';
import type { Preferences } from '../types';

const districts = ['浦东新区', '静安区', '徐汇区', '杨浦区', '闵行区', '宝山区'];
const soft = ['近地铁', '采光好', '安静', '商业便利', '适合家庭', '性价比'];
export const defaultPreferences: Preferences = {
  city: '上海', districts: [], monthly_rent_max: 6000, monthly_total_max: 6800,
  bedrooms_min: 1, area_min: 30, rental_type: 'entire', move_in_date: '2026-08-01',
  lease_months: 12, accepts_agent_fee: false, needs_elevator: true, allows_pets: false,
  commute_mode: 'transit', destinations: [{ label: '我的公司', address: '上海市浦东新区陆家嘴', weight: 1, max_minutes: 45 }],
  soft_preferences: ['近地铁', '采光好'],
};

export function SearchWizard({ onSubmit, loading, lang, initialPreferences, onPreferencesChange }: {
  onSubmit: (value: Preferences) => void;
  loading: boolean;
  lang: 'zh' | 'en';
  initialPreferences?: Preferences;
  onPreferencesChange?: (value: Preferences) => void;
}) {
  const [step, setStep] = useState(0);
  const [customPreference, setCustomPreference] = useState('');
  const [form, setForm] = useState<Preferences>(initialPreferences || defaultPreferences);
  useEffect(() => { onPreferencesChange?.(form); }, [form, onPreferencesChange]);
  const patch = (value: Partial<Preferences>) => setForm({ ...form, ...value });
  const updateDestination = (index: number, value: Partial<Preferences['destinations'][number]>) => patch({ destinations: form.destinations.map((destination, current) => current === index ? { ...destination, ...value } : destination) });
  const addDestination = () => form.destinations.length < 4 && patch({ destinations: [...form.destinations, { label: `家庭成员${form.destinations.length + 1}`, address: '', weight: 0.5, max_minutes: 45 }] });
  const removeDestination = (index: number) => form.destinations.length > 1 && patch({ destinations: form.destinations.filter((_, current) => current !== index) });
  const addCustomPreference = () => {
    const preference = customPreference.trim();
    if (!preference) return;
    patch({ soft_preferences: form.soft_preferences.includes(preference) ? form.soft_preferences : [...form.soft_preferences, preference] });
    setCustomPreference('');
  };
  const cn = lang === 'zh';
  const titles = cn
    ? ['先画出你的租房边界', '把每天的路算进去', '告诉我们什么叫“住得好”']
    : ['Set your rental boundaries', 'Count every commute', 'Define what feels like home'];
  const missingNumber = [form.monthly_rent_max, form.monthly_total_max, form.bedrooms_min, form.area_min].some(Number.isNaN);
  const invalidDestinations = form.destinations.some(destination => !destination.label.trim() || !destination.address.trim() || destination.weight <= 0 || destination.max_minutes < 5);

  const numericValue = (value: number) => Number.isNaN(value) ? '' : value;
  const numericChange = (value: string) => value === '' ? Number.NaN : Number(value);

  return <section className="wizard" aria-labelledby="wizard-title">
    <div className="step-rail">
      {titles.map((title, index) => <button key={title} onClick={() => setStep(index)} className={index === step ? 'active' : ''}>
        <span>0{index + 1}</span>{title}
      </button>)}
    </div>
    <div className="form-panel">
      <div className="eyebrow">
        {step === 0 ? <House /> : step === 1 ? <BriefcaseBusiness /> : <SlidersHorizontal />}
        {cn ? '需求向导' : 'Rental brief'}
      </div>
      <h1 id="wizard-title">{titles[step]}</h1>

      {step === 0 && <div className="field-grid">
        <label>{cn ? '目标城市' : 'City'}<input value={form.city} onChange={e => patch({ city: e.target.value })} /></label>
        <label>{cn ? '入住日期' : 'Move-in'}<input type="date" value={form.move_in_date} onChange={e => patch({ move_in_date: e.target.value })} /></label>
        <label>{cn ? '挂牌租金上限' : 'Listed rent cap'}<div className="money"><span>¥</span><input type="number" min="1000" required value={numericValue(form.monthly_rent_max)} onChange={e => patch({ monthly_rent_max: numericChange(e.target.value) })} /></div></label>
        <label>
          <span className="label-with-help">
            {cn ? '月均综合居住成本上限' : 'All-in monthly housing cap'}
            <span className="cost-help">
              <button type="button" aria-label={cn ? '查看综合居住成本计算方式' : 'Explain all-in monthly cost'} aria-describedby="cost-help-content"><CircleAlert /></button>
              <span className="cost-tooltip" role="tooltip" id="cost-help-content">
                {cn ? '月租 + 服务费 + 物业费 + 预估水电燃气 + 一次性中介费按租期月数摊销' : 'Rent + service fee + property fee + estimated utilities + agent fee amortized over the lease.'}
              </span>
            </span>
          </span>
          <div className="money"><span>¥</span><input type="number" min="1000" required value={numericValue(form.monthly_total_max)} onChange={e => patch({ monthly_total_max: numericChange(e.target.value) })} /></div>
        </label>
        <label>{cn ? '最少卧室' : 'Bedrooms'}<input type="number" min="1" required value={numericValue(form.bedrooms_min)} onChange={e => patch({ bedrooms_min: numericChange(e.target.value) })} /></label>
        <label>{cn ? '最小面积 m²' : 'Min area m²'}<input type="number" min="5" required value={numericValue(form.area_min)} onChange={e => patch({ area_min: numericChange(e.target.value) })} /></label>
        <fieldset className="wide"><legend>{cn ? '目标区域（可多选）' : 'Districts'}</legend><div className="chips">
          {districts.map(district => <button type="button" key={district} className={form.districts.includes(district) ? 'selected' : ''} onClick={() => patch({ districts: form.districts.includes(district) ? form.districts.filter(item => item !== district) : [...form.districts, district] })}>{district}</button>)}
        </div></fieldset>
      </div>}

      {step === 1 && <div className="commute-editor">
        <div className="commute-toolbar"><label>{cn ? '交通方式' : 'Mode'}<select value={form.commute_mode} onChange={e => patch({ commute_mode: e.target.value as Preferences['commute_mode'] })}><option value="transit">{cn ? '公共交通' : 'Transit'}</option><option value="driving">{cn ? '驾车' : 'Driving'}</option><option value="walking">{cn ? '步行' : 'Walking'}</option><option value="bicycling">{cn ? '骑行' : 'Cycling'}</option></select></label><button type="button" className="secondary" disabled={form.destinations.length >= 4} onClick={addDestination}><Plus/>{cn ? '添加目的地' : 'Add destination'}</button></div>
        <div className="destination-list">{form.destinations.map((destination, index) => <fieldset className="destination-card" key={index}><legend>{cn ? `目的地 ${index + 1}` : `Destination ${index + 1}`}</legend><div className="field-grid"><label>{cn ? '家庭成员 / 标签' : 'Person / label'}<input value={destination.label} onChange={e => updateDestination(index, { label: e.target.value })}/></label><label>{cn ? '地点地址' : 'Address'}<input value={destination.address} onChange={e => updateDestination(index, { address: e.target.value })}/></label><label>{cn ? '重要权重' : 'Weight'}<input type="number" min="0.05" max="1" step="0.05" value={destination.weight} onChange={e => updateDestination(index, { weight: Number(e.target.value) })}/></label><label>{cn ? '单程上限（分钟）' : 'Max one-way minutes'}<input type="number" min="5" max="180" value={destination.max_minutes} onChange={e => updateDestination(index, { max_minutes: Number(e.target.value) })}/></label></div>{form.destinations.length > 1 && <button type="button" className="remove-destination" onClick={() => removeDestination(index)}><Trash2/>{cn ? '移除' : 'Remove'}</button>}</fieldset>)}</div>
        <p className="commute-note">{cn ? '权重用于计算家庭加权通勤；系统还会单独检查每位成员的上限、最差通勤、每周总通勤和公平性。' : 'Weights drive the household average; we also check each limit, worst commute, weekly total and fairness.'}</p>
      </div>}

      {step === 2 && <div>
        <div className="chips preference">{soft.map(item => <button type="button" key={item} className={form.soft_preferences.includes(item) ? 'selected' : ''} onClick={() => patch({ soft_preferences: form.soft_preferences.includes(item) ? form.soft_preferences.filter(value => value !== item) : [...form.soft_preferences, item] })}>{item}</button>)}</div>
        <div className="custom-preference"><label>{cn ? '其它偏好' : 'Other preference'}<input maxLength={30} placeholder={cn ? '例如：可做饭、靠近公园、隔音好' : 'e.g. cooking allowed, near a park'} value={customPreference} onChange={event => setCustomPreference(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addCustomPreference(); } }}/></label><button type="button" className="secondary" onClick={addCustomPreference}>{cn ? '加入偏好' : 'Add preference'}</button></div>
        {form.soft_preferences.filter(item => !soft.includes(item)).length > 0 && <div className="custom-preference-list">{form.soft_preferences.filter(item => !soft.includes(item)).map(item => <span key={item}>{item}<button type="button" aria-label={`${cn ? '删除' : 'Remove'} ${item}`} onClick={() => patch({ soft_preferences: form.soft_preferences.filter(value => value !== item) })}>×</button></span>)}</div>}
        <div className="toggles">
          <label><input type="checkbox" checked={form.needs_elevator} onChange={e => patch({ needs_elevator: e.target.checked })} />{cn ? '必须有电梯' : 'Elevator required'}</label>
          <label><input type="checkbox" checked={form.allows_pets} onChange={e => patch({ allows_pets: e.target.checked })} />{cn ? '需要允许养宠' : 'Pets required'}</label>
          <label><input type="checkbox" checked={form.accepts_agent_fee} onChange={e => patch({ accepts_agent_fee: e.target.checked })} />{cn ? '接受一次性中介费' : 'Accept agent fee'}</label>
        </div>
      </div>}

      <div className="form-actions">
        {step > 0 && <button className="secondary" onClick={() => setStep(step - 1)}>{cn ? '上一步' : 'Back'}</button>}
        <button className="primary" disabled={loading || missingNumber || invalidDestinations} onClick={() => step < 2 ? setStep(step + 1) : onSubmit(form)}>
          {loading ? (cn ? '正在分析…' : 'Analysing…') : step < 2 ? (cn ? '继续' : 'Continue') : (cn ? '生成租房方案' : 'Build my shortlist')} <ArrowRight />
        </button>
      </div>
    </div>
  </section>;
}
