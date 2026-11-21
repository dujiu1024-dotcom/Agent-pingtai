import type { ReactNode } from 'react'

/**
 * 零依赖极简 Markdown 渲染器。
 *
 * 支持的语法（覆盖 agent output 实际会出现的全部）：
 *   - ATX 标题：# ~ ####
 *   - 段落（空行分割）
 *   - 行内：**粗体**、*斜体*、`code`、链接 [t](url)
 *   - 无序列表：- / * / +
 *   - 有序列表：1. / 2.
 *   - 表格：| header | --- | row |
 *   - 代码块：```lang ... ```
 *   - 引用：> quote
 *   - 水平分割线：---
 *   - 空行、HTML 实体转义
 *
 * 刻意不做：嵌套列表、脚注、任务列表、图片、HTML 标签。
 * 遇到未知语法按纯文本原样保留（不渲染但也不破坏布局）。
 */

// 行内装饰 —— 在一段纯文本里把 **bold** / *italic* / `code` / [t](url) 切成 nodes
function renderInline(text: string, keyPrefix = ''): ReactNode[] {
  const nodes: ReactNode[] = []
  // 正则匹配顺序很关键：code 块优先（里面的 ** ` 都不算装饰）
  const pattern =
    /`([^`]+)`|\*\*([^*]+)\*\*|__([^_]+)__|(^|\s)\*([^*]+)\*(?=\s|$)|\[([^\]]+)\]\(([^)]+)\)/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let counter = 0
  while ((match = pattern.exec(text)) !== null) {
    // 先把 match 前的纯文本 push 进去
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    const key = `${keyPrefix}-${counter++}`
    if (match[1] !== undefined) {
      // `code`
      nodes.push(
        <code key={key} className="md-inline-code">
          {match[1]}
        </code>,
      )
    } else if (match[2] !== undefined) {
      nodes.push(<strong key={key}>{match[2]}</strong>)
    } else if (match[3] !== undefined) {
      nodes.push(<strong key={key}>{match[3]}</strong>)
    } else if (match[4] !== undefined) {
      // italic *  （match[4] 是前置空格或 start，match[5] 是内容）
      nodes.push(match[4]) // 前导 whitespace
      nodes.push(<em key={key}>{match[5]}</em>)
    } else if (match[6] !== undefined) {
      nodes.push(
        <a key={key} href={match[7]} target="_blank" rel="noreferrer">
          {match[6]}
        </a>,
      )
    }
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }
  return nodes
}

// 一段文字（已经是 paragraph 或 list item）→ 切行内装饰
function InlineText({ text, keyPrefix }: { text: string; keyPrefix: string }) {
  return <>{renderInline(text, keyPrefix)}</>
}

interface Block {
  kind:
    | 'heading'
    | 'paragraph'
    | 'list-ul'
    | 'list-ol'
    | 'table'
    | 'code'
    | 'quote'
    | 'hr'
    | 'blank'
  content?: string
  level?: number
  items?: string[]
  rows?: string[][]
  header?: string[]
  language?: string
}

// 把原始 md 文本切成块
function tokenize(md: string): Block[] {
  const blocks: Block[] = []
  const lines = md.split(/\r?\n/)
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    // 空行
    if (!line.trim()) {
      blocks.push({ kind: 'blank' })
      i++
      continue
    }
    // 代码块
    const fence = /^```(\w*)\s*$/.exec(line)
    if (fence) {
      const language = fence[1] || ''
      const buf: string[] = []
      i++
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        buf.push(lines[i])
        i++
      }
      if (i < lines.length) i++ // 跳过 closing fence
      blocks.push({ kind: 'code', content: buf.join('\n'), language })
      continue
    }
    // 水平分割线
    if (/^-{3,}\s*$/.test(line)) {
      blocks.push({ kind: 'hr' })
      i++
      continue
    }
    // ATX 标题
    const heading = /^(#{1,4})\s+(.+?)\s*#*\s*$/.exec(line)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length, content: heading[2] })
      i++
      continue
    }
    // 引用（可能连续多行）
    if (/^>\s?/.test(line)) {
      const buf: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ''))
        i++
      }
      blocks.push({ kind: 'quote', content: buf.join('\n') })
      continue
    }
    // 表格（header + --- + rows，至少 2 行且下一行必须是 separator）
    if (
      line.startsWith('|') &&
      i + 1 < lines.length &&
      lines[i + 1].startsWith('|') &&
      /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(lines[i + 1])
    ) {
      const header = line
        .trim()
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map((c) => c.trim())
      i += 2 // 跳过 header 和 separator
      const rows: string[][] = []
      while (i < lines.length && lines[i].startsWith('|')) {
        rows.push(
          lines[i]
            .trim()
            .replace(/^\|/, '')
            .replace(/\|$/, '')
            .split('|')
            .map((c) => c.trim()),
        )
        i++
      }
      blocks.push({ kind: 'table', header, rows })
      continue
    }
    // 无序列表（连续多行）
    if (/^[-*+]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*+]\s+/, ''))
        i++
      }
      blocks.push({ kind: 'list-ul', items })
      continue
    }
    // 有序列表
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, ''))
        i++
      }
      blocks.push({ kind: 'list-ol', items })
      continue
    }
    // 段落：连续非空非装饰行合并
    const buf: string[] = [line]
    i++
    while (
      i < lines.length &&
      lines[i].trim() &&
      !(
        /^#{1,4}\s+/.test(lines[i]) ||
        /^```/.test(lines[i]) ||
        /^>\s?/.test(lines[i]) ||
        /^[-*+]\s+/.test(lines[i]) ||
        /^\d+\.\s+/.test(lines[i]) ||
        /^\|/.test(lines[i]) ||
        /^-{3,}\s*$/.test(lines[i])
      )
    ) {
      buf.push(lines[i])
      i++
    }
    blocks.push({ kind: 'paragraph', content: buf.join('\n') })
  }
  return blocks
}

function renderBlock(block: Block, idx: number): ReactNode {
  switch (block.kind) {
    case 'blank':
      return null
    case 'hr':
      return <hr key={idx} className="md-hr" />
    case 'heading': {
      const level = block.level ?? 1
      const text = block.content ?? ''
      const textNodes = renderInline(text, `h${idx}`)
      if (level === 1)
        return (
          <h1 key={idx} className="md-h1">
            {textNodes}
          </h1>
        )
      if (level === 2)
        return (
          <h2 key={idx} className="md-h2">
            {textNodes}
          </h2>
        )
      if (level === 3)
        return (
          <h3 key={idx} className="md-h3">
            {textNodes}
          </h3>
        )
      return (
        <h4 key={idx} className="md-h4">
          {textNodes}
        </h4>
      )
    }
    case 'paragraph':
      return (
        <p key={idx} className="md-p">
          <InlineText text={block.content ?? ''} keyPrefix={`p${idx}`} />
        </p>
      )
    case 'list-ul':
      return (
        <ul key={idx} className="md-list md-list--ul">
          {(block.items ?? []).map((item, j) => (
            <li key={j}>
              <InlineText text={item} keyPrefix={`ul${idx}-${j}`} />
            </li>
          ))}
        </ul>
      )
    case 'list-ol':
      return (
        <ol key={idx} className="md-list md-list--ol">
          {(block.items ?? []).map((item, j) => (
            <li key={j}>
              <InlineText text={item} keyPrefix={`ol${idx}-${j}`} />
            </li>
          ))}
        </ol>
      )
    case 'quote':
      return (
        <blockquote key={idx} className="md-quote">
          <InlineText text={block.content ?? ''} keyPrefix={`q${idx}`} />
        </blockquote>
      )
    case 'code':
      return (
        <pre key={idx} className="md-code-block">
          <code>{block.content ?? ''}</code>
        </pre>
      )
    case 'table': {
      const header = block.header ?? []
      const rows = block.rows ?? []
      return (
        <div key={idx} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {header.map((h, j) => (
                  <th key={j}>
                    <InlineText text={h} keyPrefix={`th${idx}-${j}`} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, j) => (
                <tr key={j}>
                  {row.map((cell, k) => (
                    <td key={k}>
                      <InlineText text={cell} keyPrefix={`td${idx}-${j}-${k}`} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    }
    default:
      return null
  }
}

export function MarkdownView({ text }: { text: string }) {
  const blocks = tokenize(text)
  const rendered = blocks
    .map((b, i) => renderBlock(b, i))
    .filter((n): n is ReactNode => n !== null)
  return <div className="md">{rendered}</div>
}
