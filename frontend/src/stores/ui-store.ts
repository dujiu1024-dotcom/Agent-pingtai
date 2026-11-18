import { create } from 'zustand'

interface UiState {
  sidebarCollapsed: boolean
  commandPaletteOpen: boolean
  setSidebarCollapsed: (collapsed: boolean) => void
  setCommandPaletteOpen: (open: boolean) => void
  toggleSidebar: () => void
}

export const useUiStore = create<UiState>((set) => ({
  sidebarCollapsed: false,
  commandPaletteOpen: false,
  setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
  setCommandPaletteOpen: (commandPaletteOpen) => set({ commandPaletteOpen }),
  toggleSidebar: () =>
    set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
}))
