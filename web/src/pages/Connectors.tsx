// Connectors: the systems this workspace can reach. An admin sets each up once; builders connect accounts through them.
import { useContext, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, when, type Connector } from '../api'
import { Block, Check, Chips, Guarantees, Icon, Segmented, SessionContext } from '../ui'
import { StatusDot } from './ConnectAccount'
import { ConnectionsHeader } from './Connections'

const SERVICE_ICON: Record<string, string> = { gmail: 'mail', 'google-sheets': 'group', 'google-calendar': 'calendar', github: 'code', bigquery: 'database', gcs: 'folder', sharepoint: 'folder', smtp: 'mail', trino: 'database', 'spark-sql': 'database' }

function ConnectorCard({ c, selected, onOpen }: { c: Connector; selected: boolean; onOpen: () => void }) {
  const how = c.type === 'trino' ? ({ none: `As user ${c.settings.user || 'agent-orchestrator'}, no password`, basic: `As user ${c.settings.user || '?'}, with a password`, jwt: 'With a JWT' } as Record<string, string>)[c.settings.auth?.kind ?? 'none']
    : c.type === 'bigquery' || c.type === 'gcs' || c.type === 'dataproc' ? ({ service_account: 'A service account key', gcloud: "The service's gcloud account", adc: "The machine's default credentials" } as Record<string, string>)[c.settings.auth?.kind ?? 'gcloud']
    : c.type === 'google' ? (c.settings.client_id ? 'OAuth client set' : 'No OAuth client yet')
    : c.type === 'github' ? 'A fine-grained token per account'
    : c.type === 'microsoft365' ? [c.settings.client_id ? (c.secrets_set?.client_secret ? 'Entra ID app' : 'Entra ID app, no secret yet') : '', c.settings.smtp?.host ? `SMTP ${c.settings.smtp.host}` : ''].filter(Boolean).join(' · ') || 'Not set up yet'
    : { oauth: 'OAuth: each builder signs in', shared: 'One shared credential', none: 'No sign-in' }[c.sign_in as string] ?? ''
  const tools = c.type === 'mcp' ? `${c.tools?.length ?? 0} tools: ${c.tools?.filter((t) => t.treat === 'read').length} read, ${c.tools?.filter((t) => t.treat === 'act').length} act` : ''
  return (
    <button type="button" className="card pad" onClick={onOpen} aria-label={`Open ${c.name}`}
      style={{ textAlign: 'left', gap: 8, border: selected ? '2px solid var(--acc)' : undefined, boxShadow: selected ? '0 4px 14px rgba(31,62,99,0.10)' : undefined }}>
      <span className="row" style={{ flexWrap: 'nowrap' }}>
        <Icon name={c.icon} size={20} color="var(--muted)" />
        <span className="stack grow" style={{ gap: 1 }}><strong style={{ fontSize: 14 }}>{c.name}</strong><span className="faint">{c.reach}</span></span>
        <StatusDot state={c.status.state} />
      </span>
      <span className="row muted" style={{ gap: 7 }}><Icon name="key" size={13} color="var(--muted)" />{how}{tools && <span className="faint">· {tools}</span>}</span>
      <span className="spread" style={{ borderTop: '1px solid var(--line-2)', paddingTop: 7 }}>
        <span className={c.status.state === 'attention' ? 'field-error' : 'faint'} style={{ fontSize: 11.5 }}>
          {c.status.message}{c.status.tested_at ? ` · ${when(c.status.tested_at)}${c.status.tested_by ? ` by ${c.status.tested_by}` : ''}` : ''}</span>
        <span className="faint" style={{ flex: 'none' }}>{c.accounts} account{c.accounts === 1 ? '' : 's'}</span>
      </span>
    </button>
  )
}

/** A secret that goes to the vault: shows whether one is set; Replace opens a password box. */
function SecretField({ label, set, admin, value, onChange }: { label: string; set: boolean; admin: boolean; value: string | null; onChange: (v: string | null) => void }) {
  return (
    <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 110 }}>{label}</span>
      {value === null
        ? <><input className="input mono grow" disabled value={set ? '•••••••••••• set' : 'not set'} aria-label={label} />
            {admin && <button className="link" onClick={() => onChange('')}>{set ? 'Replace' : 'Add'}</button>}</>
        : <input className="input mono grow" type="password" autoComplete="off" value={value} onChange={(e) => onChange(e.target.value)} aria-label={`New ${label.toLowerCase()}`} />}
    </span>
  )
}

/** Google Workspace and GitHub: app settings, what builders may ask for, who may connect, and a test. */
function BuiltInPanel({ c, admin, onSaved, onRemoved }: { c: Connector; admin: boolean; onSaved: (c: Connector) => void; onRemoved: () => void }) {
  const [clientId, setClientId] = useState(c.settings.client_id ?? '')
  const [apiUrl, setApiUrl] = useState(c.settings.api_url ?? 'https://api.github.com')
  const [bq, setBq] = useState({ auth: c.settings.auth?.kind ?? 'gcloud', billing_project: c.settings.billing_project ?? '', location: c.settings.location ?? 'US',
    allowed: (c.settings.allowed ?? []) as string[], max_bytes_cap: c.settings.max_bytes_cap ?? '10GB', monthly_budget_usd: c.settings.monthly_budget_usd ?? '',
    max_read_bytes: c.settings.max_read_bytes ?? '1GB', share_samples: c.settings.share_samples ?? true })
  const [secret, setSecret] = useState<string | null>(null)
  const [ms, setMs] = useState({ tenant_id: c.settings.tenant_id ?? '', client_id: c.settings.client_id ?? '', hostname: c.settings.hostname ?? '',
    smtp: { host: '', port: '', security: 'starttls', username: '', from_address: '', ...(c.settings.smtp ?? {}) } as Record<string, any> })
  const [eng, setEng] = useState<Record<string, any>>({ server: '', user: '', auth: 'none', catalog: '', schema: '', max_seconds: '',
    project: '', region: 'us-central1', mode: 'serverless', cluster: '', staging: '', service_account: '', subnetwork: '', runtime_version: '',
    ...c.settings, auth_kind: c.settings.auth?.kind ?? 'none',
    properties: Object.entries(c.settings.properties ?? {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    jars: (c.settings.jars ?? []) as string[], setup: ((c.settings.setup ?? []) as string[]).map((x) => x.replace(/;\s*$/, '') + ';').join('\n') })
  const [msSecrets, setMsSecrets] = useState<{ client_secret: string | null; smtp_password: string | null }>({ client_secret: null, smtp_password: null })
  const [offered, setOffered] = useState<Record<string, string[]>>(c.offered ?? {})
  const [who, setWho] = useState(c.who)
  const [domains, setDomains] = useState<string[]>(c.domains ?? [])
  const [types, setTypes] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.connectorTypes().then(setTypes) }, [])
  const redirect = `${window.location.origin}/`
  const save = async () => {
    setBusy('save'); setError(null)
    try {
      const settings = c.type === 'google' ? { client_id: clientId.trim(), client_kind: c.settings.client_kind ?? 'web' }
        : c.type === 'gcs' ? { auth: { kind: bq.auth }, allowed: bq.allowed, max_read_bytes: bq.max_read_bytes, share_samples: bq.share_samples }
        : c.type === 'trino' ? { server: String(eng.server).trim(), user: String(eng.user).trim(), auth: { kind: eng.auth_kind }, catalog: String(eng.catalog).trim(),
            schema: String(eng.schema).trim(), allowed: bq.allowed, max_seconds: eng.max_seconds === '' || eng.max_seconds == null || Number(eng.max_seconds) <= 0 ? null : Number(eng.max_seconds) }
        : c.type === 'dataproc' ? { auth: { kind: bq.auth }, project: String(eng.project).trim(), region: String(eng.region).trim(), mode: eng.mode,
            cluster: String(eng.cluster).trim(), staging: String(eng.staging).trim(), service_account: String(eng.service_account).trim(),
            subnetwork: String(eng.subnetwork).trim(), runtime_version: String(eng.runtime_version).trim(), allowed: bq.allowed,
            max_seconds: eng.max_seconds === '' || eng.max_seconds == null || Number(eng.max_seconds) <= 0 ? null : Number(eng.max_seconds), jars: eng.jars,
            setup: String(eng.setup).split(/;\s*(?:\n|$)/).map((x) => x.trim()).filter(Boolean),
            properties: Object.fromEntries(String(eng.properties).split('\n').map((l) => l.trim()).filter((l) => l.includes('=')).map((l) => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])) }
        : c.type === 'microsoft365' ? { tenant_id: ms.tenant_id.trim(), client_id: ms.client_id.trim(), hostname: ms.hostname.trim(), allowed: bq.allowed,
            max_read_bytes: bq.max_read_bytes, share_samples: bq.share_samples,
            smtp: { ...ms.smtp, host: String(ms.smtp.host ?? '').trim(), port: ms.smtp.port === '' ? null : Number(ms.smtp.port),
              username: String(ms.smtp.username ?? '').trim(), from_address: String(ms.smtp.from_address ?? '').trim() } }
        : c.type === 'bigquery' ? { auth: { kind: bq.auth }, billing_project: bq.billing_project.trim(), location: bq.location.trim(), allowed: bq.allowed,
            max_bytes_cap: bq.max_bytes_cap, monthly_budget_usd: bq.monthly_budget_usd === '' ? null : Number(bq.monthly_budget_usd), share_samples: bq.share_samples }
        : { api_url: apiUrl.trim() }
      const changedSecrets = Object.fromEntries(Object.entries(msSecrets).filter(([, v]) => v !== null))
      const out = await api.editConnector(c.id, { settings,
        secret: c.type === 'microsoft365' ? (Object.keys(changedSecrets).length ? JSON.stringify(changedSecrets) : null) : secret, offered, who, domains })
      setSecret(null); setMsSecrets({ client_secret: null, smtp_password: null }); onSaved(out); return out
    } catch (e: any) { setError(e.message) } finally { setBusy(null) }
  }
  const remove = async () => {
    if (!confirm(`Remove ${c.name}? ${c.accounts ? `Remove its ${c.accounts} account${c.accounts === 1 ? '' : 's'} on the Accounts tab first.` : 'Its secrets are deleted from the vault.'}`)) return
    setBusy('remove'); setError(null)
    try { await api.removeConnector(c.id); onRemoved() } catch (e: any) { setError(e.message) } finally { setBusy(null) }
  }
  const test = async () => {
    if (admin && !(await save())) return
    setBusy('test')
    try { onSaved(await api.testConnector(c.id)) } catch (e: any) { setError(e.message) } finally { setBusy(null) }
  }
  const services = types[c.type]?.services ?? {}
  return (
    <div className="card pad" style={{ gap: 12, position: 'sticky', top: 16 }}>
      <div className="stack" style={{ gap: 2 }}><span className="eyebrow">Connector</span><span style={{ fontSize: 17, fontWeight: 700 }}>{c.name}</span></div>
      {!admin && <div className="notice info"><Icon name="lock" size={15} /><span>Only a workspace admin can change connectors.</span></div>}
      {c.type === 'google' && (
        <Block title="OAuth client">
          <label className="stack" style={{ gap: 3 }}><span className="muted">Client ID</span>
            <input className="input mono" value={clientId} disabled={!admin} onChange={(e) => setClientId(e.target.value)} placeholder="…apps.googleusercontent.com" aria-label="Client ID" /></label>
          <label className="stack" style={{ gap: 3 }}><span className="muted">Client secret</span>
            {secret === null
              ? <span className="row" style={{ flexWrap: 'nowrap' }}><input className="input mono grow" disabled value={c.secret_set ? `•••••••••••• set${c.secret_set_at ? ' ' + when(c.secret_set_at) : ''}` : 'not set'} aria-label="Client secret" />
                  {admin && <button className="link" onClick={() => setSecret('')}>{c.secret_set ? 'Replace' : 'Add'}</button>}</span>
              : <input className="input mono" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="GOCSPX-…" aria-label="New client secret" />}
          </label>
          <label className="stack" style={{ gap: 3 }}><span className="muted">Redirect URI: add it to the client in Google Cloud</span>
            <span className="row" style={{ flexWrap: 'nowrap' }}><input className="input mono grow" readOnly value={redirect} aria-label="Redirect URI" />
              <button className="link" onClick={() => navigator.clipboard?.writeText(redirect)}>Copy</button></span></label>
          <span className="faint">Google Cloud Console → Credentials → OAuth client ID (Web application), with the Gmail API enabled. The secret goes to the vault; nobody can read it back.</span>
        </Block>
      )}
      {c.type === 'trino' && (
        <>
          <Block title="Server">
            <span className="row"><span className="muted" style={{ width: 110 }}>Address</span>
              <input className="input mono grow" value={eng.server} disabled={!admin} placeholder="http://etl-cluster-m:8060" aria-label="Trino server" onChange={(e) => setEng({ ...eng, server: e.target.value })} /></span>
            <span className="faint">On Dataproc, the Trino component listens on the master node, port 8060. The service must be able to reach it: on the same VPC
              (GKE in the cluster's network), or through an SSH tunnel on a laptop.</span>
            <span className="row"><span className="muted" style={{ width: 110 }}>User</span>
              <input className="input mono" style={{ width: 200 }} value={eng.user} disabled={!admin} placeholder="agent-orchestrator" aria-label="Trino user" onChange={(e) => setEng({ ...eng, user: e.target.value })} /></span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Signs in with</span>
              <Segmented options={['none', 'basic', 'jwt']} value={eng.auth_kind} onChange={(v) => admin && setEng({ ...eng, auth_kind: v })}
                labels={{ none: 'Nothing (user name only)', basic: 'Password (HTTPS)', jwt: 'JWT' }} /></span>
            {eng.auth_kind !== 'none' && <SecretField label={eng.auth_kind === 'jwt' ? 'Token' : 'Password'} set={c.secret_set} admin={admin} value={secret} onChange={setSecret} />}
          </Block>
          <Block title="Data">
            <span className="row"><span className="muted" style={{ width: 110 }}>Default catalog</span>
              <input className="input mono" style={{ width: 140 }} value={eng.catalog} disabled={!admin} placeholder="iceberg" aria-label="Default catalog" onChange={(e) => setEng({ ...eng, catalog: e.target.value })} />
              <span className="muted">schema</span>
              <input className="input mono" style={{ width: 140 }} value={eng.schema} disabled={!admin} placeholder="sales" aria-label="Default schema" onChange={(e) => setEng({ ...eng, schema: e.target.value })} /></span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Agents may read</span><Chips values={bq.allowed} onChange={(v) => admin && setBq({ ...bq, allowed: v })} placeholder="catalog, catalog.schema or catalog.schema.table" /></span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Time limit</span><input className="input mono" style={{ width: 80 }} value={eng.max_seconds ?? ''} disabled={!admin} placeholder="120" aria-label="Time limit" onChange={(e) => setEng({ ...eng, max_seconds: e.target.value })} />
              <span className="faint">seconds per query; Trino stops it after that.</span></span>
          </Block>
        </>
      )}
      {(c.type === 'bigquery' || c.type === 'gcs' || c.type === 'dataproc') && (
        <>
          <Block title="How it signs in">
            {(c.type === 'dataproc' ? [['service_account', 'A service account key', 'Recommended: grant it Dataproc Editor on the project (or a custom role to submit jobs and batches), and Storage Object Admin on the staging folder.'],
               ['gcloud', "The service's gcloud account", 'Whoever is signed in to gcloud where the service runs. Handy on a laptop.'],
               ['adc', "The machine's default credentials", 'Application default credentials, e.g. Workload Identity on GKE.']] as const : c.type === 'gcs' ? [['service_account', 'A service account key', 'Recommended: grant it Storage Object Viewer on the buckets (and Storage Object Creator where agents write).'],
               ['gcloud', "The service's gcloud account", 'Whoever is signed in to gcloud where the service runs. Handy on a laptop.'],
               ['adc', "The machine's default credentials", 'Application default credentials, e.g. on Google Cloud.']] as const : [['service_account', 'A service account key', 'Recommended: grant it BigQuery Data Viewer on the datasets and BigQuery Job User on the billing project.'],
               ['gcloud', "The service's gcloud account", 'Whoever is signed in to gcloud where the service runs. Handy on a laptop.'],
               ['adc', "The machine's default credentials", 'Application default credentials, e.g. on Google Cloud.']] as const).map(([k, label, detail]) => (
              <label key={k} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap', gap: 8 }}>
                <input type="radio" name="bq-auth" checked={bq.auth === k} disabled={!admin} onChange={() => setBq({ ...bq, auth: k })} style={{ marginTop: 3 }} />
                <span className="stack" style={{ gap: 1 }}><span>{label}</span><span className="faint">{detail}</span></span></label>
            ))}
            {bq.auth === 'service_account' && (secret === null
              ? <span className="row" style={{ flexWrap: 'nowrap' }}><input className="input mono grow" disabled value={c.secret_set ? `key set${c.secret_set_at ? ' ' + when(c.secret_set_at) : ''}` : 'no key yet'} aria-label="Service account key" />
                  {admin && <button className="link" onClick={() => setSecret('')}>{c.secret_set ? 'Replace' : 'Add key'}</button>}</span>
              : <textarea className="textarea cel" rows={5} value={secret} onChange={(e) => setSecret(e.target.value)} placeholder='Paste the JSON key file: {"type": "service_account", ...}' aria-label="Service account key JSON" />)}
          </Block>
          {c.type === 'dataproc' && (
            <Block title="Where Spark SQL runs">
              <span className="row"><span className="muted" style={{ width: 110 }}>Project, region</span>
                <input className="input mono grow" value={eng.project} disabled={!admin} placeholder="project ID, e.g. my-project-123456" aria-label="Project" onChange={(e) => setEng({ ...eng, project: e.target.value })} />
                <input className="input mono" style={{ width: 130 }} value={eng.region} disabled={!admin} placeholder="us-central1" aria-label="Region" onChange={(e) => setEng({ ...eng, region: e.target.value })} /></span>
              <span className="row"><span className="muted" style={{ width: 110 }}>Runs on</span>
                <Segmented options={['serverless', 'cluster']} value={eng.mode} onChange={(v) => admin && setEng({ ...eng, mode: v })}
                  labels={{ serverless: 'Dataproc Serverless (pay per query)', cluster: 'A running cluster' }} /></span>
              {eng.mode === 'cluster'
                ? <span className="row"><span className="muted" style={{ width: 110 }}>Cluster</span>
                    <input className="input mono grow" value={eng.cluster} disabled={!admin} placeholder="etl-cluster" aria-label="Cluster" onChange={(e) => setEng({ ...eng, cluster: e.target.value })} /></span>
                : <>
                    <span className="row"><span className="muted" style={{ width: 110 }}>Runs as</span>
                      <input className="input mono grow" value={eng.service_account} disabled={!admin} placeholder="optional: the batch's service account" aria-label="Batch service account" onChange={(e) => setEng({ ...eng, service_account: e.target.value })} /></span>
                    <span className="row"><span className="muted" style={{ width: 110 }}>Subnetwork</span>
                      <input className="input mono grow" value={eng.subnetwork} disabled={!admin} placeholder="optional: needs Private Google Access" aria-label="Subnetwork" onChange={(e) => setEng({ ...eng, subnetwork: e.target.value })} />
                      <input className="input mono" style={{ width: 90 }} value={eng.runtime_version} disabled={!admin} placeholder="runtime" aria-label="Runtime version" onChange={(e) => setEng({ ...eng, runtime_version: e.target.value })} /></span>
                  </>}
              <span className="row"><span className="muted" style={{ width: 110 }}>Staging folder</span>
                <input className="input mono grow" value={eng.staging} disabled={!admin} placeholder="gs://bucket/agent-sql" aria-label="Staging folder" onChange={(e) => setEng({ ...eng, staging: e.target.value })} /></span>
              <span className="faint">Each query's file and its rows (as JSON) go here, one folder per query. A lifecycle rule that deletes them after a few days keeps it tidy.</span>
              <label className="stack" style={{ gap: 3 }}><span className="muted">Spark properties, one per line</span>
                <textarea className="textarea cel" rows={3} value={eng.properties} disabled={!admin} aria-label="Spark properties" onChange={(e) => setEng({ ...eng, properties: e.target.value })}
                  placeholder={'spark.sql.catalog.lake=org.apache.iceberg.spark.SparkCatalog\nspark.sql.catalog.lake.type=hadoop\nspark.sql.catalog.lake.warehouse=gs://bucket/warehouse'} /></label>
              <span className="row"><span className="muted" style={{ width: 110 }}>Extra jars</span><Chips values={eng.jars} onChange={(v) => admin && setEng({ ...eng, jars: v })} placeholder="gs://spark-lib/iceberg/iceberg-spark-runtime-3.5_2.13-1.6.1.jar" /></span>
              <label className="stack" style={{ gap: 3 }}><span className="muted">Run before each query (setup)</span>
                <textarea className="textarea cel" rows={4} value={eng.setup} disabled={!admin} aria-label="Setup statements" onChange={(e) => setEng({ ...eng, setup: e.target.value })}
                  placeholder={"CREATE NAMESPACE IF NOT EXISTS lake.sales;\nCALL lake.system.register_table(table => 'sales.orders', metadata_file => 'gs://bucket/orders/metadata/00001-….metadata.json');"} /></label>
              <span className="faint">Statements ending in ";", run in the same job before every query: e.g. registering Iceberg tables in a session catalog. Only admins write these; steps can't.</span>
              <span className="row"><span className="muted" style={{ width: 110 }}>Agents may read</span><Chips values={bq.allowed} onChange={(v) => admin && setBq({ ...bq, allowed: v })} placeholder="schema, schema.table, or catalog.schema.table" /></span>
              <span className="row"><span className="muted" style={{ width: 110 }}>Time limit</span><input className="input mono" style={{ width: 80 }} value={eng.max_seconds ?? ''} disabled={!admin} placeholder="900" aria-label="Time limit" onChange={(e) => setEng({ ...eng, max_seconds: e.target.value })} />
                <span className="faint">seconds per query, start-up included; longer jobs are cancelled.</span></span>
            </Block>
          )}
          {c.type === 'gcs' && (
            <Block title="Buckets">
              <span className="row"><span className="muted" style={{ width: 110 }}>Agents may use</span><Chips values={bq.allowed} onChange={(v) => admin && setBq({ ...bq, allowed: v })} placeholder="bucket, or bucket/prefix/" /></span>
              <span className="faint">The most any step may list, read or write. Each step names its own prefixes within these; writes only add new files.</span>
              <span className="row"><span className="muted" style={{ width: 110 }}>Max per read</span><input className="input mono" style={{ width: 100 }} value={bq.max_read_bytes} disabled={!admin} onChange={(e) => setBq({ ...bq, max_read_bytes: e.target.value })} aria-label="Max bytes per read" />
                <span className="faint">e.g. 1GB. Steps set their own, up to this.</span></span>
            </Block>
          )}
          {c.type === 'bigquery' && <><Block title="Project and data">
            <span className="row"><span className="muted" style={{ width: 110 }}>Billing project</span><input className="input mono grow" value={bq.billing_project} disabled={!admin} onChange={(e) => setBq({ ...bq, billing_project: e.target.value })} aria-label="Billing project" /></span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Location</span><input className="input mono" style={{ width: 120 }} value={bq.location} disabled={!admin} onChange={(e) => setBq({ ...bq, location: e.target.value })} aria-label="Location" /></span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Data agents may read</span><Chips values={bq.allowed} onChange={(v) => admin && setBq({ ...bq, allowed: v })} placeholder="dataset, project.dataset or project.dataset.table" /></span>
            <span className="faint">The most any step may read. Each step names its own datasets within these.</span>
          </Block>
          <Block title="Cost">
            <span className="row"><span className="muted" style={{ width: 110 }}>Max per query</span><input className="input mono" style={{ width: 100 }} value={bq.max_bytes_cap} disabled={!admin} onChange={(e) => setBq({ ...bq, max_bytes_cap: e.target.value })} aria-label="Max bytes per query" />
              <span className="faint">scanned, e.g. 10GB. BigQuery refuses bigger queries.</span></span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Monthly budget</span><span className="muted">$</span><input className="input mono" style={{ width: 80 }} value={bq.monthly_budget_usd} disabled={!admin} onChange={(e) => setBq({ ...bq, monthly_budget_usd: e.target.value })} aria-label="Monthly budget" placeholder="none" />
              <span className="faint">across every agent; runs stop querying once it's used.</span></span>
            <span className="faint">On-demand pricing, $6.25 per TB scanned, at least 10 MB per query. Every job is labelled with its agent, run and step.</span>
          </Block></>}
        </>
      )}
      {c.type === 'microsoft365' && (
        <>
          <Block title="SharePoint: the Entra ID app">
            {([['tenant_id', 'Tenant ID', 'Directory (tenant) ID'], ['client_id', 'App (client) ID', 'Application (client) ID'], ['hostname', 'SharePoint host', 'contoso.sharepoint.com']] as const).map(([k, label, ph]) => (
              <span key={k} className="row"><span className="muted" style={{ width: 110 }}>{label}</span>
                <input className="input mono grow" value={ms[k]} disabled={!admin} placeholder={ph} aria-label={label} onChange={(e) => setMs({ ...ms, [k]: e.target.value })} /></span>
            ))}
            <SecretField label="Client secret" set={!!c.secrets_set?.client_secret} admin={admin} value={msSecrets.client_secret}
              onChange={(v) => setMsSecrets({ ...msSecrets, client_secret: v })} />
            <span className="faint">Entra admin center → App registrations → New registration, then a client secret. Ask IT for the Microsoft Graph
              application permission <code className="mono">Sites.Selected</code>, granted on just the sites below (read, or write where agents add files).
              Leave this empty if you only send email.</span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Agents may use</span><Chips values={bq.allowed} onChange={(v) => admin && setBq({ ...bq, allowed: v })} placeholder="Site, Site/Shared Documents/folder, or Site/Lists/<title>" /></span>
            <span className="faint">The most any step may list, read or write. Each step names its own folders and lists within these; writes only add new files.</span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Max per read</span><input className="input mono" style={{ width: 100 }} value={bq.max_read_bytes} disabled={!admin} onChange={(e) => setBq({ ...bq, max_read_bytes: e.target.value })} aria-label="Max bytes per read" />
              <span className="faint">e.g. 1GB. Steps set their own, up to this.</span></span>
          </Block>
          <Block title="Email: the SMTP server">
            <span className="row"><span className="muted" style={{ width: 110 }}>Server</span>
              <input className="input mono grow" value={ms.smtp.host ?? ''} disabled={!admin} placeholder="smtp.office365.com, or your mail relay" aria-label="SMTP server" onChange={(e) => setMs({ ...ms, smtp: { ...ms.smtp, host: e.target.value } })} />
              <input className="input mono" style={{ width: 70 }} value={ms.smtp.port ?? ''} disabled={!admin} placeholder={ms.smtp.security === 'ssl' ? '465' : ms.smtp.security === 'none' ? '25' : '587'} aria-label="Port" onChange={(e) => setMs({ ...ms, smtp: { ...ms.smtp, port: e.target.value } })} /></span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Secured with</span>
              <Segmented options={['starttls', 'ssl', 'none']} value={ms.smtp.security ?? 'starttls'} onChange={(v) => admin && setMs({ ...ms, smtp: { ...ms.smtp, security: v } })}
                labels={{ starttls: 'STARTTLS', ssl: 'TLS', none: 'Nothing (internal relay)' }} /></span>
            <span className="row"><span className="muted" style={{ width: 110 }}>Sign in as</span>
              <input className="input mono grow" value={ms.smtp.username ?? ''} disabled={!admin} placeholder="optional: many internal relays need no sign-in" aria-label="SMTP username" onChange={(e) => setMs({ ...ms, smtp: { ...ms.smtp, username: e.target.value } })} /></span>
            {ms.smtp.username && <SecretField label="Password" set={!!c.secrets_set?.smtp_password} admin={admin} value={msSecrets.smtp_password}
              onChange={(v) => setMsSecrets({ ...msSecrets, smtp_password: v })} />}
            <span className="row"><span className="muted" style={{ width: 110 }}>From</span>
              <input className="input mono grow" value={ms.smtp.from_address ?? ''} disabled={!admin} placeholder="agents@company.com" aria-label="From address" onChange={(e) => setMs({ ...ms, smtp: { ...ms.smtp, from_address: e.target.value } })} /></span>
            <span className="faint">Every email agents send goes from this address. Each step still names who it may send to. Leave the server empty if you only use SharePoint.</span>
          </Block>
        </>
      )}
      {c.type === 'github' && (
        <Block title="API">
          <label className="stack" style={{ gap: 3 }}><span className="muted">API URL</span>
            <input className="input mono" value={apiUrl} disabled={!admin} onChange={(e) => setApiUrl(e.target.value)} aria-label="API URL" /></label>
          <span className="faint">https://api.github.com, or your GitHub Enterprise server's API. Each account adds its own read-only token.</span>
        </Block>
      )}
      {(c.type === 'bigquery' || c.type === 'gcs' || c.type === 'microsoft365') && (
        <Block title="Building with Claude">
          <Check disabled={!admin} checked={bq.share_samples} onChange={(on) => setBq({ ...bq, share_samples: on })}
            detail={c.type === 'bigquery' ? 'Up to 5 rows per table, through this connector\u2019s limits. Off: table names, columns and row counts only.'
              : c.type === 'microsoft365' ? 'The head of a SharePoint file or list (5 rows, or a few KB of text), through this connector\u2019s limits. Off: folder, file and list names only.'
              : 'The head of a file (5 rows, or a few KB of text), through this connector\u2019s limits. Off: folder and file names and sizes only.'}>
            Let Claude read samples while drafting agents</Check>
        </Block>
      )}
      <Block title="What builders may ask for">
        <span className="faint">The most an account can be granted. Builders pick from these when they connect one.</span>
        {Object.entries(services).map(([svc, info]: [string, any]) => (
          <div key={svc} className="stack" style={{ gap: 4, paddingTop: 6, borderTop: '1px solid var(--line-2)' }}>
            <span className="row" style={{ gap: 7 }}><Icon name={SERVICE_ICON[svc] ?? 'plug'} size={14} color="var(--muted)" /><strong style={{ fontSize: 12.5 }}>{info.name}</strong></span>
            {Object.entries(info.permissions).map(([key, p]: [string, any]) => (
              <Check key={key} disabled={!admin} checked={(offered[svc] ?? []).includes(key)} detail={p.scope}
                onChange={(on) => setOffered({ ...offered, [svc]: on ? [...(offered[svc] ?? []), key] : (offered[svc] ?? []).filter((x) => x !== key) })}>{p.label}</Check>
            ))}
          </div>
        ))}
      </Block>
      <Block title="Who can connect accounts">
        <Check disabled={!admin} checked={who === 'builders'} onChange={(on) => setWho(on ? 'builders' : 'admins')} detail="Otherwise only admins can.">Any builder</Check>
        {c.type === 'google' && <span className="row"><span className="muted">Only accounts in</span><Chips values={domains} onChange={(v) => admin && setDomains(v)} placeholder="Add a domain (any if none)" /></span>}
      </Block>
      <Block title="Test">
        {c.status.state === 'ready' && c.status.tested_at ? <Guarantees items={[c.status.message ?? 'Works.']} />
          : <span className={c.status.state === 'attention' ? 'field-error' : 'faint'}>{c.status.message ?? 'Not tested yet.'}</span>}
        {c.type === 'dataproc' && (busy === 'test'
          ? <span className="row muted" style={{ gap: 8 }}><span className="spinner" />Running a test query on Dataproc. This can take a few minutes while {eng.mode === 'cluster' ? 'the job is scheduled on the cluster' : 'Serverless starts the batch'}.</span>
          : <span className="faint">The test runs a real query (SELECT 1) on Dataproc, so it can take a few minutes{eng.mode === 'cluster' ? '' : ' (and a small Serverless charge)'}.</span>)}
      </Block>
      {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
      {admin && (
        <span className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn danger" style={{ marginRight: 'auto' }} disabled={!!busy} onClick={remove}
            title={c.accounts ? 'Accounts are still connected through it' : undefined}><Icon name="trash" size={12} />Remove</button>
          <button className="btn" disabled={!!busy} onClick={test}>{busy === 'test' ? 'Testing…' : 'Test'}</button>
          <button className="btn primary" disabled={!!busy} onClick={save}>{busy === 'save' ? 'Saving…' : 'Save connector'}</button>
        </span>
      )}
    </div>
  )
}

export default function Connectors() {
  const navigate = useNavigate()
  const session = useContext(SessionContext)
  const admin = session?.user.role === 'Admin'
  const [items, setItems] = useState<Connector[] | null>(null)
  const [params, setParams] = useSearchParams()
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const load = () => api.connectors().then(setItems)
  useEffect(() => { load() }, [])
  const selected = items?.find((c) => c.id === (params.get('c') ?? items?.[0]?.id))
  const open = (c: Connector) => (c.type === 'mcp' ? navigate(`/connections/connectors/${c.id}`) : setParams({ c: c.id }))
  const add = async (type: string) => {
    setAdding(false); setError(null)
    if (type === 'mcp') { navigate('/connections/connectors/new'); return }
    try { const c = await api.addConnector({ type, name: type === 'github' ? 'GitHub Enterprise' : type === 'bigquery' ? 'BigQuery' : type === 'gcs' ? 'Cloud Storage' : type === 'microsoft365' ? 'Microsoft 365' : type === 'trino' ? 'Trino' : type === 'dataproc' ? 'Spark SQL' : 'Google Workspace',
      ...(type === 'bigquery' ? { settings: { auth: { kind: 'gcloud' }, location: 'US', allowed: [], max_bytes_cap: '10GB' } }
        : type === 'gcs' ? { settings: { auth: { kind: 'gcloud' }, allowed: [], max_read_bytes: '1GB' } }
        : type === 'microsoft365' ? { settings: { allowed: [], max_read_bytes: '1GB', smtp: { security: 'starttls' } } }
        : type === 'trino' ? { settings: { auth: { kind: 'none' }, allowed: [] } }
        : type === 'dataproc' ? { settings: { auth: { kind: 'gcloud' }, mode: 'serverless', region: 'us-central1', allowed: [] } } : {}) }); await load(); setParams({ c: c.id }) }
    catch (e: any) { setError(e.message) }
  }
  return (
    <div className="page">
      <ConnectionsHeader tab="Connectors" action={admin && (
        <span style={{ position: 'relative' }}>
          <button className="btn primary" onClick={() => setAdding(!adding)}><Icon name="plus" size={14} color="#fff" width={2.2} />Add connector</button>
          {adding && (
            <div className="menu" style={{ right: 0, left: 'auto', top: 40, width: 280 }}>
              <button onClick={() => add('mcp')}><Icon name="plug" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>MCP server</strong><span className="faint">Any system with an MCP server</span></span></button>
              <button onClick={() => add('bigquery')}><Icon name="database" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>BigQuery</strong><span className="faint">Read queries within datasets and a cost cap</span></span></button>
              <button onClick={() => add('gcs')}><Icon name="folder" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>Cloud Storage</strong><span className="faint">Read files in buckets, write new ones</span></span></button>
              <button onClick={() => add('trino')}><Icon name="database" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>Trino</strong><span className="faint">Read queries, e.g. Trino on Dataproc</span></span></button>
              <button onClick={() => add('dataproc')}><Icon name="database" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>Spark SQL (Dataproc)</strong><span className="faint">Read queries as Dataproc jobs or Serverless batches</span></span></button>
              <button onClick={() => add('microsoft365')}><Icon name="folder" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>Microsoft 365</strong><span className="faint">SharePoint files and lists; email through SMTP</span></span></button>
              <button onClick={() => add('github')}><Icon name="code" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>GitHub</strong><span className="faint">Another GitHub, e.g. Enterprise</span></span></button>
              <button onClick={() => add('google')}><Icon name="mail" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>Google Workspace</strong><span className="faint">Another OAuth client</span></span></button>
            </div>
          )}
        </span>
      )} />
      {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
      {items === null ? <span className="spinner" /> : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 440px', gap: 18, alignItems: 'start' }}>
          <div className="stack" style={{ gap: 14 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14 }}>
              {items.map((c) => <ConnectorCard key={c.id} c={c} selected={selected?.id === c.id} onOpen={() => open(c)} />)}
            </div>
            <span className="faint">Built-in types come with their actions and limits defined. Any other system can be added as an MCP server: {admin ? 'you mark' : 'an admin marks'} each of its tools as read, act or not offered. <Link to="/connections">Accounts →</Link></span>
          </div>
          {selected && selected.type !== 'mcp' && <BuiltInPanel key={selected.id + (selected.status.tested_at ?? '')} c={selected} admin={admin}
            onSaved={(c) => setItems((items ?? []).map((x) => (x.id === c.id ? c : x)))}
            onRemoved={() => { setParams({}); load() }} />}
        </div>
      )}
    </div>
  )
}
