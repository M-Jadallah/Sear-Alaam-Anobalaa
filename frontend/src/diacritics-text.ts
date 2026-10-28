import type { ReadingRun } from './api';
import type { AddedDiacritic, DiacriticsLayer } from './pilot-types';

export const usableDiacritics = (layer?: DiacriticsLayer) => layer?.status === 'ready' || layer?.status === 'partial';

export interface SourceCluster { start: number; end: number; text: string }

/** Group the source base and its existing combining marks without normalizing
 * or changing their order. All offsets remain original Unicode code points. */
export function sourceClusters(text: string, offset: number): SourceCluster[] {
  const clusters: SourceCluster[] = [];
  Array.from(text).forEach((char, index) => {
    const previous = clusters.at(-1);
    if (/\p{M}/u.test(char) && previous) { previous.text += char; previous.end++; }
    else clusters.push({ start: offset + index, end: offset + index + 1, text: char });
  });
  return clusters;
}

export function diacriticsByBase(insertions: AddedDiacritic[]) {
  return new Map(insertions.map(insertion => [insertion.base_start, insertion]));
}

export function clusterText(cluster: SourceCluster, additions: Map<number, AddedDiacritic>): string {
  const addition = additions.get(cluster.start);
  // Defense in depth: even a malformed client payload cannot move a mark
  // away from its recorded original cluster. Full validation is server-side.
  return cluster.text + (addition?.at === cluster.end && /^[\u064b-\u0652]+$/u.test(addition.marks) ? addition.marks : '');
}

export function vocalizedRunText(run: ReadingRun, insertions: AddedDiacritic[]): string {
  if (run.type !== 'text') return run.text;
  const additions = diacriticsByBase(insertions);
  return sourceClusters(run.text, run.source_char_start).map(cluster => clusterText(cluster, additions)).join('');
}
