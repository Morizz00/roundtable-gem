// Original mark (a ring of dots), not a vendor logo.
export function BrandMark({ size = 30 }: { size?: number }) {
  const dots = Array.from({ length: 12 }, (_, i) => {
    const a = (i / 12) * Math.PI * 2;
    return { x: 20 + Math.cos(a) * 15, y: 20 + Math.sin(a) * 15, r: i % 3 === 0 ? 2.1 : 1.5 };
  });
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      {dots.map((d, i) => <circle key={i} cx={d.x} cy={d.y} r={d.r} fill="#f4f6fb" opacity={0.55 + (i % 3 === 0 ? 0.45 : 0)} />)}
    </svg>
  );
}
