import { describe, expect, it } from "vitest";

import workbenchStyles from "./styles.css?raw";

describe("workbench dark color contract", () => {
  it("keeps global pages light and scopes explicit dark tokens to the scheduled-task route", () => {
    expect(workbenchStyles).toMatch(/:root\s*\{[^}]*color-scheme:\s*light;/);
    expect(workbenchStyles).not.toMatch(/@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{/);
    expect(workbenchStyles).toMatch(/@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*\.scheduled-tasks-route\s*\{[\s\S]*?color-scheme:\s*dark;[\s\S]*?--canvas:[^;]+;[\s\S]*?--surface:[^;]+;[\s\S]*?--ink:[^;]+;[\s\S]*?--ink-soft:[^;]+;[\s\S]*?--line-strong:[^;]+;/);
  });

  it("routes status, error, focus, and disabled colors through shared tokens", () => {
    for (const token of [
      "--status-success-ink", "--status-success-line", "--status-success-surface",
      "--status-progress-ink", "--status-progress-line", "--status-progress-surface",
      "--status-warning-ink", "--status-warning-line", "--status-warning-surface",
      "--status-danger-ink", "--status-danger-line", "--status-danger-surface",
      "--control-disabled-ink", "--control-disabled-surface", "--error-surface", "--focus",
    ]) {
      expect(workbenchStyles).toContain(`${token}:`);
    }

    expect(workbenchStyles).toMatch(/\.status-success\s*\{[^}]*color:\s*var\(--status-success-ink\);[^}]*border-color:\s*var\(--status-success-line\);[^}]*background:\s*var\(--status-success-surface\);/);
    expect(workbenchStyles).toMatch(/\.global-nav-item\.active\s*\{[^}]*color:\s*var\(--on-accent\);/);
    expect(workbenchStyles).toMatch(/\.primary-button\s*\{[^}]*color:\s*var\(--on-accent\);/);
    expect(workbenchStyles).toMatch(/\.scheduled-task-form-error[^}]*background:\s*var\(--error-surface\);/);
    expect(workbenchStyles).toMatch(/\.scheduled-task-form[^}]*:disabled[^}]*color:\s*var\(--control-disabled-ink\);[^}]*background:\s*var\(--control-disabled-surface\);/);
  });

  it("keeps scheduled-task surfaces and controls free of light-only color literals", () => {
    const scheduledStyles = workbenchStyles.slice(workbenchStyles.indexOf(".scheduled-tasks-page"));
    expect(scheduledStyles).not.toMatch(/(?:background|color|border-color):\s*(?:white|#[0-9a-f]{3,8})\b/i);
  });
});
