import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/../tests/test-utils";
import TopUserView from "./TopUserView";

vi.mock("@/components/shared/charts", () => ({
  BarChart: ({ data }: { data: Array<{ user_id: string }> }) => (
    <div data-testid="top-users-chart">{data.map((user) => user.user_id).join(",")}</div>
  ),
}));

describe("TopUserView", () => {
  const topUsers = [
    {
      user_id: "user-1",
      user_alias: "Alice",
      user_email: "alice@example.com",
      spend: 12.5,
    },
    {
      user_id: "user-2",
      user_alias: null,
      user_email: "bob@example.com",
      spend: 8,
    },
  ];

  it("shows user identity and spend in table view", () => {
    renderWithProviders(<TopUserView topUsers={topUsers} topUsersLimit={5} setTopUsersLimit={vi.fn()} />);

    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getByText("bob@example.com")).toBeInTheDocument();
    expect(screen.getByText("$12.50")).toBeInTheDocument();
  });

  it("switches to a chart and updates the result limit", () => {
    const setTopUsersLimit = vi.fn();
    renderWithProviders(<TopUserView topUsers={topUsers} topUsersLimit={5} setTopUsersLimit={setTopUsersLimit} />);

    fireEvent.click(screen.getByRole("radio", { name: "10" }));
    expect(setTopUsersLimit).toHaveBeenCalledWith(10);

    fireEvent.click(screen.getByRole("button", { name: "Chart View" }));
    expect(screen.getByTestId("top-users-chart")).toHaveTextContent("user-1,user-2");
  });
});
