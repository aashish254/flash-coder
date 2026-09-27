import { Instance, Instances } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useReducedMotion } from 'framer-motion'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'
import { byId, callersWithin, EDGES, NODES, type GraphNode } from '../lib/graphModel'

const NODE_BASE = '#3f4a55'
const SELECTED = '#35d07f'
const AFFECTED = '#5ac8fa'

function Scene({
  selected,
  onSelect,
}: {
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const group = useRef<THREE.Group>(null)
  const still = useReducedMotion()
  const { pointer } = useThree()
  const radius = useMemo(() => (selected ? callersWithin(selected, 2) : null), [selected])

  const colors = useMemo(() => {
    const map = new Map<string, string>()
    for (const n of NODES) {
      if (n.id === selected) map.set(n.id, SELECTED)
      else if (radius?.has(n.id)) map.set(n.id, AFFECTED)
      else map.set(n.id, NODE_BASE)
    }
    return map
  }, [selected, radius])

  const { linePos, hotPos } = useMemo(() => {
    const dim: number[] = []
    const lit: number[] = []
    for (const e of EDGES) {
      const a = byId.get(e.s)
      const b = byId.get(e.d)
      if (!a || !b) continue
      const inRadius =
        !!selected &&
        (e.s === selected ||
          e.d === selected ||
          (!!radius && radius.has(e.s) && radius.has(e.d)))
      ;(inRadius ? lit : dim).push(...a.pos, ...b.pos)
    }
    return { linePos: new Float32Array(dim), hotPos: new Float32Array(lit) }
  }, [selected, radius])

  useFrame((_, dt) => {
    if (!group.current) return
    if (!still) group.current.rotation.y += dt * 0.045
    group.current.rotation.x = THREE.MathUtils.lerp(
      group.current.rotation.x,
      pointer.y * 0.18,
      0.05,
    )
    group.current.position.x = THREE.MathUtils.lerp(
      group.current.position.x,
      -pointer.x * 3.2,
      0.05,
    )
  })

  return (
    <group ref={group}>
      <lineSegments>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[linePos, 3]} />
        </bufferGeometry>
        <lineBasicMaterial color="#2b3642" transparent opacity={0.9} />
      </lineSegments>
      {hotPos.length > 0 && (
        <lineSegments>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[hotPos, 3]} />
          </bufferGeometry>
          <lineBasicMaterial color={AFFECTED} transparent opacity={0.55} />
        </lineSegments>
      )}
      <Instances limit={NODES.length} range={NODES.length}>
        <sphereGeometry args={[1, 12, 12]} />
        <meshBasicMaterial toneMapped={false} />
        {NODES.map((n: GraphNode) => (
          <Instance
            key={n.id}
            position={n.pos}
            scale={n.id === selected ? 0.8 : 0.13 + Math.min(n.degree, 18) * 0.026}
            color={colors.get(n.id) ?? NODE_BASE}
            onClick={(ev) => {
              ev.stopPropagation()
              onSelect(n.id === selected ? null : n.id)
            }}
            onPointerOver={(ev) => {
              ev.stopPropagation()
              document.body.style.cursor = 'pointer'
            }}
            onPointerOut={() => {
              document.body.style.cursor = ''
            }}
          />
        ))}
      </Instances>
    </group>
  )
}

export default function GraphScene({
  selected,
  onSelect,
}: {
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  return (
    <Canvas
      dpr={[1, 1.6]}
      camera={{ position: [0, 0, 74], fov: 42 }}
      gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
      onPointerMissed={() => onSelect(null)}
      style={{ background: 'transparent' }}
    >
      <Scene selected={selected} onSelect={onSelect} />
    </Canvas>
  )
}
