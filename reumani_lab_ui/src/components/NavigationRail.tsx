import { useState } from 'react'

const NAV = [
  { id: 'projects', g: '◧', label: 'Projects' },
  { id: 'work', g: '◎', label: 'Work center' },
  { id: 'data', g: '▤', label: 'Data / assets' },
  { id: 'protocols', g: '≡', label: 'Protocols' },
]
const BOTTOM = [
  { id: 'settings', g: '⚙', label: 'Settings' },
  { id: 'help', g: '?', label: 'Help' },
]

export function NavigationRail() {
  const [active, setActive] = useState('work')
  return (
    <nav className="rail" aria-label="Primary">
      <div className="rail-logo" title="Reumani Lab" aria-label="Reumani Lab logo">R<span style={{ color: '#bfe6df' }}>L</span></div>
      {NAV.map((n) => (
        <button key={n.id} className="rail-btn" aria-current={active === n.id} aria-label={n.label}
          title={n.label} onClick={() => setActive(n.id)}><span aria-hidden>{n.g}</span></button>
      ))}
      <div className="rail-spacer" />
      {BOTTOM.map((n) => (
        <button key={n.id} className="rail-btn" aria-current={active === n.id} aria-label={n.label}
          title={n.label} onClick={() => setActive(n.id)}><span aria-hidden>{n.g}</span></button>
      ))}
    </nav>
  )
}
