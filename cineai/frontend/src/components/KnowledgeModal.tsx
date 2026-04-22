import { useEffect, useState } from 'react'
import { apiFetch } from '../api'

interface KnowledgeDoc {
  source: string
  chunks: number
}

interface KnowledgeData {
  total_chunks: number
  total_docs: number
  docs: KnowledgeDoc[]
  error?: string
}

// Map source paths to friendly labels and emoji icons
function formatDoc(source: string): { icon: string; title: string; category: string } {
  const name = source.split('/').pop()?.replace('.md', '') ?? source
  const parts = source.split('/')

  const categoryMap: Record<string, { icon: string; label: string }> = {
    directors: { icon: '🎬', label: 'Director' },
    genres:    { icon: '🎭', label: 'Genre' },
    classics:  { icon: '🏛', label: 'Classic' },
    heist:     { icon: '🔫', label: 'Genre' },
    decades:   { icon: '📅', label: 'Decade' },
    themes:    { icon: '💡', label: 'Theme' },
  }

  const folder = parts.length >= 2 ? parts[parts.length - 2] : ''
  const cat = categoryMap[folder] ?? { icon: '📄', label: 'Guide' }

  const titleMap: Record<string, string> = {
    'stanley-kubrick':     'Stanley Kubrick',
    'christopher-nolan':   'Christopher Nolan',
    'martin-scorsese':     'Martin Scorsese',
    'quentin-tarantino':   'Quentin Tarantino',
    'denis-villeneuve':    'Denis Villeneuve',
    'steven-spielberg':    'Steven Spielberg',
    'wes-anderson':        'Wes Anderson',
    'sci-fi-cinema-guide': 'Sci-Fi Cinema',
    'thriller-crime':      'Thriller & Crime',
    'action-adventure':    'Action & Adventure',
    'comedy':              'Comedy',
    'horror-guide':        'Horror',
    'bank-heist-films-guide': 'Bank Heist Films',
    'best-of-1990s':       'Best Films of the 1990s',
    'best-of-2000s':       'Best Films of the 2000s',
    'coming-of-age':       'Coming-of-Age Films',
    'cult-classics':       'Cult Classics',
  }

  return {
    icon: cat.icon,
    title: titleMap[name] ?? name.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
    category: cat.label,
  }
}

interface Props {
  onClose: () => void
  onSearch: (q: string) => void
}

export function KnowledgeModal({ onClose, onSearch }: Props) {
  const [data, setData] = useState<KnowledgeData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch('/api/knowledge')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const handleBackdrop = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose()
  }

  return (
    <div className="modal-backdrop" onClick={handleBackdrop}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">
            <span className="modal-title-icon">📚</span>
            <div>
              <div className="modal-title-text">Knowledge Base</div>
              <div className="modal-title-sub">
                {loading ? 'Loading…' : data
                  ? `${data.total_chunks} chunks across ${data.total_docs} documents — click any topic to search`
                  : 'Could not load'}
              </div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="modal-body">
          {loading && (
            <div className="modal-loading">Loading knowledge base…</div>
          )}

          {!loading && data && data.docs.length === 0 && (
            <div className="modal-empty">
              <div style={{ fontSize: 32, marginBottom: 10 }}>📭</div>
              <div>No documents ingested yet.</div>
              <div style={{ fontSize: 12, marginTop: 6, opacity: 0.6 }}>
                Run <code>make ingest</code> to populate the knowledge base.
              </div>
            </div>
          )}

          {!loading && data && data.docs.length > 0 && (
            <>
              {/* Group by category */}
              {(() => {
                const groups: Record<string, { doc: KnowledgeDoc; meta: ReturnType<typeof formatDoc> }[]> = {}
                data.docs.forEach(doc => {
                  const meta = formatDoc(doc.source)
                  if (!groups[meta.category]) groups[meta.category] = []
                  groups[meta.category].push({ doc, meta })
                })
                return Object.entries(groups).map(([category, items]) => (
                  <div key={category} className="modal-group">
                    <div className="modal-group-label">{items[0].meta.icon} {category}s</div>
                    <div className="modal-doc-grid">
                      {items.map(({ doc, meta }) => (
                        <button
                          key={doc.source}
                          className="modal-doc-card"
                          onClick={() => {
                            onSearch(`Tell me about ${meta.title}`)
                            onClose()
                          }}
                        >
                          <div className="modal-doc-title">{meta.title}</div>
                          <div className="modal-doc-chunks">{doc.chunks} chunks</div>
                        </button>
                      ))}
                    </div>
                  </div>
                ))
              })()}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
