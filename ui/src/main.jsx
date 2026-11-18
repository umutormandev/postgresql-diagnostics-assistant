import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const examples = [
  ['Kilit beklemeleri', 'Hangi oturumlar kilit bekliyor?'],
  ['Aktif sorgular', '10 saniyeyi aşan aktif sorguları göster.'],
  ['Sorgu istatistikleri', 'Son 15 dakikada en pahalı sorgu hangisi?'],
];

const columns = {
  lock_waits: [['waiting_pid', 'Bekleyen PID'], ['blocking_pid', 'Engelleyen PID'], ['wait_seconds', 'Bekleme (sn)']],
  long_queries: [['pid', 'Oturum PID'], ['duration_seconds', 'Çalışma süresi (sn)'], ['query_text', 'Sorgu']],
  query_stats: [['queryid', 'Sorgu kimliği'], ['total_exec_time_ms', 'Toplam (ms)'], ['mean_exec_time_ms', 'Ortalama (ms)'], ['calls', 'Çağrı sayısı']],
};

const statusText = {
  401: 'Oturum anahtarı geçersiz veya süresi dolmuş.',
  403: 'Bu teşhis için hesap yetkiniz yetersiz.',
  422: 'Soru desteklenen teşhis kapsamına uymuyor.',
  502: 'Üretilen SQL doğrulamadan geçmedi.',
  503: 'Teşhis kaynağı şu anda kullanılamıyor.',
};

const identifiers = new Set(['waiting_pid', 'blocking_pid', 'pid', 'queryid']);
const number = new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 3 });

function Results({ result, busy, onMetric }) {
  const { data, elapsed } = result || {};
  const listed = data ? columns[data.intent] || [] : [];

  return <section className="panel results" aria-labelledby="results-title" aria-busy={busy}>
    <div className="panel-heading">
      <h2 id="results-title">Teşhis sonucu</h2>
      {elapsed != null && <span className="muted timing">{number.format(elapsed)} ms</span>}
    </div>
    <div aria-live="polite">
      {busy && <div className="empty"><strong>Sorgu çalışıyor</strong><p>PostgreSQL teşhis sonucu bekleniyor.</p></div>}
      {!busy && !result && <div className="empty"><strong>Sonuçlar burada görünecek</strong><p>Bir teşhis sorusu yaz veya örneklerden birini seç.</p></div>}
      {!busy && result?.error && <p role="alert" className="notice error">{result.error}</p>}
      {!busy && data?.status === 'clarification_required' && <div className="clarification">
        <p>{data.clarification.question}</p>
        <div className="chips">{data.clarification.options.map(metric =>
          <button key={metric} type="button" onClick={() => onMetric(metric)}>
            {metric === 'total' ? 'Toplam çalışma süresi' : 'Çağrı başına ortalama'}
          </button>
        )}</div>
      </div>}
      {!busy && data?.status === 'insufficient_data' && <p className="notice">Bu zaman aralığı için yeterli ölçüm kaydı yok.</p>}
      {!busy && data?.status === 'ok' && <>
        {data.rows.length ? <div className="table-wrap"><table>
          <thead><tr>{listed.map(([key, name]) => <th scope="col" key={key}>{name}</th>)}</tr></thead>
          <tbody>{data.rows.map((row, index) => <tr key={index}>
            {listed.map(([key]) => <td key={key}>
              {typeof row[key] === 'number' && !identifiers.has(key) ? number.format(row[key]) : (row[key] ?? '—')}
            </td>)}
          </tr>)}</tbody>
        </table></div> : <div className="empty"><strong>Eşleşen kayıt yok</strong></div>}
        <div className="source">
          <span className="source-label">{data.source === 'controlled_fixture' ? 'Sentetik demo verisi' : 'Kaynak: ' + data.source}</span>
          {data.window_minutes != null && <span>Son {data.window_minutes} dakika</span>}
          {data.threshold_seconds != null && <span>{data.threshold_seconds} saniyenin üzeri</span>}
        </div>
      </>}
    </div>
  </section>;
}

function App() {
  const [question, setQuestion] = useState('');
  const [method, setMethod] = useState('proposed');
  const [token, setToken] = useState('');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  async function diagnose(input) {
    const started = performance.now();
    setBusy(true);
    setResult(null);
    try {
      const response = await fetch('/diagnose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token.trim() },
        body: JSON.stringify(input),
      });
      const data = await response.json();
      const elapsed = performance.now() - started;
      setResult(response.ok ? { data, elapsed, input } : {
        error: statusText[response.status] || 'Teşhis sırasında bir hata oluştu.', elapsed,
      });
    } catch {
      setResult({ error: 'API’ye ulaşılamadı. FastAPI bağlantısını kontrol et.' });
    } finally {
      setBusy(false);
    }
  }

  return <>
    <header className="topline">
      <div className="shell top-content"><span className="wordmark">PostgreSQL</span><span>Teşhis Asistanı</span></div>
    </header>
    <main className="shell">
      <div className="intro">
        <h1>PostgreSQL teşhis asistanı</h1>
        <p>Kilit beklemelerini, uzun sorguları ve sorgu istatistiklerini Türkçe sor.</p>
      </div>
      <div className="layout">
        <section className="panel compose" aria-labelledby="compose-title">
          <h2 id="compose-title">Teşhis sorgusu</h2>
          <form onSubmit={event => {
            event.preventDefault();
            if (!busy && question.trim() && token.trim()) diagnose({ question: question.trim(), method });
          }}>
            <div className="field">
              <label htmlFor="token">Oturum anahtarı</label>
              <input id="token" type="password" autoComplete="off" value={token} disabled={busy}
                onChange={event => { setToken(event.target.value); setResult(null); }} placeholder="Operatör veya izleyici anahtarı" required />
            </div>
            <div className="field">
              <label htmlFor="method">Yöntem</label>
              <select id="method" disabled={busy} value={method} onChange={event => setMethod(event.target.value)}>
                <option value="proposed">Önerilen yöntem</option>
                <option value="b1">B1 · SQL şablonları</option>
                <option value="b2">B2 · Zero-shot</option>
                <option value="b3">B3 · Few-shot</option>
                <option value="a1">A1 · Sabit şema</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="question">Soru</label>
              <textarea id="question" value={question} maxLength={1000} disabled={busy}
                onChange={event => setQuestion(event.target.value)} placeholder="Hangi oturumlar kilit bekliyor?" required />
              <div className="chips" aria-label="Örnek sorular">{examples.map(([label, value]) =>
                <button key={label} type="button" disabled={busy} onClick={() => setQuestion(value)}>{label}</button>
              )}</div>
            </div>
            <button className="action" type="submit" disabled={busy || !token.trim() || !question.trim()}>
              {busy ? 'Çalışıyor…' : 'Teşhisi çalıştır'}
            </button>
          </form>
        </section>
        <Results result={result} busy={busy} onMetric={metric => diagnose({ ...result.input, metric })} />
      </div>
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
