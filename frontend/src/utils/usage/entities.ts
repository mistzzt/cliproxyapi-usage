// Entity lookup by dimension (labels and color slots from the options endpoint) and dimension pairings (pure).
import type { Entity, OptionsResponse } from '@/types/api';
import { shortModel } from './modelNames';
import { ACCENT_COLOR, OTHER_COLOR, slotColor, type Theme } from './palette';
import type { Dimension } from './viewState';

export type EntityIndex = Record<Dimension, ReadonlyMap<string, Entity>>;

export const DIMENSION_LABEL: Record<Dimension, string> = {
  person: 'Person',
  model: 'Model',
  credential: 'Credential',
};

/** The paired dimension shown in a row's mix bar. */
export const MIX_DIMENSION: Record<Dimension, Dimension> = { person: 'model', model: 'person', credential: 'person' };

/** Drawer breakdowns: the first splits the over-time chart (and equals MIX_DIMENSION). */
export const DRAWER_DIMENSIONS: Record<Dimension, readonly [Dimension, Dimension]> = {
  person: ['model', 'credential'],
  model: ['person', 'credential'],
  credential: ['person', 'model'],
};

export function indexOptions(options: OptionsResponse | null): EntityIndex {
  const index = (list: readonly Entity[] = []) => new Map(list.map((e) => [e.key, e]));
  return { person: index(options?.people), model: index(options?.models), credential: index(options?.credentials) };
}

/** Unknown keys (e.g. first seen after options loaded) fall back to the key with no slot. */
export function entityOf(index: EntityIndex, dim: Dimension, key: string, label = key): Entity {
  return index[dim].get(key) ?? { key, label, slot: null };
}

/** Models read as short names (`opus-4.6`); people and credentials use their label as-is. */
export function displayName(dim: Dimension, label: string): string {
  return dim === 'model' ? shortModel(label) : label;
}

/** Theme-bound entity lookup shared by the dashboard components. */
export interface EntityLookup {
  theme: Theme;
  other: string;
  accent: string;
  entity(dim: Dimension, key: string): Entity;
  color(dim: Dimension, key: string): string;
  slotColor(slot: number | null): string;
}

export function createLookup(options: OptionsResponse | null, theme: Theme): EntityLookup {
  const index = indexOptions(options);
  return {
    theme,
    other: OTHER_COLOR[theme],
    accent: ACCENT_COLOR[theme],
    entity: (dim, key) => entityOf(index, dim, key),
    color: (dim, key) => slotColor(entityOf(index, dim, key).slot, theme),
    slotColor: (slot) => slotColor(slot, theme),
  };
}
