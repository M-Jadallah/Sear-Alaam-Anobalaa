import { expect } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import type { Page } from '@playwright/test';

/** Literal conservation assertions use the source's existing vowels. The
 * editorial additions have their own enabled/disabled browser and live QA. */
export async function useSourceVocalization(page: Page) {
  await expect(page.getByTestId('pilot-reader')).toBeVisible();
  const added = page.getByRole('checkbox', { name: 'إظهار الحركات المضافة', exact: true });
  if (await added.count()) await added.uncheck();
}

/** The installed library may legitimately contain batches after the pilot.
 * Resolve a real outside-batch control instead of assuming biography6 is raw. */
export async function firstUncuratedBiography(request: APIRequestContext, sourceId: number): Promise<{ id: number; source_ordinal: number } | undefined> {
  const response = await request.get('/api/curation/batches');
  expect(response.ok()).toBe(true);
  const catalog = await response.json() as { items: { source_id: number; legacy: boolean; end_ordinal: number; biographies: { id: number }[] }[] };
  const published = new Set(catalog.items.flatMap(batch => batch.biographies.map(biography => biography.id)));
  const legacyEnd = Math.max(0, ...catalog.items.filter(batch => batch.source_id === sourceId && batch.legacy).map(batch => batch.end_ordinal));
  for (let page = 1; ; page++) {
    const response = await request.get(`/api/biographies?page=${page}&page_size=100`);
    expect(response.ok()).toBe(true);
    const result = await response.json() as { items: { id: number; source_id: number; source_ordinal: number }[]; total: number; page_size: number };
    const candidate = result.items.find(biography => biography.source_id === sourceId && biography.source_ordinal > legacyEnd && !published.has(biography.id));
    if (candidate) return candidate;
    if (page * result.page_size >= result.total || !result.items.length) return undefined;
  }
}
