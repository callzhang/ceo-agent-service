import { describe, expect, it } from "vitest";

import workbenchStyles from "./styles.css?raw";

function mediumStyles() {
  const start = workbenchStyles.indexOf("@media (min-width: 721px) and (max-width: 1100px)");
  const end = workbenchStyles.indexOf("@media (max-width: 720px)", start);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  return workbenchStyles.slice(start, end);
}

describe("Tasks responsive layout contract", () => {
  it("keeps wide filters dense and introduces the medium breakpoint before mobile", () => {
    expect(workbenchStyles).toMatch(
      /\.filter-bar\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto;/,
    );
    expect(mediumStyles()).toMatch(
      /\.filter-bar\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/,
    );
  });

  it("uses four main controls on row one and right-aligns page metadata on row two", () => {
    const styles = mediumStyles();
    expect(styles).toMatch(
      /\.filter-bar > \.filter-bar-main\s*\{[^}]*grid-template-columns:\s*minmax\(220px, 1\.6fr\) repeat\(3, minmax\(120px, 0\.85fr\)\);/,
    );
    expect(styles).toMatch(
      /\.filter-bar > \.filter-bar-side\s*\{[^}]*justify-content:\s*flex-end;/,
    );
  });

  it("compacts every navigation destination without hiding one", () => {
    const styles = mediumStyles();
    expect(styles).toMatch(/\.global-brand\s*\{[^}]*min-width:\s*156px;/);
    expect(styles).toMatch(/\.global-nav-track\s*\{[^}]*gap:\s*4px;[^}]*padding:\s*8px 10px;/);
    expect(styles).toMatch(/\.global-nav-item\s*\{[^}]*min-width:\s*72px;[^}]*padding:\s*0 9px;/);
  });

  it("contains the wide Sent TODO table inside its own scroll region", () => {
    expect(mediumStyles()).toMatch(
      /\.sent-todos-table-wrap\s*\{[^}]*max-width:\s*100%;[^}]*overflow-x:\s*auto;/,
    );
  });
});
