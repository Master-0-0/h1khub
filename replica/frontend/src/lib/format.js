import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true })

const ALLOWED_TAGS = new Set([
  'a', 'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
  'hr', 'span', 'div', 'img',
])

function sanitize(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const walk = (node) => {
    for (const child of [...node.children]) {
      const tag = child.tagName.toLowerCase()
      if (!ALLOWED_TAGS.has(tag)) {
        child.replaceWith(...child.childNodes)
        continue
      }
      for (const attr of [...child.attributes]) {
        const name = attr.name.toLowerCase()
        if (name.startsWith('on') || name === 'style') child.removeAttribute(attr.name)
        if ((name === 'href' || name === 'src') && /^\s*javascript:/i.test(attr.value)) {
          child.removeAttribute(attr.name)
        }
        if (tag === 'a') {
          child.setAttribute('target', '_blank')
          child.setAttribute('rel', 'noreferrer noopener')
        }
      }
      walk(child)
    }
  }
  walk(doc.body)
  return doc.body.innerHTML
}

export function renderMarkdown(text) {
  if (!text) return ''
  try {
    return sanitize(marked.parse(text))
  } catch {
    return sanitize(`<p>${String(text).replace(/[<>&]/g, '')}</p>`)
  }
}

export function formatBytes(n) {
  if (n === null || n === undefined) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = Number(n)
  for (const u of units) {
    if (value < 1024 || u === 'TB') return u === 'B' ? `${Math.round(value)} B` : `${value.toFixed(1)} ${u}`
    value /= 1024
  }
  return `${value.toFixed(1)} TB`
}

export function formatTime(value) {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleString('zh-CN', { hour12: false })
}

export function shortTime(value) {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value || '')
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function timeAgo(value) {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
  return d.toLocaleDateString('zh-CN')
}

export function cellText(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'object') {
    if (value.__type === 'blob') return `0x${(value.preview || '').toUpperCase()}${value.length > 32 ? '…' : ''}`
    return JSON.stringify(value)
  }
  return String(value)
}

export function formatSql(sql) {
  if (!sql) return ''
  const upper = (m) => m.toUpperCase()
  let out = String(sql).replace(/\s+/g, ' ').trim()
  out = out.replace(
    /\b(select|from|where|and|or|group by|order by|limit|having|union|join|left join|right join|inner join|insert into|values|update|set|delete from|create|alter|drop|offset)\b/gi,
    upper,
  )
  out = out.replace(
    /\s+(FROM|WHERE|GROUP BY|ORDER BY|LIMIT|HAVING|UNION|JOIN|LEFT JOIN|RIGHT JOIN|INNER JOIN|SET|VALUES|OFFSET)\s+/g,
    '\n$1 ',
  )
  return out
}
