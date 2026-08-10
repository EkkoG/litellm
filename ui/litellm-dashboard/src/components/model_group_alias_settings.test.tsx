import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ModelGroupAliasSettings from "./model_group_alias_settings";

const { createModelGroupAliasCall, deleteModelGroupAliasCall, updateModelGroupAliasCall } = vi.hoisted(() => ({
  createModelGroupAliasCall: vi.fn(),
  deleteModelGroupAliasCall: vi.fn(),
  updateModelGroupAliasCall: vi.fn(),
}));

vi.mock("./networking", () => ({
  createModelGroupAliasCall,
  deleteModelGroupAliasCall,
  updateModelGroupAliasCall,
}));

vi.mock("./molecules/notifications_manager", () => ({
  __esModule: true,
  default: {
    success: vi.fn(),
    fromBackend: vi.fn(),
  },
}));

describe("ModelGroupAliasSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createModelGroupAliasCall.mockResolvedValue({});
    updateModelGroupAliasCall.mockResolvedValue({});
    deleteModelGroupAliasCall.mockResolvedValue({});
  });

  it("preserves hidden object alias values when adding another alias", async () => {
    render(
      <ModelGroupAliasSettings
        accessToken="sk-test"
        initialModelGroupAlias={{
          "hidden-alias": { model: "gpt-4o-prod", hidden: true },
        }}
        availableModelGroups={["gpt-4o-prod", "gpt-4o-mini"]}
        canEdit
      />,
    );

    await userEvent.type(screen.getByPlaceholderText("e.g., gpt-4o"), "new-alias");
    const targetSelect = screen.getAllByRole("combobox")[0];
    await act(async () => {
      fireEvent.mouseDown(targetSelect);
    });
    await act(async () => {
      fireEvent.click(screen.getByTitle("gpt-4o-mini"));
    });

    await userEvent.click(screen.getByRole("button", { name: /add alias/i }));
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(createModelGroupAliasCall).toHaveBeenCalledTimes(1));
    const expectedCreateRequest = {
      name: "new-alias",
      model: "gpt-4o-mini",
      hidden: false,
      enabled: true,
    };
    expect(createModelGroupAliasCall).toHaveBeenCalledWith("sk-test", expectedCreateRequest);
    expect(updateModelGroupAliasCall).not.toHaveBeenCalled();
    expect(deleteModelGroupAliasCall).not.toHaveBeenCalled();
  });
});
