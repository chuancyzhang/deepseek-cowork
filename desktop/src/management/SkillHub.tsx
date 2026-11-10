import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Download, ExternalLink, LoaderCircle, RefreshCw, Search, Sparkles, Star } from 'lucide-react';
import { Empty, ErrorState, JobProgress, PageHead } from '../Management';
import { operation, rpc } from '../managementApi';

export default function SkillHub({back, mine, onError}: {back: () => void; mine: () => void; onError: (message: string) => void}) {
  const [query, setQuery] = useState(''), [category, setCategory] = useState(''), [sort, setSort] = useState('score'), [page, setPage] = useState(1);
  const [catalog, setCatalog] = useState<any>(null), [slug, setSlug] = useState(''), [detail, setDetail] = useState<any>(null), [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false), [failure, setFailure] = useState(''), [version, setVersion] = useState(''), [explicit, setExplicit] = useState(false);
  const [job, setJob] = useState<any>(null), [installing, setInstalling] = useState(false), [installed, setInstalled] = useState<Record<string, any>>({}), [message, setMessage] = useState('');
  const generation = useRef(0);
  useEffect(() => {
    const token = ++generation.current;
    setLoading(true); setFailure(''); setMessage('');
    const timer = setTimeout(() => {
      void operation(slug ? 'skillhub.detail' : 'skillhub.search', {slug, keyword: query, category, sort, page}).then(data => {
        if (token !== generation.current) return;
        setInstalled(data.installed);
        if (slug) {setDetail(data); setVersion(data.latestVersion?.version || ''); setExplicit(false);}
        else setCatalog(data);
      }).catch(error => {if (token === generation.current) setFailure(error.message);}).finally(() => {if (token === generation.current) setLoading(false);});
    }, query && !slug ? 300 : 0);
    return () => {clearTimeout(timer); generation.current++;};
  }, [slug, query, category, sort, page, revision]);
  async function refresh() {try {await rpc('skillhub.refresh'); setRevision(value => value + 1);} catch (error) {setFailure((error as Error).message);}}
  async function install(target: string, selected: string, explicitVersion = false) {
    setInstalling(true); setFailure(''); setMessage('');
    try {const result = await operation('skillhub.install', {slug: target, version: selected, explicit_version: explicitVersion}, setJob); setInstalled(result.installed); setMessage(result.message);}
    catch (error) {setFailure((error as Error).message);} finally {setInstalling(false); setJob(null);}
  }
  const versions = detail ? [detail.latestVersion, ...(detail.versions || [])].filter((item, index, all) => item?.version && all.findIndex(other => other?.version === item.version) === index) : [];
  return <>
    <PageHead title={slug && detail ? detail.skill.displayName : 'SkillHub'} back={() => {if (slug) {setSlug(''); setDetail(null);} else back();}}>
      <button className="icon-button" title="刷新 SkillHub" disabled={loading || installing} onClick={() => void refresh()}><RefreshCw size={17}/></button>
      {slug && detail && <button className="primary" disabled={installing || loading || !version || installed[slug]?.version === version} onClick={() => void install(slug, version, explicit)}><Download size={16}/>{installed[slug]?.version === version ? '已安装' : installed[slug] ? '更新' : '安装到我的能力'}</button>}
    </PageHead>
    {!slug && <><div className="page-search"><Search size={17}/><input aria-label="搜索 SkillHub" placeholder="搜索 SkillHub 技能" value={query} onChange={event => {setQuery(event.target.value); setPage(1);}}/></div><div className="skillhub-filters"><div className="skill-categories" aria-label="SkillHub 分类"><button className={!category ? 'selected' : ''} onClick={() => {setCategory(''); setPage(1);}}>全部</button>{catalog?.categories.map((item: any) => <button key={item.key} className={category === item.key ? 'selected' : ''} onClick={() => {setCategory(item.key); setPage(1);}}>{item.name}</button>)}</div><select aria-label="技能排序" value={sort} onChange={event => {setSort(event.target.value); setPage(1);}}><option value="score">推荐</option><option value="downloads">下载热门</option><option value="updated_at">最近更新</option></select></div></>}
    <JobProgress job={job}/>{failure && <ErrorState message={failure} retry={() => void refresh()}/>}{message && <div className="skill-feedback" role="status">{message}<button className="text-link" onClick={mine}>查看我的能力</button></div>}
    {loading && <div className="loading"><LoaderCircle size={19} className="spin"/>正在读取 SkillHub…</div>}
    {!loading && (slug && detail ? <div className="skillhub-detail"><p>{detail.skill.summary_zh || detail.skill.summary}</p><p className="muted">{detail.owner?.handle || '未提供作者'} · 下载 {detail.skill.stats?.downloads || 0} · 收藏 {detail.skill.stats?.stars || 0}</p>{installed[slug] && <p>已安装 {installed[slug].version}</p>}
      <label className="hub-version">版本<select value={version} onChange={event => {setVersion(event.target.value); setExplicit(true);}}>{versions.map(item => <option key={item.version}>{item.version}</option>)}</select></label><p className="muted">{versions.find(item => item.version === version)?.changelog || '此版本暂无更新说明。'}</p>
      {detail.evaluation_error && <ErrorState message={'评估信息暂时无法读取：' + detail.evaluation_error}/>}{detail.evaluation && <details><summary>技能评估</summary><pre>{JSON.stringify(detail.evaluation, null, 2)}</pre></details>}
      <button className="secondary" onClick={() => void rpc('external.open', {url: 'https://skillhub.cn/skills/' + encodeURIComponent(slug)}).catch(error => onError(error.message))}>官网详情与文件<ExternalLink size={14}/></button>
    </div> : catalog && <><div className="capability-grid">{catalog.list.skills.map((item: any) => <article className="resource-card" key={item.slug}><div className="resource-symbol"><Sparkles size={20}/></div><div className="resource-body"><button className="resource-title" onClick={() => {setDetail(null); setSlug(item.slug);}}>{item.name}</button><p>{item.description_zh || item.description}</p><small><Download size={12}/> {item.downloads || 0}　<Star size={12}/> {item.stars || 0}</small></div><div className="resource-actions">{installed[item.slug] && <small>已安装 {installed[item.slug].version}</small>}<button className="secondary" disabled={installing || !item.version} onClick={() => installed[item.slug] ? setSlug(item.slug) : void install(item.slug, item.version)}>{installed[item.slug] ? '查看' : '安装'}</button></div></article>)}</div>{!catalog.list.skills.length && <Empty title="没有匹配的技能" detail="调整关键词或分类后重试。"/>}<div className="hub-pagination"><button className="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页 · 共 {catalog.list.total} 项</span><button className="secondary" disabled={page * 20 >= catalog.list.total} onClick={() => setPage(page + 1)}>下一页</button></div></>)}
  </>;
}
