import ReactMarkdown from 'react-markdown'
import { RagChunk, CompareTokens } from '../types'

interface Props {
  question: string
  ragText: string
  baseText: string
  chunks: RagChunk[]
  ragTokens: CompareTokens | null
  baseTokens: CompareTokens | null
  judgeText: string
  judgeASide: 'rag' | 'base' | null
  streaming: boolean
}

function Column(props: {
  title: string; icon: string; subtitle: string; accent: string
  text: string; tokens: CompareTokens | null; streaming: boolean
}) {
  const { title, icon, subtitle, accent, text, tokens, streaming } = props
  return (
    <div className="cmp-col" style={{ borderTopColor: accent }}>
      <div className="cmp-col-head">
        <span className="cmp-col-title"><span>{icon}</span> {title}</span>
        <span className="cmp-col-sub">{subtitle}</span>
      </div>
      <div className="cmp-col-body answer-content">
        {text
          ? <ReactMarkdown>{text}</ReactMarkdown>
          : <span className="cmp-thinking">thinking<span className="loading-dots"><span>.</span><span>.</span><span>.</span></span></span>}
        {streaming && text && <span className="cursor-blink" />}
      </div>
      {tokens && (
        <div className="cmp-col-foot">
          {(tokens.prompt_tokens + tokens.completion_tokens).toLocaleString()} tokens
          <span className="cmp-foot-dim"> ({tokens.prompt_tokens.toLocaleString()} in · {tokens.completion_tokens.toLocaleString()} out)</span>
        </div>
      )}
    </div>
  )
}

export function CompareView({ question, ragText, baseText, chunks, ragTokens, baseTokens, judgeText, judgeASide, streaming }: Props) {
  return (
    <div className="compare-view">
      <div className="cmp-banner">
        <span className="cmp-banner-icon">🆚</span>
        <div>
          <div className="cmp-banner-title">RAG vs. no-RAG — same question, two answers</div>
          <div className="cmp-banner-sub">
            Left is grounded on {chunks.length} retrieved knowledge-base passage{chunks.length !== 1 ? 's' : ''};
            right is the bare model with no retrieval. Compare the specificity, citations, and hallucinations.
          </div>
        </div>
      </div>

      <div className="cmp-grid">
        <Column
          title="With RAG" icon="🔍" accent="#3b82f6"
          subtitle="grounded on retrieved sources"
          text={ragText} tokens={ragTokens} streaming={streaming}
        />
        <Column
          title="Without RAG" icon="🧠" accent="#a855f7"
          subtitle="model's parametric knowledge only"
          text={baseText} tokens={baseTokens} streaming={streaming}
        />
      </div>

      {(judgeASide !== null || judgeText) && (
        <div className="cmp-judge" style={{ borderTopColor: '#eab308' }}>
          <div className="cmp-col-head">
            <span className="cmp-col-title"><span>⚖️</span> Blind AI Judge</span>
            <span className="cmp-col-sub">
              sees both answers in random order — NOT told which used RAG
            </span>
          </div>
          <div className="cmp-col-body answer-content">
            {judgeText
              ? <ReactMarkdown>{judgeText}</ReactMarkdown>
              : <span className="cmp-thinking">deliberating<span className="loading-dots"><span>.</span><span>.</span><span>.</span></span></span>}
            {streaming && judgeText && <span className="cursor-blink" />}
          </div>
          {judgeASide !== null && (
            <div className="cmp-col-foot">
              🔓 Reveal: Answer A = {judgeASide === 'rag' ? '🔍 With RAG' : '🧠 Without RAG'} ·
              {' '}Answer B = {judgeASide === 'rag' ? '🧠 Without RAG' : '🔍 With RAG'}
            </div>
          )}
        </div>
      )}

      {chunks.length > 0 && (
        <details className="cmp-sources">
          <summary>📄 {chunks.length} retrieved source{chunks.length !== 1 ? 's' : ''} fed to the RAG side</summary>
          <div className="cmp-sources-list">
            {chunks.map((c, i) => (
              <div key={i} className="cmp-source">
                <div className="cmp-source-head">
                  <span className="cmp-source-name">{c.source}</span>
                  <span className="cmp-source-score">{c.search_type ?? 'hybrid'} · {c.score.toFixed(3)}</span>
                </div>
                <div className="cmp-source-text">{c.text.slice(0, 280)}{c.text.length > 280 ? '…' : ''}</div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
