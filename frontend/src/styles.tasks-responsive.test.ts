import { describe, expect, it } from "vitest";

import workbenchStyles from "./styles.css?raw";
import emailStyles from "./pages/email/email.css?raw";

function mediumStyles() {
  const blocks = workbenchStyles.match(
    /@media \(min-width: 721px\) and \(max-width: 1100px\) \{[\s\S]*?^\}/gm,
  );
  expect(blocks?.length).toBeGreaterThan(0);
  return blocks!.join("\n");
}

describe("Tasks responsive layout contract", () => {
  it("lets the console grid and page shrink below their intrinsic content width", () => {
    expect(workbenchStyles).toMatch(
      /\.console-root\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/,
    );
    expect(workbenchStyles).toMatch(/\.console-page\s*\{[^}]*min-width:\s*0;/);
  });

  it("lets every shared page shell use the available console width", () => {
    const fullWidthShells = [
      ".console-page-card",
      ".console-page-header",
      ".attempt-title-row",
      ".console-card",
      ".status-metric-grid",
      ".settings-layout-react",
      ".history-page, .tasks-page, .feedback-page",
      ".attempt-action-message",
      ".attempt-review-grid",
      ".scheduled-task-notice",
      ".scheduled-task-workspace",
    ];

    for (const selector of fullWidthShells) {
      const rule = workbenchStyles.match(
        new RegExp(`${selector.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`),
      )?.[1];
      expect(rule, selector).toBeDefined();
      expect(rule, selector).not.toContain("max-width: 1180px");
      expect(rule, selector).not.toContain("margin: 0 auto");
    }
  });

  it("does not reintroduce a page-width cap through the Email-specific shell", () => {
    const rule = emailStyles.match(
      /\.console-page:has\(\.email-tabs\)\s*\{([^}]*)\}/,
    )?.[1];
    expect(rule).toBeDefined();
    expect(rule).not.toContain("max-width: 1560px");
  });

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
    expect(workbenchStyles.lastIndexOf("@media (min-width: 721px) and (max-width: 1100px)")).toBeGreaterThan(
      workbenchStyles.indexOf(".global-nav-item {"),
    );
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
