import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, useNavigate, useLocation, useParams } from "react-router-dom";
import { Bell, BellRing, ChevronRight, CircleUserRound, Home, LineChart, LogOut, Menu, Plus, Search, Settings, Sparkles, TrendingDown, TrendingUp, X } from "lucide-react";
import { Toaster, toast } from "sonner";
import "@/App.css";
import "@/watchlist.css";
import "@/enhancements.css";
import "@/polish.css";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const parseError = async (r) => {
  const text = await r.text();
  try { const j = JSON.parse(text); return j.detail || j.message || `Request failed (${r.status})`; }
  catch { return (text && text.slice(0,180)) || `Request failed (${r.status})`; }
};
const get = (path) => fetch(`${API}${path}`, { credentials:"include" }).then(async r => { if(!r.ok) throw new Error(await parseError(r)); return r.json(); });
const post = (path, body) => fetch(`${API}${path}`, { method:"POST", credentials:"include", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) }).then(async r => { if(!r.ok) throw new Error(await parseError(r)); return r.json(); });
const mutate = (path, method, body) => fetch(`${API}${path}`, { method, credentials:"include", headers:{"Content-Type":"application/json"}, body:body?JSON.stringify(body):undefined }).then(async r => { if(!r.ok) throw new Error(await parseError(r)); return r.status===204?null:r.json(); });

const WatchlistCtx = createContext(null);
const useWatchlists = () => useContext(WatchlistCtx);

function WatchlistProvider({ children }) {
  const [lists, setLists] = useState([]);
  const [activeId, setActiveIdRaw] = useState(() => localStorage.getItem("watchit.activeId") || null);
  const [version, setVersion] = useState(0);
  const setActiveId = useCallback((id) => {
    setActiveIdRaw(id);
    if (id) localStorage.setItem("watchit.activeId", id);
  }, []);
  const reload = useCallback(async () => {
    try {
      const x = await get("/watchlists");
      setLists(x);
      setActiveIdRaw((current) => {
        if (current && x.some((w) => w.watchlist_id === current)) return current;
        const next = x[0]?.watchlist_id || null;
        if (next) localStorage.setItem("watchit.activeId", next);
        return next;
      });
    } catch (e) { toast.error(e.message); }
  }, []);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  useEffect(() => { reload(); }, [reload]);
  const value = useMemo(() => ({ lists, activeId, setActiveId, reload, refresh, version }), [lists, activeId, setActiveId, reload, refresh, version]);
  return <WatchlistCtx.Provider value={value}>{children}</WatchlistCtx.Provider>;
}

function Auth({ onLogin }) { const [register,setRegister]=useState(false),[email,setEmail]=useState(""),[password,setPassword]=useState(""),[name,setName]=useState(""),[busy,setBusy]=useState(false); const navigate=useNavigate();
  const submit=async e=>{e.preventDefault();setBusy(true);try{const u=await post(`/auth/${register?"register":"login"}`,{email,password,name});onLogin(u);navigate("/");}catch(x){toast.error(x.message)}finally{setBusy(false)}};
  const google=()=>{ /* REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH */ window.location.href=`https://auth.emergentagent.com/?redirect=${encodeURIComponent(window.location.origin+"/")}` };
  return <main className="auth-page"><div className="auth-brand"><span className="logo-mark">W</span><span>WATCHIT<span className="green">!</span></span></div><section className="auth-panel"><p className="eyebrow">YOUR MARKET MEMORY</p><h1>{register?"Make your watchlist matter.":"Welcome back, investor."}</h1><p className="muted">{register?"See what changed while you were away.":"See what happened while you were away."}</p><form onSubmit={submit}>{register&&<label>NAME<input data-testid="auth-name-input" value={name} onChange={e=>setName(e.target.value)} placeholder="Your name" required/></label>}<label>EMAIL<input data-testid="auth-email-input" type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com" required/></label><label>PASSWORD<input data-testid="auth-password-input" type="password" value={password} onChange={e=>setPassword(e.target.value)} placeholder="At least 6 characters" required/></label><button data-testid="auth-submit-button" className="primary-btn" disabled={busy}>{busy?"Checking…":register?"Create account":"Sign in"}</button></form><div className="divider"><span>or</span></div><button data-testid="google-login-button" className="secondary-btn" onClick={google}>Continue with Google</button><button data-testid="auth-toggle-button" className="text-btn" onClick={()=>setRegister(!register)}>{register?"Already have an account? Sign in":"New here? Create an account"}</button></section></main> }

function WatchlistPanel(){
  const { lists, activeId, setActiveId, reload, refresh } = useWatchlists();
  const [visible,setVisible]=useState(false);
  const [drawerId,setDrawerId]=useState(activeId);
  const active = useMemo(()=>lists.find(l=>l.watchlist_id===drawerId)||lists[0]||null,[lists,drawerId]);
  useEffect(()=>{ if(visible && !drawerId && activeId) setDrawerId(activeId); },[visible,drawerId,activeId]);
  const [name,setName]=useState("");
  const [symbol,setSymbol]=useState("");
  const [editing,setEditing]=useState(false);
  const [preview,setPreview]=useState(null);
  const [previewBusy,setPreviewBusy]=useState(false);
  const previewTimer = useRef(null);
  useEffect(()=>{
    if(!symbol.trim()){ setPreview(null); setPreviewBusy(false); return; }
    setPreviewBusy(true);
    if(previewTimer.current) clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(async()=>{
      try{
        const p = await get(`/stocks/preview?symbol=${encodeURIComponent(symbol.trim())}`);
        setPreview({ok:true, ...p});
      }catch(e){ setPreview({ok:false, message:e.message}); }
      finally{ setPreviewBusy(false); }
    }, 400);
    return ()=>{ if(previewTimer.current) clearTimeout(previewTimer.current); };
  },[symbol]);
  const create=async()=>{ if(!name.trim())return; try{ const created = await post("/watchlists",{name}); setName(""); await reload(); setDrawerId(created.watchlist_id); setActiveId(created.watchlist_id); refresh(); toast.success(`Created ${created.name}`);}catch(e){toast.error(e.message)} };
  const rename=async()=>{ if(!active||!name.trim())return; try{ await mutate(`/watchlists/${active.watchlist_id}`,"PATCH",{name}); setName(""); setEditing(false); await reload(); refresh();}catch(e){toast.error(e.message)} };
  const removeList=async()=>{ if(!active)return; if(lists.length<=1){toast.error("Keep at least one watchlist");return;} try{ await mutate(`/watchlists/${active.watchlist_id}`,"DELETE"); await reload(); setDrawerId(null); refresh(); toast.success("Watchlist removed"); }catch(e){toast.error(e.message)} };
  const makePrimary=async()=>{ if(!active)return; try{ await post(`/watchlists/${active.watchlist_id}/primary`); await reload(); setActiveId(active.watchlist_id); refresh(); toast.success(`${active.name} is now your primary watchlist`);}catch(e){toast.error(e.message)} };
  const add=async()=>{ if(!active||!symbol.trim())return; try{ const updated = await post(`/watchlists/${active.watchlist_id}/stocks`,{symbol:symbol.trim(),exchange:"XNSE"}); setSymbol(""); setPreview(null); await reload(); refresh(); toast.success(`Added ${symbol.trim().toUpperCase()}`);}catch(e){toast.error(e.message)} };
  const remove=async s=>{ try{ await mutate(`/watchlists/${active.watchlist_id}/stocks/${s}`,"DELETE"); await reload(); refresh(); toast.success(`Removed ${s}`);}catch(e){toast.error(e.message)} };
  const pickList = (l) => { setDrawerId(l.watchlist_id); setActiveId(l.watchlist_id); refresh(); };
  return (<>
    <button data-testid="manage-watchlists-button" className="manage-watchlists-btn" onClick={()=>setVisible(true)}>Watchlists <span>{lists.length}</span></button>
    {visible && (
      <div className="drawer-backdrop" onClick={()=>setVisible(false)}>
        <aside className="watchlist-drawer" onClick={e=>e.stopPropagation()}>
          <div className="drawer-header"><div><p className="eyebrow">YOUR LISTS</p><h2>Watchlists</h2></div><button data-testid="close-watchlists-button" className="icon-btn" onClick={()=>setVisible(false)}><X size={18}/></button></div>
          <div className="create-list"><input data-testid="new-watchlist-name-input" value={name} onChange={e=>setName(e.target.value)} placeholder="New watchlist name"/><button data-testid="create-watchlist-button" className="primary-btn small" onClick={create}>Create</button></div>
          <div className="watchlist-tabs">{lists.map(l=><button data-testid={`watchlist-tab-${l.watchlist_id}`} className={active?.watchlist_id===l.watchlist_id?"watchlist-tab active":"watchlist-tab"} onClick={()=>pickList(l)} key={l.watchlist_id}>{l.name}<span>{l.symbols.length}</span></button>)}</div>
          {active && (
            <div className="watchlist-editor">
              <div className="drawer-row"><strong>{active.name}{active.is_primary && <span className="primary-tag" data-testid={`primary-tag-${active.watchlist_id}`}>PRIMARY</span>}</strong><div>{!active.is_primary && <button data-testid={`primary-watchlist-button-${active.watchlist_id}`} className="drawer-link" onClick={makePrimary}>Make primary</button>}<button data-testid="rename-watchlist-button" className="drawer-link" onClick={()=>{setEditing(!editing);setName(active.name)}}>Rename</button><button data-testid="delete-watchlist-button" className="drawer-link danger" onClick={removeList}>Delete</button></div></div>
              {editing && <div className="inline-edit"><input data-testid="rename-watchlist-input" value={name} onChange={e=>setName(e.target.value)}/><button data-testid="save-watchlist-name-button" className="primary-btn small" onClick={rename}>Save</button></div>}
              <div className="add-stock-row"><input data-testid="add-stock-symbol-input" value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} placeholder="Any NSE ticker e.g. WIPRO, PAYTM" onKeyDown={e=>e.key==="Enter"&&preview?.ok&&add()}/><button data-testid="add-stock-to-watchlist-button" className="primary-btn small" onClick={add} disabled={!preview?.ok}>Add</button></div>
              {symbol.trim() && (
                <div data-testid="preview-card" className={preview?.ok?"preview-card":"preview-card err"}>
                  {previewBusy && <small className="muted">Checking {symbol.trim()} on NSE…</small>}
                  {!previewBusy && preview?.ok && (
                    <>
                      <div className="preview-head"><strong>{preview.symbol}</strong><span className={preview.change_pct>=0?"up":"down"}>₹{Number(preview.price||0).toLocaleString("en-IN",{maximumFractionDigits:2})} · {preview.change_pct>=0?"+":""}{preview.change_pct}%</span></div>
                      <small className="muted">{preview.name} · {preview.sector} · {preview.supported?"curated":"validated via Yahoo Finance"}</small>
                    </>
                  )}
                  {!previewBusy && preview && !preview.ok && <small className="down">{preview.message}</small>}
                </div>
              )}
              <div className="drawer-stocks">{active.symbols.map(s=><div className="drawer-stock" key={s}><span><b>{s}</b><small>XNSE · live quote</small></span><button data-testid={`remove-stock-${s}-button`} className="icon-btn danger" onClick={()=>remove(s)} title={`Remove ${s}`}><X size={15}/></button></div>)}</div>
            </div>
          )}
        </aside>
      </div>
    )}
  </>);
}
function WatchlistPicker(){
  const { lists, activeId, setActiveId, refresh } = useWatchlists();
  if(lists.length===0) return null;
  return <select data-testid="watchlist-picker" className="watchlist-picker" value={activeId||""} onChange={e=>{setActiveId(e.target.value); refresh();}} aria-label="Active watchlist">
    {lists.map(l=><option key={l.watchlist_id} value={l.watchlist_id}>{`${l.name} · ${l.symbols.length}`}</option>)}
  </select>;
}

function Layout({children,user,onLogout}){const [open,setOpen]=useState(false),navigate=useNavigate();return <div className="app-shell"><aside className={open?"sidebar open":"sidebar"}><div className="brand"><span className="logo-mark">W</span><span>WATCHIT<span className="green">!</span></span></div><nav><NavLink data-testid="nav-dashboard" to="/" onClick={()=>setOpen(false)}><Home size={18}/>Overview</NavLink><NavLink data-testid="nav-insights" to="/insights" onClick={()=>setOpen(false)}><LineChart size={18}/>Insights</NavLink><NavLink data-testid="nav-compare" to="/compare" onClick={()=>setOpen(false)}><Sparkles size={18}/>Compare</NavLink><NavLink data-testid="nav-settings" to="/settings" onClick={()=>setOpen(false)}><Settings size={18}/>Settings</NavLink></nav><div className="sidebar-bottom"><div className="profile"><CircleUserRound size={32}/><div><strong data-testid="sidebar-user-name">{user.name}</strong><small>Indian equities</small></div></div><button data-testid="logout-button" className="icon-btn" onClick={onLogout} title="Log out"><LogOut size={17}/></button></div></aside><div className="main"><header className="topbar"><button data-testid="mobile-menu-button" className="icon-btn mobile-menu" onClick={()=>setOpen(!open)}><Menu size={20}/></button><div className="top-search"><Search size={17}/><input data-testid="global-search-input" placeholder="Search stocks, sectors…" onKeyDown={e=>e.key==="Enter"&&navigate(`/stock/${e.target.value.toUpperCase()}`)}/></div><div className="top-actions"><WatchlistPicker/><WatchlistPanel/><button data-testid="notifications-button" className="icon-btn" onClick={()=>navigate("/settings")} title="Alerts"><Bell size={18}/></button></div></header>{children}</div></div>}

function Stat({label,value,detail,positive=true}){return <div className="stat"><span className="muted">{label}</span><strong data-testid={`stat-${label.toLowerCase().replaceAll(" ","-")}`}>{value}</strong><small className={positive?"up":"down"}>{detail}</small></div>}

function Sparkline({points, negative=false, height=42}){
  const pts = Array.isArray(points) && points.length>=2 ? points : null;
  if(!pts){
    return <svg className="sparkline" viewBox="0 0 120 42" preserveAspectRatio="none"><polyline fill="none" stroke={negative?"#FF5B5B":"#00D09C"} strokeWidth="2.5" points={negative?"0,12 15,18 28,15 42,28 59,22 75,33 93,27 120,37":"0,34 15,28 30,30 45,17 61,23 78,10 95,14 120,4"}/></svg>;
  }
  const min=Math.min(...pts), max=Math.max(...pts), range=max-min || 1;
  const step = 120/(pts.length-1);
  const coords = pts.map((v,i)=>`${(i*step).toFixed(2)},${(38-((v-min)/range)*36).toFixed(2)}`).join(" ");
  const isNeg = pts[pts.length-1] < pts[0];
  const color = isNeg?"#FF5B5B":"#00D09C";
  return <svg className="sparkline" viewBox="0 0 120 42" preserveAspectRatio="none"><polyline fill="none" stroke={color} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" points={coords}/></svg>;
}

function MarketPulse({pulse}){
  if(!pulse||!pulse.indices) return null;
  return <section className="market-pulse" data-testid="market-pulse">
    <div className="pulse-head"><span className="live-dot"/><strong>Market pulse</strong><span className="muted">{pulse.mood}</span></div>
    <div className="pulse-grid">
      {pulse.indices.map(i=><div className={"pulse-card "+(i.change_pct>=0?"up-bg":"down-bg")} data-testid={`pulse-${i.code}`} key={i.code}>
        <div className="pulse-top"><span className="muted">{i.label}</span><strong className={i.change_pct>=0?"up":"down"}>{i.status==='live'?`${i.change_pct>=0?"+":""}${i.change_pct}%`:"—"}</strong></div>
        <div className="pulse-value">{i.status==='live'?i.value.toLocaleString("en-IN",{maximumFractionDigits:2}):"unavailable"}</div>
        <Sparkline points={i.sparkline} negative={i.change_pct<0}/>
      </div>)}
    </div>
  </section>;
}
function Dashboard(){
  const { activeId, version, lists } = useWatchlists();
  const [data,setData]=useState(null);
  useEffect(()=>{
    if(!activeId){ setData(null); return; }
    setData(null);
    const url = activeId ? `/dashboard?watchlist_id=${encodeURIComponent(activeId)}` : "/dashboard";
    get(url).then(setData).catch(e=>toast.error(e.message));
  },[activeId, version]);
  if(!data)return <div className="loading" data-testid="dashboard-loading">Loading your market memory…</div>;
  const stocks=data.stocks;
  const activeName=data.active_watchlist?.name || "your list";
  return <main className="content"><div className="page-heading"><div><p className="eyebrow">{data.last_visit} · {activeName}{data.active_watchlist?.is_primary?" · PRIMARY":""}</p><h1 data-testid="dashboard-heading">Since your last visit</h1><p className="muted">Good morning, {data.user.name.split(" ")[0]}. Here’s what deserves your attention.</p></div></div>
    <MarketPulse pulse={data.market_pulse}/>
    <div className="dashboard-grid"><section className="attention-section"><div className="section-title"><div><h2>Stocks requiring attention</h2><p className="muted">Prioritized by what changed meaningfully.</p></div><button data-testid="view-all-stocks-button" className="link-btn">View all <ChevronRight size={15}/></button></div><div className="stock-list">{stocks.slice(0,8).map(s=><StockRow key={s.symbol} stock={s}/>)}</div></section><aside className="summary-card"><div className="summary-top"><div className="spark-icon"><Sparkles size={17}/></div><span className="eyebrow">WATCHIT INTELLIGENCE</span></div><h3 data-testid="ai-summary-heading">The 30-second summary</h3><p data-testid="ai-summary-text">{data.summary}</p><div className="summary-foot"><span>{data.stats.tracked} stocks · {data.stats.gainers} gainers · {data.stats.attention} above 70 attention</span></div></aside></div><section className="lower-grid"><div className="section-block"><div className="section-title"><div><h2>Recent changes</h2><p className="muted">The moments that moved your list.</p></div></div><div className="changes">{data.changes.map((c,i)=><div className="change" key={c.symbol+i}><span className={c.direction==="down"?"change-icon red":"change-icon"}>{c.direction==="down"?<TrendingDown size={16}/>:<TrendingUp size={16}/>}</span><div><strong data-testid={`recent-change-${c.symbol}`}>{c.symbol} <span className="muted">{c.text}</span></strong></div></div>)}</div></div><div className="section-block"><div className="section-title"><div><h2>Quick statistics</h2><p className="muted">Your watchlist at a glance.</p></div></div><div className="stats-grid"><Stat label="Tracked stocks" value={data.stats.tracked} detail={`Across ${data.stats.sectors} sector${data.stats.sectors===1?"":"s"}`}/><Stat label="Need attention" value={data.stats.attention} detail="Score above 70"/><Stat label="Gainers" value={data.stats.gainers} detail="Today" positive={data.stats.gainers>0}/><Stat label="Watchlists" value={lists.length} detail={`${data.market_pulse.live_count}/${data.market_pulse.tracked} live`}/></div></div></section></main>;
}
function StockRow({stock}){const navigate=useNavigate();return <button data-testid={`stock-row-${stock.symbol}`} className="stock-row" onClick={()=>navigate(`/stock/${stock.symbol}`)}><span className="stock-avatar">{stock.symbol.slice(0,1)}</span><span className="stock-name"><strong>{stock.symbol}</strong><small>{stock.name}</small></span><Sparkline points={stock.sparkline} negative={stock.change<0}/><span className="stock-price"><strong>₹{stock.price.toLocaleString("en-IN",{maximumFractionDigits:2})}</strong><small className={stock.change>0?"up":"down"}>{stock.change>0?"+":""}{stock.change}%</small>{stock.status==="stale"&&<small data-testid={`stale-quote-${stock.symbol}`} className="stale-badge">STALE · last cached quote</small>}{stock.status==="unavailable"&&<small data-testid={`unavailable-quote-${stock.symbol}`} className="stale-badge">Quote unavailable</small>}</span><span className="score"><b>{stock.attention}</b><small>attention</small></span><ChevronRight size={17} className="row-arrow"/></button>}
function ChartFromCandles({candles, negative=false}){
  if(!candles||candles.length<2){return <svg className="large-chart" viewBox="0 0 720 230"/>;}
  const cs = candles.map(c=>c.c);
  const min=Math.min(...cs), max=Math.max(...cs), range=max-min||1;
  const step = 720/(cs.length-1);
  const coords = cs.map((v,i)=>`${(i*step).toFixed(2)},${(220-((v-min)/range)*200-10).toFixed(2)}`);
  const path = "M "+coords.join(" L ")+" L 720,230 L 0,230 Z";
  return <svg className="large-chart" viewBox="0 0 720 230" preserveAspectRatio="none">
    <defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={negative?"#FF5B5B":"#00D09C"} stopOpacity=".28"/><stop offset="1" stopColor={negative?"#FF5B5B":"#00D09C"} stopOpacity="0"/></linearGradient></defs>
    <path d={path} fill="url(#fill)"/>
    <polyline points={coords.join(" ")} fill="none" stroke={negative?"#FF5B5B":"#00D09C"} strokeWidth="2.6" strokeLinejoin="round" strokeLinecap="round"/>
  </svg>;
}

function StockDetail(){const {symbol}=useParams(),[stock,setStock]=useState(null),[err,setErr]=useState(null);useEffect(()=>{setStock(null);setErr(null);get(`/stocks/${symbol}`).then(setStock).catch(e=>{setErr(e.message);toast.error(e.message)})},[symbol]);if(err)return <main className="content"><button data-testid="back-dashboard-button" className="back-btn" onClick={()=>window.history.back()}>← Back to overview</button><div className="loading" data-testid="stock-detail-error">{err}</div></main>;if(!stock)return <div className="loading" data-testid="stock-detail-loading">Loading stock detail…</div>;const snap=stock.snapshot;return <main className="content"><button data-testid="back-dashboard-button" className="back-btn" onClick={()=>window.history.back()}>← Back to overview</button><div className="detail-heading"><div><p className="eyebrow">{stock.sector} · NSE</p><h1 data-testid="stock-detail-symbol">{stock.symbol}</h1><p className="muted">{stock.name}</p></div><div className="detail-price"><strong>₹{stock.price.toLocaleString("en-IN")}</strong><span className={stock.change>=0?"up":"down"}>{stock.change>=0?"+":""}{stock.change}% today</span></div></div>
    {snap && <section className="snapshot-card" data-testid="snapshot-card"><div className="snapshot-head"><span className="eyebrow">SNAPSHOT COMPARISON</span><small className="muted">{snap.captured_label}</small></div><div className="snapshot-grid"><div><small className="muted">Then</small><strong>₹{Number(snap.prev_close).toLocaleString("en-IN",{maximumFractionDigits:2})}</strong><small className="muted">attention {snap.prev_attention}</small></div><div className="snap-arrow" data-testid="snapshot-arrow">{snap.direction==="up"?<TrendingUp size={22}/>:<TrendingDown size={22}/>}</div><div><small className="muted">Now</small><strong>₹{Number(snap.current_close).toLocaleString("en-IN",{maximumFractionDigits:2})}</strong><small className="muted">attention {snap.current_attention}</small></div><div className="snap-delta"><small className="muted">Δ</small><strong data-testid="snapshot-delta" className={snap.price_delta_pct>=0?"up":"down"}>{snap.price_delta_pct>=0?"+":""}{snap.price_delta_pct}%</strong><small className="muted">{snap.attention_delta>=0?"+":""}{snap.attention_delta} attention</small></div></div></section>}
    <div className="detail-grid"><section className="chart-card"><div className="section-title"><h2>Price movement</h2><div className="range-tabs"><button data-testid="chart-range-1d">1D</button><button data-testid="chart-range-1w">1W</button><button data-testid="chart-range-1m" className="active">3M</button></div></div><ChartFromCandles candles={stock.candles||[]} negative={stock.change<0}/></section><aside className="why-card"><div className="attention-number"><span>Attention score</span><strong data-testid="attention-score">{stock.attention}</strong><small>/ 100</small></div><h3>Why this stock matters today</h3>{stock.reasons.map(r=><p data-testid="attention-reason" key={r}><span>✓</span>{r}</p>)}</aside></div><div className="news-block"><div className="section-title"><div><h2>Latest context</h2><p className="muted">News that helps explain the move.</p></div></div>{stock.news.map((n,i)=><a className="news-row" data-testid={`news-item-${i}`} key={n.title+i} href={n.url||undefined} target={n.url?"_blank":undefined} rel="noopener noreferrer" style={{textDecoration:"none",color:"inherit"}}><span className="news-num">0{i+1}</span><div><strong>{n.title}</strong>{n.summary&&<p className="muted" style={{margin:"4px 0 0",fontSize:13,lineHeight:1.4}}>{n.summary}</p>}<small>{n.source} · {n.time}</small></div><ChevronRight size={16}/></a>)}</div></main>}
function Insights(){const [data,setData]=useState(null);const navigate=useNavigate();useEffect(()=>{get("/insights").then(setData).catch(e=>toast.error(e.message))},[]);if(!data)return <div className="loading">Loading insights…</div>;return <main className="content"><div className="page-heading"><div><p className="eyebrow">SIGNAL BOARD</p><h1 data-testid="insights-heading">Insights</h1><p className="muted">A calmer way to see where attention is clustering.</p></div></div><div className="insight-layout"><section><h2>Most important today</h2><div className="stock-list">{data.important.map(s=><StockRow key={s.symbol} stock={s}/>)}</div></section><aside className="health-card"><span className="eyebrow">WATCHLIST HEALTH</span><strong data-testid="watchlist-health-score">{data.health}</strong><p className="muted">Health reflects how many stocks on your list are demanding attention today.</p><div className="health-bar"><i style={{width:`${data.health}%`}}/></div><small>{data.important.length} of {data.important.length+(data.gainers?.length||0)+(data.losers?.length||0)-data.important.length} names above the attention threshold</small></aside></div>
    <section className="lower-grid" style={{marginTop:56}}>
      <div className="section-block"><div className="section-title"><div><h2>Top gainers</h2><p className="muted">Leaders in your watchlist today.</p></div><TrendingUp size={17} className="green"/></div><div className="stock-list" data-testid="top-gainers">{(data.gainers||[]).map(s=><StockRow key={s.symbol} stock={s}/>)}{(data.gainers||[]).length===0 && <p className="muted" data-testid="gainers-empty">No positive movers in your watchlist today.</p>}</div></div>
      <div className="section-block"><div className="section-title"><div><h2>Top losers</h2><p className="muted">Names pulling back the hardest.</p></div><TrendingDown size={17} style={{color:"var(--red)"}}/></div><div className="stock-list" data-testid="top-losers">{(data.losers||[]).map(s=><StockRow key={s.symbol} stock={s}/>)}{(data.losers||[]).length===0 && <p className="muted" data-testid="losers-empty">No decliners in your watchlist today.</p>}</div></div>
    </section>
    <section className="sector-section"><div className="section-title"><div><h2>Sector overview</h2><p className="muted">Tap a sector for the full breakdown.</p></div></div><div className="sector-grid">{data.sectors.map(s=><button className="sector" data-testid={`sector-${s.name.toLowerCase()}`} key={s.name} onClick={()=>navigate(`/sector/${encodeURIComponent(s.name)}`)} style={{cursor:"pointer",textAlign:"left"}}><span>{s.name}</span><strong className={s.change[0]==="-"?"down":"up"}>{s.change}</strong><Sparkline negative={s.change[0]==="-"}/></button>)}</div></section></main>}

function SectorDetail(){const {name}=useParams();const [data,setData]=useState(null);const [err,setErr]=useState(null);useEffect(()=>{setData(null);setErr(null);get(`/sectors/${encodeURIComponent(name)}`).then(setData).catch(e=>setErr(e.message))},[name]);if(err)return <main className="content"><button data-testid="back-sector-button" className="back-btn" onClick={()=>window.history.back()}>← Back</button><div className="loading" data-testid="sector-empty">{err}</div></main>;if(!data)return <div className="loading" data-testid="sector-loading">Loading sector…</div>;return <main className="content"><button data-testid="back-sector-button" className="back-btn" onClick={()=>window.history.back()}>← Back to insights</button><div className="detail-heading"><div><p className="eyebrow">SECTOR DEEP DIVE</p><h1 data-testid="sector-heading">{data.name}</h1><p className="muted">{data.total} constituent{data.total===1?"":"s"} on your watchlist · top attention {data.top_attention}</p></div><div className="detail-price"><strong data-testid="sector-avg-change" className={data.avg_change>=0?"up":"down"}>{data.avg_change>=0?"+":""}{data.avg_change}%</strong><span className="muted">average change today</span></div></div><div className="insight-layout"><section><h2>Leaders</h2><div className="stock-list" data-testid="sector-leaders">{data.leaders.map(s=><StockRow key={s.symbol} stock={s}/>)}</div></section><section><h2>Laggards</h2><div className="stock-list" data-testid="sector-laggards">{data.laggards.map(s=><StockRow key={s.symbol} stock={s}/>)}</div></section></div><section className="section-block" style={{marginTop:24}}><div className="section-title"><div><h2>All constituents</h2><p className="muted">Every stock in this sector from your watchlist.</p></div></div><div className="stock-list" data-testid="sector-constituents">{data.constituents.map(s=><StockRow key={s.symbol} stock={s}/>)}</div></section></main>}
function ComparePage(){
  const { lists } = useWatchlists();
  const [a,setA]=useState(null);
  const [b,setB]=useState(null);
  const [data,setData]=useState(null);
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    if(lists.length>=2){
      setA(cur=>cur||lists[0].watchlist_id);
      setB(cur=>cur||lists[1].watchlist_id);
    }
  },[lists]);
  const run=useCallback(async()=>{
    if(!a||!b||a===b) return;
    setBusy(true); setData(null);
    try{ setData(await get(`/watchlists/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`)); }
    catch(e){toast.error(e.message)} finally{setBusy(false)}
  },[a,b]);
  useEffect(()=>{ run(); },[run]);
  const renderSide = (side, key) => <div className="compare-side" data-testid={`compare-${key}`}>
    <div className="compare-head"><span className="eyebrow">{key.toUpperCase()}</span><h2>{side.name}</h2><p className="muted">{side.aggregates.count} stocks · avg change <strong className={side.aggregates.avg_change>=0?"up":"down"}>{side.aggregates.avg_change>=0?"+":""}{side.aggregates.avg_change}%</strong></p></div>
    <div className="stats-grid"><Stat label="Gainers" value={side.aggregates.gainers} detail="Today" positive={side.aggregates.gainers>=side.aggregates.losers}/><Stat label="Losers" value={side.aggregates.losers} detail="Today" positive={side.aggregates.losers<=side.aggregates.gainers}/><Stat label="Avg attention" value={side.aggregates.avg_attention} detail="0-100"/><Stat label="Sectors" value={Object.keys(side.aggregates.sectors||{}).length} detail="represented"/></div>
    <div className="compare-sector-list">{Object.entries(side.aggregates.sectors||{}).map(([sec,n])=><div className="compare-sector" key={sec}><span>{sec}</span><strong>{n}</strong></div>)}</div>
    <div className="stock-list" data-testid={`compare-stocks-${key}`}>{side.stocks.slice(0,6).map(s=><StockRow key={s.symbol} stock={s}/>)}</div>
  </div>;
  if(lists.length<2) return <main className="content"><div className="page-heading"><div><p className="eyebrow">SIDE BY SIDE</p><h1 data-testid="compare-heading">Compare watchlists</h1><p className="muted" data-testid="compare-need-two">Create at least two watchlists to compare them.</p></div></div></main>;
  return <main className="content"><div className="page-heading"><div><p className="eyebrow">SIDE BY SIDE</p><h1 data-testid="compare-heading">Compare watchlists</h1><p className="muted">Pick two lists to see how their gainers, sectors and attention differ today.</p></div></div>
    <div className="compare-pickers">
      <select data-testid="compare-select-a" value={a||""} onChange={e=>setA(e.target.value)}>{lists.map(l=><option key={l.watchlist_id} value={l.watchlist_id}>{`${l.name} · ${l.symbols.length}`}</option>)}</select>
      <span className="muted">vs</span>
      <select data-testid="compare-select-b" value={b||""} onChange={e=>setB(e.target.value)}>{lists.map(l=><option key={l.watchlist_id} value={l.watchlist_id}>{`${l.name} · ${l.symbols.length}`}</option>)}</select>
    </div>
    {a===b && <p className="muted" data-testid="compare-same">Pick two different watchlists.</p>}
    {busy && <p className="muted">Comparing…</p>}
    {data && <>
      <div className="compare-grid">{renderSide(data.a,"a")}{renderSide(data.b,"b")}</div>
      {data.overlap.length>0 && <p className="muted" data-testid="compare-overlap" style={{marginTop:24}}>Shared symbols: <strong>{data.overlap.join(", ")}</strong></p>}
    </>}
  </main>;
}

function AlertsSection({user}){
  const [alerts,setAlerts]=useState([]);
  const [symbol,setSymbol]=useState("");
  const [threshold,setThreshold]=useState(70);
  const [email,setEmail]=useState(user.email);
  const [busy,setBusy]=useState(false);
  const load=async()=>{try{setAlerts(await get("/alerts"));}catch(e){toast.error(e.message)}};
  useEffect(()=>{load()},[]);
  const create=async()=>{
    if(!symbol.trim()){toast.error("Enter a symbol");return;}
    setBusy(true);
    try{ await post("/alerts",{symbol:symbol.trim().toUpperCase(),threshold:Number(threshold),email}); setSymbol(""); await load(); toast.success("Alert saved"); }
    catch(e){toast.error(e.message)} finally{setBusy(false)}
  };
  const toggle=async(a)=>{ try{ await mutate(`/alerts/${a.alert_id}`,"PATCH",{active:!a.active}); await load(); }catch(e){toast.error(e.message)} };
  const remove=async(a)=>{ try{ await mutate(`/alerts/${a.alert_id}`,"DELETE"); await load(); toast.success("Alert removed"); }catch(e){toast.error(e.message)} };
  return <section className="alerts-section" data-testid="alerts-section">
    <div className="section-title" style={{marginTop:24}}><div><h2>Attention alerts</h2><p className="muted">Get a quiet email when a stock crosses your threshold.</p></div><span className="live-dot" style={{marginLeft:"auto"}}/></div>
    <div className="alert-form">
      <input data-testid="alert-symbol-input" value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} placeholder="Symbol e.g. HAL"/>
      <input data-testid="alert-threshold-input" type="number" min="0" max="100" value={threshold} onChange={e=>setThreshold(e.target.value)} placeholder="Threshold"/>
      <input data-testid="alert-email-input" type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="Email"/>
      <button data-testid="alert-create-button" className="primary-btn small" onClick={create} disabled={busy}>{busy?"Saving…":"Add alert"}</button>
    </div>
    <div className="alert-list" data-testid="alert-list">
      {alerts.length===0 && <p className="muted" data-testid="alerts-empty">No alerts yet. Add one above to be pinged when attention spikes.</p>}
      {alerts.map(a=><div className="alert-row" data-testid={`alert-row-${a.alert_id}`} key={a.alert_id}>
        <span className="alert-icon">{a.active?<BellRing size={16}/>:<Bell size={16}/>}</span>
        <div className="alert-body">
          <strong>{a.symbol} <span className="muted">above {a.threshold}</span></strong>
          <small>{a.email} · {a.last_triggered_at?`last fired ${new Date(a.last_triggered_at).toLocaleString()}`:"waiting for the first trigger"} · {a.trigger_count} sent</small>
        </div>
        <button data-testid={`alert-toggle-${a.alert_id}`} className="drawer-link" onClick={()=>toggle(a)}>{a.active?"Pause":"Resume"}</button>
        <button data-testid={`alert-delete-${a.alert_id}`} className="drawer-link danger" onClick={()=>remove(a)}>Remove</button>
      </div>)}
    </div>
  </section>;
}

function SettingsPage({user,onLogout}){
  const [digestBusy,setDigestBusy]=useState(false);
  const previewDigest=async()=>{
    setDigestBusy(true);
    try{
      const r = await post("/digest/preview",{});
      if(r.ok) toast.success("Weekly digest sent to your inbox");
      else toast.error(r.error||"Digest could not be sent");
    }catch(e){toast.error(e.message)} finally{setDigestBusy(false)}
  };
  return <main className="content"><div className="page-heading"><div><p className="eyebrow">PREFERENCES</p><h1 data-testid="settings-heading">Settings</h1><p className="muted">Make WATCHIT! feel right for the way you invest.</p></div></div><section className="settings-list"><div className="setting-row"><div><strong>Account</strong><p className="muted" data-testid="settings-account-email">{user.email}</p></div><span className="setting-value" data-testid="settings-account-name">{user.name}</span></div><div className="setting-row"><div><strong>Theme</strong><p className="muted">Your interface appearance</p></div><select data-testid="theme-select" defaultValue="dark"><option value="dark">Dark premium</option><option value="light">Light</option></select></div><div className="setting-row"><div><strong>Minimum attention threshold</strong><p className="muted">Only surface stocks above this score</p></div><input data-testid="attention-threshold-input" type="range" min="0" max="100" defaultValue="70"/></div><div className="setting-row"><div><strong>Weekly digest</strong><p className="muted">A calm Monday recap of your watchlists — delivered by email.</p></div><button data-testid="preview-digest-button" className="secondary-btn" onClick={previewDigest} disabled={digestBusy}>{digestBusy?"Sending…":"Send test digest"}</button></div></section><AlertsSection user={user}/><button data-testid="settings-logout-button" className="secondary-btn logout-wide" onClick={onLogout}>Log out</button></main>;
}
function App(){const [user,setUser]=useState(null),[checking,setChecking]=useState(true),loc=useLocation(),nav=useNavigate();useEffect(()=>{const session=new URLSearchParams(loc.hash.replace("#",""));if(session.get("session_id")){post(`/auth/emergent-session?session_id=${encodeURIComponent(session.get("session_id"))}`,{}).then(u=>{setUser(u);window.history.replaceState({},"",loc.pathname)}).catch(()=>toast.error("Google sign-in could not be completed")).finally(()=>setChecking(false));return}get("/auth/me").then(setUser).catch(()=>setUser(null)).finally(()=>setChecking(false))},[loc.hash,loc.pathname]);if(checking)return <div className="loading">Opening your market memory…</div>;if(!user)return <Auth onLogin={setUser}/>;const logout=async()=>{await post("/auth/logout",{});setUser(null);nav("/")};return <WatchlistProvider><Layout user={user} onLogout={logout}><Routes><Route path="/" element={<Dashboard/>}/><Route path="/stock/:symbol" element={<StockDetail/>}/><Route path="/insights" element={<Insights/>}/><Route path="/compare" element={<ComparePage/>}/><Route path="/sector/:name" element={<SectorDetail/>}/><Route path="/settings" element={<SettingsPage user={user} onLogout={logout}/>}/></Routes></Layout></WatchlistProvider>}
export default function Root(){return <><BrowserRouter><App/></BrowserRouter><Toaster theme="dark" position="bottom-right"/></>}