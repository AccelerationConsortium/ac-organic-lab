const BD_NS = 'https://battery-dashboard.ac/ns/chart-data'

export interface ChartExportColumn {
  key: string
  label: string
}

export interface SvgExportMeta {
  title: string
  /** Optional tabular override — used when caller has a cleaner row/column
   *  representation than Plotly's internal trace data. */
  columns?: ChartExportColumn[]
  rows?: Record<string, unknown>[]
}

/** Canonical shape of the JSON payload embedded in SVG metadata and in viewer URLs. */
export interface ChartPayload {
  title: string
  exportedAt?: string
  traces?: Array<Record<string, unknown>>
  columns?: ChartExportColumn[]
  rows?: Record<string, unknown>[]
}

/** Pick a stable, readable subset of a Plotly trace for embedding. */
function compactTrace(t: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const key of ['name', 'type', 'mode', 'x', 'y', 'z', 'text', 'customdata']) {
    if (t[key] !== undefined) out[key] = t[key]
  }
  return out
}

/** UTF-8 safe base64url encode (no padding). Used in URL hash fragment. */
export function encodeChartPayload(payload: ChartPayload): string {
  const json = JSON.stringify(payload)
  const bytes = new TextEncoder().encode(json)
  let binary = ''
  for (const b of bytes) binary += String.fromCharCode(b)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** Reverse of encodeChartPayload. Accepts both base64 and base64url. */
export function decodeChartPayload(encoded: string): ChartPayload {
  let b64 = encoded.replace(/-/g, '+').replace(/_/g, '/')
  while (b64.length % 4 !== 0) b64 += '='
  const binary = atob(b64)
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0))
  const json = new TextDecoder().decode(bytes)
  return JSON.parse(json) as ChartPayload
}

function buildPayload(gdOrContainer: HTMLElement, meta: SvgExportMeta): ChartPayload {
  const rawTraces = (gdOrContainer as unknown as { data?: unknown[] }).data
  const traces = Array.isArray(rawTraces)
    ? rawTraces.map(t => compactTrace(t as Record<string, unknown>))
    : undefined
  return {
    title: meta.title,
    exportedAt: new Date().toISOString(),
    ...(traces && traces.length > 0 && { traces }),
    ...(meta.columns && meta.rows && meta.rows.length > 0 && {
      columns: meta.columns,
      rows: meta.rows,
    }),
  }
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** Minimal, dependency-free toast. Creates a floating notification bottom-right,
 *  auto-dismisses after ~6s. Supports a "Copy" action for the viewer URL. */
function showToast(options: { title: string; body: string; copyText?: string; tone?: 'info' | 'error' }) {
  const tone = options.tone ?? 'info'
  const host = document.createElement('div')
  host.setAttribute('role', 'status')
  host.style.cssText = [
    'position:fixed', 'bottom:24px', 'right:24px', 'z-index:9999',
    'max-width:380px', 'padding:14px 16px',
    'background:#fff',
    `border:1px solid ${tone === 'error' ? '#CC5B45' : '#458A74'}`,
    'border-left-width:4px',
    'border-radius:4px',
    'box-shadow:0 6px 20px rgba(33,60,81,0.18)',
    "font-family:'JetBrains Mono','Source Code Pro',Menlo,monospace",
    'font-size:12px', 'color:#213C51', 'line-height:1.5',
    'transition:opacity 200ms',
  ].join(';')

  const titleEl = document.createElement('div')
  titleEl.textContent = options.title
  titleEl.style.cssText = 'font-weight:600;margin-bottom:4px;'
  host.appendChild(titleEl)

  const bodyEl = document.createElement('div')
  bodyEl.textContent = options.body
  bodyEl.style.cssText = 'color:rgba(33,60,81,0.75);'
  host.appendChild(bodyEl)

  if (options.copyText) {
    const row = document.createElement('div')
    row.style.cssText = 'display:flex;gap:8px;align-items:center;margin-top:8px;'
    const urlEl = document.createElement('code')
    urlEl.textContent = options.copyText
    urlEl.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:rgba(33,60,81,0.06);padding:3px 6px;border-radius:2px;font-size:11px;'
    row.appendChild(urlEl)
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.textContent = 'Copy'
    btn.style.cssText = 'background:#4E5689;color:#fff;border:0;padding:4px 10px;border-radius:2px;cursor:pointer;font:inherit;font-size:11px;'
    btn.onclick = () => {
      navigator.clipboard?.writeText(options.copyText!).then(() => { btn.textContent = 'Copied' })
    }
    row.appendChild(btn)
    host.appendChild(row)
  }

  document.body.appendChild(host)
  const dismiss = () => {
    host.style.opacity = '0'
    setTimeout(() => host.remove(), 220)
  }
  const timer = setTimeout(dismiss, 6000)
  host.onclick = () => { clearTimeout(timer); dismiss() }
}

/** Build the viewer URL for the current origin with the payload in the hash fragment. */
function buildViewerUrl(payload: ChartPayload): string {
  const encoded = encodeChartPayload(payload)
  return `${window.location.origin}/viewer#data=${encoded}`
}

/** Extract Plotly's rendered .main-svg, embed source data as JSON metadata,
 *  trigger a download, and copy a "click-through" viewer URL to clipboard so
 *  the user can paste it as a PPT hyperlink on the image.
 *  During a slideshow, clicking the image opens the viewer with the full
 *  interactive chart + raw data. */
export function exportSvgWithData(
  gdOrContainer: HTMLElement | null | undefined,
  meta: SvgExportMeta,
): void {
  if (!gdOrContainer) return
  const svg = gdOrContainer.querySelector?.('.main-svg') as SVGElement | null
  if (!svg) return

  const payload = buildPayload(gdOrContainer, meta)
  const payloadJson = JSON.stringify(payload)

  const clone = svg.cloneNode(true) as SVGElement
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('xmlns:bd', BD_NS)

  const metadata = document.createElementNS('http://www.w3.org/2000/svg', 'metadata')
  const dataEl = document.createElementNS(BD_NS, 'bd:data')
  dataEl.setAttribute('format', 'json')
  if (payload.traces) dataEl.setAttribute('traces', String(payload.traces.length))
  if (payload.rows) dataEl.setAttribute('rows', String(payload.rows.length))
  if (payload.columns) dataEl.setAttribute('columns', String(payload.columns.length))
  // textContent (not CDATA) — createCDATASection throws NotSupportedError in HTML
  // documents. XMLSerializer escapes <, >, & automatically; DOMParser decodes them.
  dataEl.textContent = payloadJson
  metadata.appendChild(dataEl)

  const viewerUrl = buildViewerUrl(payload)
  const viewerLink = document.createElementNS(BD_NS, 'bd:viewerUrl')
  viewerLink.textContent = viewerUrl
  metadata.appendChild(viewerLink)

  const desc = document.createElementNS('http://www.w3.org/2000/svg', 'desc')
  desc.textContent = `Chart "${meta.title}". Raw data embedded in <metadata><bd:data>. Interactive view: ${viewerUrl}`
  clone.insertBefore(metadata, clone.firstChild)
  clone.insertBefore(desc, clone.firstChild)

  const serialized = new XMLSerializer().serializeToString(clone)
  const filename = `${meta.title.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase() || 'chart'}.svg`
  triggerDownload(new Blob([serialized], { type: 'image/svg+xml;charset=utf-8' }), filename)

  // Copy viewer URL to clipboard so user can paste into PPT's hyperlink dialog.
  // Best-effort: falls back to showing the URL in the toast if clipboard denied.
  const body = 'Paste (Ctrl+V) into PowerPoint → right-click image → Hyperlink. Clicking the image in slideshow will open the interactive chart + data.'
  navigator.clipboard?.writeText(viewerUrl).then(
    () => showToast({
      title: 'SVG downloaded — viewer URL copied',
      body,
      copyText: viewerUrl,
    }),
    () => showToast({
      title: 'SVG downloaded — copy viewer URL manually',
      body: 'Clipboard access was blocked. Click "Copy" below.',
      copyText: viewerUrl,
      tone: 'error',
    }),
  )
}

/** Plotly modebar button: "Download SVG + copy viewer URL".
 *  Sits next to Plotly's built-in PNG download button. */
export const downloadSvgButton = {
  name: 'downloadSvgWithData',
  title: 'Download SVG (embeds data + copies viewer link for PPT)',
  icon: {
    // Download-arrow icon sized to match Plotly's modebar glyphs.
    width: 857.1,
    height: 1000,
    path: 'M857.1 575v227q0 26-19 45t-45 19H64q-26 0-45-19t-19-45V575q0-26 19-45t45-19h202l85 85q41 41 96 41t96-41l86-85h201q26 0 45 19t19 45zm-187-296q-18-18-44-18t-45 18L501 411V128q0-27-18.5-45.5T437 64h-18q-26 0-45 18.5T355 128v283L235 279q-19-18-45-18t-44 18-19 44 19 45l260 260q18 18 44 18t45-18l260-260q19-19 19-45t-19-44z',
    transform: 'matrix(1 0 0 -1 0 850)',
  },
  click: (gd: unknown) => {
    const layout = (gd as { layout?: { title?: { text?: string } | string } }).layout
    const t = typeof layout?.title === 'string' ? layout.title : layout?.title?.text
    exportSvgWithData(gd as HTMLElement, { title: String(t || 'chart') })
  },
}
