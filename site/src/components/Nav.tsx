import { motion, useScroll, useSpring } from 'framer-motion'
import { useEffect, useState } from 'react'
import { REPO_URL, VERSION } from '../lib/site'

const LINKS = [
  { href: '#proof', label: 'Proof' },
  { href: '#numbers', label: 'Numbers' },
  { href: '#benchmarks', label: 'Charts' },
  { href: '#loop', label: 'Loop' },
  { href: '#install', label: 'Install' },
  { href: '#honesty', label: 'Not claimed' },
]

export function Nav() {
  const { scrollYProgress } = useScroll()
  const bar = useSpring(scrollYProgress, { stiffness: 140, damping: 30, mass: 0.3 })
  const [solid, setSolid] = useState(false)

  useEffect(() => {
    const onScroll = () => setSolid(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-300 ${
        solid ? 'bg-ink-0/88 backdrop-blur-sm' : 'bg-transparent'
      }`}
    >
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-5 md:px-8">
        <a href="#top" className="num flex items-baseline gap-2 text-[13px] text-fog-0">
          <span className="text-pass">▮</span>
          flash<span className="text-fog-2">coder</span>
          <span className="text-[10px] text-fog-2">v{VERSION}</span>
        </a>
        <nav className="ml-auto hidden items-center gap-6 md:flex">
          {LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="text-[13px] text-fog-2 transition-colors hover:text-fog-0"
            >
              {l.label}
            </a>
          ))}
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="num border border-line px-2.5 py-1 text-[12px] text-fog-1 transition-colors hover:border-signal hover:text-signal"
          >
            source ↗
          </a>
        </nav>
        <a
          href={REPO_URL}
          target="_blank"
          rel="noreferrer"
          className="num ml-auto border border-line px-2.5 py-1 text-[12px] text-fog-1 md:hidden"
        >
          source ↗
        </a>
      </div>
      <motion.div
        className="h-px origin-left bg-pass"
        style={{ scaleX: bar }}
        aria-hidden="true"
      />
    </header>
  )
}
