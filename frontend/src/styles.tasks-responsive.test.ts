import { afterEach, beforeEach, describe, expect, it } from "vitest";

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
  describe("long source references", () => {
    let stylesheet: HTMLStyleElement;
    let row: HTMLDivElement;

    beforeEach(() => {
      stylesheet = document.createElement("style");
      stylesheet.textContent = workbenchStyles;
      document.head.append(stylesheet);
      row = document.createElement("div");
      row.className = "business-project-row";
      row.style.width = "330px";
      document.body.append(row);
    });

    afterEach(() => {
      row.remove();
      stylesheet.remove();
    });

    it.each(["span", "a"])("delivers shrinkable wrapping styles to %s references", (tag) => {
      const metadata = document.createElement("p");
      metadata.className = "business-task-meta";
      const reference = document.createElement(tag);
      const source = `registry-source:${"0123456789abcdef".repeat(12)}`;
      reference.textContent = source;
      if (reference instanceof HTMLAnchorElement) reference.href = "https://example.com/source";
      metadata.append(reference);
      row.append(metadata);

      // JSDOM has no layout engine: verify delivery of the real stylesheet's
      // wrapping contract; browser viewport checks verify the rendered width.
      const metadataStyle = getComputedStyle(metadata);
      expect(metadataStyle.display).toBe("flex");
      expect(metadataStyle.flexWrap).toBe("wrap");
      expect(metadataStyle.minWidth).toBe("0px");
      const referenceStyle = getComputedStyle(reference);
      expect(referenceStyle.overflowWrap).toBe("anywhere");
      expect(referenceStyle.minWidth).toBe("0px");
      expect(referenceStyle.maxWidth).toBe("100%");
      expect(referenceStyle.whiteSpace).not.toBe("nowrap");
      expect(referenceStyle.textOverflow).not.toBe("ellipsis");
      expect(referenceStyle.overflow).not.toBe("hidden");
      expect(reference.textContent).toBe(source);
    });
  });

  it("wraps dense project accountability and stacks task rows on narrow screens", () => {
    expect(workbenchStyles).toMatch(/\.business-project-facts\s*\{[^}]*min-width:\s*0;[^}]*overflow-wrap:\s*anywhere;/);
    expect(workbenchStyles).toMatch(/\.business-citations blockquote\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere;/);
    expect(workbenchStyles).toMatch(/@media \(max-width:\s*600px\)\s*\{[\s\S]*?\.business-task-row, \.business-project-row\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/);
    expect(workbenchStyles).toMatch(/\.business-candidate-group h2\s*\{[^}]*color:\s*var\(--ink\);/);
  });
  it("scopes dark readable tokens to Tasks and keeps card text on theme variables", () => {
    expect(workbenchStyles).toMatch(/@media \(prefers-color-scheme: dark\)\s*\{\s*\.task-domain-route\s*\{[^}]*--canvas:\s*#111411;[^}]*--ink:\s*#f0f3ed;[^}]*--accent:\s*#61d0a6;/);
    expect(workbenchStyles).toMatch(/\.business-attention-card\s*\{[^}]*color:\s*var\(--ink\);[^}]*background:\s*var\(--surface\);/);
    expect(workbenchStyles).toMatch(/\.business-attention-action dd\s*\{[^}]*color:\s*var\(--ink\);/);
  });

  it("keeps the CEO action visible and text wrapping at phone width", () => {
    expect(workbenchStyles).toMatch(/@media \(max-width:\s*600px\)\s*\{[\s\S]*?\.business-attention-card dl > div\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/);
    expect(workbenchStyles).toMatch(/\.business-attention-card dd\s*\{[^}]*overflow-wrap:\s*anywhere;/);
  });
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
