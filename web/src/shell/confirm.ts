import { strings } from '../strings';

const plural = (n: number, one: string, other: string) => (n === 1 ? one : other);

/** What the "start a new upload?" dialog says is about to be lost: edited and checked results. */
export function confirmBody(edited: number, checked: number): string {
  const c = strings.shell.confirm;
  let work: string;
  if (edited > 0 && checked > 0) {
    work = c.both(edited, plural(edited, c.structure, c.structures), checked);
  } else if (edited > 0) {
    work = c.edited(edited, plural(edited, c.structure, c.structures));
  } else {
    work = c.checked(checked, plural(checked, c.structure, c.structures));
  }
  return `${work} ${c.consequence}`;
}
