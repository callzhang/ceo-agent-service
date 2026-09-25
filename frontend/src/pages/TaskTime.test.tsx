import { describe, expect, it } from "vitest";

import { formatTaskTime } from "./TaskTime";

const now = new Date(2026, 8, 25, 12, 0, 0);
const local = (month: number, day: number, hour: number, minute: number, year = 2026) => new Date(year, month - 1, day, hour, minute).toISOString();

describe("formatTaskTime", () => {
  it("says how long ago a recent change was, then switches to a clock time and a date", () => {
    expect(formatTaskTime(local(9, 25, 11, 59, 2026), now)).toBe("1 分钟前");
    expect(formatTaskTime(local(9, 25, 11, 20), now)).toBe("40 分钟前");
    expect(formatTaskTime(local(9, 25, 8, 5), now)).toBe("今天 08:05");
    expect(formatTaskTime(local(9, 24, 16, 48), now)).toBe("昨天 16:48");
    expect(formatTaskTime(local(9, 9, 16, 48), now)).toMatch(/^9月9日 16:48$/);
    expect(formatTaskTime(local(12, 20, 9, 30, 2025), now)).toMatch(/^2025\/12\/20 09:30$/);
  });

  it("reads a backend timestamp as UTC and leaves unparsable text as it came", () => {
    const utcNow = new Date(Date.UTC(2026, 8, 25, 12, 0, 0));
    expect(formatTaskTime("2026-09-25 11:30:00", utcNow)).toBe("30 分钟前");
    expect(formatTaskTime("2026-09-28", now)).toBe("2026-09-28");
    expect(formatTaskTime("下周一", now)).toBe("下周一");
  });
});
