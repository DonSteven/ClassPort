import { createDemoData, type DemoData } from "./data";

export const DEMO_STORAGE_KEY = "classport:demo";

export function readDemoData(): DemoData {
  const saved = localStorage.getItem(DEMO_STORAGE_KEY);
  if (saved) {
    try {
      return JSON.parse(saved) as DemoData;
    } catch {
      localStorage.removeItem(DEMO_STORAGE_KEY);
    }
  }
  const data = createDemoData();
  saveDemoData(data);
  return data;
}

export function saveDemoData(data: DemoData): void {
  localStorage.setItem(DEMO_STORAGE_KEY, JSON.stringify(data));
}

export function resetDemoData(): void {
  saveDemoData(createDemoData());
}
