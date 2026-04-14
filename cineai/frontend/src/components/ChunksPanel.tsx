import { PipelineEvent, RagChunk } from '../types'

interface Props {
  events: PipelineEvent[]
}

export function ChunksPanel({ events }: Props) {
  const chunksEvent = [...events].reverse().find(e => e.type === 'chunks_retrieved')
  const tmdbEvent   = [...events].reverse().find(e => e.type === 'tmdb_results')

  if (!chunksEvent && !tmdbEvent) {
    return (
      <div className="chunks-panel">
        <div style={{ color: 'var(--text-dim)', fontSize: 12, fontStyle: 'italic' }}>
          Retrieved context will appear here after the pipeline runs.
        </div>
      </div>
    )
  }

  const searchType = chunksEvent?.type === 'chunks_retrieved'
    ? (chunksEvent.chunks[0]?.search_type ?? 'dense')
    : null

  return (
    <div className="chunks-panel">
      {/* RAG chunks */}
      {chunksEvent && chunksEvent.type === 'chunks_retrieved' && (
        <>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--blue)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.8px', display: 'flex', alignItems: 'center', gap: 8 }}>
            🔍 RAG Chunks ({chunksEvent.count})
            {searchType && (
              <span style={{
                fontSize: 9,
                fontWeight: 700,
                letterSpacing: '0.6px',
                padding: '2px 6px',
                borderRadius: 4,
                background: searchType === 'hybrid' ? 'rgba(59,130,246,0.18)' : 'rgba(107,114,128,0.18)',
                color: searchType === 'hybrid' ? 'var(--blue)' : 'var(--text-dim)',
                border: `1px solid ${searchType === 'hybrid' ? 'rgba(59,130,246,0.35)' : 'rgba(107,114,128,0.3)'}`,
              }}>
                {searchType === 'hybrid' ? 'HYBRID BM25+DENSE' : 'DENSE ONLY'}
              </span>
            )}
          </div>
          {chunksEvent.chunks.map((chunk: RagChunk, i: number) => (
            <div className="chunk-card" key={i}>
              <div className="chunk-header">
                <span className="chunk-source">{chunk.source.split('/').pop()}</span>
                <span className="chunk-score">score: {chunk.score.toFixed(3)}</span>
              </div>
              <div className="chunk-text">{chunk.text.slice(0, 300)}{chunk.text.length > 300 ? '…' : ''}</div>
            </div>
          ))}
        </>
      )}

      {/* TMDB results */}
      {tmdbEvent && tmdbEvent.type === 'tmdb_results' && (
        <>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--cyan)', margin: '16px 0 10px', textTransform: 'uppercase', letterSpacing: '0.8px' }}>
            🎬 TMDB Results ({tmdbEvent.count})
          </div>
          {tmdbEvent.results.map((movie, i) => (
            <div className="chunk-card" key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              {movie.poster
                ? <img src={movie.poster} alt={movie.title} style={{ width: 40, height: 60, objectFit: 'cover', borderRadius: 4, flexShrink: 0 }} />
                : <div style={{ width: 40, height: 60, background: 'var(--surface)', borderRadius: 4, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>🎬</div>
              }
              <div>
                <div style={{ fontWeight: 600, fontSize: 12 }}>{movie.title} <span style={{ color: 'var(--text-muted)' }}>({movie.year})</span></div>
                {movie.rating && <div style={{ color: 'var(--yellow)', fontSize: 11 }}>⭐ {movie.rating?.toFixed(1)} / 10</div>}
                <div style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 3 }}>{(movie.overview || '').slice(0, 120)}{(movie.overview || '').length > 120 ? '…' : ''}</div>
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
