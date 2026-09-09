import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AppShell } from "./AppShell";

function renderShell(pathname: string) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <AppShell><main>content</main></AppShell>
    </MemoryRouter>,
  );
}

describe("AppShell route styling", () => {
  it("scopes the scheduled-task theme class to that route", () => {
    const scheduled = renderShell("/scheduled-tasks");
    expect(scheduled.container.firstElementChild).toHaveClass("console-root", "scheduled-tasks-route");
    scheduled.unmount();

    const history = renderShell("/history");
    expect(history.container.firstElementChild).toHaveClass("console-root");
    expect(history.container.firstElementChild).not.toHaveClass("scheduled-tasks-route");
  });
});
