import { RunView } from "../../../components/run/RunView";
import { getRun, getRunIndex } from "../../../lib/runs";

export function generateStaticParams() {
  return getRunIndex().map((r) => ({ id: r.id }));
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RunView run={getRun(id)} others={getRunIndex()} />;
}
